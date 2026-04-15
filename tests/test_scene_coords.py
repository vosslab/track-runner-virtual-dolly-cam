"""Tests for track_runner.scene_coords module."""

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import scene_coords


#============================================
def test_identity_transform_pixel_equals_scene():
	"""Test that with identity transform, pixel coords equal scene coords."""
	# identity motion: no translation, scale = 1.0
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# identity: scene coords equal pixel coords at any frame
	scene_x, scene_y = transform.pixel_to_scene(4, 500.5, 300.3)
	assert numpy.isclose(scene_x, 500.5)
	assert numpy.isclose(scene_y, 300.3)


#============================================
def test_pure_translation_removes_accumulated_offset():
	"""Test that pixel_to_scene correctly removes accumulated translation."""
	# pure translation: dx = [0, 10, 10, 10, 10], dy = [0, 5, 5, 5, 5]
	# cumulative: dx_cum = [0, 10, 20, 30, 40], dy_cum = [0, 5, 10, 15, 20]
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 10.0, 10.0, 10.0, 10.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 5.0, 5.0, 5.0, 5.0], dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# at frame 3, cumulative translation is (30, 15)
	# a pixel at (100, 100) should map to scene (70, 85)
	scene_x, scene_y = transform.pixel_to_scene(3, 100.0, 100.0)
	assert numpy.isclose(scene_x, 70.0)
	assert numpy.isclose(scene_y, 85.0)


#============================================
def test_round_trip_pixel_to_scene_to_pixel():
	"""Test that pixel->scene->pixel round-trip returns original coords."""
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 5.0, 10.0, 15.0, 20.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 3.0, 6.0, 9.0, 12.0], dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# round-trip at a mid frame with accumulated motion
	scene_x, scene_y = transform.pixel_to_scene(4, 100.0, 200.0)
	px_final, py_final = transform.scene_to_pixel(4, scene_x, scene_y)
	assert numpy.isclose(px_final, 100.0, atol=0.5)
	assert numpy.isclose(py_final, 200.0, atol=0.5)


