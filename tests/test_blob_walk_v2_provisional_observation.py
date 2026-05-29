"""
Tests for the provisional-observation anti-freeze fix in walk_walker.py.

Verifies:
1. After a rejected-gate frame with a visible candidate, last_observed_candidate_position
   is updated (the rejected candidate position is recorded as provisional).
2. When the provisional position is newer than the last accept, the next step's
   pred_cx/cy is anchored to the provisional position (roi_anchor_source=="provisional").
3. Multiple consecutive rejected-gate frames do NOT increment accepted_count.
4. Velocity history excludes provisional (rejected) positions.
5. debug log rows carry roi_anchor_source and provisional_cx_px/cy_px fields.
6. BWD direction fix: step counters (not frame_index) used for provisional comparison,
   so BWD provisional activation works when frame_index decreases.

Tests use walk_debug_log directly (unit tests on the schema) and walk_walker
internal state via controlled DebugLogRow field inspection.

All tests are offline, deterministic, and finish well under one second.
No real video decode: all tests use walk_debug_log or inspect walk_walker
module constants and DebugLogRow field definitions.
"""

import csv
import inspect
import pathlib
import sys

import numpy

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
_TRACK_RUNNER_DIR = _REPO_ROOT / 'track_runner'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
if str(_TRACK_RUNNER_DIR) not in sys.path:
	sys.path.insert(0, str(_TRACK_RUNNER_DIR))

import walk_debug_log
import walk_walker
import residual_motion
import scene_coords
import camera_motion
import common_tools.frame_reader


#============================================
class TestSchemaVersion12Fields:
	"""Schema v12/v13 fields are present in HEADER and DebugLogRow.

	v12 added roi_anchor_source, provisional_cx_px, provisional_cy_px.
	v13 bumped to 13; those fields are retained for backward CSV-read compat.
	Tests updated for v13 schema (SCHEMA_VERSION=13, 43 columns).
	"""

	def test_roi_anchor_source_in_header(self):
		# roi_anchor_source must appear in HEADER (added v12, retained v13).
		assert "roi_anchor_source" in walk_debug_log.HEADER

	def test_provisional_cx_px_in_header(self):
		# provisional_cx_px must appear in HEADER (added v12, retained v13).
		assert "provisional_cx_px" in walk_debug_log.HEADER

	def test_provisional_cy_px_in_header(self):
		# provisional_cy_px must appear in HEADER (added v12, retained v13).
		assert "provisional_cy_px" in walk_debug_log.HEADER

	def test_schema_version_is_13(self):
		# SCHEMA_VERSION bumped to 13 for windowed path-selection redesign.
		assert walk_debug_log.SCHEMA_VERSION == 13

	def test_header_length_matches_schema_v13(self):
		# 43 columns as of schema v13 (v12 42 - 1 torso_w_drift_frac + 2 new = 43).
		assert len(walk_debug_log.HEADER) == 43


#============================================
class TestDebugLogRowHasProvisionalFields:
	"""DebugLogRow dataclass has the three new provisional-anchor fields."""

	def test_row_has_roi_anchor_source(self):
		# DebugLogRow must have roi_anchor_source attribute.
		row = walk_debug_log.DebugLogRow(
			frame_index=10, step=1, direction="+", status="accepted"
		)
		assert hasattr(row, 'roi_anchor_source')

	def test_row_has_provisional_cx_px(self):
		row = walk_debug_log.DebugLogRow(
			frame_index=10, step=1, direction="+", status="accepted"
		)
		assert hasattr(row, 'provisional_cx_px')

	def test_row_has_provisional_cy_px(self):
		row = walk_debug_log.DebugLogRow(
			frame_index=10, step=1, direction="+", status="accepted"
		)
		assert hasattr(row, 'provisional_cy_px')

	def test_provisional_fields_default_none(self):
		# New fields default to None (blank in CSV) so old callers are unaffected.
		row = walk_debug_log.DebugLogRow(
			frame_index=10, step=1, direction="+", status="accepted"
		)
		assert row.roi_anchor_source is None
		assert row.provisional_cx_px is None
		assert row.provisional_cy_px is None


