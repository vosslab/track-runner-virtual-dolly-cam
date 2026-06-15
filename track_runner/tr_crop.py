"""Adaptive crop trajectory module for track_runner.

Computes smoothed crop rectangles that follow a tracked subject,
using exponential smoothing with deadband and velocity capping.
Pure numpy, no other dependencies.
"""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules
import torso_size_stabilizer


# Crop-trajectory post-smoothing constant (both crop paths).
# This describes OUTPUT crop feel (how the virtual camera moves), not
# tracker physics, so it lives as a fixed module constant rather than a
# per-video config knob. Both the direct_center path and the smooth
# path read this constant directly; no config key overrides it.
#
# CROP_POST_SMOOTH_SIZE_STRENGTH: forward-backward EMA alpha on the
#   crop HEIGHT. 0.15 is a ~6-7 frame time constant that absorbs the
#   typical +/-5% per-frame torso-bbox jitter so the zoom does not
#   visibly breathe in the encoded crop.
#
# The crop CENTER (position) is not post-smoothed and the crop center is
# not velocity-capped. Both legs were permanently disabled no-ops
# (position alpha 0.0, velocity cap 0.0) and have been removed. Center
# motion is already bounded by the containment clamp, and the
# fit-to-source path keeps the runner centered at all costs, which
# conflicts with a velocity cap.
CROP_POST_SMOOTH_SIZE_STRENGTH = 0.15

# Robust size-spike hardening for torso size (w/h), applied to the loaded
# trajectory BEFORE the size-EMA. The EMA is a low-pass filter, not an
# outlier rejector: on every frame it injects alpha*(desired - smoothed),
# so a single-frame torso w/h spike always moves the crop height by ~alpha
# of the spike. This stage rejects isolated single-frame torso w/h outliers
# (a window-local median replaces a spike with its local median), and
# preserves real multi-frame scale ramps (which agree with their
# neighborhood and pass through untouched). It does NOT remove broadband
# sub-pixel breathing. This separates size TRACKING (robust,
# trend-following) from size SMOOTHING (cosmetic EMA), the C5 separability
# the contract asks for. Position (cx, cy) is never touched by this stage.
#
# Honest framing: this is size-spike hardening, not a zoom-bounce fix. The
# visible residual breathing on current clips is BROADBAND ~1px wiggle near
# the integer-rounding floor, not isolated spikes, and this stage does not
# measurably reduce it. See the measured null-effect numbers in
# docs/active_plans/decisions/size_spike_hardening_evidence.md. Reducing the
# broadband breathing is a SEPARATE future task (longer window / stronger
# EMA / crop-height deadband), not done here.
#
# CROP_SIZE_STABILIZER_METHOD: one of the torso_size_stabilizer methods
#   ("none", "median", "hampel", "mad_gated"). "median" is chosen because a
#   window-local median rejects true single-frame spikes when they occur
#   while tracking a monotone ramp; a MAD-gated Hampel filter fires on under
#   2% of frames on real production trajectories and does not lower the
#   per-frame step (the residual jitter there is broadband, not spikes).
# CROP_SIZE_STABILIZER_WINDOW: centered window length. 7 frames matches the
#   ~6-7 frame time constant of the size-EMA: long enough for a stable
#   local median, short enough to preserve a genuine fast zoom.
#
# This is an OUTPUT crop-feel constant (it edits only the in-memory crop
# trajectory, never the stored torso boxes / npz), so it lives as a fixed
# module constant, not a per-video config knob.
CROP_SIZE_STABILIZER_METHOD = "median"
CROP_SIZE_STABILIZER_WINDOW = 7


#============================================
class OffCenterCropError(RuntimeError):
	"""Raised when the runner exits the safe central crop window for
	longer than the configured tolerance.

	Carries structured fields about the violation so programmatic
	consumers (and tests) can inspect the failure without parsing the
	error message:

		first_violating_frame: int  -- index of the first frame in run
		run_length:           int  -- length of the offending run so far
		edge:                 str  -- "left", "right", "top", "bottom",
		                              or "" when crop fits in source

	Distinct from generic RuntimeError so callers can catch it and
	surface a targeted message, while still propagating up via the
	RuntimeError base class for any existing top-level handler.
	"""

	def __init__(
		self,
		message: str,
		first_violating_frame: int,
		run_length: int,
		edge: str,
	) -> None:
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
	"""Return (edge_tag, explanation) describing why the crop is off-center.

	Pure formatter; never raises. The edge_tag is one of "left",
	"right", "top", "bottom", or "" when the crop fits in the source
	frame and the cause is generic.

	Args:
		crop_rect: (x, y, w, h) of the offending frame's crop rectangle.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		torso_multiple: Configured torso_height_multiple.
		aspect_ratio: Configured crop aspect ratio (width / height).

	Returns:
		Tuple of (edge_tag, paragraph_string).
	"""
	cx, cy, cw, ch = crop_rect
	# priority order: edges first, fall back to a generic hint
	if cx < 0:
		message = (
			f"crop window extends {-cx} px past the left edge of the "
			f"{frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop width of {cw} -- the cropped output's left "
			f"side is black-filled"
		)
		return ("left", message)
	if cx + cw > frame_width:
		overshoot = (cx + cw) - frame_width
		message = (
			f"crop window extends {overshoot} px past the right edge of "
			f"the {frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop width of {cw} -- the cropped output's right "
			f"side is black-filled"
		)
		return ("right", message)
	if cy < 0:
		message = (
			f"crop window extends {-cy} px past the top edge of the "
			f"{frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop height of {ch} -- the cropped output's top "
			f"is black-filled"
		)
		return ("top", message)
	if cy + ch > frame_height:
		overshoot = (cy + ch) - frame_height
		message = (
			f"crop window extends {overshoot} px past the bottom edge of "
			f"the {frame_width}x{frame_height} source frame; "
			f"torso_multiple={torso_multiple:g} and aspect={aspect_ratio:.3f} "
			f"produced a crop height of {ch} -- the cropped output's bottom "
			f"is black-filled"
		)
		return ("bottom", message)
	# crop fits in the source frame but the runner is still off-center
	generic = (
		f"crop window fits in the source frame but the runner could not "
		f"be contained at center; check the torso_height_multiple "
		f"(={torso_multiple:g}), crop_aspect (={aspect_ratio:.3f}), and "
		f"output_resolution interaction"
	)
	return ("", generic)


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
	"""Raise OffCenterCropError when the runner stays outside the safe
	central crop window for too many consecutive frames.

	Black-fill from a slightly off-source crop is acceptable; a runner
	pinned to one side of the frame for a sustained run of frames is
	almost always a configuration bug. This pre-encode validator
	enforces that contract.

	Frames with no valid torso center (None entries, missing or
	non-finite cx/cy) are skipped: they neither extend nor break a run.
	Skipping (rather than resetting) prevents a brief tracker dropout
	from hiding a real sustained off-center streak.

	Args:
		trajectory: Per-frame state dicts (may contain None entries).
		crop_rects: (x, y, w, h) crop rectangles, one per frame.
		output_w: Encoded output width in pixels.
		output_h: Encoded output height in pixels.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		torso_multiple: Configured torso_height_multiple (for diagnostic).
		aspect_ratio: Configured crop aspect ratio (for diagnostic).
		central_x_fraction: Width of the safe central horizontal window,
			as a fraction of output_w. Default 0.5 (central 50%).
		central_y_fraction: Height of the safe central vertical window,
			as a fraction of output_h. Default 0.7 (central 70%).
		max_offcenter_run: Maximum tolerated run length of consecutive
			off-center valid frames before the gate fires. Default 3.

	Raises:
		OffCenterCropError: when a run of off-center valid frames longer
			than max_offcenter_run is detected. The error message names
			the first frame of the run, the run length, the runner's
			scene-space and output-space positions, the safe-window
			bounds, and the most likely root cause.
	"""
	# safe-window bounds (inclusive)
	half_x = central_x_fraction / 2.0
	half_y = central_y_fraction / 2.0
	xlo = output_w * (0.5 - half_x)
	xhi = output_w * (0.5 + half_x)
	ylo = output_h * (0.5 - half_y)
	yhi = output_h * (0.5 + half_y)

	run_length = 0
	run_start_frame = -1
	n_frames = min(len(trajectory), len(crop_rects))
	for i in range(n_frames):
		state = trajectory[i]
		# skipped frames pause the run; do not extend, do not reset
		if state is None:
			continue
		cx = state.get("cx")
		cy = state.get("cy")
		if cx is None or cy is None:
			continue
		if not (math.isfinite(cx) and math.isfinite(cy)):
			continue
		# map scene-space center into output-space pixels
		crop_rect = crop_rects[i]
		crop_x, crop_y, crop_w, crop_h = crop_rect
		# guard against degenerate zero-sized crops (should not happen,
		# but a /0 here would mask the real diagnostic)
		if crop_w <= 0 or crop_h <= 0:
			continue
		out_x = (cx - crop_x) * (output_w / crop_w)
		out_y = (cy - crop_y) * (output_h / crop_h)
		# violation iff outside the inclusive central window
		violates = (out_x < xlo or out_x > xhi or out_y < ylo or out_y > yhi)
		if violates:
			if run_length == 0:
				run_start_frame = i
			run_length += 1
			if run_length > max_offcenter_run:
				edge, cause = _diagnose_offcenter_cause(
					crop_rect, frame_width, frame_height,
					torso_multiple, aspect_ratio,
				)
				msg = (
					f"runner torso center is outside the safe central "
					f"crop window for {run_length} consecutive frames "
					f"starting at frame {run_start_frame} "
					f"(threshold: {max_offcenter_run}). "
					f"runner scene-space center=({cx:.1f}, {cy:.1f}); "
					f"crop_rect=({crop_x}, {crop_y}, {crop_w}, {crop_h}); "
					f"runner output-space=({out_x:.1f}, {out_y:.1f}); "
					f"safe window x=[{xlo:.1f}, {xhi:.1f}] "
					f"({central_x_fraction:.0%}), "
					f"y=[{ylo:.1f}, {yhi:.1f}] "
					f"({central_y_fraction:.0%}). "
					f"{cause}. "
					f"pass --allow-offcenter-crop to skip this check."
				)
				raise OffCenterCropError(
					msg,
					first_violating_frame=run_start_frame,
					run_length=run_length,
					edge=edge,
				)
		else:
			run_length = 0
			run_start_frame = -1


