"""Public crop facade: validation, mode dispatch, and dolly containment."""

import dataclasses
import math

import numpy

import dolly_path
import torso_size_stabilizer
import tr_crop_controller
import tr_crop_direct
import tr_crop_math


CROP_POST_SMOOTH_SIZE_STRENGTH = tr_crop_math.CROP_POST_SMOOTH_SIZE_STRENGTH
CROP_SIZE_STABILIZER_METHOD = "median"
CROP_SIZE_STABILIZER_WINDOW = 7
DOLLY_MAX_CONTAINMENT_ITERATIONS = 10
DOLLY_DEFAULT_SMOOTHNESS = 20.0
DOLLY_PIN_WEIGHT = 1.0e12
DOLLY_FIXED_POINT_TOLERANCE_PX = 1.0e-6
CropController = tr_crop_controller.CropController
create_crop_controller = tr_crop_controller.create_crop_controller
compute_crop_trajectory = tr_crop_controller.compute_crop_trajectory
direct_center_crop_trajectory = tr_crop_direct.direct_center_crop_trajectory
parse_aspect_ratio = tr_crop_math.parse_aspect_ratio
_max_centered_fit_size = tr_crop_math._max_centered_fit_size
_rolling_min_ceiling_per_frame = tr_crop_math._rolling_min_ceiling_per_frame
smooth_crop_trajectory = tr_crop_math.smooth_crop_trajectory
_detect_zoom_phases = tr_crop_math._detect_zoom_phases


#============================================
@dataclasses.dataclass(frozen=True)
class DollyCropReport:
	"""Outcome of the bounded dolly containment fixed point."""
	converged: bool
	iterations: int
	fallback_used: bool

	def as_dict(self) -> dict:
		"""Return a serializable per-clip record."""
		report = {
			"converged": self.converged,
			"iterations": self.iterations,
			"fallback_used": self.fallback_used,
		}
		return report


#============================================
class OffCenterCropError(RuntimeError):
	"""A runner remained outside the configured central crop window."""

	def __init__(self, message: str, first_violating_frame: int, run_length: int, edge: str) -> None:
		super().__init__(message)
		self.first_violating_frame = first_violating_frame
		self.run_length = run_length
		self.edge = edge


#============================================
def _diagnose_offcenter_cause(
	crop_rect: tuple,
	frame_width: int,
	frame_height: int,
	torso_multiple: float,
	aspect_ratio: float,
) -> tuple:
	"""Return the edge causing a containment failure and explanation."""
	x, y, width, height = crop_rect
	if x < 0:
		message = (
			f"crop window extends {-x} px past the left edge of the "
			f"{frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop width of {width} -- the cropped output's left "
			f"side is black-filled"
		)
		return ("left", message)
	if x + width > frame_width:
		overshoot = x + width - frame_width
		message = (
			f"crop window extends {overshoot} px past the right edge of "
			f"the {frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop width of {width} -- the cropped output's right "
			f"side is black-filled"
		)
		return ("right", message)
	if y < 0:
		message = (
			f"crop window extends {-y} px past the top edge of the "
			f"{frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop height of {height} -- the cropped output's top "
			f"is black-filled"
		)
		return ("top", message)
	if y + height > frame_height:
		overshoot = y + height - frame_height
		message = (
			f"crop window extends {overshoot} px past the bottom edge of "
			f"the {frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop height of {height} -- the cropped output's bottom "
			f"is black-filled"
		)
		return ("bottom", message)
	message = (
		"crop window fits in the source frame but the runner could not "
		"be contained at center; check the torso_height_multiple "
		f"(={torso_multiple:g}), crop_aspect (={aspect_ratio:.3f}), and "
		"output_resolution interaction"
	)
	return ("", message)