#============================================
def test_pixel_box_to_scene_round_trip():
	"""Test that pixel_box_to_scene->scene_box_to_pixel round-trip works."""
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 8.0, 16.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 4.0, 8.0], dtype=numpy.float32),
		scale=numpy.ones(3, dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
		event_flags=numpy.zeros(3, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# test box at frame 1: center (100, 100), size (50, 60)
	frame_index = 1
	cx_orig, cy_orig = 100.0, 100.0
	w_orig, h_orig = 50.0, 60.0

	# convert to scene and back
	scene_cx, scene_cy, scene_w, scene_h = transform.pixel_box_to_scene(
		frame_index, cx_orig, cy_orig, w_orig, h_orig
	)
	px_final, py_final, w_final, h_final = transform.scene_box_to_pixel(
		frame_index, scene_cx, scene_cy, scene_w, scene_h
	)

	# should match original within tolerance
	assert numpy.isclose(px_final, cx_orig, atol=0.5)
	assert numpy.isclose(py_final, cy_orig, atol=0.5)
	assert numpy.isclose(w_final, w_orig, atol=0.5)
	assert numpy.isclose(h_final, h_orig, atol=0.5)


#============================================
def test_scale_factor_affects_coordinates():
	"""Test that scale != 1.0 correctly transforms coordinates and sizes."""
	# motion with scale factors: [1.0, 2.0, 2.0, 2.0]
	# cumulative scale: [1.0, 2.0, 4.0, 8.0]
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 0.0, 0.0, 0.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 0.0, 0.0, 0.0], dtype=numpy.float32),
		scale=numpy.array([1.0, 2.0, 2.0, 2.0], dtype=numpy.float32),
		quality=numpy.ones(4, dtype=numpy.float32),
		event_flags=numpy.zeros(4, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# at frame 2, cumulative scale is 4.0
	# a pixel at (100, 100) should map to scene (25, 25)
	scene_x, scene_y = transform.pixel_to_scene(2, 100.0, 100.0)
	assert numpy.isclose(scene_x, 25.0)
	assert numpy.isclose(scene_y, 25.0)

	# scene (25, 25) should map back to pixel (100, 100)
	px, py = transform.scene_to_pixel(2, scene_x, scene_y)
	assert numpy.isclose(px, 100.0)
	assert numpy.isclose(py, 100.0)


#============================================
def test_scale_factor_affects_box_size():
	"""Test that scale correctly transforms bounding box sizes."""
	# motion with cumulative scale [1.0, 1.0, 2.0, 2.0]
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 0.0, 0.0, 0.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 0.0, 0.0, 0.0], dtype=numpy.float32),
		scale=numpy.array([1.0, 1.0, 2.0, 2.0], dtype=numpy.float32),
		quality=numpy.ones(4, dtype=numpy.float32),
		event_flags=numpy.zeros(4, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# at frame 2, cumulative scale is 2.0
	# pixel box (100, 100, 80, 60) should map to scene (100, 100, 40, 30)
	scene_cx, scene_cy, scene_w, scene_h = transform.pixel_box_to_scene(
		2, 100.0, 100.0, 80.0, 60.0
	)
	assert numpy.isclose(scene_w, 40.0), \
		f"Expected scene_w=40.0, got {scene_w}"
	assert numpy.isclose(scene_h, 30.0), \
		f"Expected scene_h=30.0, got {scene_h}"

	# scene (100, 100, 40, 30) at frame 2 should map back to pixel (100, 100, 80, 60)
	px, py, w, h = transform.scene_box_to_pixel(
		2, scene_cx, scene_cy, scene_w, scene_h
	)
	assert numpy.isclose(w, 80.0)
	assert numpy.isclose(h, 60.0)


#============================================
def test_combined_translation_and_scale():
	"""Test that translation and scale work together correctly."""
	# motion: dx=[0, 10, 10], dy=[0, 5, 5], scale=[1.0, 1.5, 1.5]
	# cumulative: dx_cum=[0, 10, 20], dy_cum=[0, 5, 10], scale_cum=[1.0, 1.5, 2.25]
	motion = camera_motion.MotionTrack(
		dx=numpy.array([0.0, 10.0, 10.0], dtype=numpy.float32),
		dy=numpy.array([0.0, 5.0, 5.0], dtype=numpy.float32),
		scale=numpy.array([1.0, 1.5, 1.5], dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
		event_flags=numpy.zeros(3, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# at frame 2: cum_dx=20, cum_dy=10, cum_scale=2.25
	# pixel (100, 100) -> scene ((100-20)/2.25, (100-10)/2.25) = (35.56, 40.0)
	scene_x, scene_y = transform.pixel_to_scene(2, 100.0, 100.0)
	assert numpy.isclose(scene_x, (100.0 - 20.0) / 2.25, atol=0.1)
	assert numpy.isclose(scene_y, (100.0 - 10.0) / 2.25, atol=0.1)

	# round-trip should preserve
	px_rt, py_rt = transform.scene_to_pixel(2, scene_x, scene_y)
	assert numpy.isclose(px_rt, 100.0, atol=0.5)
	assert numpy.isclose(py_rt, 100.0, atol=0.5)


#============================================
def test_scene_coords_at_frame_zero():
	"""Test that frame 0 has no cumulative motion applied."""
	motion = camera_motion.MotionTrack(
		dx=numpy.array([5.0, 10.0, 15.0], dtype=numpy.float32),
		dy=numpy.array([3.0, 6.0, 9.0], dtype=numpy.float32),
		scale=numpy.array([2.0, 2.0, 2.0], dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
		event_flags=numpy.zeros(3, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# at frame 0, cumulative motion should be zero
	# (because cumulative sum starts with the first element)
	# note: cumsum([5, 10, 15]) = [5, 15, 30]
	# so cum_dx[0] = 5, cum_dy[0] = 3, cum_scale[0] = 2.0
	# pixel (100, 100) -> scene ((100-5)/2, (100-3)/2) = (47.5, 48.5)
	scene_x, scene_y = transform.pixel_to_scene(0, 100.0, 100.0)
	assert numpy.isclose(scene_x, (100.0 - 5.0) / 2.0)
	assert numpy.isclose(scene_y, (100.0 - 3.0) / 2.0)


#============================================
def test_piecewise_scale_zoom_jump():
	"""Test SceneTransform correctly handles piecewise constant scale.

	Tests the scenario where scale array has a jump (e.g., 1.0 -> 2.0),
	which should be handled correctly by cumprod.
	"""
	# scale with jump: [1.0, 1.0, 1.0, 2.0, 2.0, 2.0]
	# cumulative scale: [1.0, 1.0, 1.0, 2.0, 4.0, 8.0]
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(6, dtype=numpy.float32),
		dy=numpy.zeros(6, dtype=numpy.float32),
		scale=numpy.array(
			[1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
			dtype=numpy.float32
		),
		quality=numpy.ones(6, dtype=numpy.float32),
		event_flags=numpy.zeros(6, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	# before jump (frame 2): cumulative scale = 1.0, pixel == scene
	scene_x, _ = transform.pixel_to_scene(2, 100.0, 100.0)
	assert numpy.isclose(scene_x, 100.0)

	# after jump (frame 4): cumulative scale = 4.0, pixel 100 -> scene 25
	scene_x, scene_y = transform.pixel_to_scene(4, 100.0, 100.0)
	assert numpy.isclose(scene_x, 25.0)
	# round-trip preserves original pixel
	px_rt, py_rt = transform.scene_to_pixel(4, scene_x, scene_y)
	assert numpy.isclose(px_rt, 100.0, atol=0.5)
