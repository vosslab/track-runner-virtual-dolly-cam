"""Analytical velocity model using directionally asymmetric Hermite curves.

For each seed-to-seed interval, fits cubic Hermite splines with separate
forward and backward curves. Endpoints are hard-anchored at seeds, but slopes
differ by direction (backward-looking vs forward-looking regression).

The propagators are pure raw_pred builders: each computes `raw_pred[t]` for
every frame from the Hermite curves only and returns it as a list of state
dicts. No blob evidence is consulted here. The optional windowed blob walker
is a separate, opt-in consumer reached through the Stage-4 walker seam.
"""

# Standard Library
import math

# PIP3 modules
import numpy


#============================================
def estimate_directional_slope(
	seeds_scene: list,
	anchor_idx: int,
	direction: str,
	frame_indices: list,
) -> tuple:
	"""Estimate linear slope at anchor seed from directional support seeds.

	Uses 2-4 nearest seeds in the specified direction to fit a linear
	regression on (frame, position). Fallback: if only 1 neighbor,
	use simple finite difference; if 0 neighbors, return (0, 0).

	Args:
		seeds_scene: List of (frame_index, sx, sy, sw, sh) tuples in scene coords.
		anchor_idx: Index into seeds_scene of the anchor seed.
		direction: "backward" (left neighbors) or "forward" (right neighbors).
		frame_indices: Unused; kept for API consistency.

	Returns:
		Tuple (slope_x, slope_y) in scene-coordinate units per frame.
	"""
	anchor_frame, anchor_sx, anchor_sy, _, _ = seeds_scene[anchor_idx]

	# collect nearest neighbors in the specified direction
	neighbor_indices = []
	if direction == "backward":
		# look left (earlier indices)
		candidate_indices = range(anchor_idx - 1, -1, -1)
	elif direction == "forward":
		# look right (later indices)
		candidate_indices = range(anchor_idx + 1, len(seeds_scene))
	else:
		raise ValueError(f"Unknown direction: {direction}")

	# select up to 4 neighbors
	for idx in candidate_indices:
		if len(neighbor_indices) >= 4:
			break
		neighbor_indices.append(idx)

	if len(neighbor_indices) == 0:
		# no neighbors in this direction
		return (0.0, 0.0)

	if len(neighbor_indices) == 1:
		# single neighbor: use simple finite difference
		neigh_frame, neigh_sx, neigh_sy, _, _ = seeds_scene[neighbor_indices[0]]
		dt = anchor_frame - neigh_frame
		if dt != 0:
			slope_x = (anchor_sx - neigh_sx) / dt
			slope_y = (anchor_sy - neigh_sy) / dt
		else:
			slope_x = 0.0
			slope_y = 0.0
		return (slope_x, slope_y)

	# multiple neighbors: fit linear regression
	frames = []
	x_positions = []
	y_positions = []
	for idx in neighbor_indices:
		frame, sx, sy, _, _ = seeds_scene[idx]
		frames.append(frame)
		x_positions.append(sx)
		y_positions.append(sy)

	# add anchor seed to regression data
	frames.append(anchor_frame)
	x_positions.append(anchor_sx)
	y_positions.append(anchor_sy)

	# fit: position = m * frame + b
	frames_array = numpy.array(frames, dtype=numpy.float64)
	x_array = numpy.array(x_positions, dtype=numpy.float64)
	y_array = numpy.array(y_positions, dtype=numpy.float64)

	# use polyfit to get slope and intercept
	coeffs_x = numpy.polyfit(frames_array, x_array, 1)
	coeffs_y = numpy.polyfit(frames_array, y_array, 1)

	slope_x = float(coeffs_x[0])
	slope_y = float(coeffs_y[0])

	return (slope_x, slope_y)


