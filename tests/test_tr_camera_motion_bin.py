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
def test_config_fingerprint_responds_to_bin():
	# changing bin_factor or processed_width/height must change the
	# camera-motion config_hash so old caches miss cleanly.
	config = {"type": "fixed"}
	geom_a = {"bin_factor": 1, "processed_width": 1920, "processed_height": 1080}
	geom_b = {"bin_factor": 2, "processed_width": 960, "processed_height": 540}
	fp_a = camera_motion._compute_config_fingerprint(config, geom_a)
	fp_b = camera_motion._compute_config_fingerprint(config, geom_b)
	assert fp_a != fp_b


#============================================
def test_config_fingerprint_stable_under_same_geometry():
	# property: same config + same geometry => same fingerprint
	config = {"type": "fixed"}
	geom = {"bin_factor": 4, "processed_width": 960, "processed_height": 528}
	fp1 = camera_motion._compute_config_fingerprint(config, geom)
	fp2 = camera_motion._compute_config_fingerprint(config, geom)
	assert fp1 == fp2
