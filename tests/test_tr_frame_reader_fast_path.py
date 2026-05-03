"""Sequential fast-path tests for FrameReader.

The fast-path skips `cv2.VideoCapture.set(...)` when the requested
frame index equals the tracker's expected next index. On many
codecs/containers `set(POS_MSEC)` triggers a per-frame keyframe
re-seek, which dominates Stage 1 wallclock; without the fast-path,
FrameReader was 100x+ slower than VideoReader on H.264 mp4. These
tests assert that consecutive `read_frame(i), read_frame(i+1),
read_frame(i+2)` calls do not call `cap.set(...)` for the
follow-on indices, by wrapping the underlying capture with a
counting proxy.
"""

# Standard Library
import sys
import os

# PIP3 modules
import pytest
import numpy

# local repo modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
import common_tools.frame_reader


#============================================
class _CountingCap:
	"""Minimal cv2.VideoCapture proxy that counts set/read calls."""

	def __init__(self, width=64, height=48, total=20):
		self._width = width
		self._height = height
		self._total = total
		self._pos = 0
		self.set_calls = 0
		self.read_calls = 0
		self.opened = True

	def isOpened(self):
		return self.opened

	def get(self, prop):
		# 3=FRAME_WIDTH, 4=FRAME_HEIGHT, 7=FRAME_COUNT, 5=FPS
		if prop == 3:
			return float(self._width)
		if prop == 4:
			return float(self._height)
		if prop == 7:
			return float(self._total)
		if prop == 5:
			return 30.0
		return 0.0

	def set(self, prop, value):
		self.set_calls += 1
		# emulate cv2 semantics: any set repositions the capture
		if prop == 1:  # POS_FRAMES
			self._pos = int(value)
		elif prop == 0:  # POS_MSEC
			self._pos = int(value / (1000.0 / 30.0))
		return True

	def read(self):
		self.read_calls += 1
		if self._pos >= self._total:
			return False, None
		frame = numpy.full(
			(self._height, self._width, 3), self._pos, dtype=numpy.uint8,
		)
		self._pos += 1
		return True, frame

	def release(self):
		self.opened = False


#============================================
@pytest.fixture
def reader_with_counting_cap(monkeypatch):
	"""FrameReader where the underlying VideoCapture is a counting proxy."""
	cap = _CountingCap()

	def _fake_videocapture(_path):
		return cap

	# patch cv2.VideoCapture only for this test
	monkeypatch.setattr(
		common_tools.frame_reader.cv2, "VideoCapture", _fake_videocapture,
	)
	# os.path.isfile check inside FrameReader -- bypass via a fake path
	reader = common_tools.frame_reader.FrameReader(
		video_path="/tmp/_fake_clip.mov",
		fps=30.0,
		total_frames=20,
		bin_factor=1,
	)
	return reader, cap


#============================================
def test_sequential_reads_skip_set(reader_with_counting_cap):
	"""Three consecutive read_frame calls must use at most one set()."""
	reader, cap = reader_with_counting_cap
	# first read seeks (strategy 1: seek_msec) -> 1 set call
	reader.read_frame(5)
	# next two reads must hit the fast-path: no further set() calls
	reader.read_frame(6)
	reader.read_frame(7)
	# at most one set call for the initial seek
	assert cap.set_calls == 1


#============================================
def test_nonsequential_read_invalidates_fast_path(reader_with_counting_cap):
	"""A scattered access disarms the fast-path; the next sequential
	pair must seek again."""
	reader, cap = reader_with_counting_cap
	reader.read_frame(2)  # seek
	reader.read_frame(3)  # fast-path
	assert cap.set_calls == 1
	reader.read_frame(10)  # scattered -> seek
	assert cap.set_calls == 2
	reader.read_frame(11)  # fast-path again
	assert cap.set_calls == 2


#============================================
def test_first_read_always_seeks(reader_with_counting_cap):
	"""The very first read must seek (tracker starts at -1)."""
	reader, cap = reader_with_counting_cap
	assert cap.set_calls == 0
	reader.read_frame(0)
	assert cap.set_calls == 1
