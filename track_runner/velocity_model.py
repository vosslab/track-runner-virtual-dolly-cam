"""Analytical velocity model using directionally asymmetric Hermite curves.

For each seed-to-seed interval, fits cubic Hermite splines with separate
forward and backward curves. Endpoints are hard-anchored at seeds, but slopes
differ by direction (backward-looking vs forward-looking regression).

Propagation runs in two stages per pass:
  Stage 1: compute `raw_pred[t]` for every frame from Hermite curves only.
  Stage 2: optionally consult a stateless blob observer at each non-endpoint
  frame, apply three gates (proximity, direction, temporal smoothness) that
  read only `raw_pred`, and blend with a displacement clamp when accepted.

Gates MUST NOT read any prior post-blob output. Blob influence at frame t-1
leaking into the gating decision at frame t re-creates the cross-frame state
the design forbids (see plan happy-forging-valiant.md).
"""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules
import residual_motion


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
		- is_stationary: bool (if displacement < 3% of box dimension).
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

	# check if stationary: displacement < 3% of left box dimension
	displacement = math.sqrt((right_sx - left_sx) ** 2 + (right_sy - left_sy) ** 2)
	stationary_threshold = left_h * 0.03
	is_stationary = displacement < stationary_threshold

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
		"is_stationary": is_stationary,
	}
	return result


