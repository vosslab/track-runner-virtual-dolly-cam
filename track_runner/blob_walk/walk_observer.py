"""Processed-space observation, trace, heat, and candidate helpers for walking."""

# Standard Library
import json

# local repo modules
import blob_trace
import common_tools.coord_space as coord_space
import common_tools.in_box_heat
import residual_motion


#============================================
def lighten_trace(trace: blob_trace.BlobObserverTrace) -> blob_trace.BlobObserverTrace:
	"""Return the render-only trace copy with its heavy image arrays dropped."""
	light = blob_trace.BlobObserverTrace(
		frame_index=trace.frame_index, roi_bounds=trace.roi_bounds,
		has_residual=trace.has_residual, residual_dog=None,
		residual_pre_dog=None, validity_mask=None, raw_blobs=trace.raw_blobs,
		candidate_blobs=trace.candidate_blobs, winner_blob=trace.winner_blob,
		winner_score=trace.winner_score,
		roi_origin_xy=trace.roi_origin_xy, acceptance_box=trace.acceptance_box,
		dog_diameter=trace.dog_diameter,
		reject_reason=trace.reject_reason,
	)
	return light


#============================================
def measure_in_box_heat_for_frame(
	live_trace: blob_trace.BlobObserverTrace | None, cx: float, cy: float,
	w: float, h: float,
) -> tuple:
	"""Measure final-box heat while this rolling-window trace remains live."""
	if live_trace is None or live_trace.residual_dog is None:
		return None, 0
	box = coord_space.ProcessedBox(cx=float(cx), cy=float(cy), w=float(w), h=float(h))
	heat = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog=live_trace.residual_dog, validity_mask=live_trace.validity_mask,
		roi_origin=live_trace.roi_origin_xy, box=box,
		threshold=residual_motion.DEFAULT_THRESHOLD,
	)
	return heat


#============================================
def compute_roi_and_observe(
	frame_f: int, anchor_cx: float, anchor_cy: float, seed_w: float,
	seed_h: float, scene_transform: object, reader: object,
	residual_cache: dict, fps: float, stride: int, precomputed_store: object = None,
) -> tuple:
	"""Observe one frame from a processed-space anchor, soft-missing off frame."""
	trace_sink_holder = type("TraceSink", (), {"observer_trace": None})()
	pred_center = coord_space.ProcessedPoint(cx=anchor_cx, cy=anchor_cy)
	if not pred_center.in_bounds(reader.geometry):
		print(f"WARNING: walker prediction off-frame at frame {frame_f} "
			f"(processed cx={anchor_cx:.1f}, cy={anchor_cy:.1f}); soft-miss")
		return None, trace_sink_holder
	accept_x1 = anchor_cx - 0.5 * seed_w
	accept_y1 = anchor_cy - 0.75 * seed_h
	accept_x2 = anchor_cx + 0.5 * seed_w
	accept_y2 = anchor_cy + 0.75 * seed_h
	acceptance_box = coord_space.ProcessedBox(
		cx=0.5 * (accept_x1 + accept_x2), cy=0.5 * (accept_y1 + accept_y2),
		w=accept_x2 - accept_x1, h=accept_y2 - accept_y1,
	)
	roi_pad = max(20, seed_w)
	roi_x1 = max(0, int(accept_x1 - roi_pad))
	roi_y1 = max(0, int(accept_y1 - roi_pad))
	roi_x2 = min(reader.width, int(accept_x2 + roi_pad))
	roi_y2 = min(reader.height, int(accept_y2 + roi_pad))
	roi_override = coord_space.ProcessedBox(
		cx=0.5 * (roi_x1 + roi_x2), cy=0.5 * (roi_y1 + roi_y2),
		w=roi_x2 - roi_x1, h=roi_y2 - roi_y1,
	)
	pred_box = coord_space.ProcessedBox(cx=anchor_cx, cy=anchor_cy, w=seed_w, h=seed_h)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_f, pred_center=pred_center, pred_box=pred_box,
		scene_transform=scene_transform, reader=reader,
		residual_cache=residual_cache, precomputed_store=precomputed_store, fps=fps,
		stride=stride, trace_sink=trace_sink_holder, roi_override=roi_override,
		dog_diameter_override=0.7 * seed_w, acceptance_box=acceptance_box,
	)
	return obs, trace_sink_holder


#============================================
def gather_frame_candidates(obs: object, trace_sink_holder: object) -> list:
	"""Return this frame's ordered PROCESSED full-frame candidates."""
	if obs is None or trace_sink_holder.observer_trace is None:
		return []
	candidates = list(trace_sink_holder.observer_trace.candidate_blobs)
	return candidates


#============================================
def build_window_entry(
	obs: object, trace_sink_holder: object, frame_f: int, pred_cx: float,
	pred_cy: float,
) -> dict:
	"""Freeze one image-derived observation into a rolling Viterbi entry."""
	trace = trace_sink_holder.observer_trace if obs is not None else None
	candidates = tuple(gather_frame_candidates(obs, trace_sink_holder))
	if obs is not None and trace is not None:
		obs_candidate_n = len(trace.candidate_blobs)
		obs_raw_n = len(trace.raw_blobs)
		obs_confidence_val = obs.confidence
		candidates_json_val = json.dumps([
			{"centroid_x": b["centroid_x"], "centroid_y": b["centroid_y"],
			"area": b.get("area"), "integrated_mag": b.get("integrated_mag"),
			"in_acceptance_box": b.get("in_acceptance_box"), "is_candidate": b.get("is_candidate"),
			"dist_to_pred_px": b.get("dist_to_pred_px"),
			"strength_score": b.get("strength_score"), "size_score": b.get("size_score"),
			"proximity_score": b.get("proximity_score"), "total_score": b.get("total_score")}
			for b in (trace.raw_blobs + trace.candidate_blobs)
		])
		winner_strength_score = trace.winner_blob.get("strength_score") if trace.winner_blob else None
		winner_size_score = trace.winner_blob.get("size_score") if trace.winner_blob else None
		winner_proximity_score = trace.winner_blob.get("proximity_score") if trace.winner_blob else None
		winner_total_score = trace.winner_blob.get("total_score") if trace.winner_blob else None
	else:
		obs_candidate_n = obs_raw_n = obs_confidence_val = candidates_json_val = None
		winner_strength_score = winner_size_score = winner_proximity_score = winner_total_score = None
	entry = {"frame_index": frame_f, "candidates": candidates, "pred_cx": pred_cx,
		"pred_cy": pred_cy, "obs_candidate_n": obs_candidate_n,
		"obs_raw_n": obs_raw_n, "obs_confidence": obs_confidence_val,
		"candidates_json": candidates_json_val, "winner_strength_score": winner_strength_score,
		"winner_size_score": winner_size_score, "winner_proximity_score": winner_proximity_score,
		"winner_total_score": winner_total_score,
		"light_trace": lighten_trace(trace) if trace is not None else None,
		"live_trace": trace}
	return entry