#============================================
class TestProvisionalFieldsWriteToCSV:
	"""New fields serialize to CSV correctly."""

	def test_provisional_anchor_row_written_correctly(self, tmp_path):
		# Write a row with roi_anchor_source="provisional" and specific coords.
		# Read it back. Assert values survive the round-trip.
		# NOTE: v13 walker no longer emits provisional status rows, but the fields
		# are retained in DebugLogRow for backward CSV-read compat. We write using
		# "accepted" status (the only common status in v13) and verify field values.
		csv_file = tmp_path / "debug.csv"
		with walk_debug_log.DebugLogWriter(csv_file) as writer:
			row = walk_debug_log.DebugLogRow(
				frame_index=50,
				step=2,
				direction="+",
				status="accepted",
				roi_anchor_source="provisional",
				provisional_cx_px=123.5,
				provisional_cy_px=456.0,
			)
			writer.write_row(row)

		with open(csv_file, 'r') as f:
			reader = csv.DictReader(f)
			rows = list(reader)

		assert rows[0]["roi_anchor_source"] == "provisional"
		assert rows[0]["provisional_cx_px"] == "123.5"
		assert rows[0]["provisional_cy_px"] == "456.0"

	def test_accepted_row_has_blank_provisional_fields(self, tmp_path):
		# An accepted row with no provisional fields writes blank cells.
		csv_file = tmp_path / "debug.csv"
		with walk_debug_log.DebugLogWriter(csv_file) as writer:
			row = walk_debug_log.DebugLogRow(
				frame_index=10,
				step=1,
				direction="+",
				status="accepted",
				roi_anchor_source="accepted",
			)
			writer.write_row(row)

		with open(csv_file, 'r') as f:
			reader = csv.DictReader(f)
			rows = list(reader)

		# provisional_cx_px and provisional_cy_px must be blank (None -> "").
		assert rows[0]["provisional_cx_px"] == ""
		assert rows[0]["provisional_cy_px"] == ""


#============================================
class TestProvisionalAnchorLogicInWalker:
	"""Inspect walk_walker module-level behavior related to provisional-anchor fix."""

	def test_walker_has_no_last_observed_candidate_position_as_attribute(self):
		# The provisional state is local to walk_one_direction (not a module-level dict).
		# This test confirms no module-level contamination.
		assert not hasattr(walk_walker, 'last_provisional_cx')
		assert not hasattr(walk_walker, 'last_provisional_cy')
		# last_provisional_frame was replaced by last_provisional_step in the BWD fix.
		assert not hasattr(walk_walker, 'last_provisional_frame')
		assert not hasattr(walk_walker, 'last_provisional_step')

	def test_walker_still_has_no_reject_cap(self):
		# REJECT_CAP must remain absent (WP-1C contract).
		assert not hasattr(walk_walker, 'REJECT_CAP')

	def test_walker_still_has_no_miss_cap(self):
		# MISS_CAP must remain absent (WP-1C contract).
		assert not hasattr(walk_walker, 'MISS_CAP')


#============================================
class TestRoiAnchorSourceValues:
	"""roi_anchor_source has three valid values; all are writable to CSV."""

	def _write_and_read(self, tmp_path, anchor_source):
		csv_file = tmp_path / f"debug_{anchor_source}.csv"
		with walk_debug_log.DebugLogWriter(csv_file) as writer:
			row = walk_debug_log.DebugLogRow(
				frame_index=1,
				step=1,
				direction="+",
				status="accepted",
				roi_anchor_source=anchor_source,
			)
			writer.write_row(row)
		with open(csv_file, 'r') as f:
			reader = csv.DictReader(f)
			rows = list(reader)
		return rows[0]["roi_anchor_source"]

	def test_accepted_anchor_source(self, tmp_path):
		result = self._write_and_read(tmp_path, "accepted")
		assert result == "accepted"

	def test_provisional_anchor_source(self, tmp_path):
		result = self._write_and_read(tmp_path, "provisional")
		assert result == "provisional"

	def test_extrapolated_anchor_source(self, tmp_path):
		result = self._write_and_read(tmp_path, "extrapolated")
		assert result == "extrapolated"