#============================================
def estimate_directional_size_slope(
	seeds_scene: list,
	anchor_idx: int,
	direction: str,
	frame_indices: list,
) -> tuple:
	"""Estimate size slope in log-space at anchor seed.

	Same approach as estimate_directional_slope but for (w, h) in log space
	to handle multiplicative scale changes.

	Args:
		seeds_scene: List of (frame_index, sx, sy, sw, sh) tuples in scene coords.
		anchor_idx: Index into seeds_scene of the anchor seed.
		direction: "backward" (left neighbors) or "forward" (right neighbors).
		frame_indices: Unused; kept for API consistency.

	Returns:
		Tuple (slope_w, slope_h) as d(log(size))/dt.
	"""
	anchor_frame, _, _, anchor_sw, anchor_sh = seeds_scene[anchor_idx]

	# collect nearest neighbors in the specified direction
	neighbor_indices = []
	if direction == "backward":
		candidate_indices = range(anchor_idx - 1, -1, -1)
	elif direction == "forward":
		candidate_indices = range(anchor_idx + 1, len(seeds_scene))
	else:
		raise ValueError(f"Unknown direction: {direction}")

	for idx in candidate_indices:
		if len(neighbor_indices) >= 4:
			break
		neighbor_indices.append(idx)

	if len(neighbor_indices) == 0:
		return (0.0, 0.0)

	if len(neighbor_indices) == 1:
		# single neighbor: finite difference in log space
		neigh_frame, _, _, neigh_sw, neigh_sh = seeds_scene[neighbor_indices[0]]
		dt = anchor_frame - neigh_frame
		if dt != 0 and anchor_sw > 1e-6 and neigh_sw > 1e-6:
			log_w_anchor = math.log(anchor_sw)
			log_w_neigh = math.log(neigh_sw)
			slope_w = (log_w_anchor - log_w_neigh) / dt
		else:
			slope_w = 0.0
		if dt != 0 and anchor_sh > 1e-6 and neigh_sh > 1e-6:
			log_h_anchor = math.log(anchor_sh)
			log_h_neigh = math.log(neigh_sh)
			slope_h = (log_h_anchor - log_h_neigh) / dt
		else:
			slope_h = 0.0
		return (slope_w, slope_h)

	# multiple neighbors: fit linear regression in log space
	frames = []
	log_w_positions = []
	log_h_positions = []
	for idx in neighbor_indices:
		frame, _, _, sw, sh = seeds_scene[idx]
		frames.append(frame)
		if sw > 1e-6:
			log_w_positions.append(math.log(sw))
		else:
			log_w_positions.append(0.0)
		if sh > 1e-6:
			log_h_positions.append(math.log(sh))
		else:
			log_h_positions.append(0.0)

	# add anchor seed
	frames.append(anchor_frame)
	if anchor_sw > 1e-6:
		log_w_positions.append(math.log(anchor_sw))
	else:
		log_w_positions.append(0.0)
	if anchor_sh > 1e-6:
		log_h_positions.append(math.log(anchor_sh))
	else:
		log_h_positions.append(0.0)

	# fit linear regression
	frames_array = numpy.array(frames, dtype=numpy.float64)
	w_array = numpy.array(log_w_positions, dtype=numpy.float64)
	h_array = numpy.array(log_h_positions, dtype=numpy.float64)

	coeffs_w = numpy.polyfit(frames_array, w_array, 1)
	coeffs_h = numpy.polyfit(frames_array, h_array, 1)

	slope_w = float(coeffs_w[0])
	slope_h = float(coeffs_h[0])

	return (slope_w, slope_h)


#============================================
def hermite_interpolate(
	t: float,
	p0: float,
	p1: float,
	m0: float,
	m1: float,
) -> float:
	"""Standard cubic Hermite basis interpolation.

	Interpolates between p0 and p1 with tangent slopes m0 and m1.

	Args:
		t: Interpolation parameter in [0, 1].
		p0: Start value (at t=0).
		p1: End value (at t=1).
		m0: Tangent slope at start.
		m1: Tangent slope at end.

	Returns:
		Interpolated value at parameter t.
	"""
	# Hermite basis functions
	h00 = (1.0 + 2.0 * t) * (1.0 - t) ** 2
	h10 = t * (1.0 - t) ** 2
	h01 = t * t * (3.0 - 2.0 * t)
	h11 = t * t * (t - 1.0)

	# interpolate
	value = h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1
	return value


