"""Pure crop-trajectory math helpers."""

# PIP3 modules
import numpy


# Canonical output-size smoothing strength used by both crop controllers.
CROP_POST_SMOOTH_SIZE_STRENGTH = 0.15

# Canonical crop-policy defaults, applied when a per-video config omits the
# corresponding processing key. Both crop controllers read these, so the
# policy has one definition rather than one copy per controller.
DEFAULT_CROP_ASPECT = "1:1"
DEFAULT_CROP_TORSO_HEIGHT_MULTIPLE = 3.33
DEFAULT_CROP_TORSO_ANCHOR = 0.50
DEFAULT_CROP_CONTAINMENT_RADIUS = 0.20
DEFAULT_CROP_CENTERED_FIT_TO_SOURCE = True


#============================================
def _max_centered_fit_size(
	cx: float,
	cy: float,
	desired_w: float,
	frame_width: int,
	frame_height: int,
	aspect_ratio: float,
) -> tuple:
	"""Return the largest aspect-preserving centered crop that fits."""
	max_w_horiz = 2.0 * min(cx, frame_width - cx)
	max_w_vert = aspect_ratio * 2.0 * min(cy, frame_height - cy)
	fit_w = max(0.0, min(desired_w, max_w_horiz, max_w_vert))
	fit_h = fit_w / aspect_ratio if aspect_ratio > 0 else 0.0
	return (fit_w, fit_h)


#============================================
def _rolling_min_ceiling_per_frame(
	cx_arr: numpy.ndarray,
	cy_arr: numpy.ndarray,
	frame_width: int,
	frame_height: int,
	aspect_ratio: float,
	window: int = 7,
) -> numpy.ndarray:
	"""Return the centered rolling source-fit ceiling for each center."""
	cx = numpy.asarray(cx_arr, dtype=numpy.float64)
	cy = numpy.asarray(cy_arr, dtype=numpy.float64)
	horizontal = 2.0 * numpy.minimum(cx, float(frame_width) - cx)
	vertical = aspect_ratio * 2.0 * numpy.minimum(
		cy,
		float(frame_height) - cy,
	)
	ceiling = numpy.minimum(horizontal, vertical)
	window = max(int(window), 1)
	if window == 1:
		return ceiling.astype(numpy.float64, copy=True)
	half = window // 2
	out = numpy.zeros(len(ceiling), dtype=numpy.float64)
	for index in range(len(ceiling)):
		start = max(0, index - half)
		stop = min(len(ceiling), index + half + 1)
		out[index] = float(numpy.min(ceiling[start:stop]))
	return out


#============================================
def parse_aspect_ratio(aspect_str: str) -> float:
	"""Parse a ``W:H`` crop aspect string."""
	parts = aspect_str.split(":")
	if len(parts) != 2:
		raise RuntimeError(
			f"Invalid aspect ratio format '{aspect_str}', expected 'W:H'"
		)
	try:
		width = float(parts[0])
		height = float(parts[1])
	except ValueError:
		raise RuntimeError(f"Non-numeric aspect ratio '{aspect_str}'")
	if height == 0:
		raise RuntimeError(
			f"Aspect ratio height cannot be zero: '{aspect_str}'"
		)
	return width / height


#============================================
def _forward_backward_ema(signal: numpy.ndarray, alpha: float) -> numpy.ndarray:
	"""Apply a forward then backward exponential moving average."""
	n = len(signal)
	if n < 2:
		return signal.copy()
	forward = numpy.empty(n, dtype=float)
	forward[0] = signal[0]
	for index in range(1, n):
		forward[index] = alpha * signal[index] + (1.0 - alpha) * forward[index - 1]
	final = numpy.empty(n, dtype=float)
	final[-1] = forward[-1]
	for index in range(n - 2, -1, -1):
		final[index] = alpha * forward[index] + (1.0 - alpha) * final[index + 1]
	return final


#============================================
def smooth_crop_trajectory(
	crop_rects: list,
	frame_width: int,
	frame_height: int,
	alpha_size: float = 0.0,
) -> list:
	"""Post-smooth crop size while preserving each crop center."""
	if not crop_rects:
		return crop_rects
	arr = numpy.array(crop_rects, dtype=float)
	cx = arr[:, 0] + arr[:, 2] / 2.0
	cy = arr[:, 1] + arr[:, 3] / 2.0
	width = arr[:, 2].copy()
	height = arr[:, 3].copy()
	if alpha_size > 0:
		width = _forward_backward_ema(width, alpha_size)
		height = _forward_backward_ema(height, alpha_size)
	width = numpy.maximum(width, 10.0)
	height = numpy.maximum(height, 10.0)
	x = numpy.clip(cx - width / 2.0, 0.0, frame_width - width)
	y = numpy.clip(cy - height / 2.0, 0.0, frame_height - height)
	rects = []
	for index in range(len(crop_rects)):
		rect = (
			int(x[index]),
			int(y[index]),
			int(width[index]),
			int(height[index]),
		)
		rects.append(rect)
	return rects


#============================================
def _detect_zoom_phases(
	raw_h: numpy.ndarray,
	window: int = 5,
	threshold_ratio: float = 1.40,
	settle_frames: int = 60,
) -> tuple:
	"""Return active-zoom and post-zoom settling masks."""
	n = len(raw_h)
	transition = numpy.zeros(n, dtype=bool)
	settle = numpy.zeros(n, dtype=bool)
	if n < 2:
		return (transition, settle)
	safe_h = numpy.where(
		(raw_h > 0) & numpy.isfinite(raw_h),
		raw_h,
		1.0,
	)
	half_window = window // 2
	for index in range(n):
		start = max(0, index - half_window)
		stop = min(n, index + half_window + 1)
		values = safe_h[start:stop]
		transition[index] = (
			numpy.max(values) / numpy.min(values) >= threshold_ratio
		)
	in_transition = False
	block_end = -1
	for index in range(n):
		if transition[index]:
			in_transition = True
			block_end = index
		elif in_transition:
			in_transition = False
			start = block_end + 1
			stop = min(n, start + settle_frames)
			settle[start:stop] = True
	settle[transition] = False
	return (transition, settle)