#============================================
def _max_centered_fit_size(
	cx: float,
	cy: float,
	desired_w: float,
	frame_width: int,
	frame_height: int,
	aspect_ratio: float,
) -> tuple:
	"""Return the largest aspect-preserving (w, h) centered on (cx, cy)
	that fits inside [0, frame_width] x [0, frame_height].

	Used by direct_center mode when `crop_centered_fit_to_source` is on:
	if the user-requested crop window does not fit centered on the
	runner, shrink it (preserving aspect) until at least one edge of
	the crop touches a source-frame edge, instead of letting the crop
	slide off and produce black bars.

	Args:
		cx: Crop center x (full-frame coordinates).
		cy: Crop center y (full-frame coordinates).
		desired_w: Requested crop width (the user's torso_multiple x
			aspect target). Acts as an upper bound on the result.
		frame_width: Source frame width in pixels.
		frame_height: Source frame height in pixels.
		aspect_ratio: Target crop aspect (width / height).

	Returns:
		(fit_w, fit_h) tuple of floats. fit_w / fit_h == aspect_ratio
		within float epsilon. Both values are non-negative; if the
		runner sits at a frame edge, the helper can return zeros and
		the caller is expected to apply the min_crop_size floor.
	"""
	# horizontal bound from cx
	max_w_horiz = 2.0 * min(cx, frame_width - cx)
	# vertical bound, expressed as a width via aspect
	max_w_vert = aspect_ratio * 2.0 * min(cy, frame_height - cy)
	fit_w = min(desired_w, max_w_horiz, max_w_vert)
	fit_w = max(0.0, fit_w)
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
	"""Centered rolling minimum of the per-frame geometric source-fit ceiling.

	The geometric ceiling at frame i is the largest crop width that
	fits centered on (cx[i], cy[i]) inside `[0, frame_width] x [0,
	frame_height]` at the given aspect ratio:

	    ceiling_w[i] = min(2 * min(cx, frame_w - cx),
	                       aspect_ratio * 2 * min(cy, frame_h - cy))

	Smoothing this ceiling with a centered rolling minimum (window 7
	by default) stabilizes the per-frame fit during sustained
	near-edge runs, where the raw ceiling oscillates as the runner
	moves toward and away from a frame edge. The smoothed ceiling is
	bounded above by the per-frame ceiling at every frame, so a crop
	sized to the smoothed ceiling at frame i fits inside the source
	frame at frame i (no boundary violation by construction).

	Used by `direct_center_crop_trajectory` in the fit-to-source
	block. See [V1_RESULTS.md](../output_smoke/zoom_bounce/EVIDENCE/v1_ceiling_aware_s4/V1_RESULTS.md)
	for the gate evidence (-91.6% S5 hotspot severity on Glenbrook,
	-78.7% on IMG_3823, no measurable effect on a low-edge clip).

	Args:
		cx_arr: Per-frame crop center x coordinates.
		cy_arr: Per-frame crop center y coordinates.
		frame_width: Source frame width in pixels.
		frame_height: Source frame height in pixels.
		aspect_ratio: Target crop aspect (width / height).
		window: Centered window length for the rolling min. Default 7.

	Returns:
		1D float64 numpy array of length `len(cx_arr)`. Each element
		is the local-minimum geometric ceiling width over a centered
		window. Edge handling truncates the window at array
		boundaries; no NaN is introduced.
	"""
	cx = numpy.asarray(cx_arr, dtype=numpy.float64)
	cy = numpy.asarray(cy_arr, dtype=numpy.float64)
	max_w_horiz = 2.0 * numpy.minimum(cx, float(frame_width) - cx)
	max_w_vert = aspect_ratio * 2.0 * numpy.minimum(
		cy, float(frame_height) - cy,
	)
	geometric_ceiling_w = numpy.minimum(max_w_horiz, max_w_vert)
	# rolling minimum over a centered window
	n = len(geometric_ceiling_w)
	window = max(int(window), 1)
	if window == 1:
		return geometric_ceiling_w.astype(numpy.float64, copy=True)
	half = window // 2
	out = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		out[i] = float(numpy.min(geometric_ceiling_w[start:end]))
	return out


#============================================
def parse_aspect_ratio(aspect_str: str) -> float:
	"""Parse an aspect ratio string into a float.

	Args:
		aspect_str: Ratio string like '16:9', '4:3', or '1:1'.

	Returns:
		Float value of width divided by height.

	Raises:
		RuntimeError: If the string is not in 'W:H' format or
			contains non-numeric parts.
	"""
	parts = aspect_str.split(":")
	if len(parts) != 2:
		raise RuntimeError(
			f"Invalid aspect ratio format '{aspect_str}', expected 'W:H'"
		)
	try:
		w = float(parts[0])
		h = float(parts[1])
	except ValueError:
		raise RuntimeError(
			f"Non-numeric aspect ratio '{aspect_str}'"
		)
	if h == 0:
		raise RuntimeError(
			f"Aspect ratio height cannot be zero: '{aspect_str}'"
		)
	ratio = w / h
	return ratio


