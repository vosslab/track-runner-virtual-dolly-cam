"""Tests for resolve_stride helper function.

Tests the fps-invariant stride model: stride controls the time between
neighbor samples so higher-fps inputs span the same ~133 ms window the
60 fps default produces.
"""

# Standard Library
# (none)

# PIP3 modules
import pytest

# local repo modules (bare imports resolved by conftest.py)
import residual_motion


def test_resolve_stride_60fps_anchor():
	"""60 fps anchor: stride=1 (byte-identical to legacy contiguous window)."""
	result = residual_motion.resolve_stride(60.0)
	assert result == 1, f"Expected 1, got {result}"


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


def test_resolve_stride_min_clamp_and_above_reference() -> None:
	"""Sub-reference fps clamps to stride=1; well-above-reference fps yields stride>1."""
	# 15 fps is well below reference; must clamp to 1 (no frame skipping at low fps)
	assert residual_motion.resolve_stride(15.0) == 1
	# 240 fps is 4x reference; must yield a stride greater than 1 (neighbor spacing widens)
	assert residual_motion.resolve_stride(240.0) > 1


def test_resolve_stride_monotone():
	"""Higher fps -> stride >= lower fps stride (non-decreasing)."""
	strides = [residual_motion.resolve_stride(float(fps)) for fps in [30, 60, 90, 120, 180, 240]]
	for i in range(len(strides) - 1):
		assert strides[i] <= strides[i + 1], (
			f"stride not monotone: fps ladder {strides}"
		)


def test_neighbor_count_fixed_regardless_of_fps():
	"""Number of neighbors is always 2 * DEFAULT_HALF_WINDOW = 8 regardless of fps."""
	hw = residual_motion.DEFAULT_HALF_WINDOW
	for fps in [30.0, 60.0, 119.94, 120.0, 240.0]:
		stride = residual_motion.resolve_stride(fps)
		offsets = [k * stride for k in range(-hw, hw + 1) if k != 0]
		assert len(offsets) == 2 * hw, (
			f"Expected {2 * hw} neighbors at fps={fps}, got {len(offsets)}"
		)


