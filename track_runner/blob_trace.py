"""Blob observer trace dataclass for refinement diagnostics."""

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
	candidate_blobs: list
	winner_blob: dict | None
	winner_score: float | None
	# Additional fields for blob-walk integration
	roi_origin_xy: tuple | None = None
	acceptance_box: tuple | None = None
	dog_diameter: float | None = None
	# Tagged reject reason for None-return paths in observe_blob_at.
	# Empty string when observation succeeded. Current failures are
	# "no_residual", "no_raw_blobs", "acceptance_box_empty", and "off_frame".
	reject_reason: str = ""


#============================================
def assign_observer_trace(
	trace_sink: object,
	frame_index: int,
	roi: tuple,
	dog_residual: numpy.ndarray | None,
	residual_pre_dog: numpy.ndarray | None,
	validity_mask: numpy.ndarray | None,
	raw_blobs: list,
	candidate_blobs: list,
	best_blob: dict,
	best_score: float,
	acceptance_box_edges: tuple | None,
	dog_diameter: float,
	pred_cx: float,
	pred_cy: float,
) -> None:
	"""Enrich blob aliases and assign one completed observer trace.

	Args:
		trace_sink: Object receiving the completed ``observer_trace`` attribute.
		frame_index: Observed frame number.
		roi: Processed-space residual ROI edges.
		dog_residual: DoG-filtered residual image, retained by alias.
		residual_pre_dog: Residual image before DoG filtering, retained by alias.
		validity_mask: Validity mask retained by alias.
		raw_blobs: Raw blob dictionaries, enriched in place for the trace.
		candidate_blobs: Eligible blob list retained by alias in the trace.
		best_blob: Strongest candidate blob retained by alias.
		best_score: Logged total score of ``best_blob``.
		acceptance_box_edges: Optional processed-space acceptance-box edges.
		dog_diameter: DoG diameter used for this observation.
		pred_cx: Predicted processed-space x coordinate.
		pred_cy: Predicted processed-space y coordinate.
	"""
	# Blob dictionaries are mutated in place because both lists retain aliases.
	candidate_label_ids = {blob.get("label_id") for blob in candidate_blobs}
	for blob in raw_blobs:
		blob["is_candidate"] = blob.get("label_id") in candidate_label_ids
		if acceptance_box_edges is not None:
			ab_x1, ab_y1, ab_x2, ab_y2 = acceptance_box_edges
			blob["in_acceptance_box"] = (
				ab_x1 <= blob["centroid_x"] <= ab_x2 and
				ab_y1 <= blob["centroid_y"] <= ab_y2
			)
		else:
			blob["in_acceptance_box"] = None
		dx = blob["centroid_x"] - pred_cx
		dy = blob["centroid_y"] - pred_cy
		blob["dist_to_pred_px"] = (dx**2 + dy**2)**0.5

	for blob in candidate_blobs:
		strength = min(float(blob["integrated_mag"]) / 10000.0, 1.0)
		blob["strength_score"] = strength

	trace = BlobObserverTrace(
		frame_index=frame_index,
		roi_bounds=(roi[0], roi[1], roi[2], roi[3]),
		has_residual=True,
		residual_dog=dog_residual,
		residual_pre_dog=residual_pre_dog,
		validity_mask=validity_mask,
		raw_blobs=raw_blobs,
		candidate_blobs=candidate_blobs,
		winner_blob=best_blob,
		winner_score=best_score,
		roi_origin_xy=(roi[0], roi[1]),
		acceptance_box=acceptance_box_edges,
		dog_diameter=dog_diameter,
	)
	trace_sink.observer_trace = trace
