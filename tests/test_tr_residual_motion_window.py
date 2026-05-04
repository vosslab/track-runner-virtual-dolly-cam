"""Tests for resolve_stride helper function.

Tests the fps-invariant stride model introduced in M2 of plan
memoized-percolating-moler.md. The neighbor window is 9 samples
(DEFAULT_HALF_WINDOW=4 each side plus center, skipping k=0). Stride
controls the time between samples so higher-fps inputs span the same
~133 ms window the 60 fps default produces.

Naming preserved from the legacy resolve_half_window tests where possible.
"""

# PIP3 modules
import pytest

# local repo modules (bare imports resolved by conftest.py)
import residual_motion


def test_resolve_stride_60fps_anchor():
	"""60 fps anchor: stride=1 (byte-identical to legacy contiguous window)."""
	result = residual_motion.resolve_stride(60.0)
	assert result == 1, f"Expected 1, got {result}"


def test_resolve_stride_119fps():
	"""119.94 fps: stride=2 (same ~133 ms span, half the I/O)."""
	result = residual_motion.resolve_stride(119.94)
	assert result == 2, f"Expected 2, got {result}"


def test_resolve_stride_120fps():
	"""120 fps anchor: stride=2."""
	result = residual_motion.resolve_stride(120.0)
	assert result == 2, f"Expected 2, got {result}"


def test_resolve_stride_30fps():
	"""30 fps: stride=1 (round(30/60)=0, clamped to min=1)."""
	result = residual_motion.resolve_stride(30.0)
	assert result == 1, f"Expected 1 (min clamp), got {result}"


def test_resolve_stride_240fps():
	"""240 fps anchor: stride=4 (same ~133 ms span, quarter the I/O)."""
	result = residual_motion.resolve_stride(240.0)
	assert result == 4, f"Expected 4, got {result}"


def test_resolve_stride_15fps():
	"""15 fps: stride=1 (round(15/60)=0, clamped to min=1)."""
	result = residual_motion.resolve_stride(15.0)
	assert result == 1, f"Expected 1 (min clamp), got {result}"


def test_resolve_stride_fps_zero_raises():
	"""fps <= 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_stride(0.0)


def test_resolve_stride_fps_negative_raises():
	"""fps < 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_stride(-1.0)


def test_resolve_stride_fps_none_raises():
	"""fps=None raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_stride(None)


def test_resolve_stride_returns_int():
	"""resolve_stride always returns a Python int."""
	result = residual_motion.resolve_stride(60.0)
	assert isinstance(result, int), f"Expected int, got {type(result)}"


def test_resolve_stride_monotone():
	"""Higher fps -> stride >= lower fps stride (non-decreasing)."""
	strides = [residual_motion.resolve_stride(float(fps)) for fps in [30, 60, 90, 120, 180, 240]]
	for i in range(len(strides) - 1):
		assert strides[i] <= strides[i + 1], (
			f"stride not monotone: fps ladder {strides}"
		)


def test_neighbor_offsets_60fps():
	"""At 60 fps, neighbor offsets are [-4, -3, -2, -1, 1, 2, 3, 4] -- legacy behavior."""
	stride = residual_motion.resolve_stride(60.0)
	hw = residual_motion.DEFAULT_HALF_WINDOW
	# build offsets the way compute_residual_for_frame does
	offsets = [k * stride for k in range(-hw, hw + 1) if k != 0]
	expected = [-4, -3, -2, -1, 1, 2, 3, 4]
	assert offsets == expected, f"Expected {expected}, got {offsets}"


def test_neighbor_offsets_120fps():
	"""At 119.94 fps, neighbor offsets are [-8, -6, -4, -2, 2, 4, 6, 8]."""
	stride = residual_motion.resolve_stride(119.94)
	hw = residual_motion.DEFAULT_HALF_WINDOW
	offsets = [k * stride for k in range(-hw, hw + 1) if k != 0]
	expected = [-8, -6, -4, -2, 2, 4, 6, 8]
	assert offsets == expected, f"Expected {expected}, got {offsets}"


def test_neighbor_count_fixed_regardless_of_fps():
	"""Number of neighbors is always 2 * DEFAULT_HALF_WINDOW = 8 regardless of fps."""
	hw = residual_motion.DEFAULT_HALF_WINDOW
	for fps in [30.0, 60.0, 119.94, 120.0, 240.0]:
		stride = residual_motion.resolve_stride(fps)
		offsets = [k * stride for k in range(-hw, hw + 1) if k != 0]
		assert len(offsets) == 2 * hw, (
			f"Expected {2 * hw} neighbors at fps={fps}, got {len(offsets)}"
		)