#============================================
def fit_interval_curves(
	left_seed: dict,
	right_seed: dict,
	all_seeds_scene: list,
	scene_transform: object,
) -> dict:
	"""Fit directionally asymmetric Hermite curves to a seed interval.

	Args:
		left_seed: Seed dict with frame, cx, cy, w, h, status keys.
		right_seed: Seed dict with frame, cx, cy, w, h, status keys.
		all_seeds_scene: List of all seeds as (frame, sx, sy, sw, sh).
		scene_transform: SceneTransform instance for coordinate conversion.

	Returns:
		Dict with:
		- fwd_slopes: (slope_x, slope_y) at left seed (backward-looking).
		- bwd_slopes: (slope_x, slope_y) at right seed (forward-looking).
		- fwd_size_slopes: (slope_w, slope_h) at left.
		- bwd_size_slopes: (slope_w, slope_h) at right.
		- left_pos: (sx, sy) in scene coords.
		- right_pos: (sx, sy) in scene coords.
		- left_size: (sw, sh) in scene coords.
		- right_size: (sw, sh) in scene coords.
		- start_frame: int.
		- end_frame: int.
	"""
	left_frame = int(left_seed["frame_index"])
	right_frame = int(right_seed["frame_index"])
	start_frame = left_frame
	end_frame = right_frame

	# convert seeds to scene coordinates
	left_cx = float(left_seed["cx"])
	left_cy = float(left_seed["cy"])
	left_w = float(left_seed["w"])
	left_h = float(left_seed["h"])

	right_cx = float(right_seed["cx"])
	right_cy = float(right_seed["cy"])
	right_w = float(right_seed["w"])
	right_h = float(right_seed["h"])

	# convert to scene coords
	left_sx, left_sy, left_sw, left_sh = (
		scene_transform.pixel_box_to_scene(left_frame, left_cx, left_cy, left_w, left_h)
	)
	right_sx, right_sy, right_sw, right_sh = (
		scene_transform.pixel_box_to_scene(right_frame, right_cx, right_cy, right_w, right_h)
	)

	# find left and right seed indices in all_seeds_scene
	left_seed_idx = None
	right_seed_idx = None
	for idx, (frame, _, _, _, _) in enumerate(all_seeds_scene):
		if frame == left_frame:
			left_seed_idx = idx
		if frame == right_frame:
			right_seed_idx = idx

	if left_seed_idx is None or right_seed_idx is None:
		raise ValueError(
			f"Could not find seeds in all_seeds_scene: "
			f"left_frame={left_frame}, right_frame={right_frame}"
		)

	# estimate slopes at both endpoints
	# FWD curve: slope at left from backward-looking (left neighbors)
	fwd_slope_x, fwd_slope_y = estimate_directional_slope(
		all_seeds_scene, left_seed_idx, "backward", None,
	)
	fwd_size_slope_w, fwd_size_slope_h = estimate_directional_size_slope(
		all_seeds_scene, left_seed_idx, "backward", None,
	)

	# BWD curve: slope at right from forward-looking (right neighbors)
	bwd_slope_x, bwd_slope_y = estimate_directional_slope(
		all_seeds_scene, right_seed_idx, "forward", None,
	)
	bwd_size_slope_w, bwd_size_slope_h = estimate_directional_size_slope(
		all_seeds_scene, right_seed_idx, "forward", None,
	)

	result = {
		"fwd_slopes": (fwd_slope_x, fwd_slope_y),
		"bwd_slopes": (bwd_slope_x, bwd_slope_y),
		"fwd_size_slopes": (fwd_size_slope_w, fwd_size_slope_h),
		"bwd_size_slopes": (bwd_size_slope_w, bwd_size_slope_h),
		"left_pos": (left_sx, left_sy),
		"right_pos": (right_sx, right_sy),
		"left_size": (left_sw, left_sh),
		"right_size": (right_sw, right_sh),
		"start_frame": start_frame,
		"end_frame": end_frame,
	}
	return result


