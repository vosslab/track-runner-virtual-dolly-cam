"""Table-driven test for the pixel-area default-bin selector.

Asserts the default-bin mapping at max_pixels=MAX_ANALYSIS_PIXELS and the
resulting post-bin (pre-goodbox) processed dimensions source // bin_factor.
Under the area budget, 4K (3840x2160) bins at 3 and 1080p (1920x1080) bins
at 2, so every listed source lands at or under the budget.

Intentional tripwire: this table and the asserted constant value are tied to
frame_reader.MAX_ANALYSIS_PIXELS.  If someone retunes that project-wide
constant, this test fails on purpose to remind them to update the documented
bin table and the report doc to match.
"""

# PIP3 modules
import pytest

# local repo modules
import frame_reader


# area-budget mapping: (source_w, source_h) -> (bin_factor, processed_w, processed_h).
# processed dims are source // bin_factor (post-bin, pre-goodbox snap).
BIN_TABLE_AREA = {
	(3840, 2160): (3, 1280, 720),
	(2880, 1620): (3, 960, 540),
	(2704, 1520): (2, 1352, 760),
	(2560, 1440): (2, 1280, 720),
	(1920, 1080): (2, 960, 540),
	(1280, 720): (1, 1280, 720),
}


#============================================
def test_default_selector_bin_mapping_area_budget() -> None:
	"""Selector returns the budgeted bin_factor for each source shape."""
	for (source_w, source_h), (expected_bin, _pw, _ph) in BIN_TABLE_AREA.items():
		got = frame_reader.select_default_bin_factor(source_w, source_h)
		assert got == expected_bin, (
			f"source={source_w}x{source_h}: expected bin {expected_bin}, got {got}"
		)


#============================================
def test_default_selector_processed_area_stays_within_budget() -> None:
	"""Every selected bin holds the processed frame at or under the budget."""
	for (source_w, source_h) in BIN_TABLE_AREA:
		got_bin = frame_reader.select_default_bin_factor(source_w, source_h)
		processed_area = (source_w // got_bin) * (source_h // got_bin)
		assert processed_area <= frame_reader.MAX_ANALYSIS_PIXELS, (
			f"source={source_w}x{source_h}: processed area {processed_area}"
			f" exceeds {frame_reader.MAX_ANALYSIS_PIXELS}"
		)


#============================================
def test_default_selector_processed_dimensions() -> None:
	"""Post-bin dimensions equal source // selected bin_factor."""
	for (source_w, source_h), (_bin, exp_w, exp_h) in BIN_TABLE_AREA.items():
		got_bin = frame_reader.select_default_bin_factor(source_w, source_h)
		assert (source_w // got_bin, source_h // got_bin) == (exp_w, exp_h)


#============================================
def test_default_selector_prices_aspect_ratio() -> None:
	"""Two sources of equal width but different height can bin differently."""
	wide_bin = frame_reader.select_default_bin_factor(1920, 1080)
	letterboxed_bin = frame_reader.select_default_bin_factor(1920, 480)
	assert letterboxed_bin < wide_bin


#============================================
def test_default_selector_never_upscales() -> None:
	"""Sources already under budget stay at bin_factor 1."""
	assert frame_reader.select_default_bin_factor(1280, 720) == 1
	assert frame_reader.select_default_bin_factor(640, 360) == 1


#============================================
def test_default_selector_rejects_nonpositive() -> None:
	"""Selector raises ValueError on non-positive inputs."""
	with pytest.raises(ValueError):
		frame_reader.select_default_bin_factor(0, 1080)
	with pytest.raises(ValueError):
		frame_reader.select_default_bin_factor(1920, 0)
	with pytest.raises(ValueError):
		frame_reader.select_default_bin_factor(1920, 1080, 0)
