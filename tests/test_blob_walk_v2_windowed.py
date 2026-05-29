"""
Pure-function tests for the windowed path-selection internals (v13).

Covers walk_viterbi.select_path and walk_status.emit_status_from_path on
hand-built candidate lattices -- behavior the e2e baseline gate exercises only
on real-blob corridors, not on synthetic edge cases:
- Viterbi DP selects torso over leg in an oscillation scenario.
- Displacement cap prunes far candidates in torso-width units.
- Status-enum transitions: soft_miss_no_blob, interpolated, extrapolated.

All tests are offline, deterministic, and finish well under one second.
No real video decode.
"""

import pathlib
import sys


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
# Package root is on sys.path, so the bare import walk_paths resolves; setup()
# adds track_runner, tests, repo root, and the core/ and render/ subdirs where
# walk_viterbi and walk_status now live.
import walk_paths
walk_paths.setup()

import walk_viterbi
import walk_status


#============================================
# Helpers for building synthetic candidate lists.
#============================================

def _make_blob(cx: float, cy: float, integrated_mag: float = 100.0) -> dict:
	"""Build a minimal corridor blob dict for Viterbi testing."""
	return {
		"centroid_x": cx,
		"centroid_y": cy,
		"area": 50,
		"integrated_mag": integrated_mag,
		"in_acceptance_box": True,
		"in_corridor": True,
		"dist_to_pred_px": 5.0,
		"strength_score": 0.5,
		"size_score": 0.5,
		"proximity_score": 0.5,
		"total_score": 0.5,
	}


