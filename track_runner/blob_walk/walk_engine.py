"""Independent one-direction blob walker.

Owns the mutable walking loop and the per-direction entry point
`walk_one_direction`. Each direction gets its own residual cache, Viterbi memo,
and mutable state, so the forward and backward passes stay independent for
scoring (contract C9). The only cross-frame state is the bounded window buffer
of raw image-derived candidate lists.
"""

# Standard Library
import collections

# local repo modules
import residual_motion
import blob_walk.walk_debug_log as walk_debug_log
import blob_walk.walk_observer as walk_observer
import blob_walk.walk_status as walk_status
import blob_walk.walk_summary as walk_summary
import blob_walk.walk_viterbi as walk_viterbi

# Rolling Viterbi window depth, in frames.
WALKER_WINDOW_FRAMES = 9

# Per-frame confidence decay away from this pass's own seed anchor.
CONF_DECAY_PER_FRAME = 0.97
CONF_FLOOR = 0.1
CONF_START = 1.0


#============================================
def conf_from_anchor(frame_index: int, seed_frame: int) -> float:
	"""Return deterministic distance-from-this-pass-anchor confidence."""
	confidence = CONF_START * CONF_DECAY_PER_FRAME ** abs(frame_index - seed_frame)
	return max(CONF_FLOOR, confidence)


#============================================
def make_size_at_frame(
	seed_frame: int, seed_w: float, seed_h: float, neighbor_seed_frame: int,
	neighbor_seed_w: float | None, neighbor_seed_h: float | None,
) -> object:
	"""Build the C4 seed-derived processed torso-size resolver."""
	span = neighbor_seed_frame - seed_frame
	def size_at_frame(frame_index: int) -> tuple:
		if neighbor_seed_w is None or neighbor_seed_h is None or span == 0:
			return seed_w, seed_h
		fraction = max(0.0, min(1.0, (frame_index - seed_frame) / span))
		return (seed_w + fraction * (neighbor_seed_w - seed_w),
			seed_h + fraction * (neighbor_seed_h - seed_h))
	return size_at_frame


#============================================
def neighbor_reached(frame_f: int, neighbor_seed_frame: int, sign: int) -> bool:
	"""Return whether the directed walk reached or crossed its neighbor seed."""
	reached = sign * (frame_f - neighbor_seed_frame) >= 0
	return reached


#============================================
def run_viterbi_and_emit_oldest(
	window_buffer: collections.deque, seed_w: float, fps: float, sign: int,
	debug_log: walk_debug_log.DebugLogWriter, visited_frames: set, accepts: list,
	status_counts: dict, all_emitted_statuses: list, last_accepted_cx: float,
	last_accepted_cy: float, scene_transform: object, emit_count: int,
	seed_frame: int, size_at_frame: object, direction_path: list,
	direction_trace_map: dict, cost_memo: walk_viterbi.WalkCostMemo,
) -> tuple:
	"""Select the window path and emit its oldest finalized rows exactly once."""
	window_frames = [entry["frame_index"] for entry in window_buffer]
	window_candidates = [entry["candidates"] for entry in window_buffer]
	path = walk_viterbi.select_path(window_candidates, seed_w, fps,
		frame_indices=window_frames, cost_memo=cost_memo)
	path_cost_total = walk_viterbi.compute_path_cost(path, seed_w, fps, window_candidates)
	path_step_costs = walk_viterbi.compute_path_step_costs(path, seed_w, fps, window_candidates)
	candidates_in_window_count = walk_status.count_candidates_in_window(window_candidates)
	window_head_frame_val = window_frames[-1] if window_frames else None
	results = walk_status.emit_status_from_path(
		window_frames=window_frames, window_candidates=window_candidates, path=path,
		last_accepted_cx=last_accepted_cx, last_accepted_cy=last_accepted_cy,
		size_at_frame=size_at_frame)
	buffer_list = list(window_buffer)
	for index in range(min(emit_count, len(results))):
		result = results[index]
		entry = buffer_list[index]
		frame_index = result["frame_index"]
		status = result["status"]
		if status == "accepted":
			last_accepted_cx, last_accepted_cy = result["cx"], result["cy"]
			accepts.append(frame_index)
		if status in status_counts:
			status_counts[status] += 1
		all_emitted_statuses.append(status)
		cand_cx = result["cx"] if result["blob"] is not None else None
		cand_cy = result["cy"] if result["blob"] is not None else None
		cand_scene_x = cand_scene_y = None
		if cand_cx is not None and cand_cy is not None:
			scene = scene_transform.pixel_to_scene(frame_index, cand_cx, cand_cy)
			cand_scene_x, cand_scene_y = scene[0], scene[1]
		actual_jump = None
		if cand_cx is not None and cand_cy is not None:
			dx, dy = cand_cx - entry["pred_cx"], cand_cy - entry["pred_cy"]
			actual_jump = (dx * dx + dy * dy) ** 0.5
		frame_w, frame_h = result["w"], result["h"]
		heat_mean, heat_count = walk_observer.measure_in_box_heat_for_frame(
			entry["live_trace"], result["cx"], result["cy"], frame_w, frame_h)
		direction_path.append({"frame_index": frame_index, "cx": result["cx"], "cy": result["cy"],
			"w": frame_w, "h": frame_h, "conf": conf_from_anchor(frame_index, seed_frame),
			"status": status, "in_box_hot_mean": heat_mean, "in_box_hot_count": heat_count})
		if entry["light_trace"] is not None:
			direction_trace_map[frame_index] = entry["light_trace"]
		row = walk_debug_log.DebugLogRow(
			frame_index=frame_index, step=None, direction="+" if sign > 0 else "-", status=status,
			dt=None, torso_w_px=frame_w, torso_h_px=frame_h, pred_cx=entry["pred_cx"], pred_cy=entry["pred_cy"],
			cand_cx=cand_cx, cand_cy=cand_cy, cand_scene_x=cand_scene_x, cand_scene_y=cand_scene_y,
			actual_jump=actual_jump, obs_confidence=entry["obs_confidence"], obs_candidate_n=entry["obs_candidate_n"],
			obs_raw_n=entry["obs_raw_n"], winner_strength_score=entry["winner_strength_score"],
			winner_size_score=entry["winner_size_score"], winner_proximity_score=entry["winner_proximity_score"],
			winner_total_score=entry["winner_total_score"], candidates_json=entry["candidates_json"],
			reject_reason="", roi_anchor_source="accepted", path_cost=path_cost_total,
			candidates_in_window=candidates_in_window_count, path_step_cost=path_step_costs[index],
			window_head_frame=window_head_frame_val)
		debug_log.write_row(row)
		visited_frames.add(frame_index)
	for unused_index in range(min(emit_count, len(results))):
		if window_buffer:
			window_buffer.popleft()
	return last_accepted_cx, last_accepted_cy


