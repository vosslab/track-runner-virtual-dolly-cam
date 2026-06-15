"""Table-driven test for the floor-based default-bin selector.

Asserts the human-approved default-bin mapping at target_width=1440 (floor
rule, option B, confirmed 2026-06-14) and the resulting post-bin (pre-goodbox)
processed width source_width // bin_factor.  Under floor@1440, 4K (3840) bins
at 2 (1920x1080 / 1080p band) and 1440p (2560) and below stay full-res.

Intentional tripwire: this table and the asserted constant value are hard-coded
to floor@1440, tied to frame_reader.TARGET_DEFAULT_WIDTH_PX.  If someone retunes
that project-wide constant, this test fails on purpose to remind them to update
the documented bin table and the report doc to match.
"""

# Standard Library
import os
import sys

# PIP3 modules
import pytest

# path setup for repo imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import file_utils  # noqa: E402

REPO_ROOT = file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))

# local repo modules
import frame_reader  # noqa: E402


# target_width 1440 floor mapping: source_width -> (bin_factor, processed_width).
# processed_width is source_width // bin_factor (post-bin, pre-goodbox snap).
BIN_TABLE_1440 = {
	3840: (2, 1920),
	2880: (2, 1440),
	2704: (1, 2704),
	2560: (1, 2560),
	1920: (1, 1920),
	1440: (1, 1440),
}


#============================================
def test_default_selector_bin_mapping_target_1440() -> None:
	"""Selector returns the approved bin_factor for each source width."""
	for source_width, (expected_bin, _processed) in BIN_TABLE_1440.items():
		got = frame_reader.select_default_bin_factor(source_width, 1440)
		assert got == expected_bin, (
			f"source_width={source_width}: expected bin {expected_bin}, got {got}"
		)


#============================================
def test_default_selector_keeps_1080p_full_res() -> None:
	"""1080p (1920-wide) stays bin 1 under floor@1440."""
	assert frame_reader.select_default_bin_factor(1920, 1440) == 1


#============================================
def test_default_selector_keeps_1440p_full_res() -> None:
	"""1440p (2560-wide) stays bin 1 under floor@1440 (round would pick 2)."""
	assert frame_reader.select_default_bin_factor(2560, 1440) == 1


#============================================
def test_default_selector_4k_bins_to_1080p_band() -> None:
	"""4K (3840-wide) bins at 2 -> 1920-wide (1080p band)."""
	assert frame_reader.select_default_bin_factor(3840, 1440) == 2


#============================================
def test_default_selector_processed_width_target_1440() -> None:
	"""Post-bin processed width equals source_width // selected bin_factor."""
	for source_width, (expected_bin, expected_processed) in BIN_TABLE_1440.items():
		got_bin = frame_reader.select_default_bin_factor(source_width, 1440)
		processed = source_width // got_bin
		assert processed == expected_processed, (
			f"source_width={source_width}: expected processed {expected_processed},"
			f" got {processed}"
		)


#============================================
def test_default_selector_never_upscales() -> None:
	"""Sources at or below target stay at bin_factor 1 (never upscaled)."""
	assert frame_reader.select_default_bin_factor(1440, 1440) == 1
	assert frame_reader.select_default_bin_factor(640, 1440) == 1


#============================================
def test_default_selector_default_target_uses_constant() -> None:
	"""Calling without target_width uses TARGET_DEFAULT_WIDTH_PX (1440)."""
	assert frame_reader.select_default_bin_factor(3840) == 2
	assert frame_reader.select_default_bin_factor(2560) == 1
	assert frame_reader.select_default_bin_factor(1920) == 1


#============================================
def test_default_selector_rejects_nonpositive() -> None:
	"""Selector raises ValueError on non-positive inputs."""
	with pytest.raises(ValueError):
		frame_reader.select_default_bin_factor(0, 1440)
	with pytest.raises(ValueError):
		frame_reader.select_default_bin_factor(3840, 0)
