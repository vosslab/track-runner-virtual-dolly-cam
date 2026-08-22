"""Seed-truth stamping and local seed anchoring for interval trajectories."""

# Standard Library
import math

# PIP3 modules
import numpy
import scipy.interpolate

# local repo modules
import interval_fingerprint
import trajectory_confidence


#============================================
def stamp_interval_endpoint_seed_truth(
	path: list, seed_start: dict, seed_end: dict,
) -> None:
	"""Apply C3 to an interval result before persistence."""
	for index, seed in ((0, seed_start), (len(path) - 1, seed_end)):
		if index < 0 or "status" not in seed:
			continue
		if seed["status"] not in ("visible", "partial"):
			continue
		state = path[index]
		for key in ("cx", "cy", "w", "h"):
			state[key] = seed[key]
		state["conf"] = 1.0
		state["seed_status"] = seed["status"]


#============================================
def restamp_cached_interval_seed_truth(solved_intervals: dict, seeds: list) -> int:
	"""Restore C3 geometry on matching cached interval endpoints."""
	usable = interval_fingerprint.filter_usable_seeds_sorted(seeds, verbose=False)
	repaired = 0
	for seed_start, seed_end in zip(usable, usable[1:]):
		fingerprint = interval_fingerprint.compute_interval_fingerprint(seed_start, seed_end)
		result = solved_intervals.get(fingerprint)
		if result is None:
			continue
		path = result.get("blended_path")
		if not path:
			continue
		for index, seed in ((0, seed_start), (len(path) - 1, seed_end)):
			if seed["status"] not in ("visible", "partial"):
				continue
			state = path[index]
			if any(state.get(key) != seed[key] for key in ("cx", "cy", "w", "h")):
				repaired += 1
		stamp_interval_endpoint_seed_truth(path, seed_start, seed_end)
	return repaired


#============================================
def _stamp_seed_truth(trajectory: list, seeds: list) -> list:
	"""Stamp human-authored C1/C3 truth onto trajectory seed frames."""
	n = len(trajectory)
	stamped = 0
	for seed in seeds:
		frame_index = int(seed["frame_index"])
		if frame_index < 0 or frame_index >= n or trajectory[frame_index] is None:
			continue
		status = seed["status"]
		if status in ("visible", "partial"):
			state = trajectory[frame_index]
			state["cx"] = seed["cx"]
			state["cy"] = seed["cy"]
			state["w"] = seed["w"]
			state["h"] = seed["h"]
			state["conf"] = 1.0
			state["seed_status"] = status
			stamped += 1
		elif status == "approximate":
			trajectory[frame_index]["conf"] = 0.3
			stamped += 1
	if stamped > 0:
		print(f"  stamped seed truth on {stamped} seed frames")
	return trajectory


ANCHOR_PROXIMITY_SKIP = 7
ANCHOR_BLEND_SCALE_XY = 0.5
ANCHOR_BLEND_SCALE_WH = 0.3
ANCHOR_MAX_DISP_XY = 0.25
ANCHOR_MAX_DISP_WH = 0.15
ANCHOR_WINDOW_SEEDS = 4


#============================================
def _collect_anchor_knots(seeds: list) -> list:
	"""Collect trusted visible/partial knots, deduplicated by frame."""
	raw_knots = []
	for seed in seeds:
		status = seed.get("status", "")
		if status not in ("visible", "partial"):
			continue
		seed_cx = seed.get("cx")
		seed_cy = seed.get("cy")
		seed_w = seed.get("w")
		seed_h = seed.get("h")
		if seed_cx is None or seed_cy is None:
			torso_box = seed.get("torso_box")
			if torso_box is None:
				continue
			seed_cx = float(torso_box[0]) + float(torso_box[2]) / 2.0
			seed_cy = float(torso_box[1]) + float(torso_box[3]) / 2.0
			seed_w = float(torso_box[2])
			seed_h = float(torso_box[3])
		cx, cy = float(seed_cx), float(seed_cy)
		w, h = float(seed_w), float(seed_h)
		if w <= 0 or h <= 0:
			continue
		raw_knots.append((int(seed["frame_index"]), cx, cy, w, h, status))
	raw_knots.sort(key=lambda knot: knot[0])
	deduped = {}
	for knot in raw_knots:
		frame_index = knot[0]
		if frame_index not in deduped:
			deduped[frame_index] = knot
			continue
		existing = deduped[frame_index]
		if knot[5] == "visible" and existing[5] != "visible":
			deduped[frame_index] = knot
		elif knot[5] == existing[5] and knot[3] * knot[4] > existing[3] * existing[4]:
			deduped[frame_index] = knot
	result = sorted(deduped.values(), key=lambda knot: knot[0])
	return result


