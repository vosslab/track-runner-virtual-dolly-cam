"""
Debug log CSV writer for blob walker v2 per-frame telemetry.

Exports per-frame walker trace data to a CSV file with a locked header
(consumed by downstream M3 HTML caption layer). Every frame the walker
visits produces one row.

Public API:
- DebugLogRow: dataclass with one attribute per CSV column.
- DebugLogWriter: class with __init__, write_row, __enter__, __exit__, close.
- HEADER: tuple of column names (44 items as of schema v13).
- SCHEMA_VERSION: int, current schema version for this debug-log format.

Unavailable numeric values are BLANK (empty cell), never the string "None".
This behavior is the natural default of Python's csv.writer when passed None.

Schema history:
  v11 and earlier: 39 columns (frame_index..stop_reason).
  v12 (2026-05-28): Added roi_anchor_source, provisional_cx_px,
    provisional_cy_px columns (provisional-observation anti-freeze fix).
    SCHEMA_VERSION bumped from 11 to 12 per contract C10.
  v13 (2026-05-28): Window-level path-selection redesign. Removed
    torso_w_drift_frac (unused placeholder). Added path_cost (Viterbi
    cost contribution at this frame) and candidates_in_window (count of
    corridor_blobs across the 9-frame window). Status enum updated:
    added interpolated, extrapolated, soft_miss_no_path; removed
    rejected_motion_gate (legacy values remain parseable on read).
    SCHEMA_VERSION bumped from 12 to 13 per contract C10.
"""

import csv
import pathlib
from dataclasses import dataclass, asdict
from typing import Optional


# Schema version for this debug-log CSV format.
# v13 (2026-05-28): window-level path-selection redesign; removed torso_w_drift_frac;
#   added path_cost and candidates_in_window; updated status enum.
# Per contract C10, this is separate from track_runner/tr_schema.py SCHEMA_VERSION
# (that governs on-disk solver artifacts); this constant governs the walker CSV schema.
SCHEMA_VERSION = 13

# Locked CSV header (44 columns in exact order as of SCHEMA_VERSION=13).
# This tuple is consumed by downstream M3 HTML caption generation.
# Changes from v12:
#   DELETED: torso_w_drift_frac (unused placeholder per scout audit)
#   NEW: path_cost (Viterbi cost contribution at this frame)
#   NEW: candidates_in_window (count of corridor_blobs across the 9-frame window)
HEADER = (
	"frame_index",
	"step",
	"direction",
	"dt",
	"torso_w_px",
	"torso_h_px",
	"prev_cx",
	"prev_cy",
	"prev_scene_x",
	"prev_scene_y",
	"pred_cx",
	# pred_cx SEMANTIC CHANGE v13: now the walker's last-accepted center used for
	# ROI/acceptance-box anchoring, not necessarily the velocity-projected center.
	"pred_cy",
	"cand_cx",
	"cand_cy",
	"cand_scene_x",
	"cand_scene_y",
	"v_recent_scene_mag",
	# torso_w_drift_frac DELETED in v13 (was unused placeholder)
	"expected_jump",
	"allowed_jump",
	"actual_jump",
	"dt_for_gate",
	"winner_mode",
	"production_winner_cx",
	"production_winner_cy",
	"audit_winner_cx",
	"audit_winner_cy",
	"audit_winner_rule",
	"obs_confidence",
	"obs_corridor_n",
	"obs_raw_n",
	"winner_strength_score",
	"winner_size_score",
	"winner_proximity_score",
	"winner_total_score",
	"candidates_json",
	"status",
	# status SEMANTIC CHANGE v13: values are now accepted, interpolated, extrapolated,
	# soft_miss_no_blob, soft_miss_no_path. rejected_motion_gate no longer emitted.
	"reject_reason",
	# reject_reason SEMANTIC CHANGE v13: blank for all windowed statuses; retained for
	# backward CSV-read compat only.
	"stop_reason",
	"roi_anchor_source",
	# roi_anchor_source SEMANTIC CHANGE v13: now reflects last-accepted anchor used for
	# ROI placement. provisional/extrapolated are no longer emitted by the new walker;
	# field retained for backward CSV-read compat.
	"provisional_cx_px",
	# provisional_cx_px SEMANTIC CHANGE v13: always blank in new walker (no per-step
	# provisional state); retained for backward CSV-read compat.
	"provisional_cy_px",
	# provisional_cy_px SEMANTIC CHANGE v13: same as provisional_cx_px.
	"path_cost",
	# path_cost NEW in v13: Viterbi DP cost contribution at this frame (float).
	# Blank when no DP path was computed (bootstrap frame, or flush frame).
	"candidates_in_window",
	# candidates_in_window NEW in v13: number of non-empty corridor_blob candidate
	# lists in the 9-frame window when this frame's decision was finalized (int).
	# Blank for bootstrap and terminal marker rows.
)

