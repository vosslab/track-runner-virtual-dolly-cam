"""Sentinel tests for common_tools.in_box_heat.measure_in_box_heat.

These pin the FIXED box-region selection convention (floor left/top, floor
right/bottom EXCLUSIVE, single roi_origin subtraction, clamp to array, validity
mask authoritative).  An asymmetric box (w != h) with a nonzero roi_origin on a
bin=4-shaped array makes any axis transpose or double-subtraction detectable.
"""

# PIP3 modules
import numpy

# local repo modules
import common_tools.coord_space
import common_tools.in_box_heat


#============================================
def _bin4_arrays(height: int, width: int) -> tuple:
	"""Return (residual_dog float32, validity_mask uint8) of given shape, all zero/valid."""
	residual_dog = numpy.zeros((height, width), dtype=numpy.float32)
	validity_mask = numpy.ones((height, width), dtype=numpy.uint8)
	return residual_dog, validity_mask


#============================================
def test_hot_pixels_mean_and_count_only_above_threshold() -> None:
	"""Only in-box pixels above threshold contribute to the mean and count."""
	# 12x16 array; roi_origin (40, 80) in PROCESSED pixels; box w=8, h=4 (w != h).
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	roi_origin = (40, 80)
	# PROCESSED center (48, 84) -> local center (8, 4); w=8 h=4 ->
	# x edges [4, 12), y edges [2, 6).  Region rows 2..5, cols 4..11.
	box = common_tools.coord_space.ProcessedBox(cx=48.0, cy=84.0, w=8.0, h=4.0)
	# Three above-threshold pixels inside the region: values 5, 7, 9 -> mean 7.
	residual_dog[2, 4] = 5.0
	residual_dog[3, 5] = 7.0
	residual_dog[5, 11] = 9.0
	# A below-threshold in-box pixel must be ignored.
	residual_dog[4, 6] = 1.0
	# An above-threshold pixel OUTSIDE the box (col 12) must be ignored.
	residual_dog[3, 12] = 100.0
	hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, box, threshold=2.0
	)
	assert hot_count == 3
	assert abs(hot_mean - 7.0) < 1e-6


#============================================
def test_all_below_threshold_returns_sentinel() -> None:
	"""A box whose in-box pixels are all below threshold returns (None, 0)."""
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	roi_origin = (40, 80)
	box = common_tools.coord_space.ProcessedBox(cx=48.0, cy=84.0, w=8.0, h=4.0)
	# All in-box values 1.0, threshold 2.0 -> none qualify.
	residual_dog[2:6, 4:12] = 1.0
	hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, box, threshold=2.0
	)
	assert hot_mean is None
	assert hot_count == 0


#============================================
def test_fractional_center_named_pixels() -> None:
	"""A fractional box center floors to a NAMED region; only those pixels count."""
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	roi_origin = (40, 80)
	# PROCESSED center (45.5, 83.5) -> local center (5.5, 3.5); w=5 h=3.
	# x edges float [3.0, 8.0) -> floor [3, 8); y edges float [2.0, 5.0) -> [2, 5).
	# Region rows 2..4, cols 3..7.
	box = common_tools.coord_space.ProcessedBox(cx=45.5, cy=83.5, w=5.0, h=3.0)
	# Two named above-threshold pixels inside the region.
	residual_dog[2, 3] = 4.0
	residual_dog[4, 7] = 6.0
	# Just-outside row 5 / col 8 above threshold must be excluded by the floor.
	residual_dog[5, 3] = 50.0
	residual_dog[2, 8] = 50.0
	hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, box, threshold=2.0
	)
	assert hot_count == 2
	assert abs(hot_mean - 5.0) < 1e-6


#============================================
def test_zero_area_and_out_of_roi_box_no_crash() -> None:
	"""Zero-area and fully-out-of-ROI boxes return (None, 0) without crashing."""
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	roi_origin = (40, 80)
	# Zero-area box (w == 0): floors to an empty region.
	zero_box = common_tools.coord_space.ProcessedBox(cx=48.0, cy=84.0, w=0.0, h=4.0)
	zero_mean, zero_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, zero_box, threshold=2.0
	)
	assert zero_mean is None
	assert zero_count == 0
	# Fully-out-of-ROI box: center far to the right of the array.
	out_box = common_tools.coord_space.ProcessedBox(cx=400.0, cy=84.0, w=8.0, h=4.0)
	out_mean, out_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, out_box, threshold=2.0
	)
	assert out_mean is None
	assert out_count == 0


#============================================
def test_above_threshold_but_invalid_pixel_excluded() -> None:
	"""A pixel above threshold but validity_mask == 0 is excluded (mask authoritative)."""
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	roi_origin = (40, 80)
	box = common_tools.coord_space.ProcessedBox(cx=48.0, cy=84.0, w=8.0, h=4.0)
	# One valid above-threshold pixel and one invalid above-threshold pixel.
	residual_dog[3, 5] = 8.0
	residual_dog[3, 6] = 9.0
	validity_mask[3, 6] = 0
	hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
		residual_dog, validity_mask, roi_origin, box, threshold=2.0
	)
	# Only the valid pixel (value 8.0) survives.
	assert hot_count == 1
	assert abs(hot_mean - 8.0) < 1e-6


#============================================
def test_wrong_space_box_raises() -> None:
	"""A SourceBox handed to the primitive raises ValueError (require guard)."""
	residual_dog, validity_mask = _bin4_arrays(12, 16)
	source_box = common_tools.coord_space.SourceBox(cx=48.0, cy=84.0, w=8.0, h=4.0)
	raised = False
	try:
		common_tools.in_box_heat.measure_in_box_heat(
			residual_dog, validity_mask, (40, 80), source_box, threshold=2.0
		)
	except ValueError:
		raised = True
	assert raised
