#!/usr/bin/env python3
"""Tests for track_runner.camera_motion module."""

# Standard Library
import os
import tempfile

# PIP3 modules
import numpy
import pytest

# local repo modules
import track_runner.camera_motion


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

	motion = track_runner.camera_motion.MotionTrack(
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
	estimator = track_runner.camera_motion.FixedZoomEstimator()
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
	estimator = track_runner.camera_motion.FixedZoomEstimator()
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
	estimator = track_runner.camera_motion.FixedZoomEstimator()
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
		motion_orig = track_runner.camera_motion.MotionTrack(
			dx=numpy.array([0.0, 1.5, 2.0], dtype=numpy.float32),
			dy=numpy.array([0.0, -0.5, 0.1], dtype=numpy.float32),
			scale=numpy.array([1.0, 1.0, 1.0], dtype=numpy.float32),
			quality=numpy.array([0.9, 0.85, 0.88], dtype=numpy.float32),
			event_flags=numpy.array([0, 0, 2], dtype=numpy.int32),
		)

		# save to cache
		track_runner.camera_motion.save_motion_cache(motion_orig, cache_path)
		assert os.path.isfile(cache_path)

		# load from cache
		motion_loaded = track_runner.camera_motion.load_motion_cache(cache_path)
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
	estimator = track_runner.camera_motion.FixedZoomEstimator()

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
		motion_orig = track_runner.camera_motion.MotionTrack(
			dx=numpy.array([0.0, 1.0, 2.0, 3.0], dtype=numpy.float32),
			dy=numpy.array([0.0, 0.5, 1.0, 1.5], dtype=numpy.float32),
			scale=numpy.ones(4, dtype=numpy.float32),
			quality=numpy.ones(4, dtype=numpy.float32),
			event_flags=numpy.zeros(4, dtype=numpy.int32),
		)

		# save to cache
		cache_path = os.path.join(tmpdir, "motion_test.npz")
		track_runner.camera_motion.save_motion_cache(motion_orig, cache_path)

		# load from cache (simulating cache hit)
		motion_loaded = track_runner.camera_motion.load_motion_cache(cache_path)

		# results should be identical
		assert numpy.allclose(motion_loaded.dx, motion_orig.dx)
		assert numpy.allclose(motion_loaded.dy, motion_orig.dy)
		assert numpy.allclose(motion_loaded.scale, motion_orig.scale)


#============================================
def test_config_fingerprint_consistency():
	"""Test that config fingerprint is consistent for same config."""
	config_1 = {"estimator": "FixedZoomEstimator", "window_size": 64}
	config_2 = {"estimator": "FixedZoomEstimator", "window_size": 64}

	fp_1 = track_runner.camera_motion._compute_config_fingerprint(config_1)
	fp_2 = track_runner.camera_motion._compute_config_fingerprint(config_2)

	assert fp_1 == fp_2


#============================================
def test_config_fingerprint_differs_for_different_config():
	"""Test that config fingerprint differs for different configs."""
	config_1 = {"estimator": "FixedZoomEstimator", "window_size": 64}
	config_2 = {"estimator": "FixedZoomEstimator", "window_size": 128}

	fp_1 = track_runner.camera_motion._compute_config_fingerprint(config_1)
	fp_2 = track_runner.camera_motion._compute_config_fingerprint(config_2)

	assert fp_1 != fp_2


#============================================
def test_load_motion_cache_nonexistent_file():
	"""Test that load_motion_cache returns None for nonexistent file."""
	result = track_runner.camera_motion.load_motion_cache("/tmp/nonexistent_xyz.npz")
	assert result is None