#============================================
def validate_torso_within_central_window(
	trajectory: list,
	crop_rects: list,
	output_w: int,
	output_h: int,
	frame_width: int,
	frame_height: int,
	torso_multiple: float,
	aspect_ratio: float,
	central_x_fraction: float = 0.5,
	central_y_fraction: float = 0.7,
	max_offcenter_run: int = 3,
) -> None:
	"""Raise when valid torso centers remain outside the safe output center."""
	xlo = output_w * (0.5 - central_x_fraction / 2.0)
	xhi = output_w * (0.5 + central_x_fraction / 2.0)
	ylo = output_h * (0.5 - central_y_fraction / 2.0)
	yhi = output_h * (0.5 + central_y_fraction / 2.0)
	run_length = 0
	run_start = -1
	for index in range(min(len(trajectory), len(crop_rects))):
		state = trajectory[index]
		if state is None or state.get("cx") is None or state.get("cy") is None:
			continue
		cx, cy = state["cx"], state["cy"]
		if not math.isfinite(cx) or not math.isfinite(cy):
			continue
		x, y, width, height = crop_rects[index]
		if width <= 0 or height <= 0:
			continue
		out_x = (cx - x) * output_w / width
		out_y = (cy - y) * output_h / height
		if out_x < xlo or out_x > xhi or out_y < ylo or out_y > yhi:
			if run_length == 0:
				run_start = index
			run_length += 1
			if run_length > max_offcenter_run:
				edge, cause = _diagnose_offcenter_cause(
					crop_rects[index],
					frame_width,
					frame_height,
					torso_multiple,
					aspect_ratio,
				)
				message = (
					"runner torso center is outside the safe central crop window "
					f"for {run_length} consecutive frames starting at frame "
					f"{run_start} (threshold: {max_offcenter_run}). "
					f"runner scene-space center=({cx:.1f}, {cy:.1f}); "
					f"crop_rect=({x}, {y}, {width}, {height}); "
					f"runner output-space=({out_x:.1f}, {out_y:.1f}); "
					f"safe window x=[{xlo:.1f}, {xhi:.1f}] "
					f"({central_x_fraction:.0%}), y=[{ylo:.1f}, {yhi:.1f}] "
					f"({central_y_fraction:.0%}). {cause}. "
					"pass --allow-offcenter-crop to skip this check."
				)
				raise OffCenterCropError(message, run_start, run_length, edge)
		else:
			run_length, run_start = 0, -1


#============================================
def apply_crop(frame: numpy.ndarray, crop_rect: tuple) -> numpy.ndarray:
	"""Apply a crop with black padding for source out-of-bounds areas."""
	frame_height, frame_width = frame.shape[:2]
	x, y, width, height = crop_rect
	sx1, sy1 = max(x, 0), max(y, 0)
	sx2, sy2 = min(x + width, frame_width), min(y + height, frame_height)
	shape = (height, width, frame.shape[2]) if frame.ndim == 3 else (height, width)
	output = numpy.zeros(shape, dtype=frame.dtype)
	if sx2 > sx1 and sy2 > sy1:
		dx, dy = sx1 - x, sy1 - y
		output[dy:dy + sy2 - sy1, dx:dx + sx2 - sx1] = frame[sy1:sy2, sx1:sx2]
	return output


#============================================
def _apply_dolly_containment(
	raw_cx: numpy.ndarray,
	raw_cy: numpy.ndarray,
	center_x: numpy.ndarray,
	center_y: numpy.ndarray,
	crop_h: numpy.ndarray,
	frame_width: int,
	frame_height: int,
	aspect_ratio: float,
	containment_radius: float,
	fit_to_source: bool,
	use_rolling_min_ceiling: bool,
) -> tuple:
	"""Apply center and source-fit constraints to a solved dolly path."""
	cx, cy = center_x.copy(), center_y.copy()
	height = numpy.maximum(crop_h.copy(), 1.0)
	width = numpy.maximum(height * aspect_ratio, 1.0)
	bound = numpy.zeros(len(cx), dtype=bool)
	if containment_radius > 0:
		for index in range(len(cx)):
			dx = (raw_cx[index] - cx[index]) / width[index]
			dy = (raw_cy[index] - cy[index]) / height[index]
			offset = math.hypot(dx, dy)
			if offset > containment_radius:
				pull = 1.0 - containment_radius / offset
				cx[index] += (raw_cx[index] - cx[index]) * pull
				cy[index] += (raw_cy[index] - cy[index]) * pull
				bound[index] = True
	if fit_to_source:
		if use_rolling_min_ceiling:
			ceiling = _rolling_min_ceiling_per_frame(
				cx,
				cy,
				frame_width,
				frame_height,
				aspect_ratio,
				window=7,
			)
			new_width = numpy.maximum(numpy.minimum(width, ceiling), 1.0)
			bound |= new_width < width - DOLLY_FIXED_POINT_TOLERANCE_PX
			width, height = new_width, numpy.maximum(new_width / aspect_ratio, 1.0)
		else:
			for index in range(len(cx)):
				fit_width, fit_height = _max_centered_fit_size(
					cx[index],
					cy[index],
					width[index],
					frame_width,
					frame_height,
					aspect_ratio,
				)
				fit_width, fit_height = max(fit_width, 1.0), max(fit_height, 1.0)
				bound[index] |= fit_width < width[index] - DOLLY_FIXED_POINT_TOLERANCE_PX
				width[index], height[index] = fit_width, fit_height
	width = numpy.minimum(width, float(frame_width))
	height = numpy.minimum(height, float(frame_height))
	return (cx, cy, width, height, bound)


