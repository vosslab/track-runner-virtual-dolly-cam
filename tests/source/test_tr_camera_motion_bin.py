"""Fast camera-motion cache tests for bin-aware artifact reuse."""

# Standard Library
import pathlib

import numpy

# local repo modules
import camera_motion


#============================================
def _write_fixed_motion_cache(cache_path: str, bin_factor: int) -> None:
	# Helper: write a 4-frame fixed-zoom camera-motion artifact at a given
	# bin_factor so the staleness tests below can vary only bin.
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(4, dtype=numpy.float32),
		dy=numpy.zeros(4, dtype=numpy.float32),
		scale=numpy.ones(4, dtype=numpy.float32),
		quality=numpy.ones(4, dtype=numpy.float32),
	)
	video_identity = {
		"basename": "video.mkv", "size_bytes": 100, "width": 640,
		"height": 360, "fps": 30.0, "frame_count": 4,
		"duration_s": 4 / 30.0,
	}
	camera_motion.save_motion_cache(
		motion, cache_path, camera_motion.MOTION_MODEL_FIXED, video_identity,
		bin_factor=bin_factor,
	)


#============================================
def test_camera_motion_cache_hits_for_same_bin(tmp_path: pathlib.Path) -> None:
	# Property: same motion_model AND same bin_factor must hit so an
	# unchanged-bin re-solve reuses the camera motion (no recompute).
	cache_path = str(tmp_path / "video.track_runner.camera_motion.npz")
	_write_fixed_motion_cache(cache_path, bin_factor=2)
	cached = camera_motion.load_motion_cache(
		cache_path,
		expected_motion_model=camera_motion.MOTION_MODEL_FIXED,
		expected_bin_factor=2,
	)
	assert cached is not None


#============================================
def test_bin_change_invalidates_camera_motion_cache(tmp_path: pathlib.Path) -> None:
	# Property: the phase-correlation estimator runs on PROCESSED frames and
	# upscales dx/dy to SOURCE, so the stored SOURCE track depends on bin.
	# A cache written at one bin must be treated as stale (None) for a load
	# at a different bin so the caller recomputes.
	cache_path = str(tmp_path / "video.track_runner.camera_motion.npz")
	_write_fixed_motion_cache(cache_path, bin_factor=1)
	cached = camera_motion.load_motion_cache(
		cache_path,
		expected_motion_model=camera_motion.MOTION_MODEL_FIXED,
		expected_bin_factor=2,
	)
	assert cached is None


#============================================
def test_cache_without_bin_factor_is_stale(tmp_path: pathlib.Path) -> None:
	"""A cache missing current resolution metadata must be recomputed."""
	cache_path = str(tmp_path / "video.track_runner.camera_motion.npz")
	# Write without the required current bin_factor field.
	_write_fixed_motion_cache(cache_path, bin_factor=1)
	with numpy.load(cache_path, allow_pickle=False) as npz:
		arrays = {k: npz[k] for k in npz.files if k != "bin_factor"}
	numpy.savez(cache_path, **arrays)
	cached = camera_motion.load_motion_cache(
		cache_path,
		expected_motion_model=camera_motion.MOTION_MODEL_FIXED,
		expected_bin_factor=1,
	)
	assert cached is None