#============================================
class CropController:
	"""Adaptive crop controller that smoothly follows a tracked target.

	Uses exponential smoothing with deadband, confidence modulation,
	and velocity capping to produce stable crop trajectories.
	"""

	def __init__(
		self,
		frame_width: int,
		frame_height: int,
		aspect_ratio: float = 1.0,
		target_fill_ratio: float = 0.30,
		smoothing_attack: float = 0.15,
		smoothing_release: float = 0.05,
		max_crop_velocity: float = 30.0,
		deadband_fraction: float = 0.02,
		velocity_scale: float = 2.0,
		displacement_alpha: float = 0.1,
	):
		"""Initialize the crop controller.

		Args:
			frame_width: Width of the source video frame in pixels.
			frame_height: Height of the source video frame in pixels.
			aspect_ratio: Output crop width/height ratio (1.0 for square).
			target_fill_ratio: Fraction of crop height the target should fill.
			smoothing_attack: Alpha for large corrections (fast response).
			smoothing_release: Alpha for small corrections (slow drift).
			max_crop_velocity: Maximum pixels the crop center can move per frame.
			deadband_fraction: Fraction of crop size below which errors are ignored.
			velocity_scale: Multiplier for adaptive velocity cap in smooth mode.
			displacement_alpha: EMA smoothing factor for subject displacement.
		"""
		self.frame_width = frame_width
		self.frame_height = frame_height
		self.aspect_ratio = aspect_ratio
		self.target_fill_ratio = target_fill_ratio
		self.smoothing_attack = smoothing_attack
		self.smoothing_release = smoothing_release
		self.max_crop_velocity = max_crop_velocity
		self.deadband_fraction = deadband_fraction
		self.velocity_scale = velocity_scale
		self.displacement_alpha = displacement_alpha
		# Smoothed crop state, initialized on first update
		self.smooth_cx = None
		self.smooth_cy = None
		self.smooth_size = None
		# Adaptive velocity state
		self._ema_displacement = 0.0
		self._prev_desired_cx = None
		self._prev_desired_cy = None

	#============================================
	def update(
		self,
		state: dict,
	) -> tuple:
		"""Update crop position given a tracking state dict.

		Args:
			state: Tracking state with keys:
				'cx' (float): center x of tracked subject in pixels,
				'cy' (float): center y of tracked subject in pixels,
				'w' (float): bounding box width in pixels,
				'h' (float): bounding box height in pixels,
				'conf' (float): tracking confidence 0.0 to 1.0,
				'source' (str): source label for the tracking state.

		Returns:
			Tuple of (crop_x, crop_y, crop_w, crop_h) as integer pixel
			coordinates where crop_x, crop_y is the top-left corner.
		"""
		fw = self.frame_width
		fh = self.frame_height
		tcx = state["cx"]
		tcy = state["cy"]
		# bounding box width is not used; only height drives fill ratio
		th = state["h"]
		confidence = state["conf"]

		# Step 1: use the configured fill ratio directly
		fill = self.target_fill_ratio
		desired_crop_h = th / fill
		# clamp crop height to frame height (no min floor: torso_height_multiple
		# is the only zoom knob; a 1-pixel sanity floor protects against zero)
		desired_crop_h = max(1.0, min(desired_crop_h, fh))
		# compute crop width from aspect ratio
		desired_crop_w = desired_crop_h * self.aspect_ratio
		# clamp crop width to frame width
		if desired_crop_w > fw:
			desired_crop_w = float(fw)
			# adjust height to maintain aspect ratio
			desired_crop_h = desired_crop_w / self.aspect_ratio

		# Step 2: desired center is the target center
		desired_cx = tcx
		desired_cy = tcy

		# Step 2.5: compute adaptive velocity cap based on subject displacement
		if self._prev_desired_cx is not None:
			# compute frame-to-frame displacement of the subject
			disp_x = desired_cx - self._prev_desired_cx
			disp_y = desired_cy - self._prev_desired_cy
			displacement = math.sqrt(disp_x * disp_x + disp_y * disp_y)
			# update EMA of displacement
			self._ema_displacement = (
				self.displacement_alpha * displacement +
				(1.0 - self.displacement_alpha) * self._ema_displacement
			)
			# compute adaptive cap: at least max_crop_velocity, scales with EMA
			adaptive_cap = max(
				self.max_crop_velocity,
				self.velocity_scale * self._ema_displacement
			)
		else:
			adaptive_cap = self.max_crop_velocity

		# track desired center for next frame
		self._prev_desired_cx = desired_cx
		self._prev_desired_cy = desired_cy

		# Step 3: first frame snaps directly to desired values
		if self.smooth_cx is None:
			self.smooth_cx = desired_cx
			self.smooth_cy = desired_cy
			self.smooth_size = desired_crop_h
		else:
			# Step 4: exponential smoothing with deadband
			# threshold for choosing attack vs release alpha
			attack_threshold = self.deadband_fraction * self.smooth_size * 4.0
			deadband = self.deadband_fraction * self.smooth_size

			# save old values for velocity capping
			old_cx = self.smooth_cx
			old_cy = self.smooth_cy

			# smooth center x
			error_cx = desired_cx - self.smooth_cx
			if abs(error_cx) >= deadband:
				alpha = self.smoothing_attack if abs(error_cx) > attack_threshold else self.smoothing_release
				alpha *= confidence
				# floor prevents crop from freezing at very low confidence
				alpha = max(alpha, 0.02)
				self.smooth_cx += alpha * error_cx

			# smooth center y
			error_cy = desired_cy - self.smooth_cy
			if abs(error_cy) >= deadband:
				alpha = self.smoothing_attack if abs(error_cy) > attack_threshold else self.smoothing_release
				alpha *= confidence
				alpha = max(alpha, 0.02)
				self.smooth_cy += alpha * error_cy

			# smooth crop size
			error_size = desired_crop_h - self.smooth_size
			if abs(error_size) >= deadband:
				alpha = self.smoothing_attack if abs(error_size) > attack_threshold else self.smoothing_release
				alpha *= confidence
				alpha = max(alpha, 0.02)
				self.smooth_size += alpha * error_size

			# Step 5: cap per-frame velocity on center using adaptive cap
			delta_cx = self.smooth_cx - old_cx
			if abs(delta_cx) > adaptive_cap:
				# clamp to adaptive cap, preserving sign
				self.smooth_cx = old_cx + math.copysign(adaptive_cap, delta_cx)

			delta_cy = self.smooth_cy - old_cy
			if abs(delta_cy) > adaptive_cap:
				self.smooth_cy = old_cy + math.copysign(adaptive_cap, delta_cy)

		# Step 6: compute integer crop rectangle
		crop_h = self.smooth_size
		crop_w = crop_h * self.aspect_ratio
		crop_x = self.smooth_cx - crop_w / 2.0
		crop_y = self.smooth_cy - crop_h / 2.0

		# clamp to frame bounds
		crop_x = max(0.0, min(crop_x, fw - crop_w))
		crop_y = max(0.0, min(crop_y, fh - crop_h))

		result = (int(crop_x), int(crop_y), int(crop_w), int(crop_h))
		return result

	#============================================
	def reset(self) -> None:
		"""Reset smoother state."""
		self.smooth_cx = None
		self.smooth_cy = None
		self.smooth_size = None
		self._ema_displacement = 0.0
		self._prev_desired_cx = None
		self._prev_desired_cy = None

	#============================================
	def get_state(self) -> dict | None:
		"""Return current smoothed state or None if uninitialized."""
		if self.smooth_cx is None:
			return None
		state = {
			"cx": self.smooth_cx,
			"cy": self.smooth_cy,
			"size": self.smooth_size,
		}
		return state


#============================================
def apply_crop(frame: numpy.ndarray, crop_rect: tuple) -> numpy.ndarray:
	"""Apply a crop rectangle to a frame array.

	If the crop extends beyond frame bounds, the out-of-bounds area
	is filled with black (zero padding).

	Args:
		frame: Source frame as a numpy array (H, W, C) or (H, W).
		crop_rect: (x, y, w, h) integer pixel coordinates for the crop.

	Returns:
		Cropped numpy array with the requested dimensions.
	"""
	fh, fw = frame.shape[:2]
	cx, cy, cw, ch = crop_rect

	# determine if the crop is fully inside the frame
	src_x1 = max(cx, 0)
	src_y1 = max(cy, 0)
	src_x2 = min(cx + cw, fw)
	src_y2 = min(cy + ch, fh)

	# offsets into the output where the valid pixels go
	dst_x1 = src_x1 - cx
	dst_y1 = src_y1 - cy
	dst_x2 = dst_x1 + (src_x2 - src_x1)
	dst_y2 = dst_y1 + (src_y2 - src_y1)

	# allocate output filled with black
	if frame.ndim == 3:
		out_shape = (ch, cw, frame.shape[2])
	else:
		out_shape = (ch, cw)
	output = numpy.zeros(out_shape, dtype=frame.dtype)

	# copy the valid region
	if src_x2 > src_x1 and src_y2 > src_y1:
		output[dst_y1:dst_y2, dst_x1:dst_x2] = frame[src_y1:src_y2, src_x1:src_x2]

	return output


