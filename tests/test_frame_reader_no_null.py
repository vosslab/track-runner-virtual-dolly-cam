"""Tests for FrameReader null-free contract.

Verifies that read_frame() raises RuntimeError on EOF and never returns
None. The HEVC retry path requires a real HEVC fixture and is out of
scope for unit tests; it is covered by the 24-corpus dump smoke run.
"""
import sys
import os
import numpy
import pytest

# repo root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import git_file_utils  # noqa: E402  (used for REPO_ROOT discovery)

REPO_ROOT = git_file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))


#============================================
class _FakeCapture:
	"""Minimal cv2.VideoCapture stand-in for unit testing."""

	def __init__(self, total_frames: int, frame_shape: tuple):
		self.total_frames = total_frames
		self.frame_shape = frame_shape  # (h, w, c)
		self._pos = 0
		self._isOpened = True

	def isOpened(self) -> bool:
		return self._isOpened

	def get(self, prop_id: int) -> float:
		# CAP_PROP_FRAME_COUNT = 7
		# CAP_PROP_FRAME_WIDTH = 3
		# CAP_PROP_FRAME_HEIGHT = 4
		# CAP_PROP_FPS = 5
		if prop_id == 7:
			return float(self.total_frames)
		if prop_id == 3:
			return float(self.frame_shape[1])
		if prop_id == 4:
			return float(self.frame_shape[0])
		if prop_id == 5:
			return 30.0
		return 0.0

	def set(self, prop_id: int, value: float) -> bool:
		# CAP_PROP_POS_FRAMES = 1
		if prop_id == 1:
			self._pos = int(value)
		return True

	def read(self):
		if self._pos >= self.total_frames:
			return False, None
		frame = numpy.zeros(self.frame_shape, dtype=numpy.uint8)
		self._pos += 1
		return True, frame

	def release(self):
		self._isOpened = False


#============================================
def _make_reader(total_frames: int = 10):
	"""Construct a FrameReader backed by a fake capture."""
	import frame_reader as fr
	cap = _FakeCapture(total_frames, (4, 6, 3))
	reader = fr.FrameReader.__new__(fr.FrameReader)
	# inject private state directly to bypass __init__ file-open
	reader._cap = cap
	reader._total_frames = total_frames
	reader._cap_next_index = 0
	reader._video_path = "<test>"
	reader._bin = 1
	reader._goodbox = None
	reader._bin_factor = 1
	# override _apply_bin so the test does not need _geometry wired up
	reader._apply_bin = lambda frame: frame
	return reader


#============================================
def test_read_frame_eof_raises_runtime_error():
	"""Requesting a frame past total_frames raises RuntimeError."""
	reader = _make_reader(total_frames=5)
	with pytest.raises(RuntimeError, match="frame_index"):
		reader.read_frame(5)


#============================================
def test_read_frame_normal_returns_ndarray():
	"""A valid in-range read returns a numpy.ndarray, never None."""
	reader = _make_reader(total_frames=5)
	frame = reader.read_frame(0)
	assert isinstance(frame, numpy.ndarray)
