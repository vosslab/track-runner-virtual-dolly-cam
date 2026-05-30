"""Unit tests for common_tools/coord_space.py typed coordinate primitives.

Property-based tests only -- no hardcoded pixel constants pinned from the
current implementation.  All assertions verify behavioral invariants:

  1. Round-trip identity within rounding at bin_factor in {1, 2, 4}.
  2. edges() derives from float center (center == midpoint, span == size).
  3. in_bounds is a real predicate at the goodbox edge.
  4. Double-conversion is unrepresentable (the core design property).
  5. require_* guards raise ValueError for wrong-space, pass through for right-space.
  6. bin=4 asymmetric sentinel: no transpose / axis-swap / scale bug.

Geometry is built via _resolve_frame_geometry (real goodbox snap) so the
tests exercise the real conversion path, not a hand-faked geometry.
"""

import math
import sys
import pathlib
import pytest

# Ensure repo root is on sys.path for the common_tools imports.
_REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

import common_tools.coord_space
import common_tools.frame_reader


#============================================
# Helpers
#============================================

def _make_geometry(source_width: int, source_height: int, bin_factor: int):
	"""Build a real FrameGeometry via the production _resolve_frame_geometry path.

	Uses the internal _resolve_frame_geometry so the goodbox snap is exercised
	identically to how FrameReader would build the geometry.  This is the
	"real FrameGeometry" the task brief requires.
	"""
	# _resolve_frame_geometry is module-private but accessible for test use.
	return common_tools.frame_reader._resolve_frame_geometry(
		source_width, source_height, bin_factor
	)


#============================================
# Property 1: Round-trip identity (SourcePoint and SourceBox)
#============================================

@pytest.mark.parametrize("bin_factor", [1, 2, 4])
def test_source_point_round_trip(bin_factor: int):
	"""SourcePoint -> ProcessedPoint -> SourcePoint is identity within rounding."""
	geometry = _make_geometry(2816, 1584, bin_factor)
	# Choose a center well inside the frame so in_bounds is not at issue.
	cx_src = 700.0
	cy_src = 400.0
	sp = common_tools.coord_space.SourcePoint(cx=cx_src, cy=cy_src)
	pp = sp.to_processed(geometry)
	sp_rt = pp.to_source(geometry)
	# Tolerance: one full bin-pixel (bin_factor) covers the rounding loss.
	tol = float(bin_factor)
	assert abs(sp_rt.cx - cx_src) <= tol
	assert abs(sp_rt.cy - cy_src) <= tol


@pytest.mark.parametrize("bin_factor", [1, 2, 4])
def test_source_box_round_trip(bin_factor: int):
	"""SourceBox -> ProcessedBox -> SourceBox is identity within rounding."""
	geometry = _make_geometry(3840, 2160, bin_factor)
	# Non-divisible-by-bin center so the scale is exercised on a non-grid center.
	cx_src = 1870.0
	cy_src = 673.0
	w_src = 36.0
	h_src = 70.0
	sb = common_tools.coord_space.SourceBox(cx=cx_src, cy=cy_src, w=w_src, h=h_src)
	pb = sb.to_processed(geometry)
	sb_rt = pb.to_source(geometry)
	tol = float(bin_factor)
	assert abs(sb_rt.cx - cx_src) <= tol
	assert abs(sb_rt.cy - cy_src) <= tol
	assert abs(sb_rt.w - w_src) <= tol
	assert abs(sb_rt.h - h_src) <= tol


#============================================
# Property 2: edges() derives from float center
#============================================

def test_source_box_edges_midpoint_and_span():
	"""SourceBox.edges() midpoint == (cx, cy) and span == (w, h)."""
	sb = common_tools.coord_space.SourceBox(cx=300.5, cy=200.3, w=40.0, h=60.0)
	x1, y1, x2, y2 = sb.edges()
	# Midpoint of the edge pair must equal center.
	assert math.isclose((x1 + x2) / 2.0, sb.cx)
	assert math.isclose((y1 + y2) / 2.0, sb.cy)
	# Span must equal the declared size.
	assert math.isclose(x2 - x1, sb.w)
	assert math.isclose(y2 - y1, sb.h)


def test_processed_box_edges_midpoint_and_span():
	"""ProcessedBox.edges() midpoint == (cx, cy) and span == (w, h)."""
	pb = common_tools.coord_space.ProcessedBox(cx=150.7, cy=80.2, w=22.0, h=38.0)
	x1, y1, x2, y2 = pb.edges()
	assert math.isclose((x1 + x2) / 2.0, pb.cx)
	assert math.isclose((y1 + y2) / 2.0, pb.cy)
	assert math.isclose(x2 - x1, pb.w)
	assert math.isclose(y2 - y1, pb.h)


#============================================
# Property 3: in_bounds predicate at goodbox edge
#============================================

def test_in_bounds_just_inside():
	"""ProcessedPoint just inside [0, processed_width) x [0, processed_height) -> True."""
	geometry = _make_geometry(2816, 1584, 2)
	# Place the point one pixel inside each edge.
	px = float(geometry.processed_width - 1)
	py = float(geometry.processed_height - 1)
	pp = common_tools.coord_space.ProcessedPoint(cx=px, cy=py)
	assert pp.in_bounds(geometry) is True