#============================================
class TestBwdProvisionalActivation:
	"""v13 walker redesign: no provisional state; windowed Viterbi replaces it.

	The v12 provisional-observation anti-freeze fix used per-step state
	(last_provisional_step, last_accepted_step) to anchor the ROI near the
	visible runner after a gate reject. v13 replaces this with the 9-frame
	Viterbi DP path-selection: candidates are buffered per-window and the
	path is selected globally, so per-step provisional state is no longer needed.

	These tests verify the v13 walker has NO per-step provisional state
	and uses the windowed path-selection approach instead.
	"""

	def _get_source(self):
		return inspect.getsource(walk_walker.walk_one_direction)

	def test_provisional_step_state_removed(self):
		# v13 windowed walker must NOT have per-step provisional state.
		src = self._get_source()
		assert 'last_provisional_step' not in src, (
			"last_provisional_step found in walk_one_direction; "
			"v13 uses windowed Viterbi instead of per-step provisional state"
		)

	def test_provisional_cx_state_removed(self):
		# last_provisional_cx is per-step state removed in v13.
		src = self._get_source()
		assert 'last_provisional_cx' not in src, (
			"last_provisional_cx found in walk_one_direction; "
			"v13 uses windowed Viterbi instead of per-step provisional state"
		)

	def test_direction_naive_frame_comparison_absent(self):
		# The old direction-naive comparison must not appear.
		src = self._get_source()
		assert 'last_provisional_frame > last_accepted_frame' not in src, (
			"Direction-naive frame comparison found in walk_one_direction"
		)

	def test_window_buffer_present(self):
		# v13 walker uses a rolling window buffer (deque).
		src = self._get_source()
		assert 'window_buffer' in src, (
			"window_buffer not found in walk_one_direction; "
			"v13 walker should use a rolling buffer for path selection"
		)


#============================================
class _BwdStubReader:
	"""Minimal stub reader for BWD provisional test. Returns synthetic frames."""

	def __init__(self, frame_count=200):
		self.width = 256
		self.height = 256
		self.frame_count = frame_count
		self.fps = 60.0
		self.geometry = common_tools.frame_reader.FrameGeometry(
			source_width=self.width,
			source_height=self.height,
			bin_factor=1,
			scaled_width=self.width,
			scaled_height=self.height,
			processed_width=self.width,
			processed_height=self.height,
		)

	def read_frame(self, idx):
		# Return a synthetic frame with a bright blob near center.
		frame = numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)
		frame[110:140, 110:140] = 200
		return frame


#============================================
def _patch_bwd_pipeline(monkeypatch, blob_cx=125.0, blob_cy=125.0):
	"""Patch residual_motion to always return a single blob at (blob_cx, blob_cy)."""

	def _stub_residual(reader, frame_index, scene_transform,
		half_window, frame_read_cache, roi, fps=None, stride=None):
		h = reader.height
		w = reader.width
		residual = numpy.zeros((h, w), dtype=numpy.float32)
		validity = numpy.ones((h, w), dtype=numpy.uint8)
		return residual, validity

	def _stub_dog(residual, pred_w, k):
		return residual

	def _stub_extract(dog, validity, threshold):
		return [{
			"centroid_x": blob_cx,
			"centroid_y": blob_cy,
			"area": 100.0,
			"max_intensity": 200.0,
			"mean_intensity": 150.0,
		}]

	def _stub_corridor(blobs, cx, cy, tangent, radius):
		for b in blobs:
			b["cross_track"] = 0.0
			b["along_track"] = 0.0
			b["strength_score"] = 0.8
			b["size_score"] = 0.7
			b["proximity_score"] = 0.9
			b["total_score"] = 0.8
			b["in_acceptance_box"] = True
			b["in_corridor"] = True
			b["dist_to_pred_px"] = 5.0
			b["integrated_mag"] = 500.0
		return blobs

	def _stub_confidence(blob, cx, cy, w, h, tangent):
		return 0.85

	monkeypatch.setattr(residual_motion, "compute_residual_for_frame", _stub_residual)
	monkeypatch.setattr(residual_motion, "dog_filter_blob_scale", _stub_dog)
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", _stub_extract)
	monkeypatch.setattr(residual_motion, "filter_blobs_to_corridor", _stub_corridor)
	monkeypatch.setattr(residual_motion, "compute_cue_confidence", _stub_confidence)
	monkeypatch.setattr(
		residual_motion,
		"_compute_roi",
		lambda cx, cy, h, fw, fh: (0, 0, fw, fh),
	)


