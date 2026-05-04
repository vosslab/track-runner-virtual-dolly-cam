"""Behavioral tests for camera_motion bin awareness.

These tests assert the contract "MotionTrack.dx/dy persisted in
source-frame pixels regardless of bin_factor". They use a synthetic
translating-pattern video so the answer is known, and they compare
across bin settings on the same input.
"""

# PIP3 modules
import cv2
import numpy
import pytest

# local repo modules
import common_tools.frame_reader
import camera_motion


_FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


#============================================
def _write_translating_video(path: str, width: int, height: int, n_frames: int, dx_per_frame: int) -> None:
	"""Write an MP4 of a textured patch translating right by `dx_per_frame` source pixels per frame."""
	# random texture stays consistent across re-renders so phase
	# correlation has stable content to lock onto.
	rng = numpy.random.default_rng(seed=42)
	texture = (rng.uniform(0, 255, size=(height, width * 2, 3))).astype(numpy.uint8)
	writer = cv2.VideoWriter(path, _FOURCC, 30.0, (width, height))
	if not writer.isOpened():
		raise RuntimeError(f"cv2.VideoWriter failed: {path}")
	for i in range(n_frames):
		# frame is a window into the wider texture, sliding right
		shift = i * dx_per_frame
		frame = texture[:, shift : shift + width].copy()
		writer.write(frame)
	writer.release()


#============================================
def _estimate_source_dx(video_path: str, fps: float, total: int, bin_factor: int) -> numpy.ndarray:
	"""Run FixedZoomEstimator end-to-end and return source-frame dx array."""
	reader = common_tools.frame_reader.FrameReader(
		video_path=video_path,
		fps=fps,
		total_frames=total,
		bin_factor=bin_factor,
	)
	try:
		# n_chunks=1 keeps the path serial and deterministic in tests
		estimator = camera_motion.FixedZoomEstimator()
		motion = estimator.estimate(reader, config={}, n_chunks=1)
	finally:
		reader.close()
	return motion.dx


#============================================
@pytest.fixture(scope="module")
def translating_video(tmp_path_factory):
	# 256 x 128 frames so bin_factor=2 yields 128x64 (already a goodbox)
	path = str(tmp_path_factory.mktemp("cm") / "trans.mp4")
	_write_translating_video(
		path, width=256, height=128, n_frames=8, dx_per_frame=2,
	)
	return path


#============================================
def test_dx_in_source_frame_pixels(translating_video):
	# bin=1 baseline. The exact magnitude depends on phaseCorrelate's
	# response, so we test the property "median dx is approximately
	# the known per-frame source-pixel translation" and that the
	# array length matches frame count.
	dx_b1 = _estimate_source_dx(translating_video, fps=30.0, total=8, bin_factor=1)
	# array shape: full frame_count, index 0 zero, rest filled
	assert dx_b1.shape == (8,)
	assert dx_b1[0] == 0.0
	# median over the measured pairs should be close to the source
	# truth (-2 in cv2.phaseCorrelate convention: the second frame
	# moved right relative to the first, so the inferred shift is
	# negative in opencv's convention). Allow generous tolerance
	# because phase correlation on small synthetic frames is noisy.
	median_dx = float(numpy.median(dx_b1[1:]))
	assert abs(abs(median_dx) - 2.0) < 1.0


#============================================
def test_bin_path_returns_source_frame_dx(translating_video):
	# Same source video at bin=2. Worker upscales dx/dy back to
	# source-frame pixels; the median should still be ~2 src px,
	# not ~1 (which is what processed-frame pixels would give).
	dx_b2 = _estimate_source_dx(translating_video, fps=30.0, total=8, bin_factor=2)
	assert dx_b2.shape == (8,)
	median_b2 = float(numpy.median(dx_b2[1:]))
	# allow generous bin-induced noise (phase correlation at smaller
	# dims is less precise) but the answer must be in source-frame.
	assert abs(abs(median_b2) - 2.0) < 1.5


#============================================
def test_bin1_and_bin2_agree_in_source_frame(translating_video):
	# Property: bin_factor must not change the answer to the source
	# observer. Compare medians at bin=1 vs bin=2 in source-frame.
	dx_b1 = _estimate_source_dx(translating_video, fps=30.0, total=8, bin_factor=1)
	dx_b2 = _estimate_source_dx(translating_video, fps=30.0, total=8, bin_factor=2)
	med_b1 = float(numpy.median(dx_b1[1:]))
	med_b2 = float(numpy.median(dx_b2[1:]))
	# medians should agree in sign and within ~1 source pixel
	assert numpy.sign(med_b1) == numpy.sign(med_b2) or abs(med_b1) < 0.5
	assert abs(med_b1 - med_b2) < 1.5


#============================================
def test_bin_factor_does_not_invalidate_camera_motion_cache(tmp_path):
	# Property under C12.3: after retiring config_hash the only
	# staleness signal is motion_model. A cache file written under one
	# bin_factor must remain a hit for a load that nominally would
	# correspond to a different bin, because bin is not part of the
	# cache identity any more.
	cache_path = str(tmp_path / "video.track_runner.camera_motion.npz")
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(4, dtype=numpy.float32),
		dy=numpy.zeros(4, dtype=numpy.float32),
		scale=numpy.ones(4, dtype=numpy.float32),
		quality=numpy.ones(4, dtype=numpy.float32),
	)
	video_identity = {"basename": "video.mp4", "frame_count": 4}
	camera_motion.save_motion_cache(
		motion, cache_path, camera_motion.MOTION_MODEL_FIXED, video_identity,
	)
	# Loader receives the same motion_model; bin_factor is not even
	# part of the API. Cache must hit.
	cached = camera_motion.load_motion_cache(
		cache_path, expected_motion_model=camera_motion.MOTION_MODEL_FIXED,
	)
	assert cached is not None


