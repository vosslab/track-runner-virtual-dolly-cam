"""Small behavioral checks for pair-local analytical interpolation."""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules
import velocity_model


#============================================
class _IdentityTransform:
	"""Minimal SOURCE/scene identity transform for interpolation tests."""

	def pixel_box_to_scene(
		self, _frame_index: int, cx: float, cy: float, width: float, height: float,
	) -> tuple[float, float, float, float]:
		return cx, cy, width, height

	def scene_box_to_pixel(
		self, _frame_index: int, cx: float, cy: float, width: float, height: float,
	) -> tuple[float, float, float, float]:
		return cx, cy, width, height


#============================================
def test_linear_interval_prediction_preserves_endpoints() -> None:
	"""The analytical fallback uses only endpoint torso-box geometry."""
	transform = _IdentityTransform()
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


#============================================
def test_small_dimensions_keep_exact_endpoints_without_crashing() -> None:
	"""Lower-bound dimensions retain raw endpoints and finite interior output."""
	transform = _IdentityTransform()
	left = {"frame_index": 0, "cx": 10.0, "cy": 20.0, "w": 0.0, "h": 1e-6}
	right = {"frame_index": 2, "cx": 30.0, "cy": 40.0, "w": 1e-6, "h": 0.0}
	curves = velocity_model.fit_interval_curves(left, right, transform)
	forward = velocity_model.propagate_forward_analytical(curves, transform)
	backward = velocity_model.propagate_backward_analytical(curves, transform)
	for path in (forward, backward):
		assert path[0]["w"] == left["w"]
		assert path[0]["h"] == left["h"]
		assert path[-1]["w"] == right["w"]
		assert path[-1]["h"] == right["h"]
		assert path[1]["w"] == 1.0
		assert path[1]["h"] == 1.0
	assert forward[0]["conf"] == 1.0
	assert backward[-1]["conf"] == 1.0
	assert forward[1]["conf"] == backward[1]["conf"]


#============================================
def test_overlarge_dimensions_use_directional_interior_fallback() -> None:
	"""Overlarge logs keep raw endpoints and retain each pass's anchor fallback."""
	transform = _IdentityTransform()
	left_width, left_height = math.exp(100.0), math.exp(101.0)
	right_width, right_height = math.exp(101.0), math.exp(100.0)
	left = {
		"frame_index": 0, "cx": 10.0, "cy": 20.0,
		"w": left_width, "h": left_height,
	}
	right = {
		"frame_index": 2, "cx": 30.0, "cy": 40.0,
		"w": right_width, "h": right_height,
	}
	curves = velocity_model.fit_interval_curves(left, right, transform)
	forward = velocity_model.propagate_forward_analytical(curves, transform)
	backward = velocity_model.propagate_backward_analytical(curves, transform)
	for path in (forward, backward):
		assert path[0]["w"] == left_width
		assert path[0]["h"] == left_height
		assert path[-1]["w"] == right_width
		assert path[-1]["h"] == right_height
	assert forward[1]["w"] == left_width
	assert forward[1]["h"] == left_height
	assert backward[1]["w"] == right_width
	assert backward[1]["h"] == right_height
	assert forward[0]["conf"] == 1.0
	assert backward[-1]["conf"] == 1.0
	assert forward[1]["conf"] == backward[1]["conf"]
