"""Compatibility facade for the split, independent one-direction blob walker."""

# Standard Library
import collections

# local repo modules
import blob_trace
import residual_motion
import blob_walk.walk_debug_log as walk_debug_log
import blob_walk.walk_engine as walk_engine
import blob_walk.walk_observer as walk_observer
import blob_walk.walk_summary as walk_summary
import blob_walk.walk_viterbi as walk_viterbi

WALKER_WINDOW_FRAMES = 9
BOOTSTRAP_N = 1
CONF_DECAY_PER_FRAME = 0.97
CONF_FLOOR = 0.1
CONF_START = 1.0
WalkSummary = walk_summary.WalkSummary


#============================================
def conf_from_anchor(frame_index: int, seed_frame: int) -> float:
	"""Return deterministic distance-from-this-pass-anchor confidence."""
	confidence = CONF_START * CONF_DECAY_PER_FRAME ** abs(frame_index - seed_frame)
	return max(CONF_FLOOR, confidence)


#============================================
def make_size_at_frame(seed_frame: int, seed_w: float, seed_h: float,
	neighbor_seed_frame: int, neighbor_seed_w: float | None,
	neighbor_seed_h: float | None) -> object:
	"""Compatibility seam for C4 seed-derived size interpolation."""
	return walk_engine.make_size_at_frame(seed_frame, seed_w, seed_h,
		neighbor_seed_frame, neighbor_seed_w, neighbor_seed_h)


#============================================
def lighten_trace(trace: blob_trace.BlobObserverTrace) -> blob_trace.BlobObserverTrace:
	"""Compatibility seam for render-only trace storage."""
	return walk_observer.lighten_trace(trace)


#============================================
def measure_in_box_heat_for_frame(live_trace: blob_trace.BlobObserverTrace | None,
	cx: float, cy: float, w: float, h: float) -> tuple:
	"""Compatibility seam for live residual heat measurement."""
	return walk_observer.measure_in_box_heat_for_frame(live_trace, cx, cy, w, h)


#============================================
def _compute_roi_and_observe(frame_f: int, anchor_cx: float, anchor_cy: float,
	seed_w: float, seed_h: float, scene_transform: object,
	reader: object, residual_cache: dict, fps: float, stride: int,
	precomputed_store: object = None) -> tuple:
	"""Compatibility seam for processed ROI observation."""
	return walk_observer.compute_roi_and_observe(frame_f, anchor_cx, anchor_cy,
		seed_w, seed_h, scene_transform, reader, residual_cache,
		fps, stride, precomputed_store)


#============================================
def gather_frame_candidates(obs: object, trace_sink_holder: object) -> list:
	"""Return ordered candidate blobs for one observation."""
	return walk_observer.gather_frame_candidates(obs, trace_sink_holder)


#============================================
def _build_window_entry(obs: object, trace_sink_holder: object, frame_f: int,
	pred_cx: float, pred_cy: float) -> dict:
	"""Build a stable candidate-window entry using facade patch seams."""
	return walk_observer.build_window_entry(obs, trace_sink_holder, frame_f, pred_cx,
		pred_cy, gather_frame_candidates, lighten_trace)


#============================================
def _neighbor_reached(frame_f: int, neighbor_seed_frame: int, sign: int) -> bool:
	"""Compatibility seam for directional seed crossing."""
	return walk_engine.neighbor_reached(frame_f, neighbor_seed_frame, sign)


#============================================
def _run_viterbi_and_emit_oldest(**kwargs: object) -> tuple:
	"""Emit through engine while preserving facade heat/confidence patches."""
	return walk_engine.run_viterbi_and_emit_oldest(**kwargs,
		conf_from_anchor_fn=conf_from_anchor, measure_heat_fn=measure_in_box_heat_for_frame)


#============================================
def _run_bootstrap_step(**kwargs: object) -> None:
	"""Bootstrap through engine while preserving facade observation patches."""
	walk_engine.run_bootstrap_step(**kwargs, observe_fn=_compute_roi_and_observe,
		conf_from_anchor_fn=conf_from_anchor, measure_heat_fn=measure_in_box_heat_for_frame,
		lighten_trace_fn=lighten_trace)


#============================================
def _run_windowed_steps(**kwargs: object) -> tuple:
	"""Run engine with the facade seams supplied at call time."""
	return walk_engine.run_windowed_steps(**kwargs, window_frames=WALKER_WINDOW_FRAMES,
		observe_fn=_compute_roi_and_observe, build_entry_fn=_build_window_entry,
		neighbor_reached_fn=_neighbor_reached, emit_fn=_run_viterbi_and_emit_oldest)