#============================================
def run_bootstrap_step(
	seed_frame: int, seed_cx: float, seed_cy: float, seed_w: float, seed_h: float,
	scene_transform: object, reader: object, residual_cache: dict,
	precomputed_store: object, fps: float, stride: int, sign: int, debug_log: object,
	accepts: list, visited_frames: set, status_counts: dict, all_emitted_statuses: list,
	direction_path: list, direction_trace_map: dict,
) -> None:
	"""Observe and emit the independent seed-frame bootstrap row."""
	obs, holder = walk_observer.compute_roi_and_observe(
		frame_f=seed_frame, anchor_cx=seed_cx, anchor_cy=seed_cy,
		seed_w=seed_w, seed_h=seed_h,
		scene_transform=scene_transform, reader=reader, residual_cache=residual_cache,
		precomputed_store=precomputed_store, fps=fps, stride=stride)
	status = "accepted" if obs is not None else "soft_miss_no_blob"
	if obs is not None:
		accepts.append(seed_frame)
	status_counts[status] += 1
	trace = holder.observer_trace if obs is not None else None
	heat_mean, heat_count = walk_observer.measure_in_box_heat_for_frame(
		trace, seed_cx, seed_cy, seed_w, seed_h)
	direction_path.append({"frame_index": seed_frame, "cx": seed_cx, "cy": seed_cy, "w": seed_w,
		"h": seed_h, "conf": conf_from_anchor(seed_frame, seed_frame), "status": status,
		"in_box_hot_mean": heat_mean, "in_box_hot_count": heat_count})
	if trace is not None:
		direction_trace_map[seed_frame] = walk_observer.lighten_trace(trace)
	debug_log.write_row(walk_debug_log.DebugLogRow(frame_index=seed_frame, step=0,
		direction="+" if sign > 0 else "-", status=status, pred_cx=seed_cx, pred_cy=seed_cy,
		torso_w_px=seed_w, torso_h_px=seed_h, roi_anchor_source="accepted"))
	visited_frames.add(seed_frame)
	all_emitted_statuses.append(status)