#============================================
def create_crop_controller(
	config: dict,
	frame_width: int,
	frame_height: int,
) -> CropController:
	"""Factory to create a CropController from a config dictionary.

	Reads crop settings from config['processing'] and returns
	a configured CropController instance.

	Args:
		config: Project configuration dictionary with a 'processing' section.
		frame_width: Width of the source video frame.
		frame_height: Height of the source video frame.

	Returns:
		Configured CropController instance.

	Raises:
		KeyError: If config is missing 'processing' or required keys.
	"""
	# require the processing section and crop_aspect key
	processing = config["processing"]
	aspect_str = processing["crop_aspect"]
	aspect_ratio = parse_aspect_ratio(aspect_str)

	# torso_height_multiple is the user-facing crop-zoom knob
	# (crop_height = multiple * tracked torso height); the internal
	# formula uses its reciprocal, fill_ratio = torso_h / crop_h
	target_fill_ratio = 1.0 / float(processing["torso_height_multiple"])
	# tuning parameters with sensible defaults
	smoothing_attack = float(processing.get("crop_smoothing_attack", 0.15))
	smoothing_release = float(processing.get("crop_smoothing_release", 0.05))
	max_crop_velocity = float(processing.get("crop_max_velocity", 30.0))
	velocity_scale = float(processing.get("crop_velocity_scale", 2.0))
	displacement_alpha = float(processing.get("crop_displacement_alpha", 0.1))

	controller = CropController(
		frame_width=frame_width,
		frame_height=frame_height,
		aspect_ratio=aspect_ratio,
		target_fill_ratio=target_fill_ratio,
		smoothing_attack=smoothing_attack,
		smoothing_release=smoothing_release,
		max_crop_velocity=max_crop_velocity,
		velocity_scale=velocity_scale,
		displacement_alpha=displacement_alpha,
	)
	return controller


