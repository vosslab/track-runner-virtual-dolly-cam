"""Pure offline virtual-dolly path solve.

The crop stage is a batch operation, so its camera path is fit against the
entire target trajectory at once instead of being driven by a causal
controller.  This module deliberately knows nothing about readers, files,
crop rectangles, or containment.  The crop integration prepares the target
signals and applies containment after this solver returns.

For each position channel ``p`` the objective is::

    sum_i q_i ((p_i - target_i) / torso_width_i)^2
      + lambda sum_i ((p_{i-1} - 2 p_i + p_{i+1}) / torso_width_i)^2

``q_i`` is the M2 confidence weight and ``lambda`` is dimensionless.  Dividing
both terms by torso width makes a fixed lambda mean the same camera
smoothness at every footage scale (contract C2).  Log crop size has the same
objective without the width division: a log-size residual and its second
difference are already dimensionless.  Its resulting zoom is therefore
multiplicatively, rather than additively, smooth.
"""

# Standard Library
import dataclasses
import math

# PIP3 modules
import numpy
import scipy.linalg


#============================================
@dataclasses.dataclass(frozen=True)
class DollyPath:
	"""Whole-video camera targets before crop containment.

	``center_x`` and ``center_y`` are source-frame pixels.  ``log_size`` is
	the natural logarithm of the target crop height in pixels.  The caller can
	exponentiate it after applying its crop-specific containment constraints.
	"""
	center_x: numpy.ndarray
	center_y: numpy.ndarray
	log_size: numpy.ndarray


#============================================
def solve_dolly_path(
	target_trajectory: list,
	confidence_weights: numpy.ndarray | list,
	smoothness: float,
) -> DollyPath:
	"""Solve a non-causal, acceleration-bounded camera path.

	Visible target states must contain ``cx``, ``cy``, ``log_size``, and
	``torso_width``.  Center values are pixel coordinates; ``log_size`` is the
	natural log of the desired crop height in pixels; ``torso_width`` is the
	runner's torso width in pixels.  A state whose optional ``status`` is
	``not_in_frame`` may omit all four geometry keys and is always given zero
	tracking weight, even if its supplied confidence is nonzero.  Its torso
	width for the scale-normalized acceleration term is linearly bridged from
	visible frames, while smoothness bridges the camera path itself.

	Args:
		target_trajectory: One prepared target dictionary for every video frame.
		confidence_weights: M2 confidence weights, one nonnegative finite value
			per target frame.
		smoothness: Dimensionless squared-acceleration penalty in torso-width
			units.  It applies equally at all image scales.

	Returns:
		A :class:`DollyPath` with pixel centers and natural-log crop heights.

	Raises:
		ValueError: For malformed values or an underconstrained path.  With
			positive smoothness, at least two distinct nonzero-weight frames are
			required to anchor the affine null space of the
			squared-acceleration penalty.  With zero smoothness, every frame
			requires positive data weight because there is no coupling term.
	"""
	n_frames = len(target_trajectory)
	if n_frames == 0:
		empty = numpy.empty(0, dtype=numpy.float64)
		result = DollyPath(empty, empty.copy(), empty.copy())
		return result

	weights = _validated_weights(confidence_weights, n_frames)
	if not math.isfinite(smoothness) or smoothness < 0.0:
		raise ValueError("smoothness must be a finite nonnegative value")

	center_x, center_y, log_size, torso_widths = _extract_targets(target_trajectory)
	for i, state in enumerate(target_trajectory):
		if state.get("status") == "not_in_frame":
			weights[i] = 0.0
	_anchor_path(weights, n_frames, smoothness)

	solved_x = _solve_channel(center_x, weights, torso_widths, smoothness)
	solved_y = _solve_channel(center_y, weights, torso_widths, smoothness)
	# Log size is dimensionless, so no pixel scale is appropriate here.
	solved_log_size = _solve_channel(log_size, weights, None, smoothness)
	result = DollyPath(solved_x, solved_y, solved_log_size)
	return result


#============================================
def _validated_weights(confidence_weights: numpy.ndarray | list, n_frames: int) -> numpy.ndarray:
	"""Return a writable one-dimensional finite nonnegative weight vector."""
	weights = numpy.asarray(confidence_weights, dtype=numpy.float64)
	if weights.ndim != 1 or len(weights) != n_frames:
		raise ValueError("confidence_weights must have one value per target frame")
	if not numpy.all(numpy.isfinite(weights)) or numpy.any(weights < 0.0):
		raise ValueError("confidence_weights must be finite and nonnegative")
	result = weights.copy()
	return result