#============================================
def _rasterize_dolly_rects(
	center_x: numpy.ndarray,
	center_y: numpy.ndarray,
	crop_w: numpy.ndarray,
	crop_h: numpy.ndarray,
) -> list:
	"""Round sizes before origins to preserve solved centers."""
	rects = []
	for index in range(len(center_x)):
		width, height = round(crop_w[index]), round(crop_h[index])
		x = round(center_x[index] - width / 2.0)
		y = round(center_y[index] - height / 2.0)
		rects.append((x, y, width, height))
	return rects


#============================================
def dolly_crop_trajectory(
	full_trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
	nif_frames: set | None = None,
	_use_rolling_min_ceiling: bool = True,
) -> tuple:
	"""Solve a whole-path dolly crop trajectory under containment pins."""
	if nif_frames is None:
		nif_frames = set()
	processing = config.get("processing", {})
	aspect = parse_aspect_ratio(
		processing.get("crop_aspect", tr_crop_math.DEFAULT_CROP_ASPECT),
	)
	multiple = float(processing.get(
		"torso_height_multiple", tr_crop_math.DEFAULT_CROP_TORSO_HEIGHT_MULTIPLE,
	))
	anchor = float(processing.get(
		"crop_torso_anchor", tr_crop_math.DEFAULT_CROP_TORSO_ANCHOR,
	))
	radius = float(processing.get(
		"crop_containment_radius", tr_crop_math.DEFAULT_CROP_CONTAINMENT_RADIUS,
	))
	fit = bool(processing.get(
		"crop_centered_fit_to_source",
		tr_crop_math.DEFAULT_CROP_CENTERED_FIT_TO_SOURCE,
	))
	smoothness = float(processing.get("crop_dolly_smoothness", DOLLY_DEFAULT_SMOOTHNESS))
	raw_cx = numpy.asarray([state["cx"] for state in full_trajectory], dtype=float)
	raw_cy = numpy.asarray([state["cy"] for state in full_trajectory], dtype=float)
	raw_w = numpy.asarray([state["w"] for state in full_trajectory], dtype=float)
	raw_h = numpy.asarray([state["h"] for state in full_trajectory], dtype=float)
	desired_h = 0.5 * (raw_h * multiple + raw_w * multiple / aspect)
	targets = []
	for index, state in enumerate(full_trajectory):
		target = {
			"cx": raw_cx[index],
			"cy": raw_cy[index] + (0.50 - anchor) * desired_h[index],
			"log_size": math.log(max(desired_h[index], 1.0)),
			"torso_width": raw_w[index],
		}
		if index in nif_frames or state.get("status") == "not_in_frame":
			target = {"status": "not_in_frame"}
		targets.append(target)
	weights = numpy.asarray([float(state["conf"]) for state in full_trajectory], dtype=float)
	pin_x = numpy.full(len(targets), numpy.nan)
	pin_y = numpy.full(len(targets), numpy.nan)
	pin_log_h = numpy.full(len(targets), numpy.nan)
	for iteration in range(1, DOLLY_MAX_CONTAINMENT_ITERATIONS + 1):
		effective_targets = [dict(target) for target in targets]
		effective_weights = weights.copy()
		for index in range(len(targets)):
			if numpy.isfinite(pin_x[index]):
				effective_targets[index] = {
					"cx": pin_x[index],
					"cy": pin_y[index],
					"log_size": pin_log_h[index],
					"torso_width": raw_w[index],
				}
				effective_weights[index] = DOLLY_PIN_WEIGHT
		path = dolly_path.solve_dolly_path(effective_targets, effective_weights, smoothness)
		path_height = numpy.exp(path.log_size)
		cx, cy, width, height, bound = _apply_dolly_containment(
			raw_cx,
			raw_cy,
			path.center_x,
			path.center_y,
			path_height,
			frame_width,
			frame_height,
			aspect,
			radius,
			fit,
			_use_rolling_min_ceiling,
		)
		new_pin = bound & (
			(numpy.abs(cx - path.center_x) > DOLLY_FIXED_POINT_TOLERANCE_PX)
			| (numpy.abs(cy - path.center_y) > DOLLY_FIXED_POINT_TOLERANCE_PX)
			| (numpy.abs(height - path_height) > DOLLY_FIXED_POINT_TOLERANCE_PX)
		)
		if not numpy.any(new_pin):
			return (_rasterize_dolly_rects(cx, cy, width, height), DollyCropReport(True, iteration, False))
		pin_x[new_pin] = cx[new_pin]
		pin_y[new_pin] = cy[new_pin]
		pin_log_h[new_pin] = numpy.log(height[new_pin])
	return (None, DollyCropReport(False, DOLLY_MAX_CONTAINMENT_ITERATIONS, True))


