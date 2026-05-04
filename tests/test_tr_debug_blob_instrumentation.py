"""Tests for M0b blob debug instrumentation in frame_reader and residual_motion.

Verifies:
- enable_blob_debug() / disable_blob_debug() round-trip in frame_reader.
- get_blob_debug_summary() returns a dict with expected keys.
- enable_blob_debug_residual() / disable_blob_debug_residual() round-trip.
- get_residual_debug_summary() returns a dict with expected keys.
- Default state is OFF (no leakage between tests via the reset fixture).

Does NOT test print output (brittle) or wall-clock timing (flaky).

Note on imports: conftest adds track_runner/ to sys.path for bare module
imports (e.g. `import scoring`). Modules inside track_runner/ are imported
bare (e.g. `import residual_motion`), not as `track_runner.residual_motion`,
because inserting the directory into sys.path shadows the package namespace.
"""

# PIP3 modules
import pytest

pytest.importorskip("common_tools.frame_reader")
pytest.importorskip("residual_motion")

# local repo modules
import common_tools.frame_reader
import residual_motion


#============================================
@pytest.fixture(autouse=True)
def reset_blob_debug_state():
	"""Reset both module debug states before and after each test."""
	common_tools.frame_reader.disable_blob_debug()
	residual_motion.disable_blob_debug_residual()
	yield
	common_tools.frame_reader.disable_blob_debug()
	residual_motion.disable_blob_debug_residual()


#============================================
def test_frame_reader_debug_default_off():
	"""Default state is off; no leakage from previous tests."""
	summary = common_tools.frame_reader.get_blob_debug_summary()
	assert summary["enabled"] is False


#============================================
def test_frame_reader_enable_disable_round_trip():
	"""enable_blob_debug / disable_blob_debug flip the flag correctly."""
	common_tools.frame_reader.enable_blob_debug()
	assert common_tools.frame_reader.get_blob_debug_summary()["enabled"] is True
	common_tools.frame_reader.disable_blob_debug()
	assert common_tools.frame_reader.get_blob_debug_summary()["enabled"] is False


#============================================
def test_frame_reader_summary_keys():
	"""get_blob_debug_summary returns a dict with all expected keys."""
	summary = common_tools.frame_reader.get_blob_debug_summary()
	expected_keys = {
		"enabled", "strategy_calls", "strategy_total_ms",
		"strategy_failures", "strategy_4_fired", "strategy_5_fired",
		"remux_total_ms",
	}
	assert expected_keys <= set(summary.keys())


#============================================
def test_frame_reader_summary_is_copy():
	"""get_blob_debug_summary returns a shallow copy; mutations do not affect state."""
	summary1 = common_tools.frame_reader.get_blob_debug_summary()
	summary1["strategy_4_fired"] = 999
	summary2 = common_tools.frame_reader.get_blob_debug_summary()
	assert summary2["strategy_4_fired"] == 0


#============================================
def test_residual_debug_default_off():
	"""Default state is off; no leakage from previous tests."""
	summary = residual_motion.get_residual_debug_summary()
	assert summary["observe_calls"] == 0
	assert summary["compute_calls"] == 0


#============================================
def test_residual_enable_disable_round_trip():
	"""enable_blob_debug_residual / disable_blob_debug_residual round-trip via public summary."""
	residual_motion.enable_blob_debug_residual()
	assert residual_motion.get_residual_debug_summary()["enabled"] is True
	residual_motion.disable_blob_debug_residual()
	assert residual_motion.get_residual_debug_summary()["enabled"] is False


#============================================
def test_residual_summary_keys():
	"""get_residual_debug_summary returns a dict with all expected keys."""
	summary = residual_motion.get_residual_debug_summary()
	expected_keys = {
		"observe_calls", "compute_calls", "compute_total_ms",
		"bgr_cache_hits", "bgr_cache_misses",
	}
	assert expected_keys <= set(summary.keys())
