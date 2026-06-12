"""Tests for select_bin_factor_for_analysis in common_tools/frame_reader.py.

Verifies the selection function independently; FrameReader mechanism tests
stay in test_frame_reader_no_null.py.
"""
import sys
import os
import numpy

# repo root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import file_utils  # noqa: E402

REPO_ROOT = file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))

import frame_reader as fr


# Convenience alias
select = fr.select_bin_factor_for_analysis
TARGET = fr.TARGET_ANALYSIS_WIDTH_PX


#============================================
def test_auto_bin_picks_4_for_3840_wide():
	"""3840-wide source (4K) should select bin_factor=4."""
	assert select(3840) == 4


#============================================
def test_auto_bin_picks_2_for_1920_wide():
	"""1920-wide source (1080p) should select bin_factor=2."""
	assert select(1920) == 2


#============================================
def test_auto_bin_picks_1_for_640_wide():
	"""640-wide source is already within target; bin_factor=1 (no bin)."""
	assert select(640) == 1


#============================================
def test_post_bin_width_within_target_3840():
	"""Post-bin width for 3840 source must be <= TARGET_ANALYSIS_WIDTH_PX."""
	bf = select(3840)
	post_bin_width = 3840 // bf
	assert post_bin_width <= TARGET


#============================================
def test_post_bin_width_within_target_1920():
	"""Post-bin width for 1920 source must be <= TARGET_ANALYSIS_WIDTH_PX."""
	bf = select(1920)
	post_bin_width = 1920 // bf
	assert post_bin_width <= TARGET


#============================================
def test_pow2_only():
	"""bin_factor must always be a power of two (1, 2, 4, 8...)."""
	# test a variety of plausible source widths
	test_widths = [320, 640, 720, 960, 1280, 1440, 1920, 2560, 3840, 4096, 7680]
	for w in test_widths:
		bf = select(w)
		# power of two: bf & (bf - 1) == 0 for all positive powers of two
		assert bf >= 1
		assert (bf & (bf - 1)) == 0, f"bin_factor={bf} is not a power of two for width={w}"


#============================================
class _FakeCapture:
	"""Minimal cv2.VideoCapture stand-in for integration test."""

	def __init__(self, width: int, height: int, total_frames: int = 10):
		self._width = width
		self._height = height
		self.total_frames = total_frames
		self._pos = 0
		self._isOpened = True

	def isOpened(self) -> bool:
		return self._isOpened

	def get(self, prop_id: int) -> float:
		# CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4
		if prop_id == 3:
			return float(self._width)
		if prop_id == 4:
			return float(self._height)
		return 0.0

	def set(self, prop_id: int, value: float) -> bool:
		if prop_id == 1:
			self._pos = int(value)
		return True

	def read(self):
		if self._pos >= self.total_frames:
			return False, None
		frame = numpy.zeros((self._height, self._width, 3), dtype=numpy.uint8)
		self._pos += 1
		return True, frame

	def release(self):
		self._isOpened = False


#============================================
def _make_reader_with_width(source_width: int, source_height: int, bin_factor: int):
	"""Construct a FrameReader backed by a fake capture at given bin_factor."""
	cap = _FakeCapture(source_width, source_height, total_frames=10)
	reader = fr.FrameReader.__new__(fr.FrameReader)
	# inject private state directly to bypass __init__ file-open
	reader._cap = cap
	reader._total_frames = 10
	reader._cap_next_index = 0
	reader._video_path = "<test>"
	reader._debug = False
	reader._bin_factor = bin_factor
	reader._source_width = source_width
	reader._source_height = source_height
	# resolve geometry properly using the module-level helper
	reader._geometry = fr._resolve_frame_geometry(source_width, source_height, bin_factor)
	reader._width = reader._geometry.processed_width
	reader._height = reader._geometry.processed_height
	return reader


#============================================
def test_integration_select_then_open_reader_width_within_target():
	"""select_bin_factor_for_analysis + FrameReader: post-bin width <= TARGET."""
	source_width = 3840
	source_height = 2160
	bin_factor = select(source_width)
	reader = _make_reader_with_width(source_width, source_height, bin_factor)
	assert reader.width <= TARGET
