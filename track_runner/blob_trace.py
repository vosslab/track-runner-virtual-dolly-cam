"""Blob observer and gate trace dataclasses for refinement diagnostics."""

import dataclasses
import numpy


@dataclasses.dataclass
class BlobObserverTrace:
	"""Per-frame blob observation trace captured during interval solving."""
	frame_index: int
	roi_bounds: tuple
	has_residual: bool
	residual_dog: numpy.ndarray | None
	residual_pre_dog: numpy.ndarray | None
	validity_mask: numpy.ndarray | None
	raw_blobs: list
	corridor_blobs: list
	winner_blob: dict | None
	winner_score: float | None
	corridor_radius: float
	local_tangent: tuple


@dataclasses.dataclass
class BlobGateTrace:
	"""Per-frame blob-gate decision trace for FWD and BWD passes."""
	frame_index: int
	pass_name: str
	raw_prev: tuple
	raw_curr: tuple
	raw_next: tuple
	raw_box: tuple
	v_pred: tuple
	v_pred_mag: float
	observer_trace: BlobObserverTrace | None
	winner_dist_px: float | None
	winner_dist_h: float | None
	proximity_threshold: float
	proximity_ok: bool | None
	direction_dot: float | None
	direction_ok: bool | None
	path_ok_prev: object
	blob_gate: str
