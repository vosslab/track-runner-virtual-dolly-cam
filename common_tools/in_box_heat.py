"""In-box motion-cue heat primitive (shared coordinate-sensitive seam).

This module computes how much residual-motion heat sits INSIDE a solved torso
box, in one shared primitive. Keeping the box->ROI selection here prevents
callers from drifting on the coordinate convention (the failure mode behind
bug #101).

THE COORDINATE CONVENTION
=========================

The residual DoG array and the validity mask are ROI crops of the PROCESSED
frame: array index (0, 0) corresponds to PROCESSED pixel `roi_origin`.  The box
is a typed PROCESSED-space center-size box.  We map the box into array
coordinates with EXACTLY ONE subtraction of `roi_origin`: the center is shifted
once, then the float edges are derived from the float center BEFORE any int
cast.

Box-region selection rule (FIXED, tested):

  - edges derive from the float center (no rounding before the shift);
  - left/top edges floor; right/bottom edges floor and are EXCLUSIVE;
  - the resulting region is clamped to the array bounds;
  - a zero-area or fully-out-of-ROI box yields no pixels (returns (None, 0));
  - inside the region, a pixel is selected only when it is BOTH valid
    (validity_mask != 0) and above threshold (residual_dog > threshold).  The
    validity mask is authoritative: a pixel above threshold but marked invalid
    is EXCLUDED, even though the residual was already zeroed at invalid pixels.

The intensity `threshold` is passed IN by the caller, which owns the
single-source-of-truth threshold.  This module never hardcodes or imports it.
"""

# PIP3 modules
import numpy

# local repo modules
import common_tools.coord_space


#============================================
def measure_in_box_heat(
	residual_dog: numpy.ndarray,
	validity_mask: numpy.ndarray,
	roi_origin: tuple,
	box: common_tools.coord_space.ProcessedBox,
	threshold: float,
) -> tuple:
	"""Measure motion-cue heat inside a PROCESSED torso box.

	Args:
		residual_dog: float32 HxW DoG response, ROI-cropped to PROCESSED frame;
			array index (0, 0) is PROCESSED pixel roi_origin.  Invalid pixels
			are already zeroed, but the validity mask remains authoritative.
		validity_mask: uint8 HxW, same ROI crop; nonzero marks a valid pixel.
		roi_origin: (roi_x_origin, roi_y_origin) in PROCESSED pixels.
		box: a typed PROCESSED-space center-size box.
		threshold: intensity threshold; a pixel qualifies when its DoG value is
			strictly greater than this value.

	Returns:
		(hot_mean, hot_count): hot_mean is the mean DoG value over selected
		pixels (in-box, valid, above threshold); hot_count is how many.  When
		no pixel qualifies, returns (None, 0).
	"""
	# Guard: refuse any non-ProcessedBox so a space mismatch fails loud.
	common_tools.coord_space.require_processed_box(box)
	roi_x_origin, roi_y_origin = roi_origin
	# Single ROI-origin subtraction: PROCESSED center -> array-local center.
	local_cx = box.cx - roi_x_origin
	local_cy = box.cy - roi_y_origin
	# Float edges derived from the float center BEFORE any int cast.
	x1_float = local_cx - box.w / 2.0
	y1_float = local_cy - box.h / 2.0
	x2_float = local_cx + box.w / 2.0
	y2_float = local_cy + box.h / 2.0
	# Rounding rule: floor left/top, floor right/bottom (right/bottom EXCLUSIVE).
	height, width = residual_dog.shape
	x1 = int(numpy.floor(x1_float))
	y1 = int(numpy.floor(y1_float))
	x2 = int(numpy.floor(x2_float))
	y2 = int(numpy.floor(y2_float))
	# Clamp the half-open region [x1, x2) x [y1, y2) to the array bounds.
	x1 = max(0, min(x1, width))
	x2 = max(0, min(x2, width))
	y1 = max(0, min(y1, height))
	y2 = max(0, min(y2, height))
	# Zero-area or fully-out-of-ROI box: nothing to measure.
	if x2 <= x1 or y2 <= y1:
		return (None, 0)
	# Crop both arrays to the clamped box region.
	dog_region = residual_dog[y1:y2, x1:x2]
	valid_region = validity_mask[y1:y2, x1:x2]
	# Select pixels that are BOTH valid and above threshold.
	# Validity mask is authoritative: an above-threshold but invalid pixel is excluded.
	selection_mask = (valid_region != 0) & (dog_region > threshold)
	selected = dog_region[selection_mask]
	hot_count = int(selected.size)
	# No qualifying pixel: report the sentinel.
	if hot_count == 0:
		return (None, 0)
	hot_mean = float(selected.mean())
	return (hot_mean, hot_count)