#============================================
# Allowed status values (enforce via module-level set).
# Legacy values (rejected_motion_gate, gate_reject_cap, miss_cap_no_blob) are
# listed in _LEGACY_STATUS for backward CSV-read parsing; they are never emitted
# by the v13 walker.
_ALLOWED_STATUS = {
	"accepted",
	"interpolated",
	"extrapolated",
	"soft_miss_no_blob",
	"soft_miss_no_path",
	# Terminal markers emitted at walk end.
	"hit_neighbor_seed",
	"boundary",
	# Diagnostic marker emitted for extra_diagnostic_frames after walk termination.
	"after_walk_terminated",
}

# Legacy values: parseable on read, never emitted by v13 walker.
_LEGACY_STATUS = {
	"rejected_motion_gate",
	"gate_reject_cap",
	"miss_cap_no_blob",
	"soft_miss_low_conf",
}

# Combined set for readers that parse v12 or v13 CSV files.
ALL_KNOWN_STATUS = _ALLOWED_STATUS | _LEGACY_STATUS


@dataclass
class DebugLogRow:
	"""
	One row of walker debug log CSV.

	Attributes correspond to the HEADER columns in order.
	Required fields: frame_index, step, direction, status.
	All other fields default to None (which renders as blank in CSV).
	"""
	frame_index: int
	step: int
	direction: str
	status: str
	dt: Optional[float] = None
	torso_w_px: Optional[float] = None
	torso_h_px: Optional[float] = None
	prev_cx: Optional[float] = None
	prev_cy: Optional[float] = None
	prev_scene_x: Optional[float] = None
	prev_scene_y: Optional[float] = None
	pred_cx: Optional[float] = None
	pred_cy: Optional[float] = None
	cand_cx: Optional[float] = None
	cand_cy: Optional[float] = None
	cand_scene_x: Optional[float] = None
	cand_scene_y: Optional[float] = None
	v_recent_scene_mag: Optional[float] = None
	# torso_w_drift_frac DELETED in v13 (was unused placeholder per scout audit)
	expected_jump: Optional[float] = None
	allowed_jump: Optional[float] = None
	actual_jump: Optional[float] = None
	dt_for_gate: Optional[int] = None
	winner_mode: Optional[str] = None
	production_winner_cx: Optional[float] = None
	production_winner_cy: Optional[float] = None
	audit_winner_cx: Optional[float] = None
	audit_winner_cy: Optional[float] = None
	audit_winner_rule: Optional[str] = None
	obs_confidence: Optional[float] = None
	obs_corridor_n: Optional[int] = None
	obs_raw_n: Optional[int] = None
	winner_strength_score: Optional[float] = None
	winner_size_score: Optional[float] = None
	winner_proximity_score: Optional[float] = None
	winner_total_score: Optional[float] = None
	candidates_json: Optional[str] = None
	reject_reason: Optional[str] = None
	stop_reason: Optional[str] = None
	# Provisional-anchor anti-freeze fields (added schema v12, 2026-05-28).
	# v13 SEMANTIC CHANGE: roi_anchor_source now reflects last-accepted anchor;
	# provisional/extrapolated values no longer emitted by new walker but parseable.
	roi_anchor_source: Optional[str] = None
	# provisional_cx_px / provisional_cy_px: always blank in v13 walker;
	# retained for backward CSV-read compat.
	provisional_cx_px: Optional[float] = None
	provisional_cy_px: Optional[float] = None
	# New v13 fields: Viterbi DP diagnostics.
	# path_cost: Viterbi cost contribution at this frame (float); blank for bootstrap rows.
	path_cost: Optional[float] = None
	# candidates_in_window: count of non-empty corridor_blob lists in the 9-frame window
	# when this frame's decision was finalized; blank for bootstrap/terminal rows.
	candidates_in_window: Optional[int] = None


#============================================

class DebugLogWriter:
	"""
	CSV writer for walker debug log with context-manager support.

	Usage:
		with DebugLogWriter(pathlib.Path("/tmp/walk.csv")) as writer:
			writer.write_row(row)
		# File is automatically closed.

	Or:
		writer = DebugLogWriter(pathlib.Path("/tmp/walk.csv"))
		writer.write_row(row)
		writer.close()
	"""

	def __init__(self, csv_path: pathlib.Path) -> None:
		"""
		Initialize the writer and write the header row.

		Args:
			csv_path: pathlib.Path where the CSV will be written.
		"""
		self.csv_path = csv_path
		self._file = open(self.csv_path, mode='w', newline='')
		self._writer = csv.DictWriter(self._file, fieldnames=HEADER)
		self._writer.writeheader()

	def write_row(self, row: DebugLogRow) -> None:
		"""
		Write one data row to the CSV.

		Args:
			row: DebugLogRow instance.

		Raises:
			ValueError: if row.status is not in the allowed set.
		"""
		if row.status not in _ALLOWED_STATUS:
			raise ValueError(
				f"Invalid status '{row.status}'. "
				f"Allowed: {sorted(_ALLOWED_STATUS)}"
			)

		row_dict = asdict(row)
		self._writer.writerow(row_dict)

	def close(self) -> None:
		"""Close the CSV file."""
		self._file.close()

	def __enter__(self):
		"""Context manager entry."""
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		"""Context manager exit; closes the file."""
		self.close()
		return False
