"""
Debug log CSV writer for blob walker v2 per-frame telemetry.

Exports per-frame walker trace data to a CSV file with a locked header
(consumed by the downstream HTML caption layer). Every frame the walker
visits produces one row.

Public API:
- DebugLogRow: dataclass with one attribute per CSV column.
- DebugLogWriter: class with __init__, write_row, __enter__, __exit__, close.
- HEADER: locked verdict-CSV column tuple (45 columns); see TR_SCHEMA_VERSION_HISTORY.md for version history.
- SCHEMA_VERSION: int, the unified tr_schema.SCHEMA_VERSION (C10); metadata only,
  not stamped into the CSV. The column-meaning history (v12/v13) below documents
  the CSV layout for readers parsing older files.

Unavailable numeric values are BLANK (empty cell), never the string "None".
This behavior is the natural default of Python's csv.writer when passed None.

Schema history:
  v11 and earlier: 39 columns (frame_index..stop_reason).
  v12 (2026-05-28): Added roi_anchor_source, provisional_cx_px,
    provisional_cy_px columns (provisional-observation anti-freeze fix).
    SCHEMA_VERSION bumped from 11 to 12 per contract C10.
  v13 (2026-05-28): Window-level path-selection redesign. Removed
    torso_w_drift_frac (unused placeholder). Added path_cost and
    candidates_in_window (count of corridor_blobs across the 9-frame window).
    Status enum updated: added interpolated, extrapolated, soft_miss_no_path;
    removed rejected_motion_gate (legacy values remain parseable on read).
    SCHEMA_VERSION bumped from 12 to 13 per contract C10.
  v14 (2026-06-10): P15 telemetry-truthfulness fix. The path_cost column was
    documented as the "Viterbi DP cost contribution at this frame" but the
    writer stamped the SAME whole-window Viterbi total on every emitted row;
    its documented meaning was a lie. Two coordinated corrections, telemetry
    only (no selection, status, position, accepted-count, or fallback change):
      - path_cost is now documented truthfully as the whole-window Viterbi
        total for the window that produced this frame's decision (this is what
        the column has always held). Its semantics are unchanged; only the
        documentation is corrected.
      - path_step_cost (NEW): the per-frame Viterbi cost contribution of the
        selected node (its local node cost plus the transition cost into it),
        i.e. the value path_cost falsely claimed to be. Summing path_step_cost
        across a window equals that window's path_cost.
      - window_head_frame (NEW): source frame index of the window head (newest
        frame in the rolling buffer) when this frame's decision was finalized,
        per spec section 7.
    SCHEMA_VERSION bumped per contract C10 (unified tr_schema constant 11 ->
    13; P15 (v14 CSV label) and P12 (v13 unified integer) landed together so
    the on-disk unified constant was never 12 -- v12 is a CSV column-meaning
    label only; the v12/v13/v14 labels here are the CSV column-meaning history,
    which advances independently of and faster than the unified integer).
"""

import csv
import dataclasses
import pathlib

# local repo modules
import tr_schema


# Schema version stamp for this debug-log CSV format.
# Per contract C10 there is exactly one SCHEMA_VERSION authority in the codebase:
# track_runner/tr_schema.py. Now that this module lives inside track_runner/ beside
# tr_schema, it reads that unified constant rather than carrying its own. The verdict
# CSV is a diagnostic artifact; this value is metadata only and is not written into the
# CSV (the CSV header is the column-name tuple HEADER below, which is unchanged).
# The column-meaning history (v12, v13) is retained in the module docstring above for
# readers parsing older CSVs; the running stamp now tracks tr_schema.SCHEMA_VERSION.
SCHEMA_VERSION = tr_schema.SCHEMA_VERSION