#============================================
def direct_center_crop_trajectory(
	full_trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
	fps: float = 60.0,
	nif_frames: set = None,
	_use_rolling_min_ceiling: bool = True,
) -> list:
	"""Compute crop rectangles by centering directly on the solved trajectory.

	Pure offline signal processing: no deadband, no attack/release alpha,
	no online state. Centers the crop on the dense solved trajectory center,
	smooths with forward-backward EMA, applies zoom stability and center
	containment constraints, and optional velocity capping.

	Args:
		full_trajectory: Dense list of tracking state dicts (gap-filled,
			one per frame). Required keys: 'cx', 'cy', 'h'.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		config: Project configuration dict with 'processing' section.
		fps: Video frame rate in frames per second.
		nif_frames: Optional set of frame indices carrying NIF-edge-anchored
			geometry (pre-filled by the encoder). These frames are treated as
			authoritative and their raw cx/cy values are consumed as-is. Defaults
			to empty set if None.
		_use_rolling_min_ceiling: Diagnostic harness hook only. Default True
			(production V1 behavior: rolling-min-stabilized fit-to-source
			ceiling at S3 and S5). False reverts to the legacy per-frame
			geometric clip via `_max_centered_fit_size`. Used by
			tools/analyze_torso_box_noise.py to render side-by-side review
			clips. Not reachable from any user-facing CLI or config; do not
			pass False from production code.

	Returns:
		List of (x, y, w, h) integer crop rectangles, one per frame.
	"""
	n = len(full_trajectory)
	if n == 0:
		return []

	# Default empty set if not provided (legacy callers)
	if nif_frames is None:
		nif_frames = set()

	processing = config.get("processing", {})
	# parse aspect ratio
	aspect_str = processing.get("crop_aspect", "1:1")
	aspect_ratio = parse_aspect_ratio(aspect_str)
	# torso_height_multiple: zoom knob. See Step 1 below for the
	# W+H averaging contract that turns this into desired_crop_h.
	torso_multiple = float(processing.get("torso_height_multiple", 3.33))
	# Size-smoothing alpha. Size smoothing avoids the zoom-bouncing failure
	# mode where per-frame torso-bbox jitter (typically +/-5%) translates
	# directly into visible breathing in the encoded crop. This is an
	# OUTPUT crop-feel constant (see module top), not a per-video config
	# knob. The crop center is intentionally not post-smoothed (it stays
	# glued to the runner; the containment clamp bounds center motion).
	alpha_size = CROP_POST_SMOOTH_SIZE_STRENGTH

	# Step 1: extract raw signals from trajectory
	raw_cx = numpy.empty(n, dtype=float)
	raw_cy = numpy.empty(n, dtype=float)
	raw_w = numpy.empty(n, dtype=float)
	raw_h = numpy.empty(n, dtype=float)
	for i in range(n):
		state = full_trajectory[i]
		raw_cx[i] = state["cx"]
		raw_cy[i] = state["cy"]
		raw_w[i] = state["w"]
		raw_h[i] = state["h"]

	# Compute desired crop height from BOTH torso dimensions and average.
	# Two estimates of how tall the crop should be:
	#   (a) height-driven: crop_h = torso_multiple * raw_h
	#   (b) width-driven:  crop_w = torso_multiple * raw_w
	#                      crop_h = crop_w / aspect_ratio
	# Both use the same torso_height_multiple knob, so for a runner
	# whose torso has the canonical W:H aspect they agree exactly.
	# Averaging makes the zoom robust to either dimension being noisy
	# on a given frame (e.g. arms swung wide inflates raw_w).
	desired_h_from_h = raw_h * torso_multiple
	desired_h_from_w = (raw_w * torso_multiple) / aspect_ratio
	desired_crop_h = 0.5 * (desired_h_from_h + desired_h_from_w)

	# Step 2: crop center stays glued to the runner (no position EMA);
	# apply forward-backward EMA to size only.
	smoothed_cx = raw_cx.copy()
	smoothed_cy = raw_cy.copy()

	if alpha_size > 0:
		smoothed_h = _forward_backward_ema(desired_crop_h, alpha_size)
	else:
		smoothed_h = desired_crop_h.copy()

	# Step 2.3: vertical composition offset (torso anchor)
	# anchor=0.50 is identity (no offset). anchor<0.50 shifts crop down
	# so the torso appears higher in the frame, leaving room for legs/feet.
	torso_anchor = float(processing.get("crop_torso_anchor", 0.50))
	if torso_anchor != 0.50:
		# offset uses smoothed height to avoid coupling tracking noise
		composition_offset = (0.50 - torso_anchor) * smoothed_h
		smoothed_cy += composition_offset

	# Step 2.5: zoom stability constraint
	# When EMA size smoothing is active (alpha_size > 0), the forward-backward
	# EMA already bounds the rate of height change and produces a smooth signal.
	# The rate limiter would re-quantize the smooth output into staircase
	# artifacts (Experiment 7d: variant D was worst despite combining both).
	# Only apply rate limiting when EMA smoothing is disabled.
	zoom_stabilization = bool(processing.get("crop_zoom_stabilization", False))
	max_height_change_frac = float(processing.get("crop_max_height_change", 0.005))
	if alpha_size > 0:
		# EMA handles zoom stability; skip rate limiting to avoid
		# re-quantizing the smooth signal into staircase steps
		pass
	elif zoom_stabilization and max_height_change_frac > 0:
		# three-mode constraint: detect zoom phases and apply per-mode rates
		# settling window is time-based: 3 seconds regardless of frame rate
		settle_seconds = 3.0
		settle_frames = round(settle_seconds * fps)
		transition_mask, settle_mask = _detect_zoom_phases(
			raw_h, settle_frames=settle_frames,
		)
		# rate multipliers per mode
		transition_rate = 0.02
		settling_rate = 0.20
		# global biased monotonicity: crop height resists direction changes
		# on ALL frames, not just settling. This prevents the crop from
		# chasing seed-height jitter and stride-phase oscillation.
		mono_direction = 0  # 0=unset, 1=growing, -1=shrinking
		mono_reversal_count = 0
		# minimum reversal threshold: 0.3% of current height
		mono_threshold_frac = 0.003
		# consecutive frames needed to allow a reversal
		# settling zones require more evidence than normal frames
		mono_sustain_normal = 5
		mono_sustain_settling = 3
		for i in range(1, n):
			delta = smoothed_h[i] - smoothed_h[i - 1]
			# determine rate limit and sustain requirement per mode
			if transition_mask[i]:
				# near-freeze during active zoom change
				max_delta = max_height_change_frac * transition_rate * smoothed_h[i - 1]
				sustain_required = mono_sustain_settling
			elif settle_mask[i]:
				# slow convergence during settling
				max_delta = max_height_change_frac * settling_rate * smoothed_h[i - 1]
				sustain_required = mono_sustain_settling
			else:
				# normal frames: full rate
				max_delta = max_height_change_frac * smoothed_h[i - 1]
				sustain_required = mono_sustain_normal
			# biased monotonicity: suppress direction reversals globally
			if mono_direction == 0:
				# set direction from first movement
				if delta > 0:
					mono_direction = 1
				elif delta < 0:
					mono_direction = -1
			else:
				# check if this frame reverses direction
				is_reversal = (
					(mono_direction == 1 and delta < 0)
					or (mono_direction == -1 and delta > 0)
				)
				if is_reversal:
					reversal_threshold = mono_threshold_frac * smoothed_h[i - 1]
					if abs(delta) < reversal_threshold:
						# suppress small reversal
						smoothed_h[i] = smoothed_h[i - 1]
						continue
					else:
						# potential sustained reversal, accumulate evidence
						mono_reversal_count += 1
						if mono_reversal_count < sustain_required:
							# not yet sustained, suppress
							smoothed_h[i] = smoothed_h[i - 1]
							continue
						else:
							# sustained reversal, allow and flip direction
							mono_direction = -mono_direction
							mono_reversal_count = 0
				else:
					mono_reversal_count = 0
			# apply rate limit
			if abs(delta) > max_delta:
				smoothed_h[i] = smoothed_h[i - 1] + math.copysign(max_delta, delta)
	elif max_height_change_frac > 0:
		# scalar constraint (original behavior when zoom_stabilization=False)
		for i in range(1, n):
			delta = smoothed_h[i] - smoothed_h[i - 1]
			max_delta = max_height_change_frac * smoothed_h[i - 1]
			if abs(delta) > max_delta:
				smoothed_h[i] = smoothed_h[i - 1] + math.copysign(max_delta, delta)

	# Step 3: guard against zero/negative size only (1-pixel sanity floor
	# to keep the containment-radius division safe). torso_height_multiple
	# is the only zoom knob -- no hidden minimum-crop floor that would
	# silently override the user's requested zoom.
	smoothed_h = numpy.maximum(smoothed_h, 1.0)
	# recompute width from smoothed height
	smoothed_w = smoothed_h * aspect_ratio
	smoothed_w = numpy.maximum(smoothed_w, 1.0)

	# Step 3.5: center containment clamp (first pass)
	containment_radius = float(processing.get("crop_containment_radius", 0.20))
	if containment_radius > 0:
		# first containment pass
		for i in range(n):
			dx_norm = (raw_cx[i] - smoothed_cx[i]) / smoothed_w[i]
			dy_norm = (raw_cy[i] - smoothed_cy[i]) / smoothed_h[i]
			offset_norm = math.hypot(dx_norm, dy_norm)
			if offset_norm > containment_radius:
				pull_factor = 1.0 - containment_radius / offset_norm
				smoothed_cx[i] += (raw_cx[i] - smoothed_cx[i]) * pull_factor
				smoothed_cy[i] += (raw_cy[i] - smoothed_cy[i]) * pull_factor

		# light re-smoothing to blunt corrective snaps (hardcoded alpha=0.3)
		smoothed_cx = _forward_backward_ema(smoothed_cx, 0.3)
		smoothed_cy = _forward_backward_ema(smoothed_cy, 0.3)

		# second containment pass (re-smoothing can reintroduce violations)
		for i in range(n):
			dx_norm = (raw_cx[i] - smoothed_cx[i]) / smoothed_w[i]
			dy_norm = (raw_cy[i] - smoothed_cy[i]) / smoothed_h[i]
			offset_norm = math.hypot(dx_norm, dy_norm)
			if offset_norm > containment_radius:
				pull_factor = 1.0 - containment_radius / offset_norm
				smoothed_cx[i] += (raw_cx[i] - smoothed_cx[i]) * pull_factor
				smoothed_cy[i] += (raw_cy[i] - smoothed_cy[i]) * pull_factor

	# Step 3.6: centered-fit-to-source. Treat torso_multiple (which
	# drove desired_crop_h above) as the maximum zoom-out, not a fixed
	# target. If the centered crop would extend past a source edge,
	# shrink it (preserving aspect) until at least one edge touches the
	# source. This keeps the runner at the output center -- the failure
	# mode the legacy black-fill path produced.
	#
	# No min-size floor: torso_height_multiple is authoritative. The
	# 1-pixel sanity floor below only protects downstream divisions
	# from a degenerate zero-size crop when the runner sits on a
	# frame edge.
	fit_to_source = bool(processing.get("crop_centered_fit_to_source", True))
	if fit_to_source:
		if _use_rolling_min_ceiling:
			# V1 ceiling-stabilized fit-to-source. Compute the per-frame
			# geometric source-fit ceiling once, then smooth it with a
			# centered rolling minimum (window 7). Use the smoothed
			# ceiling at S3 (first fit) and S5 (second fit, after the
			# S4 EMA) in place of a per-frame _max_centered_fit_size
			# call. See
			# output_smoke/zoom_bounce/EVIDENCE/v1_ceiling_aware_s4/V1_RESULTS.md
			# for gate evidence (-91.6% S5 hotspot on Glenbrook).
			v1_window = 7
			smooth_ceiling_w = _rolling_min_ceiling_per_frame(
				smoothed_cx, smoothed_cy,
				frame_width, frame_height, aspect_ratio,
				window=v1_window,
			)
			smoothed_w = numpy.minimum(smoothed_w, smooth_ceiling_w)
			smoothed_w = numpy.maximum(smoothed_w, 1.0)
			smoothed_h = smoothed_w / aspect_ratio
			smoothed_h = numpy.maximum(smoothed_h, 1.0)
			if alpha_size > 0:
				smoothed_h = _forward_backward_ema(smoothed_h, alpha_size)
				smoothed_w = smoothed_h * aspect_ratio
				smoothed_w = numpy.minimum(smoothed_w, smooth_ceiling_w)
				smoothed_w = numpy.maximum(smoothed_w, 1.0)
				smoothed_h = smoothed_w / aspect_ratio
				smoothed_h = numpy.maximum(smoothed_h, 1.0)
		else:
			# Legacy per-frame fit-to-source. Diagnostic-only path
			# selected by `_use_rolling_min_ceiling=False` (private
			# kwarg used by the V1 review harness to compare pre-V1
			# vs V1 behavior on the SAME upstream pipeline). Not
			# reachable from any user-facing CLI or config.
			for i in range(n):
				fit_w, fit_h = _max_centered_fit_size(
					smoothed_cx[i], smoothed_cy[i],
					smoothed_w[i],
					frame_width, frame_height,
					aspect_ratio,
				)
				smoothed_w[i] = max(fit_w, 1.0)
				smoothed_h[i] = max(fit_h, 1.0)
			if alpha_size > 0:
				smoothed_h = _forward_backward_ema(smoothed_h, alpha_size)
				smoothed_w = smoothed_h * aspect_ratio
				for i in range(n):
					fit_w, fit_h = _max_centered_fit_size(
						smoothed_cx[i], smoothed_cy[i],
						smoothed_w[i],
						frame_width, frame_height,
						aspect_ratio,
					)
					smoothed_w[i] = max(fit_w, 1.0)
					smoothed_h[i] = max(fit_h, 1.0)

	# Step 4: reconstruct rectangles from center + size
	x = smoothed_cx - smoothed_w / 2.0
	y = smoothed_cy - smoothed_h / 2.0

	# Step 5: BLACK FILL POLICY - clamp crop SIZE to frame, allow position out-of-bounds
	# clamp crop size to frame dimensions (crop cannot be larger than frame)
	smoothed_w = numpy.minimum(smoothed_w, float(frame_width))
	smoothed_h = numpy.minimum(smoothed_h, float(frame_height))
	# do NOT clamp position to frame bounds -- allow black fill at edges
	# (apply_crop in encoder.py fills out-of-bounds with black)
	#
	# The crop center is intentionally not velocity-capped: the
	# fit-to-source path keeps the runner centered at all costs, and a
	# per-frame cap would let the crop center lag the runner (decentring
	# the runner and possibly re-introducing a black-fill edge the fit
	# step avoided).

	# Step 6: convert to integer tuples using round() for stability
	result = []
	for i in range(n):
		rect = (
			round(x[i]),
			round(y[i]),
			round(smoothed_w[i]),
			round(smoothed_h[i]),
		)
		result.append(rect)
	return result


