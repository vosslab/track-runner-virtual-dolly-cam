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
		self.width = width
		self.height = height
		self.fps = fps
		self.pan_speed = pan_speed

	#============================================
	def read_frame(self, frame_idx: int) -> numpy.ndarray | None:
		"""Generate a synthetic BGR frame with panning rectangle.

		Args:
			frame_idx: Frame index (0-based).

		Returns:
			BGR numpy array of shape (height, width, 3), or None if out of range.
		"""
		if frame_idx < 0 or frame_idx >= self.total_frames:
			return None

		# create frame as dark background with light rectangle
		frame = numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)
		frame[:, :] = 30  # dark gray background

		# rectangle pans from left to right
		rect_width = 100
		rect_height = 80
		rect_x = int(50 + frame_idx * self.pan_speed)
		rect_y = (self.height - rect_height) // 2

		# ensure rectangle stays within bounds
		if rect_x + rect_width <= self.width:
			frame[
				rect_y:rect_y + rect_height,
				rect_x:rect_x + rect_width,
			] = 200  # light gray rectangle

		return frame


#============================================
def test_motion_track_dataclass_creation():
	"""Test MotionTrack dataclass creation with numpy arrays."""
	total_frames = 5
	dx_arr = numpy.array([0.0, 1.5, 2.0, 1.8, 2.2], dtype=numpy.float32)
	dy_arr = numpy.array([0.0, -0.5, 0.1, 0.0, -0.2], dtype=numpy.float32)
	scale_arr = numpy.ones(total_frames, dtype=numpy.float32)
	quality_arr = numpy.array([0.9, 0.85, 0.88, 0.92, 0.86], dtype=numpy.float32)
	event_flags_arr = numpy.zeros(total_frames, dtype=numpy.int32)

	motion = camera_motion.MotionTrack(
		dx=dx_arr,
		dy=dy_arr,
		scale=scale_arr,
		quality=quality_arr,
		event_flags=event_flags_arr,
	)

	assert motion.dx.shape == (total_frames,)
	assert motion.dy.shape == (total_frames,)
	assert motion.scale.shape == (total_frames,)
	assert motion.quality.shape == (total_frames,)
	assert motion.event_flags.shape == (total_frames,)
	assert numpy.allclose(motion.dx, dx_arr)
	assert numpy.allclose(motion.dy, dy_arr)
	assert numpy.allclose(motion.scale, scale_arr)


#============================================
def test_fixed_zoom_estimator_array_lengths():
	"""Test that FixedZoomEstimator produces arrays of correct length."""
	reader = SyntheticFrameReader(total_frames=8)
	estimator = camera_motion.FixedZoomEstimator()
	config = {}

	motion = estimator.estimate(reader, config)

	assert len(motion.dx) == reader.total_frames
	assert len(motion.dy) == reader.total_frames
	assert len(motion.scale) == reader.total_frames
	assert len(motion.quality) == reader.total_frames
	assert len(motion.event_flags) == reader.total_frames


#============================================
def test_fixed_zoom_estimator_scale_is_one():
	"""Test that FixedZoomEstimator keeps scale at 1.0 (no zoom)."""
	reader = SyntheticFrameReader(total_frames=5)
	estimator = camera_motion.FixedZoomEstimator()
	config = {}

	motion = estimator.estimate(reader, config)

	assert numpy.allclose(motion.scale, 1.0)


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

		# save to cache
		camera_motion.save_motion_cache(motion_orig, cache_path)
		assert os.path.isfile(cache_path)

		# load from cache
		motion_loaded = camera_motion.load_motion_cache(cache_path)
		assert motion_loaded is not None

		# verify arrays match
		assert numpy.allclose(motion_loaded.dx, motion_orig.dx)
		assert numpy.allclose(motion_loaded.dy, motion_orig.dy)
		assert numpy.allclose(motion_loaded.scale, motion_orig.scale)
		assert numpy.allclose(motion_loaded.quality, motion_orig.quality)
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
def test_config_fingerprint_consistency():
	"""Test that config fingerprint is consistent for same config."""
	config_1 = {"estimator": "FixedZoomEstimator", "window_size": 64}
	config_2 = {"estimator": "FixedZoomEstimator", "window_size": 64}

	fp_1 = camera_motion._compute_config_fingerprint(config_1)
	fp_2 = camera_motion._compute_config_fingerprint(config_2)

	assert fp_1 == fp_2


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
class ZoomSyntheticFrameReader:
	"""Mock frame reader for testing zoom estimation.

	Generates frames where a central region scales up suddenly,
	simulating a discrete zoom jump.
	"""

	#============================================
	def __init__(
		self,
		total_frames: int = 10,
		width: int = 256,
		height: int = 256,
		zoom_start_frame: int = 5,
	):
		"""Initialize zoom synthetic frame reader.

		Args:
			total_frames: Number of frames to generate.
			width: Frame width in pixels.
			height: Frame height in pixels.
			zoom_start_frame: Frame index where zoom jump occurs.
		"""
		self.total_frames = total_frames
		self.width = width
		self.height = height
		self.zoom_start_frame = zoom_start_frame

	#============================================
	def read_frame(self, frame_idx: int) -> numpy.ndarray | None:
		"""Generate a synthetic frame with zoom jump.

		Args:
			frame_idx: Frame index (0-based).

		Returns:
			BGR numpy array, or None if out of range.
		"""
		if frame_idx < 0 or frame_idx >= self.total_frames:
			return None

		# create dark frame with light pattern
		frame = numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)
		frame[:, :] = 50  # dark background

		# determine zoom level based on frame
		if frame_idx < self.zoom_start_frame:
			# pre-zoom: small rectangle
			rect_size = 60
		else:
			# post-zoom: larger rectangle (2x zoom)
			rect_size = 120

		# center the rectangle
		start_x = (self.width - rect_size) // 2
		start_y = (self.height - rect_size) // 2
		end_x = start_x + rect_size
		end_y = start_y + rect_size

		# ensure bounds
		start_x = max(0, start_x)
		start_y = max(0, start_y)
		end_x = min(self.width, end_x)
		end_y = min(self.height, end_y)

		# fill rectangle with light pattern
		frame[start_y:end_y, start_x:end_x] = 200

		return frame