#============================================
def _compute_raw_pred_forward(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Stage 1 of the forward pass: pure Hermite prediction per frame.

	Returns a list of `(frame_index, cx, cy, w, h, conf)` tuples in
	pixel coordinates, one per frame from start_frame to end_frame
	inclusive. This array is FROZEN -- the gating code MUST read from
	it only, never from any post-blob output.
	"""
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]

	left_sx, left_sy = interval_curves["left_pos"]
	right_sx, right_sy = interval_curves["right_pos"]
	left_sw, left_sh = interval_curves["left_size"]
	right_sw, right_sh = interval_curves["right_size"]

	fwd_slope_x, fwd_slope_y = interval_curves["fwd_slopes"]
	fwd_size_slope_w, fwd_size_slope_h = interval_curves["fwd_size_slopes"]

	m0_x = fwd_slope_x
	m0_y = fwd_slope_y

	interval_length = float(end_frame - start_frame)
	if interval_length > 0:
		m1_x = (right_sx - left_sx) / interval_length
		m1_y = (right_sy - left_sy) / interval_length
	else:
		m1_x = 0.0
		m1_y = 0.0

	m0_w = fwd_size_slope_w
	m0_h = fwd_size_slope_h
	if interval_length > 0:
		m1_w = (math.log(right_sw) - math.log(left_sw)) / interval_length if left_sw > 1e-6 and right_sw > 1e-6 else 0.0
		m1_h = (math.log(right_sh) - math.log(left_sh)) / interval_length if left_sh > 1e-6 and right_sh > 1e-6 else 0.0
	else:
		m1_w = 0.0
		m1_h = 0.0

	raw = []
	conf_decay_per_frame = 0.97
	conf_floor = 0.1
	start_conf = 1.0

	for frame_index in range(start_frame, end_frame + 1):
		if interval_length > 0:
			t = (frame_index - start_frame) / interval_length
		else:
			t = 0.0 if frame_index == start_frame else 1.0
		t = max(0.0, min(1.0, t))

		scene_cx = hermite_interpolate(t, left_sx, right_sx, m0_x * interval_length, m1_x * interval_length)
		scene_cy = hermite_interpolate(t, left_sy, right_sy, m0_y * interval_length, m1_y * interval_length)
		log_left_w = math.log(left_sw) if left_sw > 1e-6 else 0.0
		log_right_w = math.log(right_sw) if right_sw > 1e-6 else 0.0
		log_left_h = math.log(left_sh) if left_sh > 1e-6 else 0.0
		log_right_h = math.log(right_sh) if right_sh > 1e-6 else 0.0
		log_w = hermite_interpolate(t, log_left_w, log_right_w, m0_w * interval_length, m1_w * interval_length)
		log_h = hermite_interpolate(t, log_left_h, log_right_h, m0_h * interval_length, m1_h * interval_length)
		scene_w = math.exp(log_w) if log_w < 100 else left_sw
		scene_h = math.exp(log_h) if log_h < 100 else left_sh

		pixel_cx, pixel_cy, pixel_w, pixel_h = (
			scene_transform.scene_box_to_pixel(frame_index, scene_cx, scene_cy, scene_w, scene_h)
		)

		frames_from_start = frame_index - start_frame
		confidence = max(conf_floor, start_conf * (conf_decay_per_frame ** frames_from_start))

		raw.append((
			int(frame_index),
			float(pixel_cx),
			float(pixel_cy),
			float(pixel_w),
			float(pixel_h),
			float(confidence),
		))

	return raw


#============================================
def _compute_raw_pred_backward(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Stage 1 of the backward pass: pure Hermite prediction per frame.

	Returns the same shape and ordering as _compute_raw_pred_forward:
	raw[i] holds the tuple for absolute frame start_frame + i. The
	backward pass is distinguished by its Hermite slopes (evaluated at
	the right seed) and its confidence decay (measured from the end
	seed, so confidence peaks at the last slot). Solve direction is
	still conceptually end -> start; only storage is chronological.
	"""
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]

	left_sx, left_sy = interval_curves["left_pos"]
	right_sx, right_sy = interval_curves["right_pos"]
	left_sw, left_sh = interval_curves["left_size"]
	right_sw, right_sh = interval_curves["right_size"]

	bwd_slope_x, bwd_slope_y = interval_curves["bwd_slopes"]
	bwd_size_slope_w, bwd_size_slope_h = interval_curves["bwd_size_slopes"]

	interval_length = float(end_frame - start_frame)
	if interval_length > 0:
		m0_x = (right_sx - left_sx) / interval_length
		m0_y = (right_sy - left_sy) / interval_length
	else:
		m0_x = 0.0
		m0_y = 0.0

	m1_x = bwd_slope_x
	m1_y = bwd_slope_y

	if interval_length > 0:
		m0_w = (math.log(right_sw) - math.log(left_sw)) / interval_length if left_sw > 1e-6 and right_sw > 1e-6 else 0.0
		m0_h = (math.log(right_sh) - math.log(left_sh)) / interval_length if left_sh > 1e-6 and right_sh > 1e-6 else 0.0
	else:
		m0_w = 0.0
		m0_h = 0.0

	m1_w = bwd_size_slope_w
	m1_h = bwd_size_slope_h

	raw = []
	conf_decay_per_frame = 0.97
	conf_floor = 0.1
	start_conf = 1.0

	# iterate chronologically so raw[i] corresponds to absolute frame
	# start_frame + i (same slot convention as _compute_raw_pred_forward).
	# Confidence is still end-anchored via frames_from_end below.
	for frame_index in range(start_frame, end_frame + 1):
		if interval_length > 0:
			t = (frame_index - start_frame) / interval_length
		else:
			t = 1.0 if frame_index == end_frame else 0.0
		t = max(0.0, min(1.0, t))

		scene_cx = hermite_interpolate(t, left_sx, right_sx, m0_x * interval_length, m1_x * interval_length)
		scene_cy = hermite_interpolate(t, left_sy, right_sy, m0_y * interval_length, m1_y * interval_length)
		log_left_w = math.log(left_sw) if left_sw > 1e-6 else 0.0
		log_right_w = math.log(right_sw) if right_sw > 1e-6 else 0.0
		log_left_h = math.log(left_sh) if left_sh > 1e-6 else 0.0
		log_right_h = math.log(right_sh) if right_sh > 1e-6 else 0.0
		log_w = hermite_interpolate(t, log_left_w, log_right_w, m0_w * interval_length, m1_w * interval_length)
		log_h = hermite_interpolate(t, log_left_h, log_right_h, m0_h * interval_length, m1_h * interval_length)
		scene_w = math.exp(log_w) if log_w < 100 else right_sw
		scene_h = math.exp(log_h) if log_h < 100 else right_sh

		pixel_cx, pixel_cy, pixel_w, pixel_h = (
			scene_transform.scene_box_to_pixel(frame_index, scene_cx, scene_cy, scene_w, scene_h)
		)

		frames_from_end = end_frame - frame_index
		confidence = max(conf_floor, start_conf * (conf_decay_per_frame ** frames_from_end))

		raw.append((
			int(frame_index),
			float(pixel_cx),
			float(pixel_cy),
			float(pixel_w),
			float(pixel_h),
			float(confidence),
		))

	return raw


