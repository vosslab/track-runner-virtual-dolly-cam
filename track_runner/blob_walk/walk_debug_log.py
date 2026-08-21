"""
Debug log CSV writer for production blob-walker per-frame telemetry.

Exports per-frame walker trace data to a CSV file with a locked header
(consumed by the downstream HTML caption layer). Every frame the walker
visits produces one row.

This diagnostic CSV does not define or change the solver schema. The solver
schema lives in `track_runner/tr_schema.py`.

Public API:
- DebugLogRow: dataclass with one attribute per CSV column.
- DebugLogWriter: class with __init__, write_row, __enter__, __exit__, close.
- HEADER: current diagnostic CSV column tuple.

Unavailable numeric values are BLANK (empty cell), never the string "None".
This behavior is the natural default of Python's csv.writer when passed None.

"""

import csv
import dataclasses
import pathlib


# Current diagnostic CSV columns.
# This tuple is consumed by the downstream HTML caption generation.
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
	"pred_cy",
	"cand_cx",
	"cand_cy",
	"cand_scene_x",
	"cand_scene_y",
	"v_recent_scene_mag",
	"expected_jump",
	"allowed_jump",
	"actual_jump",
	"dt_for_gate",
	"obs_confidence",
	"obs_candidate_n",
	"obs_raw_n",
	"winner_strength_score",
	"winner_size_score",
	"winner_proximity_score",
	"winner_total_score",
	"candidates_json",
	"status",
	"reject_reason",
	"stop_reason",
	"roi_anchor_source",
	"path_cost",
	"candidates_in_window",
	"path_step_cost",
	"window_head_frame",
)

#============================================
# Status values emitted by the current walker.
_ALLOWED_STATUS = {
	"accepted",
	"interpolated",
	"extrapolated",
	"soft_miss_no_blob",
	"soft_miss_no_path",
	"hit_neighbor_seed",
	"boundary",
	"after_walk_terminated",
}


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
	expected_jump: float | None = None
	allowed_jump: float | None = None
	actual_jump: float | None = None
	dt_for_gate: int | None = None
	obs_confidence: float | None = None
	obs_candidate_n: int | None = None
	obs_raw_n: int | None = None
	winner_strength_score: float | None = None
	winner_size_score: float | None = None
	winner_proximity_score: float | None = None
	winner_total_score: float | None = None
	candidates_json: str | None = None
	reject_reason: str | None = None
	stop_reason: str | None = None
	roi_anchor_source: str | None = None
	path_cost: float | None = None
	candidates_in_window: int | None = None
	path_step_cost: float | None = None
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

	def __enter__(self) -> "DebugLogWriter":
		"""Context manager entry."""
		return self

	def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
		"""Context manager exit; closes the file."""
		self.close()
		return False
