"""Unit tests for FrameReader: bin_factor, FrameGeometry, .mkv guard.

The bin path needs a real video. We use a tiny fixture video generated
on the fly via cv2.VideoWriter into pytest's tmp_path so the tests stay
deterministic and self-contained. FFV1 in MKV is lossless and supported
by the cv2 FFmpeg backend on the platforms this repo targets.
"""

# Standard Library
import warnings

# PIP3 modules
import cv2
import numpy
import pytest

# local repo modules
import common_tools.frame_reader
import common_tools.goodbox


_FOURCC = cv2.VideoWriter_fourcc(*"FFV1")


#============================================
def _write_synthetic_video(path: str, width: int, height: int, n_frames: int = 5, fps: float = 30.0) -> None:
	"""Write a tiny synthetic .mkv with a known constant pattern."""
	writer = cv2.VideoWriter(path, _FOURCC, fps, (width, height))
	if not writer.isOpened():
		raise RuntimeError(f"cv2.VideoWriter failed to open {path}")
	for f in range(n_frames):
		# constant frame; values just need to be non-zero
		frame = numpy.full((height, width, 3), 100 + f * 10, dtype=numpy.uint8)
		writer.write(frame)
	writer.release()


#============================================
def _open(path: str, n_frames: int, fps: float = 30.0, **kwargs):
	return common_tools.frame_reader.FrameReader(
		video_path=path,
		fps=fps,
		total_frames=n_frames,
		**kwargs,
	)


#============================================
def test_rejects_non_mkv_path():
	# .mkv-only restriction: a .mov path must raise before any decode
	# is attempted, with a message naming mkvmerge so the user knows
	# how to remux.
	with pytest.raises(ValueError, match="mkvmerge"):
		common_tools.frame_reader.FrameReader(
			video_path="/tmp/not_real.mov",
			fps=30.0,
			total_frames=10,
		)


#============================================
def test_bin_factor_default_is_one(tmp_path):
	video = str(tmp_path / "v.mkv")
	_write_synthetic_video(video, 320, 240, n_frames=3)
	reader = _open(video, n_frames=3)
	assert reader.bin_factor == 1
	assert reader.width == 320
	assert reader.height == 240
	geom = reader.geometry
	assert geom.source_width == 320 and geom.source_height == 240
	assert geom.bin_factor == 1
	assert geom.scaled_width == 320 and geom.scaled_height == 240
	assert geom.processed_width == 320 and geom.processed_height == 240
	reader.close()


#============================================
def test_bin_one_byte_identical(tmp_path):
	# bin_factor == 1 must short-circuit the resize path entirely
	video = str(tmp_path / "v.mkv")
	_write_synthetic_video(video, 128, 96, n_frames=3)
	reader1 = _open(video, n_frames=3, bin_factor=1)
	frame1 = reader1.read_frame(0)
	reader1.close()
	# read again with no kwargs
	reader2 = _open(video, n_frames=3)
	frame2 = reader2.read_frame(0)
	reader2.close()
	assert frame1 is not None and frame2 is not None
	assert numpy.array_equal(frame1, frame2)


#============================================
def test_bin_factor_validation(tmp_path):
	video = str(tmp_path / "v.mkv")
	_write_synthetic_video(video, 64, 64, n_frames=2)
	with pytest.raises(ValueError):
		_open(video, n_frames=2, bin_factor=0)
	with pytest.raises(ValueError):
		_open(video, n_frames=2, bin_factor=-2)
	with pytest.raises(TypeError):
		_open(video, n_frames=2, bin_factor=2.0)


#============================================
def test_bin_two_dims_and_processed_frame(tmp_path):
	# 320 x 240 binned by 2 -> 160 x 120, both already goodboxes
	# (160 = 2^5 * 5, 160 % 16 == 0; 120 = 2^3 * 3 * 5, 120 % 8 == 0)
	video = str(tmp_path / "v.mkv")
	_write_synthetic_video(video, 320, 240, n_frames=3)
	reader = _open(video, n_frames=3, bin_factor=2)
	geom = reader.geometry
	assert geom.bin_factor == 2
	assert geom.scaled_width == 160 and geom.scaled_height == 120
	assert geom.processed_width == 160 and geom.processed_height == 120
	frame = reader.read_frame(0)
	assert frame is not None
	# returned frame uses (rows, cols) = (height, width)
	assert frame.shape[0] == 120 and frame.shape[1] == 160
	# and the public width/height match
	assert reader.width == 160 and reader.height == 120
	reader.close()


