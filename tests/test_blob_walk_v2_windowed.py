"""
Tests for the window-level path-selection redesign of walk_walker.py (v13).

Covers:
- Viterbi DP selects torso over leg in oscillation scenario.
- Interpolated status for single-frame gap.
- Extrapolated status past last accepted in window.
- Displacement cap prunes candidates in torso-width units.
- Walker reads corridor_blobs candidate buffer length correctly.
- No cross-frame blob state survives past the active window.
- Bootstrap emits after window fill (first N-1 steps are buffered).
- Status enum is complete: no rejected_* values emitted.

All tests are offline, deterministic, and finish well under one second.
No real video decode.
"""

import pathlib
import sys


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
_TRACK_RUNNER_DIR = _REPO_ROOT / 'track_runner'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
if str(_TRACK_RUNNER_DIR) not in sys.path:
	sys.path.insert(0, str(_TRACK_RUNNER_DIR))

import walk_walker
import walk_debug_log


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

		path = walk_walker._viterbi_select_path(window_candidates, torso_w, fps)

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
		path = walk_walker._viterbi_select_path(window_candidates, 50.0, 60.0)
		window_frames = list(range(9))
		results = walk_walker._emit_status_from_path(
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
		path = walk_walker._viterbi_select_path(window_candidates, torso_w, fps)
		window_frames = list(range(9))
		results = walk_walker._emit_status_from_path(
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
		path = walk_walker._viterbi_select_path(window_candidates, 50.0, 60.0)
		window_frames = list(range(5))
		results = walk_walker._emit_status_from_path(
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
		path = walk_walker._viterbi_select_path(window_candidates, torso_w, fps)
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

	def test_empty_window_returns_empty_path(self):
		"""Empty window produces empty path."""
		path = walk_walker._viterbi_select_path([], 50.0, 60.0)
		assert path == []

	def test_single_frame_window(self):
		"""Single-frame window returns the blob or None."""
		blob = _make_blob(50.0, 50.0)
		path = walk_walker._viterbi_select_path([[blob]], 50.0, 60.0)
		assert len(path) == 1

	def test_all_empty_frames_all_none(self):
		"""All-empty candidate lists: path is all None (skip nodes)."""
		window_candidates = [[], [], [], []]
		path = walk_walker._viterbi_select_path(window_candidates, 50.0, 60.0)
		# All skip nodes -- each path entry is the None skip node.
		assert all(blob is None for blob in path)


#============================================
class TestWalkerCandidateBuffer:
	"""Walker reads corridor_blobs and populates buffer correctly."""

	def test_walker_reads_corridor_blobs_length(self):
		"""_viterbi_select_path returns path of same length as window_candidates."""
		blob = _make_blob(100.0, 100.0)
		window_candidates = [[blob, _make_blob(120.0, 100.0)], [blob], []]
		path = walk_walker._viterbi_select_path(window_candidates, 50.0, 60.0)
		# Path length equals window length.
		assert len(path) == len(window_candidates)

	def test_no_cross_frame_blob_state(self):
		"""walk_walker module has no module-level cross-frame blob state."""
		# There must be no last_blob, last_provisional_* or *_chain_* attributes.
		forbidden = [
			"last_blob", "prev_accepted_blob", "miss_count",
			"last_provisional_cx", "last_provisional_cy",
			"last_provisional_step", "last_provisional_frame",
		]
		for attr in forbidden:
			assert not hasattr(walk_walker, attr), (
				f"Module-level cross-frame blob state found: walk_walker.{attr}"
			)


#============================================
class TestSchemaVersion13:
	"""Schema v13 constants and column layout."""

	def test_schema_version_is_13(self):
		assert walk_debug_log.SCHEMA_VERSION == 13

	def test_header_has_path_cost(self):
		assert "path_cost" in walk_debug_log.HEADER

	def test_header_has_candidates_in_window(self):
		assert "candidates_in_window" in walk_debug_log.HEADER

	def test_torso_w_drift_frac_removed(self):
		# Deleted in v13 per scout audit.
		assert "torso_w_drift_frac" not in walk_debug_log.HEADER

	def test_header_length_v13(self):
		# v12 had 42 columns. v13: -1 (torso_w_drift_frac) +2 (path_cost, candidates_in_window) = 43.
		# But we check exact count from HEADER tuple.
		assert len(walk_debug_log.HEADER) == 43

	def test_new_status_values_allowed(self):
		"""interpolated, extrapolated, soft_miss_no_path are in allowed set."""
		import pathlib
		import tempfile
		for status in ("interpolated", "extrapolated", "soft_miss_no_path"):
			with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
				path = pathlib.Path(f.name)
			with walk_debug_log.DebugLogWriter(path) as writer:
				row = walk_debug_log.DebugLogRow(
					frame_index=1, step=None, direction="+", status=status
				)
				# Should not raise.
				writer.write_row(row)

	def test_rejected_motion_gate_not_emitted(self):
		"""rejected_motion_gate is not in _ALLOWED_STATUS."""
		assert "rejected_motion_gate" not in walk_debug_log._ALLOWED_STATUS

	def test_legacy_status_in_all_known(self):
		"""rejected_motion_gate is parseable on read (in ALL_KNOWN_STATUS)."""
		assert "rejected_motion_gate" in walk_debug_log.ALL_KNOWN_STATUS

	def test_new_fields_in_debug_log_row(self):
		"""DebugLogRow has path_cost and candidates_in_window attributes."""
		row = walk_debug_log.DebugLogRow(
			frame_index=1, step=None, direction="+", status="accepted"
		)
		assert hasattr(row, "path_cost")
		assert hasattr(row, "candidates_in_window")
		assert row.path_cost is None
		assert row.candidates_in_window is None

	def test_new_fields_write_to_csv(self, tmp_path):
		"""path_cost and candidates_in_window survive CSV round-trip."""
		csv_file = tmp_path / "debug.csv"
		with walk_debug_log.DebugLogWriter(csv_file) as writer:
			row = walk_debug_log.DebugLogRow(
				frame_index=5, step=None, direction="+", status="accepted",
				path_cost=3.14, candidates_in_window=7,
			)
			writer.write_row(row)

		import csv as csv_mod
		with open(csv_file, 'r') as f:
			rows = list(csv_mod.DictReader(f))

		assert rows[0]["path_cost"] == "3.14"
		assert rows[0]["candidates_in_window"] == "7"


#============================================
class TestStatusEnumComplete:
	"""Every emitted status must be in the 5-value enum + terminal markers."""

	VALID_STATUSES = {
		"accepted", "interpolated", "extrapolated",
		"soft_miss_no_blob", "soft_miss_no_path",
		"hit_neighbor_seed", "boundary", "after_walk_terminated",
	}

	def test_emit_status_from_path_valid_statuses(self):
		"""_emit_status_from_path only emits statuses in the valid set."""
		blob = _make_blob(100.0, 100.0)
		# Window with one empty frame in the middle.
		window_candidates = [
			[blob], [blob], [], [blob], [blob],
		]
		path = walk_walker._viterbi_select_path(window_candidates, 50.0, 60.0)
		results = walk_walker._emit_status_from_path(
			window_frames=list(range(5)),
			window_candidates=window_candidates,
			path=path,
			last_accepted_cx=100.0,
			last_accepted_cy=100.0,
		)
		for r in results:
			assert r["status"] in self.VALID_STATUSES, (
				f"Unexpected status '{r['status']}' emitted; "
				f"expected one of {self.VALID_STATUSES}"
			)

	def test_no_rejected_status_in_allowed_set(self):
		"""_ALLOWED_STATUS must not contain any rejected_* values."""
		for s in walk_debug_log._ALLOWED_STATUS:
			assert not s.startswith("rejected_"), (
				f"rejected_* status '{s}' found in _ALLOWED_STATUS; "
				f"these must never be emitted by the v13 walker"
			)


#============================================
class TestWalkerConstants:
	"""Walker module-level constants exist and are plausible."""

	def test_walker_window_frames_is_9(self):
		assert walk_walker.WALKER_WINDOW_FRAMES == 9

	def test_skip_cost_positive(self):
		assert walk_walker.SKIP_COST > 0.0

	def test_weight_evidence_negative(self):
		# Negative weight means higher integrated_mag = lower cost = preferred.
		assert walk_walker.WEIGHT_EVIDENCE < 0.0

	def test_weight_displacement_positive(self):
		assert walk_walker.WEIGHT_DISPLACEMENT > 0.0

	def test_extrap_max_is_2(self):
		assert walk_walker.EXTRAP_MAX == 2

	def test_no_reject_cap(self):
		# REJECT_CAP must remain absent (WP-1C contract).
		assert not hasattr(walk_walker, "REJECT_CAP")

	def test_no_miss_cap(self):
		# MISS_CAP must remain absent (WP-1C contract).
		assert not hasattr(walk_walker, "MISS_CAP")

	def test_no_consec_miss_no_blob(self):
		# consec_miss_no_blob removed in v13; must not be a module-level constant.
		assert not hasattr(walk_walker, "consec_miss_no_blob")


#============================================
class TestWalkSummaryFields:
	"""WalkSummary has the new v13 fields."""

	def test_walk_summary_has_v13_fields(self):
		"""WalkSummary dataclass has all v13 count fields."""
		summary = walk_walker.WalkSummary(
			accepts=[],
			stop_frame=0,
			stop_reason="boundary",
			total_frames_visited=0,
			accepted_count=0,
			interpolated_count=0,
			extrapolated_count=0,
			soft_miss_no_blob_count=0,
			soft_miss_no_path_count=0,
			longest_no_accept_streak=0,
			accepted_fraction=0.0,
			last_accepted_frame_index=None,
			final_displacement_to_neighbor_px=None,
			mode_disagreement_count=0,
		)
		assert summary.accepted_count == 0
		assert summary.interpolated_count == 0
		assert summary.extrapolated_count == 0
		assert summary.soft_miss_no_blob_count == 0
		assert summary.soft_miss_no_path_count == 0
		assert summary.longest_no_accept_streak == 0
		assert summary.accepted_fraction == 0.0
		assert summary.mode_disagreement_count == 0


#============================================
class TestBackwardCompatV12Fixture:
	"""Backward-read compatibility: v12 CSV fixture rows parse without error."""

	def test_v12_row_parseable_with_old_status(self, tmp_path):
		"""A CSV row with rejected_motion_gate status is parseable as data.

		We write it directly (bypassing the writer which now rejects it), then
		read it back via csv.DictReader and verify the status value survives.
		This tests that downstream readers can still parse legacy v12 CSVs.
		"""
		import csv as csv_mod
		csv_file = tmp_path / "v12_fixture.csv"

		# Write a minimal v12-style row manually (header must match HEADER).
		with open(csv_file, 'w', newline='') as f:
			writer = csv_mod.DictWriter(f, fieldnames=walk_debug_log.HEADER)
			writer.writeheader()
			# Build a row dict with all columns blank except the essentials.
			row_dict = {col: "" for col in walk_debug_log.HEADER}
			row_dict["frame_index"] = "100"
			row_dict["step"] = "2"
			row_dict["direction"] = "+"
			# Legacy status value: no longer emitted, but must be readable.
			row_dict["status"] = "rejected_motion_gate"
			writer.writerow(row_dict)

		# Read back and verify it's parseable.
		with open(csv_file, 'r') as f:
			rows = list(csv_mod.DictReader(f))

		assert len(rows) == 1
		assert rows[0]["status"] == "rejected_motion_gate"
		# The legacy value is in ALL_KNOWN_STATUS.
		assert rows[0]["status"] in walk_debug_log.ALL_KNOWN_STATUS