#============================================
def compute_crop_trajectory(
	trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
) -> list:
	"""Compute a smoothed crop rectangle for each frame in a trajectory.

	Creates a CropController from config, feeds each tracking state through
	it in order, and returns the resulting crop rectangles.

	Args:
		trajectory: List of tracking state dicts, one per frame. Each dict
			must have keys 'cx', 'cy', 'w', 'h', 'conf', and 'source'.
		frame_width: Width of the source video frame in pixels.
		frame_height: Height of the source video frame in pixels.
		config: Project configuration dictionary passed to create_crop_controller.

	Returns:
		List of (crop_x, crop_y, crop_w, crop_h) tuples, one per frame,
		as integer pixel coordinates where crop_x, crop_y is the top-left corner.
	"""
	controller = create_crop_controller(config, frame_width, frame_height)
	crop_rects = []
	for state in trajectory:
		rect = controller.update(state)
		crop_rects.append(rect)
	return crop_rects


#============================================
def trajectory_to_crop_rects(
	trajectory: list,
	video_info: dict,
	config: dict,
	nif_frames: set = None,
) -> list:
	"""Compute crop rectangles from a solved trajectory with gap filling.

	Fills None gaps in the trajectory with a hold-last-known state (or a
	center-frame fallback before any state is known). When the input is
	shorter than total_frames (e.g. analyze mode on a post-erasure list),
	the tail is padded with the same hold/fallback policy. The result is
	always exactly total_frames long.

	Args:
		trajectory: List of tracking state dicts from interval_solver.
			May contain None entries for unsolved frames; may be shorter
			than total_frames but never longer.
		video_info: Dict with frame_count, width, height, fps.
		config: Project configuration dict.
		nif_frames: Optional set of frame indices that carry NIF-edge-anchored
			geometry (pre-filled by the encoder). Frames in this set are treated
			as authoritative crop geometry and the hold_last branch is bypassed
			for them. Defaults to empty set if None.

	Returns:
		List of (x, y, w, h) crop rectangles, one per frame.
	"""
	frame_width = video_info["width"]
	frame_height = video_info["height"]
	total_frames = video_info["frame_count"]
	# A trajectory longer than the source frame count is always a wiring
	# bug (mismatched video_info, stale solve cache); fail loudly rather
	# than silently truncate, which would hide the upstream defect.
	assert len(trajectory) <= total_frames, (
		f"trajectory longer than total_frames: "
		f"{len(trajectory)} > {total_frames}"
	)

	# Default empty set if not provided (legacy callers)
	if nif_frames is None:
		nif_frames = set()

	# fill any None gaps by holding last known position with decaying confidence
	# instead of snapping to center-frame which pulls the crop away from the runner
	last_known = None
	full_trajectory = []
	for i in range(total_frames):
		if i < len(trajectory) and trajectory[i] is not None:
			full_trajectory.append(trajectory[i])
			last_known = trajectory[i]
		elif i in nif_frames:
			# NIF frame already filled with edge-anchored geometry by encoder.
			# Treat as authoritative crop geometry; do not apply hold_last.
			full_trajectory.append(trajectory[i])
		elif last_known is not None:
			# hold last known position with reduced confidence
			hold_state = {
				"cx": last_known["cx"],
				"cy": last_known["cy"],
				"w": last_known["w"],
				"h": last_known["h"],
				"conf": 0.15,
				"source": "hold_last",
			}
			full_trajectory.append(hold_state)
		else:
			# no prior position yet, use center-frame as last resort
			fallback = {
				"cx": frame_width / 2.0,
				"cy": frame_height / 2.0,
				"w": float(frame_width) * 0.3,
				"h": float(frame_height) * 0.5,
				"conf": 0.1,
				"source": "fallback",
			}
			full_trajectory.append(fallback)

	# Robust size-spike hardening (rejects isolated single-frame torso w/h
	# outliers, preserves real multi-frame scale ramps); does NOT remove
	# broadband sub-pixel breathing. Applied once here so BOTH crop modes
	# benefit from a single wiring point, BEFORE either mode's size-EMA runs,
	# leaving the EMA to do only cosmetic smoothing. Position (cx, cy) is
	# untouched (C5). This edits only the in-memory full_trajectory used for
	# crop geometry; the stored torso boxes / npz are unchanged
	# (output-feel only).
	full_trajectory = torso_size_stabilizer.stabilize_trajectory(
		full_trajectory,
		method=CROP_SIZE_STABILIZER_METHOD,
		window=CROP_SIZE_STABILIZER_WINDOW,
	)

	# read crop mode from config (default: smooth for backward compatibility)
	processing = config.get("processing", {})
	crop_mode = str(processing.get("crop_mode", "smooth"))

	if crop_mode == "direct_center":
		# validate trajectory entries have required keys before dispatch
		required_keys = {"cx", "cy", "h"}
		for i, entry in enumerate(full_trajectory):
			if not isinstance(entry, dict) or not required_keys.issubset(entry):
				missing = required_keys - set(entry.keys()) if isinstance(entry, dict) else required_keys
				raise RuntimeError(
					f"Trajectory entry {i} missing required keys for "
					f"direct_center mode: {missing}"
				)
		# direct-center mode: center on solved trajectory, skip CropController
		fps = float(video_info.get("fps", 60.0))
		crop_rects = direct_center_crop_trajectory(
			full_trajectory, frame_width, frame_height, config, fps=fps,
			nif_frames=nif_frames,
		)
	elif crop_mode == "smooth":
		# existing online CropController pass
		crop_rects = compute_crop_trajectory(
			full_trajectory, frame_width, frame_height, config,
		)
		# offline post-smoothing of crop SIZE (forward-backward EMA).
		# use the same fixed module constant as the direct_center path.
		# The crop center is not post-smoothed (position EMA was a
		# permanently disabled no-op and has been removed).
		alpha_size = CROP_POST_SMOOTH_SIZE_STRENGTH
		if alpha_size > 0:
			crop_rects = smooth_crop_trajectory(
				crop_rects, frame_width, frame_height,
				alpha_size=alpha_size,
			)
	else:
		raise RuntimeError(
			f"Unknown crop_mode '{crop_mode}', expected 'smooth' or 'direct_center'"
		)

	return crop_rects


#============================================
def _forward_backward_ema(
	signal: numpy.ndarray,
	alpha: float,
) -> numpy.ndarray:
	"""Apply a true forward-backward exponential moving average.

	Runs a forward EMA on the raw signal, then a backward EMA on the
	forward result. This produces zero-phase-like smoothing that
	preserves local dynamics without needing scipy.

	Args:
		signal: 1-D numpy array of float values.
		alpha: EMA coefficient (0 < alpha <= 1). Lower values give
			heavier smoothing and slower response.

	Returns:
		Smoothed 1-D numpy array of the same length as signal.
	"""
	n = len(signal)
	if n == 0:
		return signal.copy()
	if n == 1:
		return signal.copy()

	# forward pass: initialize at raw[0]
	forward = numpy.empty(n, dtype=float)
	forward[0] = signal[0]
	for i in range(1, n):
		forward[i] = alpha * signal[i] + (1.0 - alpha) * forward[i - 1]

	# backward pass on forward result: initialize at forward[-1]
	final = numpy.empty(n, dtype=float)
	final[-1] = forward[-1]
	for i in range(n - 2, -1, -1):
		final[i] = alpha * forward[i] + (1.0 - alpha) * final[i + 1]

	return final