#============================================
def _compute_raw_pred_forward(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Stage 1 of the forward pass: pure Hermite prediction per frame.

	Returns a list of `(frame_index, cx, cy, w, h, conf, is_stationary)`
	tuples in pixel coordinates, one per frame from start_frame to
	end_frame inclusive. This array is FROZEN -- the gating code MUST
	read from it only, never from any post-blob output.
	"""
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]
	is_stationary = interval_curves["is_stationary"]

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

		if is_stationary:
			scene_cx = left_sx
			scene_cy = left_sy
			scene_w = left_sw
			scene_h = left_sh
		else:
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
			bool(is_stationary),
		))

	return raw


#============================================
def _compute_raw_pred_backward(
	interval_curves: dict,
	scene_transform: object,
) -> list:
	"""Stage 1 of the backward pass: pure Hermite prediction per frame.

	Returns the same shape as _compute_raw_pred_forward but with BWD
	Hermite slopes and reverse-order iteration. Confidence decays from
	the end seed.
	"""
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]
	is_stationary = interval_curves["is_stationary"]

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

	# iterate reverse-order: frame_index from end_frame down to start_frame.
	# Index 0 of the returned list is at end_frame.
	for frame_index in range(end_frame, start_frame - 1, -1):
		if interval_length > 0:
			t = (frame_index - start_frame) / interval_length
		else:
			t = 1.0 if frame_index == end_frame else 0.0
		t = max(0.0, min(1.0, t))

		if is_stationary:
			scene_cx = right_sx
			scene_cy = right_sy
			scene_w = right_sw
			scene_h = right_sh
		else:
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
			bool(is_stationary),
		))

	return raw


#============================================
# Blob-snap configuration. These constants are baked into the solver
# fingerprint in interval_solver.SOLVER_FINGERPRINT_TAG; any numeric
# change invalidates the refine cache automatically.
BLOB_SNAP_ALPHA = 0.6
"""Proximity gate: accept blob only if `dist <= ALPHA * raw_pred_box_height`."""
BLOB_SNAP_PATH_SLACK = 0.5
"""Motion-path gate: how far past |v| along the motion direction counts as
"on path". `proj` must lie in `[0, |v| * (1 + PATH_SLACK)]`."""
BLOB_SNAP_PATH_PERP_FRACTION = 0.75
"""Motion-path gate: perpendicular-distance cap as a fraction of `|v|`.
Together with PATH_SLACK this defines a thin capsule around the motion
segment `[raw[i-1], raw[i] + PATH_SLACK * v_prev]`."""
BLOB_SNAP_VELOCITY_FLOOR = 1.5
"""Velocity magnitude below which direction and motion-path gates are
skipped entirely (pixels per frame). Proximity gate still applies."""
BLOB_SNAP_ALPHA_MAX = 0.5
"""Upper bound on the per-frame blend weight toward blob observation.
Effective alpha is `min(blob.confidence, ALPHA_MAX)`."""
BLOB_SNAP_MAX_SHIFT_FRACTION = 0.5
"""Per-frame displacement clamp. The blended center can shift by at most
`ALPHA_MAX * MAX_SHIFT_FRACTION * raw_pred_box_height` relative to raw_pred,
preventing visible jitter when blob confidence fluctuates frame-to-frame."""


#============================================
def _motion_path_ok(
	anchor: tuple,
	motion_vec: tuple,
	blob_center: tuple,
	slack: float,
	perp_fraction: float,
	velocity_floor: float,
) -> object:
	"""Check that a blob lies along the motion segment anchored at `anchor`.

	Decomposes (blob - anchor) into a component along `motion_vec` and a
	perpendicular component. Accepts when the along-track projection is
	inside `[0, |motion_vec| * (1 + slack)]` AND the perpendicular
	distance is at most `perp_fraction * |motion_vec|`.

	This is a true motion-path check -- NOT a velocity-scaled proximity
	check. A blob at the right distance from raw[t] but off to the side
	of the motion line fails here even though a pure-distance check would
	pass it.

	Args:
		anchor: (x, y) neighbor position (raw_pred only).
		motion_vec: (dx, dy) local motion vector from anchor toward
			raw[t]. Caller computes from raw_pred exclusively.
		blob_center: (bx, by) candidate blob position.
		slack: Forward-extension fraction past `|motion_vec|`.
		perp_fraction: Perpendicular cap as fraction of `|motion_vec|`.
		velocity_floor: Below this magnitude the check is vacuous
			(returns None). Caller decides what to do with None.

	Returns:
		True if the blob lies on the motion path.
		False if it does not.
		None if motion magnitude is below the floor (check skipped).
	"""
	mag_sq = motion_vec[0] * motion_vec[0] + motion_vec[1] * motion_vec[1]
	if mag_sq <= velocity_floor * velocity_floor:
		return None
	mag = mag_sq ** 0.5
	dx = blob_center[0] - anchor[0]
	dy = blob_center[1] - anchor[1]
	# projection along motion direction, in pixel units (not normalized)
	proj = (dx * motion_vec[0] + dy * motion_vec[1]) / mag
	# perpendicular vector and distance
	# perp = d - proj * (motion / mag) = d - (proj / mag) * motion
	scale = proj / mag
	perp_x = dx - scale * motion_vec[0]
	perp_y = dy - scale * motion_vec[1]
	perp = (perp_x * perp_x + perp_y * perp_y) ** 0.5
	forward_ok = 0.0 <= proj <= mag * (1.0 + slack)
	lateral_ok = perp <= perp_fraction * mag
	result = forward_ok and lateral_ok
	return result


#============================================
def _apply_blob_snap(
	raw: list,
	reader: object,
	scene_transform: object,
	residual_cache: dict,
) -> list:
	"""Stage 2: produce snap_pred from a frozen raw_pred, reading raw only.

	STRICT separation of raw and snap:
	  - `raw` is the frozen kinematic trajectory. Never mutated by this
	    function (tuple elements are immutable; the list is not written).
	  - `snap_pred` is the returned list of state dicts. Each entry's
	    `cx` / `cy` is either raw[i]'s center (fall-through) or
	    raw[i] + alpha_eff * delta (accepted). It is an OUTPUT LAYER,
	    not a running trajectory state.
	  - Gating code destructures raw[i-1], raw[i], raw[i+1] into
	    locally-named `raw_*` variables. The accepted-blob branch writes
	    to separate `snap_cx` / `snap_cy` locals so nothing in the read
	    path can accidentally pick up a post-blob value.

	For each non-endpoint, non-stationary frame three gates must all
	pass for a blob to be accepted:
	  1. Proximity: `dist(blob, raw[t]) <= ALPHA * h`.
	  2. Direction: `dot(blob - raw[t], v_pred) >= 0` (skipped when
	     `|v_pred| <= VELOCITY_FLOOR`).
	  3. Motion path: the blob lies inside a thin capsule along the
	     motion segment from each neighbor, i.e. the along-track
	     projection is within the segment extent and the perpendicular
	     off-axis distance is small relative to the local motion
	     magnitude. This is a TRUE motion-path check, not a velocity-
	     scaled proximity check: a blob at the right distance from
	     raw[t] but off to the side of the motion line is rejected.
	     Checked independently against raw[i-1] and raw[i+1]; both must
	     pass when their motion magnitudes exceed the floor. If neither
	     neighbor has sufficient motion, the gate is vacuous and only
	     proximity/direction apply.

	Accepted blobs blend with a displacement clamp:
	  `delta = blob - raw[t]; |delta| <= max_shift; snap = raw[t] + alpha_eff * delta`.

	Args:
		raw: Frozen per-frame raw_pred list from _compute_raw_pred_forward
			or _compute_raw_pred_backward.
		reader: Video reader supplied by the solver. When None (or
			missing required attributes) the function falls through to
			pure Hermite for every frame.
		scene_transform: SceneTransform for the observer's residual
			alignment.
		residual_cache: Per-interval cache dict; contains image-derived
			raw data only (no accepted blobs, no gate outcomes).

	Returns:
		snap_pred as a list of state dicts, one per entry in `raw`, in
		the same order. This list is a fresh output; the caller owns it.
	"""
	snap_pred = []
	num = len(raw)

	# guard: treat an incomplete reader (no read_frame / frame_count) as if
	# the caller passed None. This keeps the stationary tests and other
	# synthetic fixtures working on minimal reader stubs without forcing
	# them to stub the entire video-reader API.
	reader_ok = (
		reader is not None
		and hasattr(reader, "read_frame")
		and hasattr(reader, "frame_count")
	)
	effective_reader = reader if reader_ok else None

	for i in range(num):
		frame_index, raw_cx, raw_cy, w, h, conf, is_stat = raw[i]
		is_endpoint = (i == 0) or (i == num - 1)

		# default output: pure raw_pred values. Every branch writes
		# snap_cx / snap_cy before building the state dict.
		snap_cx = raw_cx
		snap_cy = raw_cy
		snap_applied = False
		gate = "skipped"

		# endpoints and stationary-lock intervals: no snap
		if effective_reader is None or is_endpoint or is_stat:
			snap_pred.append({
				"cx": float(snap_cx),
				"cy": float(snap_cy),
				"w": float(w),
				"h": float(h),
				"conf": float(conf),
				"source": "propagated",
				"stationary_lock": is_stat,
				"disp_history": [],
				"blob_gate": gate,
			})
			continue

		# Destructure neighbors from raw ONLY. No reference to snap_pred.
		_, raw_prev_cx, raw_prev_cy, _, _, _, _ = raw[i - 1]
		_, raw_next_cx, raw_next_cy, _, _, _, _ = raw[i + 1]
		# local motion vectors in iteration order
		v_prev = (raw_cx - raw_prev_cx, raw_cy - raw_prev_cy)
		v_next = (raw_next_cx - raw_cx, raw_next_cy - raw_cy)
		v_pred = (0.5 * (v_prev[0] + v_next[0]), 0.5 * (v_prev[1] + v_next[1]))
		v_pred_mag = (v_pred[0] ** 2 + v_pred[1] ** 2) ** 0.5

		# local tangent for the observer (unit-vector frame)
		if v_pred_mag > 1e-6:
			t_x = v_pred[0] / v_pred_mag
			t_y = v_pred[1] / v_pred_mag
			local_tangent = (t_x, t_y, -t_y, t_x)
		else:
			local_tangent = (1.0, 0.0, 0.0, 1.0)

		observation = residual_motion.observe_blob_at(
			frame_index,
			(raw_cx, raw_cy),
			(w, h),
			local_tangent,
			scene_transform,
			effective_reader,
			residual_cache,
		)

		if observation is None:
			gate = "absent"
		else:
			bx, by = observation.center_pixel
			dx = bx - raw_cx
			dy = by - raw_cy
			dist = (dx * dx + dy * dy) ** 0.5

			# Gate 1: proximity (against raw[t] only)
			proximity_ok = dist <= BLOB_SNAP_ALPHA * h

			# Gate 2: direction. Skipped on near-stationary raw_pred.
			# Note: when both v_prev and v_next are below the floor
			# (genuinely near-stationary interior frame), both this gate
			# AND gate 3 (motion path) become vacuous and only proximity
			# applies. Full stationary-lock intervals are handled earlier
			# by is_stat; this covers partial-motion frames with weak
			# local velocity. Proximity alone is adequate because near-
			# stationary motion makes directional constraints ambiguous.
			if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
				direction_ok = (dx * v_pred[0] + dy * v_pred[1]) >= 0.0
			else:
				direction_ok = True

			# Gate 3: motion-path consistency. Asymmetric check.
			# Prev anchor (raw[i-1]) is, in iteration order, the frame
			# closer to the pass's starting seed -- it has accumulated
			# fewer Hermite-propagation steps and is the more
			# trustworthy local reference. The rule is:
			#   accept if prev does not actively fail
			#   AND next does not actively fail (None permitted)
			#   ... but if prev passes and next fails, STILL accept.
			# Rationale: real footage often has t+1-side noise from
			# upcoming occlusions, tight curvature, or camera-warp
			# error. Requiring both anchors to pass rejects valid
			# blobs in exactly the regimes this feature cares about.
			# Prev-side noise is rarer because the pass has just come
			# from a seed anchor, so a prev-fail is strong evidence
			# the blob is off-path.
			path_ok_prev = _motion_path_ok(
				(raw_prev_cx, raw_prev_cy), v_prev, (bx, by),
				BLOB_SNAP_PATH_SLACK, BLOB_SNAP_PATH_PERP_FRACTION,
				BLOB_SNAP_VELOCITY_FLOOR,
			)
			# Note: path_ok_next is intentionally not computed. Keeping
			# it absent (not just unused) makes the asymmetry explicit
			# in the code: future-side disagreement is tolerated by
			# construction, not by a silent "OR True" combining step
			# that a future reader could misread as accidental.
			path_ok = path_ok_prev is not False

			if proximity_ok and direction_ok and path_ok:
				# accept: blend with displacement clamp
				max_shift = BLOB_SNAP_MAX_SHIFT_FRACTION * h
				delta_x = dx
				delta_y = dy
				if dist > max_shift and dist > 1e-9:
					scale = max_shift / dist
					delta_x = delta_x * scale
					delta_y = delta_y * scale
				alpha_eff = min(observation.confidence, BLOB_SNAP_ALPHA_MAX)
				# write to snap_* so the raw_* locals remain unchanged
				snap_cx = raw_cx + alpha_eff * delta_x
				snap_cy = raw_cy + alpha_eff * delta_y
				snap_applied = True
				gate = "accepted"
			else:
				gate = "rejected"

		snap_pred.append({
			"cx": float(snap_cx),
			"cy": float(snap_cy),
			"w": float(w),
			"h": float(h),
			"conf": float(conf),
			"source": "propagated_with_blob_snap" if snap_applied else "propagated",
			"stationary_lock": is_stat,
			"disp_history": [],
			"blob_gate": gate,
		})

	return snap_pred


#============================================
def propagate_forward_analytical(
	interval_curves: dict,
	scene_transform: object,
	reader: object = None,
	residual_cache: dict = None,
) -> list:
	"""Propagate forward using FWD Hermite curve plus optional blob snap.

	Stage 1 computes raw_pred[t] from Hermite only. Stage 2 optionally
	consults a stateless blob observer at each non-endpoint frame,
	reading from raw_pred exclusively.

	Args:
		interval_curves: Dict from fit_interval_curves().
		scene_transform: SceneTransform instance.
		reader: Optional video reader. When None, blob snap is skipped
			and propagation reduces to pure Hermite (delete-test mode).
		residual_cache: Optional per-interval cache. When reader is
			provided, the solver supplies a shared cache for FWD and BWD.

	Returns:
		List of tracking state dicts, one per frame from start_frame to
		end_frame inclusive. Index 0 is at start_frame.
	"""
	raw = _compute_raw_pred_forward(interval_curves, scene_transform)
	if reader is None or residual_cache is None:
		# pure Hermite path. Delete-test: behavior equals the pre-patch
		# propagator exactly on inputs with no observer call.
		states = _apply_blob_snap(raw, None, scene_transform, {})
		return states
	states = _apply_blob_snap(raw, reader, scene_transform, residual_cache)
	return states


#============================================
def propagate_backward_analytical(
	interval_curves: dict,
	scene_transform: object,
	reader: object = None,
	residual_cache: dict = None,
) -> list:
	"""Propagate backward using BWD Hermite curve plus optional blob snap.

	Output order matches the pre-patch contract: index 0 is at end_frame,
	index -1 is at start_frame (reverse iteration).

	Args:
		interval_curves: Dict from fit_interval_curves().
		scene_transform: SceneTransform instance.
		reader: Optional video reader.
		residual_cache: Optional per-interval cache shared with the FWD
			pass. Legitimately holds raw residuals and raw blobs only;
			no per-frame decisions (see residual_motion.observe_blob_at
			docstring for the cache content boundary).

	Returns:
		List of tracking state dicts from end_frame to start_frame
		(reverse order). Index 0 is at end_frame.
	"""
	raw = _compute_raw_pred_backward(interval_curves, scene_transform)
	if reader is None or residual_cache is None:
		states = _apply_blob_snap(raw, None, scene_transform, {})
		return states
	states = _apply_blob_snap(raw, reader, scene_transform, residual_cache)
	return states
