"""Small-target blob recovery across bin factors.

The pixel-area bin budget (frame_reader.MAX_ANALYSIS_PIXELS) trades analysis
resolution for speed, which matters most when the runner is small in frame.
This harness synthesizes a frame pair in memory, runs the production residual
and blob-extraction path over a sweep of target sizes and bin factors, and
measures whether the moving target is still recovered and how far its centroid
lands from the true displacement midpoint.

Everything is generated in memory, so the measurement is repeatable and needs
no recorded footage. The sweep below is the source of the recovery table
published in docs/active_plans/reports/bin_target_table.md; regenerate that
table from `measure_recovery` rather than by hand.
"""

# PIP3 modules
import cv2
import numpy
import pytest

# local repo modules
import residual_motion

# Synthetic source geometry and the displacement applied between the pair.
SOURCE_WIDTH = 1920
SOURCE_HEIGHT = 1080
TARGET_START_X = 800
TARGET_Y = 500
TARGET_STEP_X = 12

# Target sizes spanning comfortable down to near the extraction floor.
TARGET_SIZES = ((40, 80), (20, 40), (10, 20), (6, 12))

# Bin factors the area budget selects, plus one beyond it for headroom.
BIN_FACTORS = (1, 2, 3, 4)

# Fraction of the residual peak used as the extraction threshold.
THRESHOLD_FRACTION = 0.25


#============================================
def _frame_with_target(target_x: int, target_w: int, target_h: int) -> numpy.ndarray:
	"""Return a grayscale frame holding one bright rectangle."""
	frame = numpy.full(
		(SOURCE_HEIGHT, SOURCE_WIDTH), 40, dtype=numpy.uint8,
	)
	frame[TARGET_Y:TARGET_Y + target_h, target_x:target_x + target_w] = 220
	return frame


#============================================
def _bin_frame(frame: numpy.ndarray, bin_factor: int) -> numpy.ndarray:
	"""Downsample a frame the way the reader does at this bin factor."""
	if bin_factor == 1:
		return frame
	scaled = cv2.resize(
		frame,
		(SOURCE_WIDTH // bin_factor, SOURCE_HEIGHT // bin_factor),
		interpolation=cv2.INTER_AREA,
	)
	return scaled


#============================================
def measure_recovery(target_w: int, target_h: int, bin_factor: int) -> tuple:
	"""Return (recovered, blob_area, centroid error in SOURCE pixels).

	Builds the displaced frame pair at this target size, bins both frames, and
	runs the production residual extraction over the result.

	Args:
		target_w: Target width in source pixels.
		target_h: Target height in source pixels.
		bin_factor: Downsample factor applied before extraction.

	Returns:
		Tuple of (recovered, area, error_source_px). When no blob is
		recovered, area is 0 and the error is NaN.
	"""
	first = _bin_frame(_frame_with_target(TARGET_START_X, target_w, target_h), bin_factor)
	second = _bin_frame(
		_frame_with_target(TARGET_START_X + TARGET_STEP_X, target_w, target_h),
		bin_factor,
	)
	residual = numpy.abs(
		second.astype(numpy.float32) - first.astype(numpy.float32),
	)
	validity_mask = numpy.full(residual.shape, 255, dtype=numpy.uint8)
	peak = float(residual.max())
	if peak <= 0.0:
		return (False, 0, float("nan"))
	blobs = residual_motion.extract_frame_blobs(
		residual, validity_mask, THRESHOLD_FRACTION * peak,
	)
	if not blobs:
		return (False, 0, float("nan"))
	blob = blobs[0]
	# True midpoint of the leave/arrive pair, in this bin factor's pixels.
	expected_x = (TARGET_START_X + target_w * 0.5 + TARGET_STEP_X * 0.5) / bin_factor
	error_source_px = abs(blob["centroid_x"] - expected_x) * bin_factor
	return (True, blob["area"], error_source_px)


#============================================
@pytest.mark.parametrize("target_size", TARGET_SIZES)
@pytest.mark.parametrize("bin_factor", BIN_FACTORS)
def test_small_target_is_recovered(target_size: tuple, bin_factor: int) -> None:
	"""Every swept target size still yields a blob at every bin factor."""
	target_w, target_h = target_size
	recovered, _area, _error = measure_recovery(target_w, target_h, bin_factor)
	assert recovered, (
		f"bin_factor={bin_factor}: no residual blob recovered for a"
		f" {target_w}x{target_h} source target"
	)


#============================================
@pytest.mark.parametrize("target_size", TARGET_SIZES)
@pytest.mark.parametrize("bin_factor", BIN_FACTORS)
def test_recovered_centroid_tracks_the_true_displacement(
	target_size: tuple, bin_factor: int,
) -> None:
	"""Recovered centroids stay within the leave/arrive lobe half-span.

	A residual of a displaced rectangle has two lobes, one where the target
	left and one where it arrived. Their combined centroid sits near the
	midpoint, so the achievable error scales with the lobe separation,
	(target width + displacement) / 2, rather than with target width alone.
	"""
	target_w, target_h = target_size
	_recovered, _area, error_source_px = measure_recovery(
		target_w, target_h, bin_factor,
	)
	lobe_half_span = (target_w + TARGET_STEP_X) / 2.0
	assert error_source_px <= lobe_half_span, (
		f"bin_factor={bin_factor}, target {target_w}x{target_h}: centroid error"
		f" {error_source_px:.2f} source px exceeds the lobe half-span"
		f" {lobe_half_span:.2f} px"
	)