# Locked verdict-CSV column tuple (45 columns). See TR_SCHEMA_VERSION_HISTORY.md for version history.
# This tuple is consumed by the downstream HTML caption generation.
# Changes from v12:
#   DELETED: torso_w_drift_frac (unused placeholder per scout audit)
#   NEW: path_cost (whole-window Viterbi total for this frame's window)
#   NEW: candidates_in_window (count of corridor_blobs across the 9-frame window)
# Changes from v13 (v14, telemetry-truthfulness fix):
#   NEW: path_step_cost (per-frame Viterbi cost contribution of the selected node)
#   NEW: window_head_frame (source frame index of the window head at decision time)
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
	# path_cost (v13; meaning clarified v14): the WHOLE-WINDOW Viterbi total
	# cost for the window that produced this frame's decision (float). Every row
	# emitted from one Viterbi pass shares the same value. Despite the v13
	# comment, this was never a per-frame value; see path_step_cost for that.
	# Blank when no DP path was computed (bootstrap frame).
	"candidates_in_window",
	# candidates_in_window NEW in v13: number of non-empty corridor_blob candidate
	# lists in the 9-frame window when this frame's decision was finalized (int).
	# Blank for bootstrap and terminal marker rows.
	"path_step_cost",
	# path_step_cost NEW in v14 (P15 fix): per-frame Viterbi cost contribution of
	# the selected node on the chosen path -- its local node cost (evidence bonus
	# for a real blob, else SKIP_COST) plus the transition cost into it from the
	# previous node (float). This is the value path_cost falsely claimed to be.
	# Summing path_step_cost across one window equals that window's path_cost.
	# Blank for bootstrap and terminal marker rows.
	"window_head_frame",
	# window_head_frame NEW in v14 (P15 / spec section 7): source frame index of
	# the window head (newest frame in the rolling buffer) at the moment this
	# frame's window decision was finalized (int). Blank for bootstrap and
	# terminal marker rows.
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


@dataclasses.dataclass
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
	dt: float | None = None
	torso_w_px: float | None = None
	torso_h_px: float | None = None
	prev_cx: float | None = None
	prev_cy: float | None = None
	prev_scene_x: float | None = None
	prev_scene_y: float | None = None
	pred_cx: float | None = None
	pred_cy: float | None = None
	cand_cx: float | None = None
	cand_cy: float | None = None
	cand_scene_x: float | None = None
	cand_scene_y: float | None = None
	v_recent_scene_mag: float | None = None
	# torso_w_drift_frac DELETED in v13 (was unused placeholder per scout audit)
	expected_jump: float | None = None
	allowed_jump: float | None = None
	actual_jump: float | None = None
	dt_for_gate: int | None = None
	winner_mode: str | None = None
	production_winner_cx: float | None = None
	production_winner_cy: float | None = None
	audit_winner_cx: float | None = None
	audit_winner_cy: float | None = None
	audit_winner_rule: str | None = None
	obs_confidence: float | None = None
	obs_corridor_n: int | None = None
	obs_raw_n: int | None = None
	winner_strength_score: float | None = None
	winner_size_score: float | None = None
	winner_proximity_score: float | None = None
	winner_total_score: float | None = None
	candidates_json: str | None = None
	reject_reason: str | None = None
	stop_reason: str | None = None
	# Provisional-anchor anti-freeze fields (added schema v12, 2026-05-28).
	# v13 SEMANTIC CHANGE: roi_anchor_source now reflects last-accepted anchor;
	# provisional/extrapolated values no longer emitted by new walker but parseable.
	roi_anchor_source: str | None = None
	# provisional_cx_px / provisional_cy_px: always blank in v13 walker;
	# retained for backward CSV-read compat.
	provisional_cx_px: float | None = None
	provisional_cy_px: float | None = None
	# v13 Viterbi DP diagnostics (path_cost meaning clarified in v14).
	# path_cost: whole-window Viterbi total for this frame's window (float); blank
	# for bootstrap rows. Shared across every row emitted from one Viterbi pass.
	path_cost: float | None = None
	# candidates_in_window: count of non-empty corridor_blob lists in the 9-frame window
	# when this frame's decision was finalized; blank for bootstrap/terminal rows.
	candidates_in_window: int | None = None
	# New v14 fields (P15 telemetry-truthfulness fix), recording only.
	# path_step_cost: per-frame Viterbi cost contribution of the selected node
	# (local node cost + transition cost into it); blank for bootstrap/terminal rows.
	path_step_cost: float | None = None
	# window_head_frame: source frame index of the window head at decision time;
	# blank for bootstrap/terminal rows.
	window_head_frame: int | None = None


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

		row_dict = dataclasses.asdict(row)
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
