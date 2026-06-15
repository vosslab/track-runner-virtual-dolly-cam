"""Tests for the solve-path default bin resolver in cli._resolve_solve_bin_factor.

Covers the WS2-default policy: when neither --bin nor --auto-bin is given, the
solve bin_factor is routed through the shared floor selector on source WIDTH at
the project-wide default target 1440 (4K bins to 2 -> 1080p band; 1440p and
below stay full-res). Explicit --bin N stays exact (--bin 1 is the full-res
escape hatch), and --auto-bin HEIGHT keeps its height-based meaning.
"""

# PIP3 modules
import pytest

# local repo modules
import cli


#============================================
def test_no_flag_default_uses_width_floor_selector() -> None:
	"""No --bin/--auto-bin: bin resolves from source width (floor @ 1440)."""
	# (source_width, source_height, expected_bin)
	cases = [
		(3840, 2160, 2),
		(2880, 1620, 2),
		(2560, 1440, 1),
		(1920, 1080, 1),
		(1440, 1080, 1),
	]
	for source_width, source_height, expected_bin in cases:
		got, _msg = cli._resolve_solve_bin_factor(
			None, None, source_width, source_height
		)
		assert got == expected_bin, (
			f"width={source_width}: expected bin {expected_bin}, got {got}"
		)


#============================================
def test_no_flag_default_keeps_1080p_full_res() -> None:
	"""1080p (1920-wide) stays bin 1 under the no-flag default (floor)."""
	got, _msg = cli._resolve_solve_bin_factor(None, None, 1920, 1080)
	assert got == 1


#============================================
def test_explicit_bin_is_exact() -> None:
	"""Explicit --bin N is used as-is, ignoring source dims."""
	got, msg = cli._resolve_solve_bin_factor(4, None, 3840, 2160)
	assert got == 4
	assert msg is None


#============================================
def test_explicit_bin_one_is_full_res_escape_hatch() -> None:
	"""--bin 1 forces full resolution on a 4K source."""
	got, _msg = cli._resolve_solve_bin_factor(1, None, 3840, 2160)
	assert got == 1


#============================================
def test_auto_bin_keeps_height_based_meaning() -> None:
	"""--auto-bin HEIGHT keys on source HEIGHT, not width."""
	# round(2160 / 720) = 3
	got, _msg = cli._resolve_solve_bin_factor(None, 720, 3840, 2160)
	assert got == 3
	# round(1080 / 480) = 2
	got, _msg = cli._resolve_solve_bin_factor(None, 480, 1920, 1080)
	assert got == 2


#============================================
def test_rejects_bad_inputs() -> None:
	"""Sub-1 --bin and sub-1 --auto-bin target raise ValueError."""
	with pytest.raises(ValueError):
		cli._resolve_solve_bin_factor(0, None, 1920, 1080)
	with pytest.raises(ValueError):
		cli._resolve_solve_bin_factor(None, 0, 1920, 1080)