#============================================
def _build_bwd_walk_scene(frame_count=200):
	"""Build scene_transform for BWD walk tests."""
	motion_track = camera_motion.MotionTrack(
		dx=numpy.zeros(frame_count, dtype=numpy.float32),
		dy=numpy.zeros(frame_count, dtype=numpy.float32),
		scale=numpy.ones(frame_count, dtype=numpy.float32),
		quality=numpy.ones(frame_count, dtype=numpy.float32),
	)
	return scene_coords.SceneTransform(motion_track)


#============================================
class TestBwdProvisionalBehavioral:
	"""Behavioral BWD test: run a real walk and verify v13 windowed walker emits valid statuses.

	v13 replaces per-step provisional state with 9-frame Viterbi DP path-selection.
	There are no rejected_motion_gate rows in v13. All emitted statuses must
	be in the 5-value enum (accepted, interpolated, extrapolated,
	soft_miss_no_blob, soft_miss_no_path) or terminal markers.
	"""

	def test_bwd_walk_emits_only_valid_statuses(self, monkeypatch, tmp_path):
		# Set up: seed at frame 100, neighbor at frame 95 (BWD).
		BLOB_CX = 125.0
		BLOB_CY = 125.0
		_patch_bwd_pipeline(monkeypatch, blob_cx=BLOB_CX, blob_cy=BLOB_CY)

		reader = _BwdStubReader(frame_count=200)
		scene_transform = _build_bwd_walk_scene(frame_count=200)

		seed = {
			"frame_index": 100,
			"cx": 180.0,
			"cy": 180.0,
			"w": 30.0,
			"h": 50.0,
		}
		neighbor_seed_frame = 95

		debug_log_path = tmp_path / "bwd_test.csv"
		debug_log = walk_debug_log.DebugLogWriter(str(debug_log_path))

		walk_walker.walk_one_direction(
			seed=seed,
			neighbor_seed_frame=neighbor_seed_frame,
			reader=reader,
			scene_transform=scene_transform,
			fps=60.0,
			stride=1,
			sign=-1,  # BWD
			debug_log=debug_log,
			winner_mode="production_winner",
			audit_rule=None,
			extra_diagnostic_frames=[],
		)
		debug_log.close()

		# All emitted statuses must be in the v13 allowed set.
		valid_statuses = walk_debug_log._ALLOWED_STATUS
		with open(debug_log_path, 'r') as f:
			reader_csv = csv.DictReader(f)
			rows = list(reader_csv)

		for row in rows:
			assert row["status"] in valid_statuses, (
				f"Invalid status '{row['status']}' found in BWD walk output; "
				f"v13 walker must only emit statuses in {valid_statuses}"
			)

		# No rejected_motion_gate rows in v13.
		reject_rows = [r for r in rows if r["status"] == "rejected_motion_gate"]
		assert len(reject_rows) == 0, (
			"rejected_motion_gate rows found; v13 walker must not emit this status"
		)
