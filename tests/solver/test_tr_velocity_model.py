"""Small behavioral checks for pair-local analytical interpolation."""

# PIP3 modules
import numpy

# local repo modules
import camera_motion
import scene_coords
import velocity_model


#============================================
def test_hermite_interpolation_preserves_endpoints() -> None:
	"""A cubic segment always passes through its two human seed values."""
	assert velocity_model.hermite_interpolate(0.0, 10.0, 30.0, 2.0, 4.0) == 10.0
	assert velocity_model.hermite_interpolate(1.0, 10.0, 30.0, 2.0, 4.0) == 30.0


#============================================
def test_interval_prediction_is_linear_between_its_two_seed_boxes() -> None:
	"""The analytical fallback uses only endpoint torso-box geometry."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(11, dtype=numpy.float32),
		dy=numpy.zeros(11, dtype=numpy.float32),
		scale=numpy.ones(11, dtype=numpy.float32),
		quality=numpy.ones(11, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	left = {"frame_index": 0, "cx": 10.0, "cy": 20.0, "w": 20.0, "h": 40.0}
	right = {"frame_index": 10, "cx": 30.0, "cy": 40.0, "w": 40.0, "h": 80.0}
	curves = velocity_model.fit_interval_curves(left, right, transform)
	forward = velocity_model.propagate_forward_analytical(curves, transform)
	backward = velocity_model.propagate_backward_analytical(curves, transform)
	assert forward[0]["cx"] == 10.0
	assert forward[-1]["cx"] == 30.0
	assert forward[5]["cx"] == 20.0
	assert forward[5]["cy"] == 30.0
	assert numpy.isclose(forward[5]["w"], numpy.sqrt(20.0 * 40.0))
	assert backward[5]["cx"] == forward[5]["cx"]
	assert forward[2]["conf"] > backward[2]["conf"]