#============================================
def smooth_crop_trajectory(
	crop_rects: list,
	frame_width: int,
	frame_height: int,
	alpha_size: float = 0.0,
) -> list:
	"""Post-smooth a crop trajectory's SIZE using forward-backward EMA.

	Decomposes crop rectangles into center (cx, cy) and size (w, h)
	signals, applies optional forward-backward EMA smoothing to the size,
	reconstructs rectangles, and clamps to frame bounds. The crop center
	is passed through unchanged (the position-smoothing and velocity-cap
	legs were permanently disabled no-ops and have been removed).

	Args:
		crop_rects: List of (x, y, w, h) integer crop rectangles.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		alpha_size: EMA alpha for size smoothing. 0 = disabled.

	Returns:
		List of (x, y, w, h) integer crop rectangles after smoothing.
	"""
	n = len(crop_rects)
	if n == 0:
		return crop_rects

	# extract center and size arrays from rectangles
	arr = numpy.array(crop_rects, dtype=float)
	# arr columns: x, y, w, h
	cx = arr[:, 0] + arr[:, 2] / 2.0
	cy = arr[:, 1] + arr[:, 3] / 2.0
	w = arr[:, 2].copy()
	h = arr[:, 3].copy()

	# smooth size signals
	if alpha_size > 0:
		w = _forward_backward_ema(w, alpha_size)
		h = _forward_backward_ema(h, alpha_size)

	# clamp size to minimum positive value
	min_dim = 10.0
	w = numpy.maximum(w, min_dim)
	h = numpy.maximum(h, min_dim)

	# reconstruct rectangles from passed-through center + smoothed size
	x = cx - w / 2.0
	y = cy - h / 2.0

	# clamp to frame bounds
	x = numpy.clip(x, 0.0, frame_width - w)
	y = numpy.clip(y, 0.0, frame_height - h)

	# convert back to integer tuples
	result = []
	for i in range(n):
		rect = (int(x[i]), int(y[i]), int(w[i]), int(h[i]))
		result.append(rect)
	return result


#============================================
def _detect_zoom_phases(
	raw_h: numpy.ndarray,
	window: int = 5,
	threshold_ratio: float = 1.40,
	settle_frames: int = 60,
) -> tuple:
	"""Detect zoom transition and settling phases in a height signal.

	Uses a sliding window to find frames where the max/min height ratio
	exceeds a threshold, indicating a camera zoom transition (e.g. iPhone
	lens switch). Returns two boolean masks:
	- transition_mask: frames during active zoom change
	- settle_mask: frames in the settling window after each transition

	Args:
		raw_h: 1-D numpy array of raw bbox heights per frame.
		window: Sliding window size in frames.
		threshold_ratio: Max/min ratio within window that triggers detection.
		settle_frames: Number of frames to mark as settling after each
			transition window ends.

	Returns:
		Tuple of (transition_mask, settle_mask) boolean numpy arrays,
		each of length len(raw_h).
	"""
	n = len(raw_h)
	transition_mask = numpy.zeros(n, dtype=bool)
	settle_mask = numpy.zeros(n, dtype=bool)
	if n < 2:
		return (transition_mask, settle_mask)

	# guard against zero or NaN heights
	safe_h = numpy.where(
		(raw_h > 0) & numpy.isfinite(raw_h), raw_h, 1.0,
	)

	# sliding window: flag frames where max/min ratio exceeds threshold
	half_w = window // 2
	for i in range(n):
		lo = max(0, i - half_w)
		hi = min(n, i + half_w + 1)
		win_slice = safe_h[lo:hi]
		ratio = numpy.max(win_slice) / numpy.min(win_slice)
		if ratio >= threshold_ratio:
			transition_mask[i] = True

	# build settle mask: settle_frames after each contiguous transition block
	in_transition = False
	block_end = -1
	for i in range(n):
		if transition_mask[i]:
			in_transition = True
			block_end = i
		elif in_transition:
			# transition block just ended at block_end
			in_transition = False
			settle_start = block_end + 1
			settle_end = min(n, settle_start + settle_frames)
			settle_mask[settle_start:settle_end] = True

	# handle case where transition extends to end of signal
	# (no settling after final transition block)

	# merge overlapping settle zones with subsequent transitions
	# if a transition starts during a settle zone, extend settle after it
	changed = True
	while changed:
		changed = False
		in_transition = False
		block_end = -1
		for i in range(n):
			if transition_mask[i]:
				in_transition = True
				block_end = i
			elif in_transition:
				in_transition = False
				settle_start = block_end + 1
				settle_end = min(n, settle_start + settle_frames)
				for j in range(settle_start, settle_end):
					if not settle_mask[j]:
						settle_mask[j] = True
						changed = True

	# ensure transition frames are not also settle frames
	settle_mask[transition_mask] = False

	return (transition_mask, settle_mask)


# ============================================================
# Retained override levers for broadband zoom-breathing work
# ============================================================
# These functions take baseline crop rects and optionally the solved
# trajectory, then replace center or size channels while preserving
# the other. slow_size and fixed_height are the relevant levers
# for the deferred zoom-breathing task. Retained intentionally.


#============================================
def center_lock_override(
	crop_rects: list,
	trajectory: list,
	frame_width: int,
	frame_height: int,
	alpha: float = 0.03,
	max_velocity: float = 0.0,
) -> list:
	"""Replace crop centers with smoothed trajectory centers.

	Preserves baseline width and height for each frame while replacing
	the center path with a forward-backward EMA of the solved trajectory
	cx, cy positions. This bypasses the CropController's reactive
	attack/release behavior to get zero-phase-lag tracking.

	Args:
		crop_rects: Baseline (x, y, w, h) integer crop rectangles.
		trajectory: Dense list of tracking state dicts with 'cx', 'cy'.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		alpha: EMA coefficient for center smoothing. Lower = heavier.
		max_velocity: Max center displacement per frame (px). 0 = no cap.

	Returns:
		List of (x, y, w, h) integer crop rectangles with locked centers.
	"""
	n = len(crop_rects)
	if n == 0:
		return []

	# extract baseline width and height (preserve these)
	arr = numpy.array(crop_rects, dtype=float)
	base_w = arr[:, 2].copy()
	base_h = arr[:, 3].copy()
	# baseline centers used as fallback for frames beyond trajectory
	base_cx = arr[:, 0] + arr[:, 2] / 2.0
	base_cy = arr[:, 1] + arr[:, 3] / 2.0

	# extract trajectory centers, padding with baseline centers if shorter
	traj_len = len(trajectory)
	traj_cx = numpy.empty(n, dtype=float)
	traj_cy = numpy.empty(n, dtype=float)
	for i in range(n):
		if i < traj_len and trajectory[i] is not None:
			traj_cx[i] = trajectory[i]["cx"]
			traj_cy[i] = trajectory[i]["cy"]
		else:
			# use baseline crop center as fallback
			traj_cx[i] = base_cx[i]
			traj_cy[i] = base_cy[i]

	# smooth trajectory centers with forward-backward EMA
	if alpha > 0:
		locked_cx = _forward_backward_ema(traj_cx, alpha)
		locked_cy = _forward_backward_ema(traj_cy, alpha)
	else:
		locked_cx = traj_cx.copy()
		locked_cy = traj_cy.copy()

	# apply velocity cap on smoothed centers
	if max_velocity > 0:
		for i in range(1, n):
			dx = locked_cx[i] - locked_cx[i - 1]
			dy = locked_cy[i] - locked_cy[i - 1]
			dist = math.sqrt(dx * dx + dy * dy)
			if dist > max_velocity:
				scale = max_velocity / dist
				locked_cx[i] = locked_cx[i - 1] + dx * scale
				locked_cy[i] = locked_cy[i - 1] + dy * scale

	# reconstruct rects from locked centers + baseline sizes
	x = locked_cx - base_w / 2.0
	y = locked_cy - base_h / 2.0

	# clamp to frame bounds
	x = numpy.clip(x, 0.0, numpy.maximum(frame_width - base_w, 0.0))
	y = numpy.clip(y, 0.0, numpy.maximum(frame_height - base_h, 0.0))

	# convert to integer tuples
	result = []
	for i in range(n):
		rect = (round(x[i]), round(y[i]), round(base_w[i]), round(base_h[i]))
		result.append(rect)
	return result