#============================================
def _extract_targets(target_trajectory: list) -> tuple:
	"""Extract and validate the prepared crop target signals."""
	center_x = []
	center_y = []
	log_size = []
	torso_widths = []
	not_in_frame = []
	for state in target_trajectory:
		is_not_in_frame = state.get("status") == "not_in_frame"
		not_in_frame.append(is_not_in_frame)
		if is_not_in_frame:
			# NIF is an absence of geometry, not an unreliable observation.
			# Zero placeholders cannot affect the objective after its data weight
			# is cleared above; widths are bridged below for C2 normalization.
			center_x.append(0.0)
			center_y.append(0.0)
			log_size.append(0.0)
			torso_widths.append(numpy.nan)
			continue
		center_x.append(float(state["cx"]))
		center_y.append(float(state["cy"]))
		log_size.append(float(state["log_size"]))
		torso_widths.append(float(state["torso_width"]))

	center_x = numpy.asarray(center_x, dtype=numpy.float64)
	center_y = numpy.asarray(center_y, dtype=numpy.float64)
	log_size = numpy.asarray(log_size, dtype=numpy.float64)
	torso_widths = numpy.asarray(torso_widths, dtype=numpy.float64)
	visible = numpy.logical_not(numpy.asarray(not_in_frame, dtype=bool))
	if not numpy.all(numpy.isfinite(center_x[visible])) or not numpy.all(numpy.isfinite(center_y[visible])):
		raise ValueError("visible target centers must be finite pixels")
	if not numpy.all(numpy.isfinite(log_size[visible])):
		raise ValueError("visible target log sizes must be finite")
	if not numpy.all(numpy.isfinite(torso_widths[visible])) or numpy.any(torso_widths[visible] <= 0.0):
		raise ValueError("visible target torso widths must be finite positive pixels")
	torso_widths = _bridge_nif_torso_widths(torso_widths)
	result = (center_x, center_y, log_size, torso_widths)
	return result


#============================================
def _bridge_nif_torso_widths(torso_widths: numpy.ndarray) -> numpy.ndarray:
	"""Linearly bridge missing NIF widths for C2 acceleration normalization."""
	known = numpy.isfinite(torso_widths) & (torso_widths > 0.0)
	if not numpy.any(known):
		raise ValueError("dolly path needs a visible torso width")
	frame_indices = numpy.arange(len(torso_widths), dtype=numpy.float64)
	result = numpy.interp(frame_indices, frame_indices[known], torso_widths[known])
	return result


#============================================
def _anchor_path(weights: numpy.ndarray, n_frames: int, smoothness: float) -> None:
	"""Reject a path whose acceleration objective still has an affine null space."""
	if smoothness == 0.0:
		# Without the second-difference term, every output frame is independent.
		# A zero data weight would leave that frame's normal-equation row empty.
		if numpy.any(weights == 0.0):
			raise ValueError(
				"zero smoothness requires positive data weight for every frame"
			)
		return
	# One frame has no acceleration term, so its one weighted observation is
	# sufficient.  At two or more frames the second-difference term leaves an
	# affine null space and two observations remove its offset and slope.
	minimum_anchors = min(2, n_frames)
	if numpy.count_nonzero(weights) < minimum_anchors:
		raise ValueError("dolly path has too few nonzero-weight frames to anchor it")


#============================================
def _solve_channel(
	target: numpy.ndarray,
	weights: numpy.ndarray,
	torso_widths: numpy.ndarray | None,
	smoothness: float,
) -> numpy.ndarray:
	"""Build and solve one symmetric pentadiagonal normal equation."""
	n_frames = len(target)
	banded = numpy.zeros((5, n_frames), dtype=numpy.float64)
	rhs = numpy.zeros(n_frames, dtype=numpy.float64)

	if torso_widths is None:
		data_scales = numpy.ones(n_frames, dtype=numpy.float64)
	else:
		data_scales = torso_widths
	for i in range(n_frames):
		coefficient = weights[i] / (data_scales[i] ** 2)
		_banded_add(banded, i, i, coefficient)
		rhs[i] += coefficient * target[i]

	if smoothness > 0.0:
		for i in range(1, n_frames - 1):
			if torso_widths is None:
				acceleration_scale = 1.0
			else:
				acceleration_scale = torso_widths[i]
			row_weight = smoothness / (acceleration_scale ** 2)
			_indices = (i - 1, i, i + 1)
			_coefficients = (1.0, -2.0, 1.0)
			for row_index, row_coefficient in zip(_indices, _coefficients):
				for col_index, col_coefficient in zip(_indices, _coefficients):
					_banded_add(
						banded,
						row_index,
						col_index,
						row_weight * row_coefficient * col_coefficient,
					)

	solution = scipy.linalg.solve_banded((2, 2), banded, rhs, check_finite=True)
	return solution


#============================================
def _banded_add(banded: numpy.ndarray, row: int, column: int, value: float) -> None:
	"""Add one value to scipy's upper/lower-two banded storage layout."""
	band_row = 2 + row - column
	banded[band_row, column] += value
