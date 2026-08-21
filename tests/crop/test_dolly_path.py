"""Behavior tests for the pure, offline virtual-dolly path solver."""

# PIP3 modules
import numpy

# local repo modules
import dolly_path


#============================================
def _linear_target(frame: int, status: str = "visible") -> dict:
	"""Return a target that is exactly zero-acceleration in every channel."""
	result = {
		"cx": 100.0 + 8.0 * frame,
		"cy": 240.0 - 3.0 * frame,
		"log_size": numpy.log(300.0) + 0.02 * frame,
		"torso_width": 40.0,
		"status": status,
	}
	return result


#============================================
def test_dolly_solver_reproduces_exact_zero_acceleration_target() -> None:
	"""A target with zero objective cost is preserved exactly."""
	targets = [_linear_target(frame) for frame in range(7)]
	path = dolly_path.solve_dolly_path(targets, [1.0] * 7, smoothness=20.0)

	assert numpy.allclose(path.center_x, [target["cx"] for target in targets])
	assert numpy.allclose(path.log_size, [target["log_size"] for target in targets])


#============================================
def test_not_in_frame_target_has_no_tracking_pull() -> None:
	"""A NIF target is bridged from visible endpoints even with high input weight."""
	targets = [_linear_target(frame) for frame in range(5)]
	targets[2] = {"status": "not_in_frame"}
	path = dolly_path.solve_dolly_path(targets, [1.0] * 5, smoothness=10.0)

	assert numpy.allclose(path.center_y, [240.0 - 3.0 * frame for frame in range(5)])


#============================================
def _dense_reference(targets: list, weights: list, smoothness: float) -> tuple:
	"""Solve the documented objective directly, independently of band storage."""
	n_frames = len(targets)
	widths = numpy.array([target["torso_width"] for target in targets], dtype=float)
	weight_array = numpy.asarray(weights, dtype=float)
	position_matrix = numpy.diag(weight_array / (widths ** 2))
	log_size_matrix = numpy.diag(weight_array)
	for frame in range(1, n_frames - 1):
		second_difference = numpy.zeros(n_frames, dtype=float)
		second_difference[frame - 1:frame + 2] = (1.0, -2.0, 1.0)
		position_matrix += (
			smoothness / (widths[frame] ** 2)
			* numpy.outer(second_difference, second_difference)
		)
		log_size_matrix += smoothness * numpy.outer(
			second_difference, second_difference,
		)
	center_x = numpy.array([target["cx"] for target in targets], dtype=float)
	center_y = numpy.array([target["cy"] for target in targets], dtype=float)
	log_size = numpy.array([target["log_size"] for target in targets], dtype=float)
	expected_x = numpy.linalg.solve(position_matrix, weight_array * center_x / (widths ** 2))
	expected_y = numpy.linalg.solve(position_matrix, weight_array * center_y / (widths ** 2))
	expected_log_size = numpy.linalg.solve(log_size_matrix, weight_array * log_size)
	result = (expected_x, expected_y, expected_log_size)
	return result


#============================================
def test_dolly_banded_solution_matches_dense_nonlinear_reference() -> None:
	"""The banded normal equation equals a direct dense solve on varied input."""
	targets = [
		{"cx": 12.0, "cy": 40.0, "log_size": 4.8, "torso_width": 18.0},
		{"cx": 29.0, "cy": 31.0, "log_size": 5.1, "torso_width": 24.0},
		{"cx": 15.0, "cy": 67.0, "log_size": 4.7, "torso_width": 16.0},
		{"cx": 61.0, "cy": 52.0, "log_size": 5.4, "torso_width": 30.0},
		{"cx": 57.0, "cy": 88.0, "log_size": 5.0, "torso_width": 21.0},
	]
	weights = [0.4, 1.0, 0.7, 0.5, 0.9]
	path = dolly_path.solve_dolly_path(targets, weights, smoothness=2.3)
	expected = _dense_reference(targets, weights, smoothness=2.3)

	actual = (path.center_x, path.center_y, path.log_size)
	assert all(numpy.allclose(value, expected_value) for value, expected_value in zip(actual, expected))


#============================================
def test_dolly_position_is_invariant_to_uniform_footage_scale() -> None:
	"""C2 normalization preserves the same solution in torso-relative units."""
	targets = [
		{"cx": 12.0, "cy": 40.0, "log_size": 4.8, "torso_width": 18.0},
		{"cx": 29.0, "cy": 31.0, "log_size": 5.1, "torso_width": 24.0},
		{"cx": 15.0, "cy": 67.0, "log_size": 4.7, "torso_width": 16.0},
		{"cx": 61.0, "cy": 52.0, "log_size": 5.4, "torso_width": 30.0},
		{"cx": 57.0, "cy": 88.0, "log_size": 5.0, "torso_width": 21.0},
	]
	weights = [0.4, 1.0, 0.7, 0.5, 0.9]
	scale = 3.5
	scaled_targets = [
		{
			"cx": target["cx"] * scale,
			"cy": target["cy"] * scale,
			"log_size": target["log_size"] + numpy.log(scale),
			"torso_width": target["torso_width"] * scale,
		}
		for target in targets
	]
	path = dolly_path.solve_dolly_path(targets, weights, smoothness=2.3)
	scaled_path = dolly_path.solve_dolly_path(scaled_targets, weights, smoothness=2.3)

	assert (
		numpy.allclose(scaled_path.center_x, path.center_x * scale)
		and numpy.allclose(scaled_path.center_y, path.center_y * scale)
		and numpy.allclose(scaled_path.log_size, path.log_size + numpy.log(scale))
	)


#============================================
def test_zero_smoothness_rejects_an_unweighted_frame() -> None:
	"""Independent data-only frames cannot be solved when one lacks evidence."""
	targets = [_linear_target(frame) for frame in range(3)]

	with numpy.testing.assert_raises_regex(ValueError, "positive data weight"):
		dolly_path.solve_dolly_path(targets, [1.0, 1.0, 0.0], smoothness=0.0)


#============================================
def test_zero_smoothness_with_complete_evidence_returns_each_target() -> None:
	"""The data-only solve is exact when every independent frame is anchored."""
	targets = [_linear_target(frame) for frame in range(3)]
	path = dolly_path.solve_dolly_path(targets, [0.2, 0.7, 1.0], smoothness=0.0)
	expected = (
		numpy.array([target["cx"] for target in targets]),
		numpy.array([target["cy"] for target in targets]),
		numpy.array([target["log_size"] for target in targets]),
	)
	actual = (path.center_x, path.center_y, path.log_size)

	assert all(numpy.allclose(value, expected_value) for value, expected_value in zip(actual, expected))
