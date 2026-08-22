"""Tests for track_runner.camera_motion module."""

# Standard Library
import json
import os
import pathlib
import tempfile

# PIP3 modules
import numpy
import pytest

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
	) -> None:
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
def test_fixed_zoom_estimator_detects_motion() -> None:
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
def _dummy_identity(frame_count: int = 3) -> dict:
	"""Return a source geometry identity for cache tests."""
	identity = {
		"width": 640,
		"height": 360,
		"frame_count": frame_count,
	}
	return identity


#============================================
def _make_motion_track(scale_values: list) -> "camera_motion.MotionTrack":
	"""Build a MotionTrack with matching-length dx/dy/quality arrays."""
	n = len(scale_values)
	return camera_motion.MotionTrack(
		dx=numpy.arange(n, dtype=numpy.float32),
		dy=numpy.zeros(n, dtype=numpy.float32),
		scale=numpy.array(scale_values, dtype=numpy.float32),
		quality=numpy.linspace(0.8, 0.95, n, dtype=numpy.float32),
	)


#============================================
def test_fixed_zoom_cache_round_trip(tmp_path: pathlib.Path) -> None:
	"""Round-trip invariant: dx/dy/quality preserved; scale synthesized."""
	cache_path = str(tmp_path / "motion.npz")
	motion_orig = _make_motion_track([1.0, 1.0, 1.0])
	camera_motion.save_motion_cache(
		motion_orig, cache_path,
		motion_model=camera_motion.MOTION_MODEL_FIXED,
		video_identity=_dummy_identity(),
	)
	loaded = camera_motion.load_motion_cache(cache_path)
	# round-trip preserves signal; fixed_zoom synthesizes scale=1 on load
	assert numpy.allclose(loaded.dx, motion_orig.dx)
	assert numpy.allclose(loaded.scale, 1.0)


#============================================
def test_discrete_zoom_cache_preserves_scale(tmp_path: pathlib.Path) -> None:
	"""discrete_zoom persists the scale array across a round-trip."""
	cache_path = str(tmp_path / "motion.npz")
	motion_orig = _make_motion_track([1.0, 2.0, 1.0])
	camera_motion.save_motion_cache(
		motion_orig, cache_path,
		motion_model=camera_motion.MOTION_MODEL_DISCRETE,
		video_identity=_dummy_identity(),
	)
	loaded = camera_motion.load_motion_cache(cache_path)
	assert numpy.allclose(loaded.scale, motion_orig.scale)


#============================================
def test_stale_motion_model_treated_as_miss(tmp_path: pathlib.Path) -> None:
	"""A mismatched motion_model makes load_motion_cache return None."""
	cache_path = str(tmp_path / "motion.npz")
	camera_motion.save_motion_cache(
		_make_motion_track([1.0, 1.0, 1.0]), cache_path,
		motion_model=camera_motion.MOTION_MODEL_FIXED,
		video_identity=_dummy_identity(),
	)
	# expected motion_model disagrees -> None
	assert camera_motion.load_motion_cache(
		cache_path, expected_motion_model=camera_motion.MOTION_MODEL_DISCRETE
	) is None
	# matching motion_model -> still loads
	assert camera_motion.load_motion_cache(
		cache_path, expected_motion_model=camera_motion.MOTION_MODEL_FIXED
	) is not None


#============================================
def test_motion_cache_recomputes_for_different_video_identity(
		tmp_path: pathlib.Path,
) -> None:
	"""A saved motion track is not reused for a different source video."""
	cache_path = str(tmp_path / "motion.npz")
	camera_motion.save_motion_cache(
		_make_motion_track([1.0, 1.0, 1.0]), cache_path,
		motion_model=camera_motion.MOTION_MODEL_FIXED,
		video_identity=_dummy_identity(),
	)
	different_video = _dummy_identity(frame_count=4)
	loaded = camera_motion.load_motion_cache(
		cache_path, expected_video_identity=different_video,
	)
	assert loaded is None


#============================================
def test_motion_cache_reuses_legacy_identity_with_matching_geometry(
		tmp_path: pathlib.Path,
) -> None:
	"""Retired cache metadata does not invalidate matching source geometry."""
	cache_path = str(tmp_path / "motion.npz")
	legacy_identity = {
		"basename": "runner.MOV",
		"size_bytes": 3_180_000_000,
		"width": 640,
		"height": 360,
		"fps": 119.94,
		"frame_count": 3,
		"duration_s": 3 / 119.94,
	}
	camera_motion.save_motion_cache(
		_make_motion_track([1.0, 1.0, 1.0]), cache_path,
		motion_model=camera_motion.MOTION_MODEL_FIXED,
		video_identity=legacy_identity,
	)
	loaded = camera_motion.load_motion_cache(
		cache_path, expected_video_identity=_dummy_identity(),
	)
	assert loaded is not None