#============================================
def run_windowed_steps(
	seed_frame: int, neighbor_seed_frame: int, seed_w: float, seed_h: float,
	scene_transform: object, reader: object, residual_cache: dict,
	precomputed_store: object, fps: float, stride: int, sign: int, debug_log: object,
	window_buffer: collections.deque, accepts: list, visited_frames: set,
	status_counts: dict, all_emitted_statuses: list, last_accepted_cx: float,
	last_accepted_cy: float, size_at_frame: object, direction_path: list,
	direction_trace_map: dict, cost_memo: object,
	window_frames: int = WALKER_WINDOW_FRAMES,
) -> tuple:
	"""Fill, emit, and flush the Viterbi window without cross-frame blob state."""
	step, frame_f, stop_reason = 1, seed_frame, "loop_guard"
	max_steps_guard = abs(neighbor_seed_frame - seed_frame) + 1
	while True:
		frame_f = seed_frame + sign * step * stride
		if frame_f < 0 or frame_f >= reader.frame_count:
			stop_reason = "boundary"
			break
		if neighbor_reached(frame_f, neighbor_seed_frame, sign):
			frame_f, stop_reason = neighbor_seed_frame, "hit_neighbor_seed"
			break
		if step > max_steps_guard:
			stop_reason = "loop_guard"
			break
		obs, holder = walk_observer.compute_roi_and_observe(
			frame_f=frame_f, anchor_cx=last_accepted_cx, anchor_cy=last_accepted_cy,
			seed_w=seed_w, seed_h=seed_h, scene_transform=scene_transform,
			reader=reader, residual_cache=residual_cache, precomputed_store=precomputed_store, fps=fps, stride=stride)
		window_buffer.append(walk_observer.build_window_entry(
			obs, holder, frame_f, last_accepted_cx, last_accepted_cy))
		if len(window_buffer) >= window_frames:
			last_accepted_cx, last_accepted_cy = run_viterbi_and_emit_oldest(window_buffer=window_buffer, seed_w=seed_w,
				fps=fps, sign=sign, debug_log=debug_log, visited_frames=visited_frames, accepts=accepts,
				status_counts=status_counts, all_emitted_statuses=all_emitted_statuses,
				last_accepted_cx=last_accepted_cx, last_accepted_cy=last_accepted_cy,
				scene_transform=scene_transform, emit_count=1, seed_frame=seed_frame,
				size_at_frame=size_at_frame, direction_path=direction_path,
				direction_trace_map=direction_trace_map, cost_memo=cost_memo)
		step += 1
	if window_buffer:
		last_accepted_cx, last_accepted_cy = run_viterbi_and_emit_oldest(window_buffer=window_buffer, seed_w=seed_w,
			fps=fps, sign=sign, debug_log=debug_log, visited_frames=visited_frames, accepts=accepts,
			status_counts=status_counts, all_emitted_statuses=all_emitted_statuses,
			last_accepted_cx=last_accepted_cx, last_accepted_cy=last_accepted_cy,
			scene_transform=scene_transform, emit_count=len(window_buffer), seed_frame=seed_frame,
			size_at_frame=size_at_frame, direction_path=direction_path,
			direction_trace_map=direction_trace_map, cost_memo=cost_memo)
	return last_accepted_cx, last_accepted_cy, frame_f, stop_reason


#============================================
def emit_diagnostic_rows(extra_diagnostic_frames: list, visited_frames: set,
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
def walk_one_direction(seed: dict, neighbor_seed_frame: int, reader: object,
	scene_transform: object, fps: float, stride: int, sign: int,
	debug_log: walk_debug_log.DebugLogWriter, extra_diagnostic_frames: list | None = None,
	neighbor_seed_cx: float | None = None, neighbor_seed_cy: float | None = None,
	neighbor_seed_w: float | None = None, neighbor_seed_h: float | None = None,
	precomputed_store: object = None) -> walk_summary.WalkSummary:
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
	run_bootstrap_step(seed_frame=seed_frame, seed_cx=seed_cx, seed_cy=seed_cy,
		seed_w=seed_w, seed_h=seed_h,
		scene_transform=scene_transform, reader=reader, residual_cache=residual_cache,
		precomputed_store=precomputed_store, fps=fps, stride=stride, sign=sign,
		debug_log=debug_log, accepts=accepts, visited_frames=visited_frames,
		status_counts=status_counts, all_emitted_statuses=all_statuses,
		direction_path=direction_path, direction_trace_map=direction_trace_map)
	last_x, last_y, frame_f, reason = run_windowed_steps(seed_frame=seed_frame,
		neighbor_seed_frame=neighbor_seed_frame, seed_w=seed_w, seed_h=seed_h,
		scene_transform=scene_transform, reader=reader,
		residual_cache=residual_cache, precomputed_store=precomputed_store, fps=fps,
		stride=stride, sign=sign, debug_log=debug_log, window_buffer=collections.deque(),
		accepts=accepts, visited_frames=visited_frames, status_counts=status_counts,
		all_emitted_statuses=all_statuses, last_accepted_cx=seed_cx, last_accepted_cy=seed_cy,
		size_at_frame=size_at_frame, direction_path=direction_path,
		direction_trace_map=direction_trace_map, cost_memo=cost_memo)
	emit_diagnostic_rows(extra_diagnostic_frames, visited_frames, seed_frame,
		neighbor_seed_frame, seed_cx, seed_cy, seed_w, seed_h, neighbor_seed_cx,
		neighbor_seed_cy, accepts, last_x, last_y, sign, reason, debug_log)
	return walk_summary.compute_summary_metrics(all_emitted_statuses=all_statuses,
		visited_frames=visited_frames, status_counts=status_counts, accepts=accepts,
		last_accepted_cx=last_x, last_accepted_cy=last_y,
		neighbor_seed_cx=neighbor_seed_cx, neighbor_seed_cy=neighbor_seed_cy,
		frame_f=frame_f, stop_reason=reason, direction_path=direction_path,
		direction_trace_map=direction_trace_map)
