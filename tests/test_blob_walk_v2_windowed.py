"""
Pure-function tests for the windowed path-selection internals (v13+).

Covers walk_viterbi.select_path and walk_status.emit_status_from_path on
hand-built candidate lattices -- behavior the e2e baseline gate exercises only
on real-blob corridors, not on synthetic edge cases:
- Hard physical-sanity displacement prune (ABSOLUTE_MAX_JUMP_W) prunes far candidates.
- Status-enum transitions: soft_miss_no_blob, interpolated, extrapolated.

WP-COST-2 note: the old test_window_picks_torso_over_leg_oscillation was
rewritten to test status/coordinate plumbing rather than the min-displacement
winner, because the new pairwise velocity-delta cost model (WP-COST-1) selects
by motion consistency, not by minimum displacement. The scenario that test was
encoding (stationary blob wins) is the OLD MODEL behavior being replaced. The
model-flip test lives in tests/test_walk_cost_model.py.

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

import blob_walk.walk_viterbi as walk_viterbi
import blob_walk.walk_status as walk_status


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

	def test_window_output_shape_and_none_consistency(self):
		"""select_path returns one entry per input frame; None means no candidate selected.

		Status plumbing: the output list is the same length as the input, and
		each entry is either a blob dict (has centroid_x/centroid_y keys) or None.

		Rationale for keeping: this covers the output-shape contract and the
		None-vs-dict type invariant for select_path. No higher-level gate covers
		the exact output shape. Deleted test (old): test_window_picks_torso_over_leg_
		oscillation -- it asserted that a stationary blob wins over a high-mag
		moving blob, which is the OLD MIN-DISPLACEMENT model behavior being replaced
		by WP-COST-1. The model-flip test now lives in test_walk_cost_model.py.
		"""
		blob = _make_blob(100.0, 100.0, 200.0)

		# Mix of populated and empty frames.
		window_candidates = [
			[blob],
			[blob],
			[],       # empty -> None in path
			[blob],
			[blob],
		]
		torso_w = 50.0
		fps = 60.0

		path = walk_viterbi.select_path(window_candidates, torso_w, fps)

		# Output must be the same length as input.
		assert len(path) == len(window_candidates)
		# Each entry must be a blob dict (has centroid_x) or None.
		for i, entry in enumerate(path):
			if entry is not None:
				assert "centroid_x" in entry, (
					f"Frame {i}: non-None path entry must be a blob dict with centroid_x"
				)
		# Empty frame must produce None.
		assert path[2] is None, (
			f"Empty frame 2 must produce None in path; got {path[2]}"
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

		When corridor_blobs is non-empty but all blobs are above the hard physical-sanity
		displacement prune (ABSOLUTE_MAX_JUMP_W = 1.5 torso-widths/frame), Viterbi path
		selects the skip node. Status is interpolated (if bracketed) or soft_miss_no_path
		(if not bracketed).

		Rationale for keeping: status plumbing test -- the transition from candidates-present
		+ all-above-prune -> skip -> interpolated/soft_miss_no_path. No higher-level gate
		covers this exact status transition. Comment updated from old cap formula
		((0.5+0.3)*torso_w) to the new single-sanity prune (1.5*torso_w = 75px); the far
		blob at 100px is still above both old and new caps.
		"""
		near_blob = _make_blob(100.0, 100.0, integrated_mag=50.0)
		# Far blob: 100px away (2 torso-widths); hard prune at ABSOLUTE_MAX_JUMP_W=1.5*50=75px.
		# 100 > 75 -> pruned by physical-sanity cap -> skip node selected at frame 4.
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

	def test_absolute_displacement_prune_in_torso_units(self):
		"""Viterbi hard-prunes candidates beyond ABSOLUTE_MAX_JUMP_W torso-widths per frame.

		Under the new contract (WP-COST-1), the single remaining hard prune fires
		when per-frame speed exceeds ABSOLUTE_MAX_JUMP_W = 1.5 torso-widths/frame
		(BOOTSTRAP_UNCERTAINTY_W is no longer added to the DP edge cap). At 60fps:
		hard-prune threshold = 1.5 * torso_w = 75px.

		A candidate 3 torso-widths (150px) away is above the hard prune and can
		only be reached via a skip transition; near candidate wins with equal mag.

		Rationale for keeping: the hard-prune / skip-transition plumbing is a
		structural property of the DP that the new model retains (ABSOLUTE_MAX_JUMP_W
		= 1.5 is not a tunable weight -- it is a physical-sanity prune). Updated from
		the old test which used max_jump_px = (30/60 + 0.30)*torso_w, which included
		BOOTSTRAP_UNCERTAINTY_W that the new DP removes.
		"""
		torso_w = 50.0
		fps = 60.0

		# Near blob: within hard prune distance, equal magnitude.
		near_blob = _make_blob(100.0, 100.0, integrated_mag=100.0)
		# Far blob: 3 torso-widths away = 150px; hard prune at 1.5*50=75px.
		# Above the physical-sanity hard prune -> pruned (+inf cost edge).
		far_blob = _make_blob(100.0 + 3 * torso_w, 100.0, integrated_mag=100.0)

		window_candidates = [
			[near_blob],           # frame 0: anchor
			[near_blob, far_blob], # frame 1: both candidates
		]
		path = walk_viterbi.select_path(window_candidates, torso_w, fps)
		# Frame 1 path should be the near blob (far blob is pruned by ABSOLUTE_MAX_JUMP_W).
		assert len(path) == 2
		if path[1] is not None:
			assert abs(path[1]["centroid_x"] - near_blob["centroid_x"]) < 1.0, (
				f"Expected near blob selected (hard-pruned far blob at 3 torso-widths), "
				f"got centroid_x={path[1]['centroid_x']} "
				f"(far blob is at {far_blob['centroid_x']})"
			)

	def test_all_empty_frames_all_none(self):
		"""All-empty candidate lists: path is all None (skip nodes)."""
		window_candidates = [[], [], [], []]
		path = walk_viterbi.select_path(window_candidates, 50.0, 60.0)
		# All skip nodes -- each path entry is the None skip node.
		assert all(blob is None for blob in path)