#============================================
def _raw_pred_to_states(raw: list) -> list:
	"""Wrap a frozen raw_pred list as a list of tracking state dicts.

	Each entry carries the pure-Hermite center, size, and confidence with
	source "propagated". No blob evidence is consulted; the propagators are
	pure raw_pred builders. The optional blob walker is a separate,
	opt-in consumer reached through the Stage-4 walker seam, not through
	this function.
	"""
	states = []
	for frame_index, raw_cx, raw_cy, w, h, conf in raw:
		states.append({
			"cx": float(raw_cx),
			"cy": float(raw_cy),
			"w": float(w),
			"h": float(h),
			"conf": float(conf),
			"source": "propagated",
		})
	return states


#============================================
def propagate_forward_analytical(
	interval_curves: dict,
	scene_transform: object,
	reader: object = None,
	residual_cache: dict = None,
	precomputed_store: "dict | None" = None,
) -> list:
	"""Propagate forward using the FWD Hermite curve only.

	Builds raw_pred[t] from the forward Hermite curves and returns it as
	a list of tracking state dicts. This is a pure raw_pred builder; no
	blob evidence is consulted. The optional blob walker is a separate,
	opt-in consumer reached through the Stage-4 walker seam.

	The reader, residual_cache, and precomputed_store parameters are
	accepted for call-site compatibility with the walker seam and are
	unused by the pure-Hermite propagator.

	Args:
		interval_curves: Dict from fit_interval_curves().
		scene_transform: SceneTransform instance.
		reader: Unused. Accepted for call-site compatibility.
		residual_cache: Unused. Accepted for call-site compatibility.
		precomputed_store: Unused. Accepted for call-site compatibility.

	Returns:
		List of tracking state dicts, one per frame from start_frame to
		end_frame inclusive. Index 0 is at start_frame.
	"""
	raw = _compute_raw_pred_forward(interval_curves, scene_transform)
	states = _raw_pred_to_states(raw)
	return states


#============================================
def propagate_backward_analytical(
	interval_curves: dict,
	scene_transform: object,
	reader: object = None,
	residual_cache: dict = None,
	precomputed_store: "dict | None" = None,
) -> list:
	"""Propagate backward using the BWD Hermite curve only.

	Builds raw_pred[t] from the backward Hermite curves and returns it as
	a list of tracking state dicts. Pure raw_pred builder; no blob
	evidence is consulted. Same ordering convention as
	propagate_forward_analytical (index 0 at start_frame, chronological).
	BWD-specific behavior (backward Hermite slopes, end-anchored
	confidence decay) lives in the Hermite math, not in array ordering.

	Args:
		interval_curves: Dict from fit_interval_curves().
		scene_transform: SceneTransform instance.
		reader: Unused. Accepted for call-site compatibility.
		residual_cache: Unused. Accepted for call-site compatibility.
		precomputed_store: Unused. Accepted for call-site compatibility.

	Returns:
		List of tracking state dicts, one per frame from start_frame to
		end_frame inclusive. Index 0 is at start_frame (chronological).
	"""
	raw = _compute_raw_pred_backward(interval_curves, scene_transform)
	states = _raw_pred_to_states(raw)
	return states