#============================================
def test_loader_rejects_unknown_motion_model(tmp_path: pathlib.Path) -> None:
	"""Loader raises on unknown motion_model values."""
	cache_path = str(tmp_path / "motion.npz")
	numpy.savez(
		cache_path,
		motion_model=numpy.frombuffer(
			b"mystery_model", dtype=numpy.uint8
		),
		video_identity=numpy.frombuffer(
			json.dumps(_dummy_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		bin_factor=numpy.asarray(1, dtype=numpy.int64),
		frame_count=numpy.asarray(3, dtype=numpy.int64),
		dx=numpy.zeros(3, dtype=numpy.float32),
		dy=numpy.zeros(3, dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
	)
	with pytest.raises(RuntimeError, match="unknown motion_model"):
		camera_motion.load_motion_cache(cache_path)


#============================================
def test_loader_rejects_discrete_without_scale(tmp_path: pathlib.Path) -> None:
	"""discrete_zoom without a scale array raises on load."""
	cache_path = str(tmp_path / "motion.npz")
	numpy.savez(
		cache_path,
		motion_model=numpy.frombuffer(
			b"discrete_zoom", dtype=numpy.uint8
		),
		video_identity=numpy.frombuffer(
			json.dumps(_dummy_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		bin_factor=numpy.asarray(1, dtype=numpy.int64),
		frame_count=numpy.asarray(3, dtype=numpy.int64),
		dx=numpy.zeros(3, dtype=numpy.float32),
		dy=numpy.zeros(3, dtype=numpy.float32),
		quality=numpy.ones(3, dtype=numpy.float32),
	)
	with pytest.raises(RuntimeError, match="missing required array"):
		camera_motion.load_motion_cache(cache_path)


#============================================
def test_median_filter_smoothing() -> None:
	"""Test 3-frame median filter smoothing with outlier injection."""
	estimator = camera_motion.FixedZoomEstimator()

	# create array with an outlier in the middle
	arr = numpy.array([1.0, 1.0, 10.0, 1.0, 1.0], dtype=numpy.float32)

	filtered = estimator._median_filter_1d(arr, 3)

	# the middle value (index 2, value 10.0) should be smoothed to median
	# of [1.0, 10.0, 1.0] = 1.0
	assert filtered[2] < arr[2]

	# edge values should be unchanged by median filter
	assert filtered[0] == arr[0]
	assert filtered[-1] == arr[-1]


#============================================
def test_load_motion_cache_nonexistent_file(tmp_path: pathlib.Path) -> None:
	"""Test that load_motion_cache returns None for nonexistent file."""
	fake_path = str(tmp_path / "nonexistent_xyz.npz")
	result = camera_motion.load_motion_cache(fake_path)
	assert result is None


#============================================
def test_split_pair_range_covers_all_pairs() -> None:
	"""Every pair in [1, total_frames) is covered exactly once across chunks."""
	total = 50
	for n_chunks in (1, 2, 4, 7, 8):
		ranges = camera_motion._split_pair_range(total, n_chunks)
		# reconstruct the union of half-open ranges as a set of indices
		covered = set()
		for start, end in ranges:
			for idx in range(start, end):
				assert idx not in covered, "ranges overlap"
				covered.add(idx)
		expected = set(range(1, total))
		assert covered == expected


#============================================
def test_measure_pairs_chunked_matches_whole_range() -> None:
	"""Splitting a measurement range and concatenating reproduces the whole.

	Guards the chunking arithmetic that the parallel driver relies on.
	"""
	reader = SyntheticFrameReader(total_frames=24, pan_speed=3.0)
	whole = camera_motion._measure_pairs_in_range(
		reader, 1, 24, camera_motion.MOTION_MODEL_FIXED,
	)
	# split into 4 chunks; each call needs a fresh reader anchor
	# because read_frame() is stateful via _next_frame in real readers
	chunks = []
	for start, end in camera_motion._split_pair_range(24, 4):
		chunk = camera_motion._measure_pairs_in_range(
			SyntheticFrameReader(total_frames=24, pan_speed=3.0),
			start, end, camera_motion.MOTION_MODEL_FIXED,
		)
		chunks.append(chunk)
	merged_dx = numpy.concatenate([c[0] for c in chunks])
	merged_dy = numpy.concatenate([c[1] for c in chunks])
	merged_scale = numpy.concatenate([c[2] for c in chunks])
	merged_quality = numpy.concatenate([c[3] for c in chunks])
	# same OpenCV calls on identical synthetic frames must produce
	# bit-identical float32 output regardless of how the range was split
	assert numpy.array_equal(merged_dx, whole[0])
	assert numpy.array_equal(merged_dy, whole[1])
	assert numpy.array_equal(merged_scale, whole[2])
	assert numpy.array_equal(merged_quality, whole[3])


#============================================
def test_measure_pairs_continuous_chunked_matches_whole() -> None:
	"""Same chunking guarantee holds for the continuous-zoom log-polar path."""
	reader = SyntheticFrameReader(total_frames=20, pan_speed=2.0)
	whole = camera_motion._measure_pairs_in_range(
		reader, 1, 20, camera_motion.MOTION_MODEL_CONTINUOUS,
	)
	chunks = []
	for start, end in camera_motion._split_pair_range(20, 3):
		chunk = camera_motion._measure_pairs_in_range(
			SyntheticFrameReader(total_frames=20, pan_speed=2.0),
			start, end, camera_motion.MOTION_MODEL_CONTINUOUS,
		)
		chunks.append(chunk)
	for axis in range(4):
		merged = numpy.concatenate([c[axis] for c in chunks])
		assert numpy.array_equal(merged, whole[axis])


#============================================
def test_cache_round_trip_serial_to_new_loader(tmp_path: pathlib.Path) -> None:
	"""A camera_motion.npz written today loads intact via load_motion_cache.

	Guards the on-disk compatibility boundary independent of any
	estimator code path: serial-saved caches must keep working as the
	measurement pipeline evolves.
	"""
	cache_path = str(tmp_path / "motion.npz")
	original = _make_motion_track([1.0, 1.5, 2.0, 1.5, 1.0])
	camera_motion.save_motion_cache(
		original, cache_path,
		motion_model=camera_motion.MOTION_MODEL_DISCRETE,
		video_identity=_dummy_identity(frame_count=5),
	)
	loaded = camera_motion.load_motion_cache(
		cache_path, expected_motion_model=camera_motion.MOTION_MODEL_DISCRETE
	)
	assert loaded is not None
	assert numpy.array_equal(loaded.dx, original.dx)
	assert numpy.array_equal(loaded.dy, original.dy)
	assert numpy.array_equal(loaded.scale, original.scale)
	assert numpy.array_equal(loaded.quality, original.quality)


#============================================
def test_scene_transform_zoom_jump() -> None:
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


#============================================
def test_load_motion_cache_rejects_frame_count_array_mismatch(
	tmp_path: pathlib.Path,
) -> None:
	"""Verify load_motion_cache rejects frame_count vs array length mismatch.

	Tests the internal consistency check: if persisted frame_count
	disagrees with len(dx), raise RuntimeError with "frame_count" in message.
	"""
	cache_path = tmp_path / "test.camera_motion.npz"

	# Create motion arrays (100 frames)
	dx = numpy.arange(100, dtype=numpy.float32) * 0.5
	dy = numpy.ones(100, dtype=numpy.float32) * 0.1
	quality = numpy.ones(100, dtype=numpy.float32) * 0.9
	scale = numpy.ones(100, dtype=numpy.float32)

	# Write with corrupted frame_count (claim 50 but have 100)
	arrays = {
		"motion_model": numpy.frombuffer(
			camera_motion.MOTION_MODEL_CONTINUOUS.encode("utf-8"),
			dtype=numpy.uint8,
		),
		"video_identity": numpy.frombuffer(
			json.dumps(_dummy_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		"bin_factor": numpy.asarray(1, dtype=numpy.int64),
		"frame_count": numpy.asarray(50, dtype=numpy.int64),
		"dx": dx,
		"dy": dy,
		"scale": scale,
		"quality": quality,
	}

	# Write atomically
	dir_path = tmp_path
	fd, tmp_file = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_file, **arrays)
		real_tmp = tmp_file if tmp_file.endswith(".npz") else tmp_file + ".npz"
		os.replace(real_tmp, str(cache_path))
	except Exception:
		for candidate in (tmp_file, tmp_file + ".npz"):
			if os.path.exists(candidate):
				os.unlink(candidate)
		raise

	# Load should raise RuntimeError about frame_count
	with pytest.raises(RuntimeError) as exc_info:
		camera_motion.load_motion_cache(str(cache_path))

	error_msg = str(exc_info.value).lower()
	assert "frame_count" in error_msg
	assert "corrupt" in error_msg or "entries" in error_msg