#============================================
def _build_local_fit(knots: list, center_frame: int, window_seeds: int) -> tuple | None:
	"""Build local spline or linear interpolators around one frame."""
	before = []
	after = []
	for knot in knots:
		if knot[0] < center_frame:
			before.append(knot)
		elif knot[0] > center_frame:
			after.append(knot)
		else:
			before.append(knot)
			after.append(knot)
	combined = {}
	for knot in before[-window_seeds:] + after[:window_seeds]:
		combined[knot[0]] = knot
	local_knots = sorted(combined.values(), key=lambda knot: knot[0])
	if len(local_knots) < 2:
		return None
	frames = numpy.array([knot[0] for knot in local_knots], dtype=float)
	cx_vals = numpy.array([knot[1] for knot in local_knots], dtype=float)
	cy_vals = numpy.array([knot[2] for knot in local_knots], dtype=float)
	logw_vals = numpy.array([math.log(knot[3]) for knot in local_knots], dtype=float)
	logh_vals = numpy.array([math.log(knot[4]) for knot in local_knots], dtype=float)
	interps = {}
	knot_frames = tuple(int(knot[0]) for knot in local_knots)
	if len(local_knots) == 2:
		interps["cx_interp"] = (frames, cx_vals)
		interps["cy_interp"] = (frames, cy_vals)
		interps["logw_interp"] = (frames, logw_vals)
		interps["logh_interp"] = (frames, logh_vals)
	else:
		interps["cx_interp"] = scipy.interpolate.CubicSpline(frames, cx_vals, bc_type="natural")
		interps["cy_interp"] = scipy.interpolate.CubicSpline(frames, cy_vals, bc_type="natural")
		interps["logw_interp"] = scipy.interpolate.PchipInterpolator(frames, logw_vals)
		interps["logh_interp"] = scipy.interpolate.PchipInterpolator(frames, logh_vals)
	return (interps, knot_frames)


#============================================
def _eval_fit(interps: dict, frame: float) -> tuple:
	"""Evaluate the local center/log-size interpolators."""
	values = []
	for key in ("cx_interp", "cy_interp", "logw_interp", "logh_interp"):
		interpolator = interps[key]
		if callable(interpolator):
			value = float(interpolator(frame))
		else:
			value = float(numpy.interp(frame, interpolator[0], interpolator[1]))
		values.append(value)
	result = (values[0], values[1], math.exp(values[2]), math.exp(values[3]))
	return result


#============================================
def _segment_by_knot_window(frame_range: range, knots: list, window_seeds: int) -> list:
	"""Group frames sharing the same local knot window."""
	if not knots:
		return []
	segments = []
	current_key = None
	current_start = None
	current_knots = None
	for frame_index in frame_range:
		before, after = [], []
		for knot in knots:
			if knot[0] < frame_index:
				before.append(knot)
			elif knot[0] > frame_index:
				after.append(knot)
			else:
				before.append(knot)
				after.append(knot)
		combined = {}
		for knot in before[-window_seeds:] + after[:window_seeds]:
			combined[knot[0]] = knot
		local_knots = sorted(combined.values(), key=lambda knot: knot[0])
		key = tuple(knot[0] for knot in local_knots)
		if key != current_key:
			if current_key is not None:
				segments.append((current_start, frame_index - 1, current_knots))
			current_key, current_start, current_knots = key, frame_index, local_knots
	if current_key is not None:
		last_frame = frame_range[-1] if frame_range else current_start
		segments.append((current_start, last_frame, current_knots))
	return segments