def test_in_bounds_at_processed_width_is_false():
	"""ProcessedPoint at exactly processed_width -> False (half-open interval)."""
	geometry = _make_geometry(3840, 2160, 4)
	# The processed_width itself is outside the half-open [0, processed_width) range.
	pp = common_tools.coord_space.ProcessedPoint(
		cx=float(geometry.processed_width),
		cy=0.0,
	)
	assert pp.in_bounds(geometry) is False


def test_in_bounds_at_processed_height_is_false():
	"""ProcessedPoint at exactly processed_height -> False (half-open interval)."""
	geometry = _make_geometry(3840, 2160, 4)
	pp = common_tools.coord_space.ProcessedPoint(
		cx=0.0,
		cy=float(geometry.processed_height),
	)
	assert pp.in_bounds(geometry) is False


def test_in_bounds_negative_is_false():
	"""ProcessedPoint with negative coordinate -> False."""
	geometry = _make_geometry(2816, 1584, 2)
	pp = common_tools.coord_space.ProcessedPoint(cx=-1.0, cy=100.0)
	assert pp.in_bounds(geometry) is False


def test_processed_box_in_bounds_uses_center():
	"""ProcessedBox.in_bounds mirrors ProcessedPoint.in_bounds on the center."""
	geometry = _make_geometry(2816, 1584, 2)
	inside_cx = float(geometry.processed_width - 1)
	inside_cy = float(geometry.processed_height - 1)
	# Large box whose center is inside; in_bounds should be True.
	pb_inside = common_tools.coord_space.ProcessedBox(
		cx=inside_cx, cy=inside_cy, w=500.0, h=500.0
	)
	assert pb_inside.in_bounds(geometry) is True
	# Box whose center is at the boundary; in_bounds should be False.
	pb_outside = common_tools.coord_space.ProcessedBox(
		cx=float(geometry.processed_width),
		cy=inside_cy,
		w=10.0,
		h=10.0,
	)
	assert pb_outside.in_bounds(geometry) is False


#============================================
# Property 4: Double-conversion is unrepresentable (core design contract)
#============================================

def test_processed_point_has_no_to_processed():
	"""ProcessedPoint must not have a to_processed method (double-conversion impossible)."""
	assert hasattr(common_tools.coord_space.ProcessedPoint(1.0, 1.0), "to_processed") is False


def test_source_point_has_no_to_source():
	"""SourcePoint must not have a to_source method (double-conversion impossible)."""
	assert hasattr(common_tools.coord_space.SourcePoint(1.0, 1.0), "to_source") is False


def test_processed_box_has_no_to_processed():
	"""ProcessedBox must not have a to_processed method."""
	assert hasattr(
		common_tools.coord_space.ProcessedBox(1.0, 1.0, 10.0, 10.0), "to_processed"
	) is False


def test_source_box_has_no_to_source():
	"""SourceBox must not have a to_source method."""
	assert hasattr(
		common_tools.coord_space.SourceBox(1.0, 1.0, 10.0, 10.0), "to_source"
	) is False


#============================================
# Property 5: require_* boundary guards
#============================================

def test_require_source_point_passes_correct_type():
	"""require_source_point returns the same object when given a SourcePoint."""
	sp = common_tools.coord_space.SourcePoint(cx=10.0, cy=20.0)
	result = common_tools.coord_space.require_source_point(sp)
	assert result is sp


def test_require_source_point_raises_for_wrong_type():
	"""require_source_point raises ValueError when given a ProcessedPoint."""
	pp = common_tools.coord_space.ProcessedPoint(cx=10.0, cy=20.0)
	with pytest.raises(ValueError):
		common_tools.coord_space.require_source_point(pp)


def test_require_processed_point_passes_correct_type():
	"""require_processed_point returns the object when given a ProcessedPoint."""
	pp = common_tools.coord_space.ProcessedPoint(cx=5.0, cy=6.0)
	result = common_tools.coord_space.require_processed_point(pp)
	assert result is pp


def test_require_processed_point_raises_for_wrong_type():
	"""require_processed_point raises ValueError when given a SourcePoint."""
	sp = common_tools.coord_space.SourcePoint(cx=5.0, cy=6.0)
	with pytest.raises(ValueError):
		common_tools.coord_space.require_processed_point(sp)


def test_require_source_box_passes_correct_type():
	"""require_source_box returns the object when given a SourceBox."""
	sb = common_tools.coord_space.SourceBox(cx=1.0, cy=2.0, w=3.0, h=4.0)
	result = common_tools.coord_space.require_source_box(sb)
	assert result is sb


def test_require_source_box_raises_for_wrong_type():
	"""require_source_box raises ValueError when given a ProcessedBox."""
	pb = common_tools.coord_space.ProcessedBox(cx=1.0, cy=2.0, w=3.0, h=4.0)
	with pytest.raises(ValueError):
		common_tools.coord_space.require_source_box(pb)