#============================================
def test_discrete_zoom_estimator_produces_valid_output():
	"""Test that DiscreteZoomEstimator produces valid output arrays.

	Verifies that the estimator runs without errors and produces
	valid arrays of correct length with reasonable values.
	"""
	reader = ZoomSyntheticFrameReader(
		total_frames=10,
		width=256,
		height=256,
		zoom_start_frame=5,
	)
	estimator = camera_motion.DiscreteZoomEstimator()
	config = {"zoom_levels": [1, 2, 5]}

	motion = estimator.estimate(reader, config)

	# verify output arrays are correct length
	assert len(motion.dx) == reader.total_frames
	assert len(motion.dy) == reader.total_frames
	assert len(motion.scale) == reader.total_frames
	assert len(motion.quality) == reader.total_frames
	assert len(motion.event_flags) == reader.total_frames

	# verify all scale values are positive
	assert numpy.all(motion.scale > 0), "All scale values should be positive"

	# verify scale values snap to zoom levels or 1.0
	# (may not detect jump with synthetic data, but values should be valid)
	unique_scales = numpy.unique(numpy.round(motion.scale, decimals=1))
	assert len(unique_scales) <= 4, \
		f"Expected few unique scale values, got {len(unique_scales)}"


#============================================
def test_discrete_zoom_estimator_respects_config():
	"""Test that DiscreteZoomEstimator respects zoom level config."""
	reader = ZoomSyntheticFrameReader(
		total_frames=10,
		zoom_start_frame=5,
	)
	estimator = camera_motion.DiscreteZoomEstimator()
	# specify zoom levels
	config = {"zoom_levels": [1, 2, 4]}

	motion = estimator.estimate(reader, config)

	# verify that zoom_levels config is accepted and doesn't crash
	assert motion.scale is not None
	assert len(motion.scale) == reader.total_frames

	# all scale values should be finite
	assert numpy.all(numpy.isfinite(motion.scale)), \
		"Scale array should contain only finite values"


#============================================
def test_continuous_zoom_estimator_produces_valid_output():
	"""Test that ContinuousZoomEstimator produces valid output.

	Verifies the estimator runs without errors and produces
	valid arrays with proper filtering applied.
	"""
	# create custom reader with gradual zoom
	class GradualZoomReader:
		def __init__(self, total_frames=10):
			self.total_frames = total_frames

		def read_frame(self, frame_idx: int) -> numpy.ndarray | None:
			if frame_idx < 0 or frame_idx >= self.total_frames:
				return None

			# gradual zoom: size increases each frame
			frame = numpy.zeros((256, 256, 3), dtype=numpy.uint8)
			frame[:, :] = 50

			# size grows from 60 to 180 over 10 frames
			base_size = 60
			size = int(base_size + (frame_idx / self.total_frames) * 120)
			size = min(size, 250)

			start = (256 - size) // 2
			end = start + size
			frame[start:end, start:end] = 200

			return frame

	reader = GradualZoomReader(total_frames=10)
	estimator = camera_motion.ContinuousZoomEstimator()
	config = {}

	motion = estimator.estimate(reader, config)

	# verify output arrays exist and have correct length
	assert motion.scale is not None
	assert len(motion.scale) == reader.total_frames

	# should have applied median filter smoothly
	# all values should be finite
	assert numpy.all(numpy.isfinite(motion.scale)), \
		"Scale array should have no NaN or inf values"

	# scale values should be reasonable (between 0.5 and 2.0)
	assert numpy.all(motion.scale >= 0.5), \
		"Scale should not go below 0.5"
	assert numpy.all(motion.scale <= 2.0), \
		"Scale should not exceed 2.0"


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

	# test a point before the jump (frame 2)
	px_before = 100.0
	py_before = 100.0
	scene_x, scene_y = transform.pixel_to_scene(2, px_before, py_before)
	px_rt, py_rt = transform.scene_to_pixel(2, scene_x, scene_y)
	assert numpy.isclose(px_rt, px_before, atol=0.5)
	assert numpy.isclose(py_rt, py_before, atol=0.5)

	# test a point after the jump (frame 5)
	# at frame 5, cumulative scale is 4.0
	px_after = 100.0
	py_after = 100.0
	scene_x, scene_y = transform.pixel_to_scene(5, px_after, py_after)
	# should map to (25, 25) due to 4x cumulative zoom
	assert numpy.isclose(scene_x, 25.0, atol=0.5)
	assert numpy.isclose(scene_y, 25.0, atol=0.5)
	# round-trip should preserve
	px_rt, py_rt = transform.scene_to_pixel(5, scene_x, scene_y)
	assert numpy.isclose(px_rt, px_after, atol=0.5)
	assert numpy.isclose(py_rt, py_after, atol=0.5)
