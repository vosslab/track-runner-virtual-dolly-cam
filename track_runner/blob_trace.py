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
	local_tangent: tuple
	# New fields for M2 walker integration (WP-1B)
	roi_origin_xy: tuple | None = None
	acceptance_box: tuple | None = None
	dog_diameter: float | None = None
	corridor_radius: float = 0.0
	# Tagged reject reason for None-return paths in observe_blob_at.
	# Empty string when observation succeeded. One of:
	#   "no_residual", "no_raw_blobs", "corridor_empty",
	#   "acceptance_box_empty", "no_winner".
	# Set by observe_blob_at before returning None; lets capture/audit
	# tools differentiate failure modes without re-instrumenting the
	# heat-map pipeline.
	reject_reason: str = ""


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
