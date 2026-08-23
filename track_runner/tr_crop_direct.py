"""Direct-center offline crop trajectory implementation."""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules
import tr_crop_math


#============================================
def direct_center_crop_trajectory(
	full_trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
	fps: float = 60.0,
	nif_frames: set | None = None,
	_use_rolling_min_ceiling: bool = True,
	_size_smoothing_strength: float | None = None,
) -> list:
	"""Compute centered offline crop rectangles from a dense trajectory.

	Args:
		full_trajectory: Dense, gap-filled tracking states.
		frame_width: Source frame width in pixels.
		frame_height: Source frame height in pixels.
		config: Project configuration including its processing section.
		fps: Video rate used for the zoom settling duration.
		nif_frames: Retained compatibility seam for NIF crop inputs.
		_use_rolling_min_ceiling: Use the production source-fit calculation.
		_size_smoothing_strength: Facade-injected output-size EMA strength.

	Returns:
		One integer ``(x, y, width, height)`` crop rectangle per frame.
	"""
	n = len(full_trajectory)
	if n == 0:
		return []
	if nif_frames is None:
		nif_frames = set()
	if _size_smoothing_strength is None:
		_size_smoothing_strength = tr_crop_math.CROP_POST_SMOOTH_SIZE_STRENGTH

	processing = config.get("processing", {})
	aspect_ratio = tr_crop_math.parse_aspect_ratio(
		processing.get("crop_aspect", tr_crop_math.DEFAULT_CROP_ASPECT),
	)
	torso_multiple = float(processing.get(
		"torso_height_multiple", tr_crop_math.DEFAULT_CROP_TORSO_HEIGHT_MULTIPLE,
	))
	raw_cx = numpy.asarray(
		[state["cx"] for state in full_trajectory],
		dtype=float,
	)
	raw_cy = numpy.asarray(
		[state["cy"] for state in full_trajectory],
		dtype=float,
	)
	raw_w = numpy.asarray(
		[state["w"] for state in full_trajectory],
		dtype=float,
	)
	raw_h = numpy.asarray(
		[state["h"] for state in full_trajectory],
		dtype=float,
	)
	desired_h = 0.5 * (
		raw_h * torso_multiple
		+ raw_w * torso_multiple / aspect_ratio
	)
	center_x = raw_cx.copy()
	center_y = raw_cy.copy()
	alpha_size = _size_smoothing_strength
	if alpha_size > 0:
		size_h = tr_crop_math._forward_backward_ema(desired_h, alpha_size)
	else:
		size_h = desired_h.copy()

	torso_anchor = float(processing.get(
		"crop_torso_anchor", tr_crop_math.DEFAULT_CROP_TORSO_ANCHOR,
	))
	# The centered anchor needs no shift; any other anchor offsets the center.
	if torso_anchor != tr_crop_math.DEFAULT_CROP_TORSO_ANCHOR:
		center_y += (tr_crop_math.DEFAULT_CROP_TORSO_ANCHOR - torso_anchor) * size_h

	max_height_change = float(processing.get("crop_max_height_change", 0.005))
	zoom_stabilization = bool(processing.get("crop_zoom_stabilization", False))
	if alpha_size <= 0 and max_height_change > 0:
		if zoom_stabilization:
			transition, settling = tr_crop_math._detect_zoom_phases(
				raw_h,
				settle_frames=round(3.0 * fps),
			)
		else:
			transition = numpy.zeros(n, dtype=bool)
			settling = numpy.zeros(n, dtype=bool)
		_apply_size_rate_limit(
			size_h,
			max_height_change,
			zoom_stabilization,
			transition,
			settling,
		)

	size_h = numpy.maximum(size_h, 1.0)
	size_w = numpy.maximum(size_h * aspect_ratio, 1.0)
	containment_radius = float(processing.get(
		"crop_containment_radius", tr_crop_math.DEFAULT_CROP_CONTAINMENT_RADIUS,
	))
	if containment_radius > 0:
		for crop_pass in range(2):
			for index in range(n):
				dx = (raw_cx[index] - center_x[index]) / size_w[index]
				dy = (raw_cy[index] - center_y[index]) / size_h[index]
				offset = math.hypot(dx, dy)
				if offset > containment_radius:
					pull = 1.0 - containment_radius / offset
					center_x[index] += (raw_cx[index] - center_x[index]) * pull
					center_y[index] += (raw_cy[index] - center_y[index]) * pull
			if crop_pass == 0:
				center_x = tr_crop_math._forward_backward_ema(center_x, 0.3)
				center_y = tr_crop_math._forward_backward_ema(center_y, 0.3)

	if bool(processing.get(
		"crop_centered_fit_to_source",
		tr_crop_math.DEFAULT_CROP_CENTERED_FIT_TO_SOURCE,
	)):
		if _use_rolling_min_ceiling:
			ceiling = tr_crop_math._rolling_min_ceiling_per_frame(
				center_x,
				center_y,
				frame_width,
				frame_height,
				aspect_ratio,
				window=7,
			)
			size_w = numpy.maximum(numpy.minimum(size_w, ceiling), 1.0)
			size_h = numpy.maximum(size_w / aspect_ratio, 1.0)
			if alpha_size > 0:
				size_h = tr_crop_math._forward_backward_ema(size_h, alpha_size)
				size_w = numpy.maximum(
					numpy.minimum(size_h * aspect_ratio, ceiling),
					1.0,
				)
				size_h = numpy.maximum(size_w / aspect_ratio, 1.0)
		else:
			passes = 2 if alpha_size > 0 else 1
			for crop_pass in range(passes):
				for index in range(n):
					fit_w, fit_h = tr_crop_math._max_centered_fit_size(
						center_x[index],
						center_y[index],
						size_w[index],
						frame_width,
						frame_height,
						aspect_ratio,
					)
					size_w[index] = max(fit_w, 1.0)
					size_h[index] = max(fit_h, 1.0)
				if crop_pass == 0 and alpha_size > 0:
					size_h = tr_crop_math._forward_backward_ema(
						size_h,
						alpha_size,
					)
					size_w = size_h * aspect_ratio

	x = center_x - size_w / 2.0
	y = center_y - size_h / 2.0
	size_w = numpy.minimum(size_w, float(frame_width))
	size_h = numpy.minimum(size_h, float(frame_height))
	rects = []
	for index in range(n):
		rect = (
			round(x[index]),
			round(y[index]),
			round(size_w[index]),
			round(size_h[index]),
		)
		rects.append(rect)
	return rects


