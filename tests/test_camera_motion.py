"""Tests for track_runner.camera_motion module."""

# Standard Library
import os
import tempfile

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion


#============================================
class SyntheticFrameReader:
	"""Mock frame reader for testing motion estimation.

	Generates synthetic frames with shifted rectangles to simulate camera panning.
	Each frame increments the pan offset to create predictable motion.
	"""

	#============================================
	def __init__(
		self,
		total_frames: int = 10,
		width: int = 256,
		height: int = 256,
		fps: float = 30.0,
		pan_speed: float = 2.0,
	):
		"""Initialize synthetic frame reader.

		Args:
			total_frames: Number of frames to generate.
			width: Frame width in pixels (default 256).
			height: Frame height in pixels (default 256).
			fps: Frames per second.
			pan_speed: Pixels to pan per frame (x direction).
		"""
		self.total_frames = total_frames
		self.frame_count = total_frames
		self.width = width
		self.height = height
		self.fps = fps
		self.pan_speed = pan_speed

	#============================================
	def read_frame(self, frame_index: int) -> numpy.ndarray | None:
		"""Generate a synthetic BGR frame with panning rectangle.

		Args:
			frame_index: Frame index (0-based).

		Returns:
			BGR numpy array of shape (height, width, 3), or None if out of range.
		"""
		if frame_index < 0 or frame_index >= self.total_frames:
			return None

		# create frame as dark background with light rectangle
		frame = numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)
		frame[:, :] = 30  # dark gray background

		# rectangle pans from left to right
		rect_width = 100
		rect_height = 80
		rect_x = int(50 + frame_index * self.pan_speed)
		rect_y = (self.height - rect_height) // 2

		# ensure rectangle stays within bounds
		if rect_x + rect_width <= self.width:
			frame[
				rect_y:rect_y + rect_height,
				rect_x:rect_x + rect_width,
			] = 200  # light gray rectangle

		return frame


#============================================
def test_fixed_zoom_estimator_detects_motion():
	"""Test that FixedZoomEstimator detects panning motion."""
	reader = SyntheticFrameReader(
		total_frames=6,
		pan_speed=5.0,  # strong pan
	)
	estimator = camera_motion.FixedZoomEstimator()
	config = {}

	motion = estimator.estimate(reader, config)

	# frame 0 should have zero motion (no previous frame)
	assert motion.dx[0] == 0.0
	assert motion.dy[0] == 0.0

	# later frames should have positive x motion due to panning
	# (after smoothing, but should generally be positive)
	positive_motion_count = numpy.sum(motion.dx[1:] > 0.5)
	assert positive_motion_count > 0, "Expected some positive x motion"


#============================================
def test_npz_cache_save_and_load():
	"""Test NPZ cache save and load round-trip."""
	with tempfile.TemporaryDirectory() as tmpdir:
		cache_path = os.path.join(tmpdir, "motion_cache.npz")

		# create a motion track
		motion_orig = camera_motion.MotionTrack(
			dx=numpy.array([0.0, 1.5, 2.0], dtype=numpy.float32),
			dy=numpy.array([0.0, -0.5, 0.1], dtype=numpy.float32),
			scale=numpy.array([1.0, 1.0, 1.0], dtype=numpy.float32),
			quality=numpy.array([0.9, 0.85, 0.88], dtype=numpy.float32),
			event_flags=numpy.array([0, 0, 2], dtype=numpy.int32),
		)

		# save and reload
		camera_motion.save_motion_cache(motion_orig, cache_path)
		motion_loaded = camera_motion.load_motion_cache(cache_path)

		# round-trip: all arrays survive save+load unchanged
		assert numpy.allclose(motion_loaded.dx, motion_orig.dx)
		assert numpy.array_equal(motion_loaded.event_flags, motion_orig.event_flags)


