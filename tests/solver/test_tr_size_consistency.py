"""Behavioral regression tests for interval scoring."""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules
import camera_motion
import scene_coords
import scoring
import velocity_model


#============================================
def _score_with_cumulative_scale(cumulative_scale: float) -> float:
	"""Return the same scene-space residual through one camera zoom scale."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.array([1.0, 1.0, cumulative_scale, 1.2, 0.75], dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	# The middle state is 10% taller than the log-linear scene expectation.
	scene_heights = [10.0, 22.0, 40.0]
	path = []
	start_frame = 2
	for offset, scene_height in enumerate(scene_heights):
		frame_index = start_frame + offset
		pixel_cx, pixel_cy, pixel_w, pixel_h = transform.scene_box_to_pixel(
			frame_index, 20.0, 30.0, 8.0, scene_height,
		)
		path.append({
			"cx": pixel_cx,
			"cy": pixel_cy,
			"w": pixel_w,
			"h": pixel_h,
			"conf": 1.0,
		})
	interval_curves = {
		"start_frame": start_frame,
		"end_frame": start_frame + 2,
		"left_size": (8.0, 10.0),
		"right_size": (8.0, 40.0),
	}
	score = scoring.score_interval_analytical(
		path, path, [], interval_curves, transform, blended_path=path,
	)
	result = float(score["size_consistency"])
	return result


#============================================
def test_size_consistency_is_invariant_to_camera_zoom() -> None:
	"""The same nonzero scene residual scores equally across camera zoom."""
	score_without_zoom = _score_with_cumulative_scale(1.0)
	score_with_zoom = _score_with_cumulative_scale(1.5)
	expected_score = 1.0 - (0.0 + 0.1 + 0.0) / 3.0

	assert numpy.isclose(score_without_zoom, expected_score)
	assert abs(score_without_zoom - score_with_zoom) < 0.01


#============================================
def test_size_consistency_matches_producer_tiny_size_fallback() -> None:
	"""Tiny endpoints score exactly against the producer's finite-log policy."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(3, dtype=numpy.float32),
		dy=numpy.zeros(3, dtype=numpy.float32),
		scale=numpy.ones(3, dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	left_height = 1e-7
	right_height = 5e-7
	interval_curves = {
		"start_frame": 0,
		"end_frame": 2,
		"left_pos": (20.0, 30.0),
		"right_pos": (20.0, 30.0),
		"left_size": (8.0, left_height),
		"right_size": (8.0, right_height),
	}
	path = velocity_model.propagate_forward_analytical(interval_curves, transform)
	score = scoring.score_interval_analytical(
		path, path, [], interval_curves, transform, blended_path=path,
	)

	assert score["size_consistency"] == 1.0


#============================================
def test_size_consistency_matches_producer_high_log_fallback() -> None:
	"""An overlarge interior size retains the forward producer's anchor."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(3, dtype=numpy.float32),
		dy=numpy.zeros(3, dtype=numpy.float32),
		scale=numpy.ones(3, dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	interval_curves = {
		"start_frame": 0,
		"end_frame": 2,
		"left_pos": (20.0, 30.0),
		"right_pos": (20.0, 30.0),
		"left_size": (8.0, 10.0),
		"right_size": (8.0, math.exp(200.0)),
	}
	path = velocity_model.propagate_forward_analytical(interval_curves, transform)
	score = scoring.score_interval_analytical(
		path, path, [], interval_curves, transform, blended_path=path,
	)

	assert score["size_consistency"] == 1.0