#============================================
def fixed_height_override(
	crop_rects: list,
	frame_width: int,
	frame_height: int,
) -> list:
	"""Replace per-frame crop height with a clip-constant height.

	Uses the median baseline crop height as the fixed value. Centers
	are preserved from the baseline rects; width is recomputed from
	the fixed height and the baseline aspect ratio.

	Args:
		crop_rects: Baseline (x, y, w, h) integer crop rectangles.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.

	Returns:
		List of (x, y, w, h) integer crop rectangles with constant height.
	"""
	n = len(crop_rects)
	if n == 0:
		return []

	arr = numpy.array(crop_rects, dtype=float)
	# extract baseline centers
	cx = arr[:, 0] + arr[:, 2] / 2.0
	cy = arr[:, 1] + arr[:, 3] / 2.0
	base_w = arr[:, 2]
	base_h = arr[:, 3]

	# compute fixed height from median of baseline heights
	fixed_h = float(numpy.median(base_h))
	# clamp to frame height (1-pixel sanity floor; no min_crop_size override)
	fixed_h = max(1.0, min(fixed_h, float(frame_height)))

	# compute aspect ratio from median baseline dimensions
	median_w = float(numpy.median(base_w))
	median_h = float(numpy.median(base_h))
	# avoid division by zero
	aspect = median_w / median_h if median_h > 0 else 1.0

	# compute fixed width from aspect ratio
	fixed_w = fixed_h * aspect
	# clamp width to frame
	if fixed_w > frame_width:
		fixed_w = float(frame_width)
		fixed_h = fixed_w / aspect

	# reconstruct rects from baseline centers + fixed size
	x = cx - fixed_w / 2.0
	y = cy - fixed_h / 2.0

	# clamp to frame bounds
	max_x = max(frame_width - fixed_w, 0.0)
	max_y = max(frame_height - fixed_h, 0.0)
	x = numpy.clip(x, 0.0, max_x)
	y = numpy.clip(y, 0.0, max_y)

	# convert to integer tuples
	result = []
	for i in range(n):
		rect = (round(x[i]), round(y[i]), round(fixed_w), round(fixed_h))
		result.append(rect)
	return result


#============================================
def slow_size_override(
	crop_rects: list,
	frame_width: int,
	frame_height: int,
	alpha: float = 0.01,
	deadband_fraction: float = 0.03,
) -> list:
	"""Replace crop height with a heavily smoothed path.

	Applies a deadband to reject small oscillations, then uses
	forward-backward EMA on the remaining height signal. Width is
	recomputed from the smoothed height and baseline aspect ratio.
	Centers are preserved from baseline.

	Args:
		crop_rects: Baseline (x, y, w, h) integer crop rectangles.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		alpha: EMA coefficient for size smoothing. Lower = heavier.
		deadband_fraction: Fraction of current height below which
			frame-to-frame changes are suppressed.

	Returns:
		List of (x, y, w, h) integer crop rectangles with smoothed height.
	"""
	n = len(crop_rects)
	if n == 0:
		return []

	arr = numpy.array(crop_rects, dtype=float)
	# extract baseline centers and sizes
	cx = arr[:, 0] + arr[:, 2] / 2.0
	cy = arr[:, 1] + arr[:, 3] / 2.0
	base_w = arr[:, 2]
	base_h = arr[:, 3]

	# compute aspect ratio from median baseline dimensions
	median_w = float(numpy.median(base_w))
	median_h = float(numpy.median(base_h))
	aspect = median_w / median_h if median_h > 0 else 1.0

	# apply deadband: suppress small frame-to-frame height changes
	# start from the first frame's height and only update when
	# the change exceeds the deadband threshold
	deadbanded_h = numpy.empty(n, dtype=float)
	deadbanded_h[0] = base_h[0]
	for i in range(1, n):
		threshold = deadband_fraction * deadbanded_h[i - 1]
		delta = base_h[i] - deadbanded_h[i - 1]
		if abs(delta) > threshold:
			deadbanded_h[i] = base_h[i]
		else:
			# suppress: hold previous value
			deadbanded_h[i] = deadbanded_h[i - 1]

	# apply forward-backward EMA to the deadbanded signal
	if alpha > 0:
		smoothed_h = _forward_backward_ema(deadbanded_h, alpha)
	else:
		smoothed_h = deadbanded_h.copy()

	# 1-pixel sanity floor; no min_crop_size override
	smoothed_h = numpy.maximum(smoothed_h, 1.0)
	# clamp to frame height
	smoothed_h = numpy.minimum(smoothed_h, float(frame_height))

	# recompute width from smoothed height
	smoothed_w = smoothed_h * aspect
	# clamp width to frame
	smoothed_w = numpy.minimum(smoothed_w, float(frame_width))

	# reconstruct rects from baseline centers + smoothed size
	x = cx - smoothed_w / 2.0
	y = cy - smoothed_h / 2.0

	# clamp to frame bounds
	x = numpy.clip(x, 0.0, numpy.maximum(frame_width - smoothed_w, 0.0))
	y = numpy.clip(y, 0.0, numpy.maximum(frame_height - smoothed_h, 0.0))

	# convert to integer tuples
	result = []
	for i in range(n):
		rect = (round(x[i]), round(y[i]), round(smoothed_w[i]), round(smoothed_h[i]))
		result.append(rect)
	return result


#============================================
def apply_experiment_overrides(
	crop_rects: list,
	trajectory: list,
	frame_width: int,
	frame_height: int,
	config: dict,
) -> list:
	"""Apply center and/or size overrides to baseline crop rects.

	Reads override settings from config['processing'] and dispatches to the
	appropriate override functions. Center overrides are applied first, then
	size overrides. Retained as levers for the broadband zoom-breathing task.

	Config keys (all optional):
		exp_center_override: 'center_lock' or None
		exp_center_alpha: float, EMA alpha for center lock
		exp_center_max_velocity: float, velocity cap for center lock
		exp_size_override: 'fixed_crop' or 'slow_size' or None
		exp_slow_size_alpha: float, EMA alpha for slow size
		exp_slow_size_deadband: float, deadband fraction for slow size

	Args:
		crop_rects: Baseline crop rectangles from trajectory_to_crop_rects.
		trajectory: Dense solved trajectory list.
		frame_width: Source video frame width in pixels.
		frame_height: Source video frame height in pixels.
		config: Project configuration dict.

	Returns:
		Crop rectangles with experiment overrides applied.
	"""
	processing = config.get("processing", {})

	# step 1: apply center override if requested
	center_mode = processing.get("exp_center_override")
	if center_mode == "center_lock":
		center_alpha = float(processing.get("exp_center_alpha", 0.03))
		center_vel = float(processing.get("exp_center_max_velocity", 0.0))
		crop_rects = center_lock_override(
			crop_rects, trajectory,
			frame_width, frame_height,
			alpha=center_alpha,
			max_velocity=center_vel,
		)

	# step 2: apply size override if requested
	size_mode = processing.get("exp_size_override")
	if size_mode == "fixed_crop":
		crop_rects = fixed_height_override(
			crop_rects, frame_width, frame_height,
		)
	elif size_mode == "slow_size":
		slow_alpha = float(processing.get("exp_slow_size_alpha", 0.01))
		slow_deadband = float(processing.get("exp_slow_size_deadband", 0.03))
		crop_rects = slow_size_override(
			crop_rects, frame_width, frame_height,
			alpha=slow_alpha,
			deadband_fraction=slow_deadband,
		)

	return crop_rects