#============================================
def _emit_diagnostic_rows(extra_diagnostic_frames: list, visited_frames: set,
	seed_frame: int, neighbor_seed_frame: int, seed_cx: float, seed_cy: float,
	seed_w: float, seed_h: float, neighbor_seed_cx: float | None,
	neighbor_seed_cy: float | None, accepts: list, last_accepted_cx: float,
	last_accepted_cy: float, sign: int, stop_reason: str,
	debug_log: walk_debug_log.DebugLogWriter) -> None:
	"""Emit after-stop diagnostics without altering independent solved state."""
	for frame_index in extra_diagnostic_frames:
		if frame_index in visited_frames:
			continue
		if neighbor_seed_cx is not None and neighbor_seed_cy is not None:
			fraction = ((frame_index - seed_frame) / (neighbor_seed_frame - seed_frame)
				if neighbor_seed_frame != seed_frame else 0.5)
			fraction = max(0.0, min(1.0, fraction))
			pred_cx = (1 - fraction) * seed_cx + fraction * neighbor_seed_cx
			pred_cy = (1 - fraction) * seed_cy + fraction * neighbor_seed_cy
		elif accepts:
			pred_cx, pred_cy = last_accepted_cx, last_accepted_cy
		else:
			pred_cx, pred_cy = seed_cx, seed_cy
		debug_log.write_row(walk_debug_log.DebugLogRow(frame_index=frame_index,
			step=None, direction="+" if sign > 0 else "-", status="after_walk_terminated",
			pred_cx=pred_cx, pred_cy=pred_cy, torso_w_px=seed_w, torso_h_px=seed_h,
			stop_reason=stop_reason))


#============================================
def _compute_summary_metrics(**kwargs: object) -> WalkSummary:
	"""Compatibility seam for summary metrics."""
	return walk_summary.compute_summary_metrics(**kwargs)


#============================================
def walk_one_direction(seed: dict, neighbor_seed_frame: int, reader: object,
	scene_transform: object, fps: float, stride: int, sign: int,
	debug_log: walk_debug_log.DebugLogWriter, extra_diagnostic_frames: list | None = None,
	neighbor_seed_cx: float | None = None, neighbor_seed_cy: float | None = None,
	neighbor_seed_w: float | None = None, neighbor_seed_h: float | None = None,
	precomputed_store: object = None) -> WalkSummary:
	"""Walk independently from one processed-space seed toward its neighbor."""
	if extra_diagnostic_frames is None:
		extra_diagnostic_frames = []
	seed_frame, seed_cx, seed_cy = seed["frame_index"], seed["cx"], seed["cy"]
	seed_w, seed_h = seed["w"], seed["h"]
	accepts, visited_frames, direction_path, direction_trace_map = [], set(), [], {}
	size_at_frame = make_size_at_frame(seed_frame, seed_w, seed_h,
		neighbor_seed_frame, neighbor_seed_w, neighbor_seed_h)
	# Fresh bounded cache, Viterbi memo, and all mutable lists per direction (C9).
	residual_cache = residual_motion.make_bounded_residual_cache()
	cost_memo = walk_viterbi.WalkCostMemo()
	status_counts = {"accepted": 0, "interpolated": 0, "extrapolated": 0,
		"soft_miss_no_blob": 0, "soft_miss_no_path": 0}
	all_statuses = []
	_run_bootstrap_step(seed_frame=seed_frame, seed_cx=seed_cx, seed_cy=seed_cy,
		seed_w=seed_w, seed_h=seed_h,
		scene_transform=scene_transform, reader=reader, residual_cache=residual_cache,
		precomputed_store=precomputed_store, fps=fps, stride=stride, sign=sign,
		debug_log=debug_log, accepts=accepts, visited_frames=visited_frames,
		status_counts=status_counts, all_emitted_statuses=all_statuses,
		direction_path=direction_path, direction_trace_map=direction_trace_map)
	last_x, last_y, frame_f, reason = _run_windowed_steps(seed_frame=seed_frame,
		neighbor_seed_frame=neighbor_seed_frame, seed_w=seed_w, seed_h=seed_h,
		scene_transform=scene_transform, reader=reader,
		residual_cache=residual_cache, precomputed_store=precomputed_store, fps=fps,
		stride=stride, sign=sign, debug_log=debug_log, window_buffer=collections.deque(),
		accepts=accepts, visited_frames=visited_frames, status_counts=status_counts,
		all_emitted_statuses=all_statuses, last_accepted_cx=seed_cx, last_accepted_cy=seed_cy,
		size_at_frame=size_at_frame, direction_path=direction_path,
		direction_trace_map=direction_trace_map, cost_memo=cost_memo)
	_emit_diagnostic_rows(extra_diagnostic_frames, visited_frames, seed_frame,
		neighbor_seed_frame, seed_cx, seed_cy, seed_w, seed_h, neighbor_seed_cx,
		neighbor_seed_cy, accepts, last_x, last_y, sign, reason, debug_log)
	return _compute_summary_metrics(all_emitted_statuses=all_statuses,
		visited_frames=visited_frames, status_counts=status_counts, accepts=accepts,
		last_accepted_cx=last_x, last_accepted_cy=last_y,
		neighbor_seed_cx=neighbor_seed_cx, neighbor_seed_cy=neighbor_seed_cy,
		frame_f=frame_f, stop_reason=reason, direction_path=direction_path,
		direction_trace_map=direction_trace_map)