#============================================
def test_goodbox_snap_applies_at_bin_one(tmp_path):
	# Property: the goodbox snap applies at any bin_factor including 1.
	# A non-goodbox source dim like 540 gets snapped down to 528 even
	# with no binning, so downstream FFT consumers always see
	# FFT-friendly dims.
	geom = common_tools.frame_reader._resolve_frame_geometry(540, 540, 1)
	# scaled stays at the source dim because there is no resize
	assert geom.scaled_width == 540
	assert geom.scaled_height == 540
	# processed snaps down to the largest goodbox <= 540
	assert geom.processed_width == 528
	assert geom.processed_height == 528


#============================================
def test_bin_one_no_snap_when_already_goodbox():
	# 320 and 240 are both goodboxes, so bin=1 leaves them unchanged.
	geom = common_tools.frame_reader._resolve_frame_geometry(320, 240, 1)
	assert geom.scaled_width == 320 and geom.scaled_height == 240
	assert geom.processed_width == 320 and geom.processed_height == 240


#============================================
def test_bin_one_hd_1080_snaps_to_1056():
	# Real-world example: 1920x1080 source has 1920 (goodbox) and
	# 1080 (not goodbox; snap to 1056). bin=1 still applies the snap.
	geom = common_tools.frame_reader._resolve_frame_geometry(1920, 1080, 1)
	assert geom.processed_width == 1920
	assert geom.processed_height == 1056


#============================================
def test_bin_one_uhd_2160_snaps_to_2112():
	# Real-world example: 3840x2160 source has 3840 (goodbox) and
	# 2160 (not goodbox; snap to 2112). bin=1 still applies the snap.
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 1)
	assert geom.processed_width == 3840
	assert geom.processed_height == 2112


#============================================
def test_resolve_geometry_snap_540(tmp_path):
	# scaled axis 540 must snap down to 528 when bin > 1
	geom = common_tools.frame_reader._resolve_frame_geometry(1080, 1080, 2)
	assert geom.bin_factor == 2
	assert geom.scaled_width == 540 and geom.scaled_height == 540
	assert geom.processed_width == 528 and geom.processed_height == 528


#============================================
def test_resolve_geometry_4k_bin4():
	# 4K 3840x2160 with bin=4 -> scaled 960x540 -> processed 960x528
	# 960 is already a goodbox; 540 snaps down to 528.
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 4)
	assert geom.scaled_width == 960 and geom.scaled_height == 540
	assert geom.processed_width == 960 and geom.processed_height == 528


#============================================
def test_resolve_geometry_safety_floor_keeps_scaled_dim():
	# Property: when the goodbox snap would discard more than the
	# safety-floor fraction (10%), processed_dim falls back to
	# scaled_dim. We pick scaled=23 (largest goodbox <= 23 is 20;
	# loss 3/23 ~= 13% > floor) so the snap MUST be skipped.
	with warnings.catch_warnings():
		warnings.simplefilter("ignore")
		geom = common_tools.frame_reader._resolve_frame_geometry(46, 64, 2)
	assert geom.scaled_width == geom.processed_width


#============================================
def test_resolve_geometry_divisibility_loss_silent_floor():
	# source not divisible by bin: the fractional column is dropped
	# at the cv2.resize boundary. Property: scaled_width must be
	# floor(source_width / bin_factor); we silence the warning
	# (its existence is incidental, the math is what matters).
	with warnings.catch_warnings():
		warnings.simplefilter("ignore")
		geom = common_tools.frame_reader._resolve_frame_geometry(321, 240, 2)
	assert geom.scaled_width == 321 // 2


#============================================
def test_geometry_roundtrip_identity():
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 4)
	# round trip source -> processed -> source within 0.5 px
	src_x, src_y = 1234.0, 567.0
	px, py = geom.source_to_processed(src_x, src_y)
	rx, ry = geom.processed_to_source(px, py)
	assert abs(rx - src_x) < 0.5 and abs(ry - src_y) < 0.5
	# delta round trip
	dx, dy = 12.0, -7.5
	pdx, pdy = geom.source_to_processed_delta(dx, dy)
	rdx, rdy = geom.processed_to_source_delta(pdx, pdy)
	assert abs(rdx - dx) < 1e-9 and abs(rdy - dy) < 1e-9


#============================================
def test_geometry_pure_scale_no_offset():
	# origin maps to origin both directions
	geom = common_tools.frame_reader._resolve_frame_geometry(3840, 2160, 4)
	assert geom.source_to_processed(0.0, 0.0) == (0.0, 0.0)
	assert geom.processed_to_source(0.0, 0.0) == (0.0, 0.0)