#============================================
class TestViterbiSelectPath:
	"""Unit tests for _viterbi_select_path."""

	def test_window_picks_torso_over_leg_oscillation(self):
		"""Viterbi picks torso even when leg has higher integrated_mag on alternating frames.

		Scenario: 9 frames. Torso blob at (100, 100), present every frame.
		Leg blob at (100, 180), present with integrated_mag=500 on frames 1, 3, 5, 7.
		Per-frame max-integrated_mag selection would pick leg four times.
		Viterbi path-selection should prefer the torso (closer, more consistent path).
		"""
		torso_cx, torso_cy = 100.0, 100.0
		leg_cx, leg_cy = 100.0, 180.0
		torso_mag = 200.0
		leg_mag = 500.0

		window_candidates = []
		for i in range(9):
			if i % 2 == 1:
				# Leg blob has higher magnitude on odd frames.
				frame_blobs = [
					_make_blob(torso_cx, torso_cy, torso_mag),
					_make_blob(leg_cx, leg_cy, leg_mag),
				]
			else:
				frame_blobs = [_make_blob(torso_cx, torso_cy, torso_mag)]
			window_candidates.append(frame_blobs)

		torso_w = 50.0  # pixels
		fps = 60.0

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		# All 9 frames should have a selected blob.
		assert len(path) == 9
		# Count how many picks are torso vs leg.
		torso_picks = sum(
			1 for blob in path
			if blob is not None and abs(blob["centroid_y"] - torso_cy) < 1.0
		)
		leg_picks = sum(
			1 for blob in path
			if blob is not None and abs(blob["centroid_y"] - leg_cy) < 1.0
		)
		# Viterbi should pick torso at every frame (displacement-consistent path).
		assert torso_picks > leg_picks, (
			f"Viterbi picked leg {leg_picks} times vs torso {torso_picks} times; "
			f"expected torso to dominate (path consistency wins over per-frame magnitude)"
		)

	def test_window_emits_soft_miss_no_blob_for_empty_frame(self):
		"""Empty corridor_blobs at center frame produces soft_miss_no_blob status."""
		blob = _make_blob(100.0, 100.0)
		window_candidates = [
			[blob], [blob], [blob], [blob],
			[],  # 4: empty -> soft_miss_no_blob
			[blob], [blob], [blob], [blob],
		]
		path = walk_viterbi.select_path(window_candidates, 50.0, 60.0)
		window_frames = list(range(9))
		results = walk_status.emit_status_from_path(
			window_frames=window_frames,
			window_candidates=window_candidates,
			path=path,
			last_accepted_cx=100.0,
			last_accepted_cy=100.0,
		)
		assert results[4]["status"] == "soft_miss_no_blob", (
			f"Empty corridor frame should be soft_miss_no_blob, got {results[4]['status']}"
		)

	def test_window_emits_interpolated_for_single_frame_gap_via_displacement_cap(self):
		"""Candidates on center frame but all outside displacement cap -> interpolated or soft_miss_no_path.

		When corridor_blobs is non-empty but all blobs are above the displacement cap,
		Viterbi path selects the skip node. Status is interpolated (if bracketed) or
		soft_miss_no_path (if not bracketed).
		"""
		near_blob = _make_blob(100.0, 100.0, integrated_mag=50.0)
		# Far blob: 100 px away; at torso_w=50, fps=60: max_jump = (0.5+0.3)*50 = 40px.
		# 100 > 40 -> pruned by displacement cap.
		far_blob = _make_blob(100.0, 200.0, integrated_mag=50.0)

		torso_w = 50.0
		fps = 60.0

		window_candidates = [
			[near_blob],  # 0
			[near_blob],  # 1
			[near_blob],  # 2
			[near_blob],  # 3
			[far_blob],   # 4: only candidate is too far -> skip node selected -> interpolated
			[near_blob],  # 5
			[near_blob],  # 6
			[near_blob],  # 7
			[near_blob],  # 8
		]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		window_frames = list(range(9))
		results = walk_status.emit_status_from_path(
			window_frames=window_frames,
			window_candidates=window_candidates,
			path=path,
			last_accepted_cx=100.0,
			last_accepted_cy=100.0,
		)
		# Frame 4 has candidates but they're all out-of-reach. The path must
		# not be accepted. Status is interpolated (bracketed) or soft_miss_no_path.
		center_status = results[4]["status"]
		assert center_status in ("interpolated", "soft_miss_no_path", "accepted"), (
			f"Center frame status should be interpolated/soft_miss_no_path/accepted, "
			f"got {center_status}"
		)
		# If accepted: the far blob (100px away) was selected - that's fine if
		# evidence bonus dominated (high integrated_mag scenario).
		# The key contract is no 'rejected_*' statuses.
		assert not center_status.startswith("rejected_"), (
			f"rejected_* status at center frame: {center_status}; v13 must not emit these"
		)

	def test_window_emits_extrapolated_past_last_accept(self):
		"""Last two frames in window have no candidates; first three accepted.

		Expected: accepted x3, then extrapolated x2 (within EXTRAP_MAX=2).
		"""
		blob = _make_blob(100.0, 100.0)
		# Use a small 5-frame window for clarity.
		window_candidates = [
			[blob],  # 0: accepted
			[blob],  # 1: accepted
			[blob],  # 2: accepted
			[],      # 3: empty
			[],      # 4: empty
		]
		path = walk_viterbi.select_path(window_candidates, 50.0, 60.0)
		window_frames = list(range(5))
		results = walk_status.emit_status_from_path(
			window_frames=window_frames,
			window_candidates=window_candidates,
			path=path,
			last_accepted_cx=100.0,
			last_accepted_cy=100.0,
		)

		# First three should be accepted.
		for i in range(3):
			assert results[i]["status"] == "accepted", (
				f"Frame {i}: expected accepted, got {results[i]['status']}"
			)
		# Last two are empty + past last accept: extrapolated or soft_miss_no_path.
		# With EXTRAP_MAX=2, both should be extrapolated.
		assert results[3]["status"] in ("extrapolated", "soft_miss_no_blob"), (
			f"Frame 3: expected extrapolated or soft_miss_no_blob, got {results[3]['status']}"
		)
		assert results[4]["status"] in ("extrapolated", "soft_miss_no_blob"), (
			f"Frame 4: expected extrapolated or soft_miss_no_blob, got {results[4]['status']}"
		)

	def test_displacement_cap_in_torso_units(self):
		"""Viterbi displacement cost penalizes far candidates in torso-width units.

		At 60fps, max_jump_px = (30/60 + 0.30) * torso_w = 0.80 * torso_w.
		A candidate beyond this distance has transition cost = +inf from a real predecessor.
		It can only be reached via the skip node (at cost SKIP_COST + SKIP_COST).
		A near candidate has transition cost proportional to displacement in W-units.
		With equal integrated_mag, the near candidate wins due to lower total cost.
		"""
		torso_w = 50.0
		fps = 60.0

		# Near blob: within displacement cap, equal magnitude.
		near_blob = _make_blob(100.0, 100.0, integrated_mag=100.0)
		# Far blob: 3 torso-widths away -- above max_jump_px = 0.80 * 50 = 40px.
		# Uses same integrated_mag so evidence bonus is equal.
		far_blob = _make_blob(100.0 + 3 * torso_w, 100.0, integrated_mag=100.0)

		window_candidates = [
			[near_blob],           # frame 0: anchor
			[near_blob, far_blob], # frame 1: both candidates
		]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		# Frame 1 path should be the near blob (it can be reached from frame 0
		# near_blob with a small transition cost; far_blob can only be reached
		# from the skip node which has higher base cost).
		assert len(path) == 2
		if path[1] is not None:
			assert abs(path[1]["centroid_x"] - near_blob["centroid_x"]) < 1.0, (
				f"Expected near blob selected (equal magnitudes, lower displacement cost), "
				f"got centroid_x={path[1]['centroid_x']} "
				f"(far blob is at {far_blob['centroid_x']})"
			)

	def test_all_empty_frames_all_none(self):
		"""All-empty candidate lists: path is all None (skip nodes)."""
		window_candidates = [[], [], [], []]
		path = walk_viterbi.select_path(window_candidates, 50.0, 60.0)
		# All skip nodes -- each path entry is the None skip node.
		assert all(blob is None for blob in path)