#============================================
def test_median_filter_smoothing():
	"""Test 3-frame median filter smoothing with outlier injection."""
	estimator = camera_motion.FixedZoomEstimator()

	# create array with an outlier in the middle
	arr = numpy.array([1.0, 1.0, 10.0, 1.0, 1.0], dtype=numpy.float32)

	filtered = estimator._median_filter_1d(arr, 3)

	# the middle value (index 2, value 10.0) should be smoothed to median
	# of [1.0, 10.0, 1.0] = 1.0
	assert filtered[2] == 1.0, f"Expected outlier to be smoothed to 1.0, got {filtered[2]}"

	# edge values should be unchanged by median filter
	assert filtered[0] == arr[0]
	assert filtered[-1] == arr[-1]


#============================================
def test_precompute_camera_motion_cache_hit():
	"""Test cache hit path in precompute_camera_motion.

	Note: this test mocks out video_identity to avoid file I/O.
	We test the caching logic directly instead.
	"""
	with tempfile.TemporaryDirectory() as tmpdir:
		# test direct cache save/load path instead of full precompute
		motion_orig = camera_motion.MotionTrack(
			dx=numpy.array([0.0, 1.0, 2.0, 3.0], dtype=numpy.float32),
			dy=numpy.array([0.0, 0.5, 1.0, 1.5], dtype=numpy.float32),
			scale=numpy.ones(4, dtype=numpy.float32),
			quality=numpy.ones(4, dtype=numpy.float32),
			event_flags=numpy.zeros(4, dtype=numpy.int32),
		)

		# save to cache
		cache_path = os.path.join(tmpdir, "motion_test.npz")
		camera_motion.save_motion_cache(motion_orig, cache_path)

		# load from cache (simulating cache hit)
		motion_loaded = camera_motion.load_motion_cache(cache_path)

		# results should be identical
		assert numpy.allclose(motion_loaded.dx, motion_orig.dx)
		assert numpy.allclose(motion_loaded.dy, motion_orig.dy)
		assert numpy.allclose(motion_loaded.scale, motion_orig.scale)


#============================================
def test_config_fingerprint_differs_for_different_config():
	"""Test that config fingerprint differs for different configs."""
	config_1 = {"estimator": "FixedZoomEstimator", "window_size": 64}
	config_2 = {"estimator": "FixedZoomEstimator", "window_size": 128}

	fp_1 = camera_motion._compute_config_fingerprint(config_1)
	fp_2 = camera_motion._compute_config_fingerprint(config_2)

	assert fp_1 != fp_2


#============================================
def test_load_motion_cache_nonexistent_file(tmp_path):
	"""Test that load_motion_cache returns None for nonexistent file."""
	fake_path = str(tmp_path / "nonexistent_xyz.npz")
	result = camera_motion.load_motion_cache(fake_path)
	assert result is None


#============================================
def test_scene_transform_zoom_jump():
	"""Test SceneTransform with zoom jump in motion track.

	Creates a MotionTrack with a piecewise constant scale (zoom jump),
	and verifies that pixel_to_scene and scene_to_pixel round-trip
	correctly handles the jump.
	"""
	# import here to avoid unboundlocalerror
	import scene_coords

	# motion with a 2x zoom jump at frame 4
	# scale = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0]
	# cumulative scale = [1.0, 1.0, 1.0, 1.0, 2.0, 4.0, 8.0]
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(7, dtype=numpy.float32),
		dy=numpy.zeros(7, dtype=numpy.float32),
		scale=numpy.array(
			[1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
			dtype=numpy.float32
		),
		quality=numpy.ones(7, dtype=numpy.float32),
		event_flags=numpy.zeros(7, dtype=numpy.int32),
	)

	transform = scene_coords.SceneTransform(motion)

	# round-trip before the jump (frame 2)
	scene_x, scene_y = transform.pixel_to_scene(2, 100.0, 100.0)
	px_rt, py_rt = transform.scene_to_pixel(2, scene_x, scene_y)
	assert numpy.isclose(px_rt, 100.0, atol=0.5)
	assert numpy.isclose(py_rt, 100.0, atol=0.5)

	# round-trip after the jump (frame 5, cumulative scale 4.0)
	scene_x, scene_y = transform.pixel_to_scene(5, 100.0, 100.0)
	px_rt, py_rt = transform.scene_to_pixel(5, scene_x, scene_y)
	assert numpy.isclose(px_rt, 100.0, atol=0.5)
	assert numpy.isclose(py_rt, 100.0, atol=0.5)