#============================================
def anchor_to_seeds(trajectory: list, seeds: list) -> list:
	"""Blend a local seed fit into a stitched trajectory and restore seed identity."""
	first_state = next((state for state in trajectory if state is not None), None)
	if first_state is None or first_state.get("_anchor_applied"):
		return trajectory
	knots = _collect_anchor_knots(seeds)
	if len(knots) < 2:
		return trajectory
	knot_frame_set = set(knot[0] for knot in knots)
	visible_knots = {knot[0]: knot for knot in knots if knot[5] == "visible"}
	partial_knots = {knot[0]: knot for knot in knots if knot[5] == "partial"}
	segments = _segment_by_knot_window(
		range(knots[0][0], knots[-1][0] + 1),
		knots,
		ANCHOR_WINDOW_SEEDS,
	)
	corrected_count = 0
	for seg_start, seg_end, unused_seg_knots in segments:
		fit_result = _build_local_fit(knots, (seg_start + seg_end) // 2, ANCHOR_WINDOW_SEEDS)
		if fit_result is None:
			continue
		interps, unused_knot_frames = fit_result
		for frame_index in range(seg_start, seg_end + 1):
			if frame_index < 0 or frame_index >= len(trajectory):
				continue
			state = trajectory[frame_index]
			if state is None or any(
				abs(frame_index - knot_frame) <= ANCHOR_PROXIMITY_SKIP
				for knot_frame in knot_frame_set
			):
				continue
			ref_cx, ref_cy, ref_w, ref_h = _eval_fit(interps, float(frame_index))
			cur_cx, cur_cy = float(state["cx"]), float(state["cy"])
			cur_w, cur_h, conf = float(state["w"]), float(state["h"]), float(state["conf"])
			blend_xy = ANCHOR_BLEND_SCALE_XY * (1.0 - conf) ** 2
			blend_wh = ANCHOR_BLEND_SCALE_WH * (1.0 - conf) ** 2
			dx = max(-ANCHOR_MAX_DISP_XY * cur_w, min(ANCHOR_MAX_DISP_XY * cur_w, ref_cx - cur_cx))
			dy = max(-ANCHOR_MAX_DISP_XY * cur_h, min(ANCHOR_MAX_DISP_XY * cur_h, ref_cy - cur_cy))
			dw = max(-ANCHOR_MAX_DISP_WH * cur_w, min(ANCHOR_MAX_DISP_WH * cur_w, ref_w - cur_w))
			dh = max(-ANCHOR_MAX_DISP_WH * cur_h, min(ANCHOR_MAX_DISP_WH * cur_h, ref_h - cur_h))
			new_w, new_h = cur_w + blend_wh * dw, cur_h + blend_wh * dh
			if new_w > 0 and new_h > 0:
				state["cx"], state["cy"] = cur_cx + blend_xy * dx, cur_cy + blend_xy * dy
				state["w"], state["h"] = new_w, new_h
				corrected_count += 1
	pinned_count = 0
	for frame_index, knot in visible_knots.items():
		if 0 <= frame_index < len(trajectory) and trajectory[frame_index] is not None:
			state = trajectory[frame_index]
			state["cx"], state["cy"], state["w"], state["h"] = knot[1:5]
			state["source"], state["seed_status"] = "seed", knot[5]
			pinned_count += 1
	for frame_index, knot in partial_knots.items():
		if 0 <= frame_index < len(trajectory) and trajectory[frame_index] is not None:
			trajectory[frame_index]["source"] = "seed"
			trajectory[frame_index]["seed_status"] = knot[5]
	if corrected_count > 0 or pinned_count > 0:
		print(
			f"  anchor_to_seeds: corrected {corrected_count} frames, "
			f"pinned {pinned_count} visible seeds"
		)
	for state in trajectory:
		if state is not None:
			state["_anchor_applied"] = True
			break
	return trajectory


#============================================
def stitch_trajectories(interval_results: list) -> list:
	"""Concatenate interval blended paths into one frame-indexed trajectory."""
	if not interval_results:
		return []
	last_end = max(result["end_frame"] for result in interval_results)
	trajectory = [None] * (last_end + 1)
	for result in interval_results:
		for index, state in enumerate(result["blended_path"]):
			frame_index = result["start_frame"] + index
			if 0 <= frame_index <= last_end:
				trajectory[frame_index] = state
	return trajectory


#============================================
def reconstruct_trajectory_with_confidence(interval_results: list) -> list:
	"""Stitch paths and restore their owner-defined confidence."""
	trajectory = stitch_trajectories(interval_results)
	trajectory = trajectory_confidence.apply_per_frame_confidence(interval_results, trajectory)
	return trajectory