#============================================
def trajectory_to_crop_rects(
	trajectory: list,
	video_info: dict,
	config: dict,
	nif_frames: set | None = None,
	return_dolly_report: bool = False,
) -> list | tuple:
	"""Fill crop gaps, harden size, and dispatch the selected crop mode."""
	frame_width, frame_height = video_info["width"], video_info["height"]
	total_frames = video_info["frame_count"]
	assert len(trajectory) <= total_frames, (
		f"trajectory longer than total_frames: {len(trajectory)} > {total_frames}"
	)
	if nif_frames is None:
		nif_frames = set()
	full, last_known = [], None
	for index in range(total_frames):
		if index < len(trajectory) and trajectory[index] is not None:
			state = trajectory[index]
			last_known = state
		elif index in nif_frames:
			state = trajectory[index]
		elif last_known is not None:
			state = {
				"cx": last_known["cx"],
				"cy": last_known["cy"],
				"w": last_known["w"],
				"h": last_known["h"],
				"conf": 0.15,
				"source": "hold_last",
			}
		else:
			state = {
				"cx": frame_width / 2.0,
				"cy": frame_height / 2.0,
				"w": frame_width * 0.3,
				"h": frame_height * 0.5,
				"conf": 0.1,
				"source": "fallback",
			}
		full.append(state)
	mode = str(config.get("processing", {}).get("crop_mode", "dolly"))
	report = None
	if mode == "direct_center":
		required_keys = {"cx", "cy", "h"}
		for index, state in enumerate(full):
			if not isinstance(state, dict) or not required_keys.issubset(state):
				missing = (
					required_keys - set(state.keys())
					if isinstance(state, dict)
					else required_keys
				)
				raise RuntimeError(
					f"Trajectory entry {index} missing required keys for "
					f"direct_center mode: {missing}"
				)
	full = torso_size_stabilizer.stabilize_trajectory(
		full,
		method=CROP_SIZE_STABILIZER_METHOD,
		window=CROP_SIZE_STABILIZER_WINDOW,
	)
	if mode == "direct_center":
		rects = direct_center_crop_trajectory(
			full,
			frame_width,
			frame_height,
			config,
			fps=float(video_info.get("fps", 60.0)),
			nif_frames=nif_frames,
			_size_smoothing_strength=CROP_POST_SMOOTH_SIZE_STRENGTH,
		)
	elif mode == "smooth":
		raw_rects = compute_crop_trajectory(
			full,
			frame_width,
			frame_height,
			config,
		)
		rects = smooth_crop_trajectory(
			raw_rects,
			frame_width,
			frame_height,
			alpha_size=CROP_POST_SMOOTH_SIZE_STRENGTH,
		)
	elif mode == "dolly":
		rects, report = dolly_crop_trajectory(
			full,
			frame_width,
			frame_height,
			config,
			nif_frames=nif_frames,
		)
		if rects is None:
			raw_rects = compute_crop_trajectory(
				full,
				frame_width,
				frame_height,
				config,
			)
			rects = smooth_crop_trajectory(
				raw_rects,
				frame_width,
				frame_height,
				alpha_size=CROP_POST_SMOOTH_SIZE_STRENGTH,
			)
	else:
		raise RuntimeError(f"Unknown crop_mode '{mode}', expected 'smooth', 'direct_center', or 'dolly'")
	return (rects, report) if return_dolly_report else rects