def test_require_processed_box_passes_correct_type():
	"""require_processed_box returns the object when given a ProcessedBox."""
	pb = common_tools.coord_space.ProcessedBox(cx=7.0, cy=8.0, w=9.0, h=10.0)
	result = common_tools.coord_space.require_processed_box(pb)
	assert result is pb


def test_require_processed_box_raises_for_wrong_type():
	"""require_processed_box raises ValueError when given a SourceBox."""
	sb = common_tools.coord_space.SourceBox(cx=7.0, cy=8.0, w=9.0, h=10.0)
	with pytest.raises(ValueError):
		common_tools.coord_space.require_processed_box(sb)


#============================================
# Property 6: bin=4 asymmetric sentinel (no transpose / axis-swap / scale bug)
#============================================

# Sentinel constants: non-square source, bin=4, center not divisible by 4, w != h.
# Any axis-swap (x<->y) or scale error (off-by-bin) will break the assertions.
_SENTINEL_BIN = 4
_SENTINEL_SOURCE_W = 3840
_SENTINEL_SOURCE_H = 2160
# Processed-space box: cx%4 != 0, w != h.
_SENTINEL_PROC_CX = 213.0
_SENTINEL_PROC_CY = 157.0
_SENTINEL_PROC_W = 46.0   # x-extent (narrower)
_SENTINEL_PROC_H = 74.0   # y-extent (taller)


def test_sentinel_source_box_round_trip_asymmetric():
	"""bin=4, non-square, non-aligned: SourceBox round-trips within bin_factor tolerance.

	Any transpose (swapping x<->y or w<->h) will fail because w != h and cx != cy.
	Any double-conversion will be off by bin_factor^2.
	"""
	geometry = _make_geometry(_SENTINEL_SOURCE_W, _SENTINEL_SOURCE_H, _SENTINEL_BIN)
	# Build a SourceBox using the sentinel values scaled up to source space.
	cx_src = _SENTINEL_PROC_CX * _SENTINEL_BIN
	cy_src = _SENTINEL_PROC_CY * _SENTINEL_BIN
	w_src = _SENTINEL_PROC_W * _SENTINEL_BIN
	h_src = _SENTINEL_PROC_H * _SENTINEL_BIN
	sb = common_tools.coord_space.SourceBox(cx=cx_src, cy=cy_src, w=w_src, h=h_src)
	pb = sb.to_processed(geometry)
	sb_rt = pb.to_source(geometry)
	tol = float(_SENTINEL_BIN)
	assert abs(sb_rt.cx - cx_src) <= tol
	assert abs(sb_rt.cy - cy_src) <= tol
	assert abs(sb_rt.w - w_src) <= tol
	assert abs(sb_rt.h - h_src) <= tol


def test_sentinel_edges_no_axis_swap():
	"""bin=4 ProcessedBox edges: x-extent derives from w (narrow), y-extent from h (tall).

	With w < h and cx != cy, any transpose of w<->h or cx<->cy is detectable.
	"""
	pb = common_tools.coord_space.ProcessedBox(
		cx=_SENTINEL_PROC_CX,
		cy=_SENTINEL_PROC_CY,
		w=_SENTINEL_PROC_W,
		h=_SENTINEL_PROC_H,
	)
	x1, y1, x2, y2 = pb.edges()
	# x-extent (horizontal span) must equal w, not h.
	assert math.isclose(x2 - x1, _SENTINEL_PROC_W)
	# y-extent (vertical span) must equal h, not w.
	assert math.isclose(y2 - y1, _SENTINEL_PROC_H)
	# Horizontal midpoint must equal cx.
	assert math.isclose((x1 + x2) / 2.0, _SENTINEL_PROC_CX)
	# Vertical midpoint must equal cy.
	assert math.isclose((y1 + y2) / 2.0, _SENTINEL_PROC_CY)
	# Sanity: with w < h the x-span must be strictly less than the y-span.
	assert (x2 - x1) < (y2 - y1)


def test_sentinel_processed_box_to_source_scale_not_doubled():
	"""bin=4: ProcessedBox -> SourceBox scales by exactly bin_factor, not bin^2.

	A double-conversion bug would multiply by bin_factor^2 == 16.
	"""
	geometry = _make_geometry(_SENTINEL_SOURCE_W, _SENTINEL_SOURCE_H, _SENTINEL_BIN)
	pb = common_tools.coord_space.ProcessedBox(
		cx=_SENTINEL_PROC_CX,
		cy=_SENTINEL_PROC_CY,
		w=_SENTINEL_PROC_W,
		h=_SENTINEL_PROC_H,
	)
	sb = pb.to_source(geometry)
	# Pure-scale relationship: source == processed * bin_factor.
	assert math.isclose(sb.cx, _SENTINEL_PROC_CX * _SENTINEL_BIN)
	assert math.isclose(sb.cy, _SENTINEL_PROC_CY * _SENTINEL_BIN)
	# w and h scale independently: no transpose.
	assert math.isclose(sb.w, _SENTINEL_PROC_W * _SENTINEL_BIN)
	assert math.isclose(sb.h, _SENTINEL_PROC_H * _SENTINEL_BIN)
	# Cross-check: source w != source h (same asymmetry preserved, no swap).
	assert sb.w != sb.h
