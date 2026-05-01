"""Tests for resolve_half_window helper function.

Tests the conversion from temporal background window (seconds, fps) to
frame-count half_window used by the residual-motion heat map.
"""

# PIP3 modules
import pytest

# local repo modules (bare imports resolved by conftest.py)
import residual_motion


def test_resolve_half_window_60fps_anchor():
	"""60 fps anchor: legacy 9-frame window yields exactly 4."""
	result = residual_motion.resolve_half_window(8.0 / 60.0, 60.0)
	assert result == 4, f"Expected 4, got {result}"


def test_resolve_half_window_120fps():
	"""120 fps anchor: same temporal span yields 8."""
	result = residual_motion.resolve_half_window(8.0 / 60.0, 120.0)
	assert result == 8, f"Expected 8, got {result}"


def test_resolve_half_window_30fps():
	"""30 fps anchor: same temporal span yields 2."""
	result = residual_motion.resolve_half_window(8.0 / 60.0, 30.0)
	assert result == 2, f"Expected 2, got {result}"


def test_resolve_half_window_240fps_max_clamp():
	"""240 fps yields max_half_window=12 (not 16)."""
	result = residual_motion.resolve_half_window(8.0 / 60.0, 240.0)
	assert result == 12, f"Expected 12 (max clamp), got {result}"


def test_resolve_half_window_15fps_min_clamp():
	"""15 fps yields min_half_window=2 (not 1)."""
	result = residual_motion.resolve_half_window(8.0 / 60.0, 15.0)
	assert result == 2, f"Expected 2 (min clamp), got {result}"


def test_resolve_half_window_fps_zero_raises():
	"""fps <= 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_half_window(8.0 / 60.0, 0.0)


def test_resolve_half_window_fps_negative_raises():
	"""fps < 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_half_window(8.0 / 60.0, -1.0)


def test_resolve_half_window_window_zero_raises():
	"""window_seconds <= 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_half_window(0.0, 60.0)


def test_resolve_half_window_window_negative_raises():
	"""window_seconds < 0 raises ValueError."""
	with pytest.raises(ValueError):
		residual_motion.resolve_half_window(-1.0, 60.0)


def test_resolve_half_window_custom_bounds():
	"""Custom min/max bounds are respected."""
	# At 60 fps with default window, we get 4. With min=5, should clamp to 5.
	result = residual_motion.resolve_half_window(
		8.0 / 60.0, 60.0, min_half_window=5
	)
	assert result == 5, f"Expected 5 (min clamp to 5), got {result}"

	# At 60 fps with default window, we get 4. With max=3, should clamp to 3.
	result = residual_motion.resolve_half_window(
		8.0 / 60.0, 60.0, max_half_window=3
	)
	assert result == 3, f"Expected 3 (max clamp to 3), got {result}"


def test_resolve_half_window_small_window_seconds():
	"""Small window_seconds at reasonable fps yields small half_window."""
	# window_seconds = 0.033 (2 frames at 60 fps) should yield hw = 1, clamped to min=2
	result = residual_motion.resolve_half_window(
		0.033, 60.0, min_half_window=2, max_half_window=12
	)
	assert result == 2, f"Expected 2 (min clamp), got {result}"


def test_resolve_half_window_large_window_seconds():
	"""Large window_seconds at low fps yields large half_window, clamped."""
	# window_seconds = 1.0 at 30 fps = 15 frames = hw of 7.5, clamped to max=12
	result = residual_motion.resolve_half_window(
		1.0, 30.0, min_half_window=2, max_half_window=12
	)
	assert result == 12, f"Expected 12 (max clamp), got {result}"


# Note: a synthetic "new default beats legacy half_window=4" comparison
# is intentionally NOT included here. Wider windows accumulate "ghost"
# residual at past target positions (median picks target there because
# it was present in many neighbors, while the center frame has bare
# background, so |center - median| is large at the ghost). On a clean
# synthetic background that ghost contribution can dominate or offset
# the gain at the current target location, making integrated-magnitude
# comparisons direction-ambiguous. Real-world validation lives in the
# S1 daughter-clip smoke test (see docs/CHANGELOG.md), where camera
# motion, validity masking, and DoG band-passing combine to produce
# the failure mode this fix targets.