#============================================
def _apply_size_rate_limit(
	size_h: numpy.ndarray,
	max_height_change: float,
	zoom_stabilization: bool,
	transition: numpy.ndarray,
	settling: numpy.ndarray,
) -> None:
	"""Rate-limit crop height, optionally suppressing noisy reversals."""
	mono_direction = 0
	reversal_count = 0
	for index in range(1, len(size_h)):
		delta = size_h[index] - size_h[index - 1]
		if zoom_stabilization:
			if transition[index]:
				rate_multiplier = 0.02
				sustain_required = 3
			elif settling[index]:
				rate_multiplier = 0.20
				sustain_required = 3
			else:
				rate_multiplier = 1.0
				sustain_required = 5
			if mono_direction == 0:
				if delta > 0:
					mono_direction = 1
				elif delta < 0:
					mono_direction = -1
			else:
				is_reversal = (
					(mono_direction == 1 and delta < 0)
					or (mono_direction == -1 and delta > 0)
				)
				if is_reversal:
					reversal_threshold = 0.003 * size_h[index - 1]
					if abs(delta) < reversal_threshold:
						size_h[index] = size_h[index - 1]
						continue
					reversal_count += 1
					if reversal_count < sustain_required:
						size_h[index] = size_h[index - 1]
						continue
					mono_direction = -mono_direction
					reversal_count = 0
				else:
					reversal_count = 0
		else:
			rate_multiplier = 1.0
		limit = max_height_change * rate_multiplier * size_h[index - 1]
		if abs(delta) > limit:
			size_h[index] = size_h[index - 1] + math.copysign(limit, delta)
