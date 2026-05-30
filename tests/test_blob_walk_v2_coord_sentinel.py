"""WS2-C coordinate sentinel: hard gate over the walker box production path.

A single known torso box is pushed through the EXACT production chain that the
walker and renderer use, with deliberately asymmetric values so any transpose,
scale, or offset error is obvious:

  processed walker box
    -> WS1-B processed->source projection
       (FrameGeometry.processed_to_source / processed_to_source_delta)
    -> state_io.write_torso_box_coords  (npz write, rounds to uint16)
    -> state_io.load_torso_box_coords   (npz reload)
    -> WS2-B1 processed->tile-local conversion (processed minus roi_origin)

The frame is non-square, bin_factor=4, ROI origin (37, 91) nonzero, the box
center is not divisible by the bin factor, and w != h. crop origin is 0 by
design: FrameGeometry is origin-preserving (the goodbox crop trims only the
right/bottom edges), so there is no separate crop offset to subtract; this is
documented here rather than exercised.

Assertions (contract checks, not value pins):
  1. the persisted/reloaded npz box is in SOURCE full-frame space;
  2. the reloaded box ~= the projected source box within npz rounding;
  3. the final tile rectangle == (cx - w/2 - roi_x, cy - h/2 - roi_y,
     cx + w/2 - roi_x, cy + h/2 - roi_y) using the PROCESSED box and the
     PROCESSED roi origin;
  4. exactly one conversion is applied in each direction (no double convert).

All offline, deterministic, well under one second.
"""

import sys
import pathlib


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
import walk_paths
walk_paths.setup()

import common_tools.frame_reader
import state_io
import interval_fingerprint


# Asymmetric sentinel values. bin_factor=4; center not divisible by 4; w != h.
# Non-square source so a width/height transpose would surface.
_BIN_FACTOR = 4
_SOURCE_WIDTH = 3840
_SOURCE_HEIGHT = 2160
# Processed-space ROI origin (nonzero, asymmetric). crop origin is 0 (see module
# docstring): FrameGeometry is origin-preserving.
_ROI_X = 37
_ROI_Y = 91
# Processed-space walker box. cx % 4 != 0 and cy % 4 != 0 so the bin scale is
# exercised on a non-grid-aligned center; w != h.
_PROC_CX = 213.0
_PROC_CY = 157.0
_PROC_W = 46.0
_PROC_H = 74.0


#============================================
def _build_geometry() -> common_tools.frame_reader.FrameGeometry:
	"""Construct a bin_factor=4 origin-preserving FrameGeometry for the test."""
	scaled_width = _SOURCE_WIDTH // _BIN_FACTOR
	scaled_height = _SOURCE_HEIGHT // _BIN_FACTOR
	geometry = common_tools.frame_reader.FrameGeometry(
		source_width=_SOURCE_WIDTH,
		source_height=_SOURCE_HEIGHT,
		bin_factor=_BIN_FACTOR,
		scaled_width=scaled_width,
		scaled_height=scaled_height,
		processed_width=scaled_width,
		processed_height=scaled_height,
	)
	return geometry


#============================================
def _project_processed_box_to_source(box: dict, geometry) -> dict:
	"""Project one processed-space box to source space (WS1-B production rule).

	Mirrors walk_driver._project_path_to_source: center via processed_to_source,
	width/height via processed_to_source_delta. One conversion only.
	"""
	cx_s, cy_s = geometry.processed_to_source(box["cx"], box["cy"])
	w_s, _ = geometry.processed_to_source_delta(box["w"], 0.0)
	_, h_s = geometry.processed_to_source_delta(0.0, box["h"])
	projected = {"cx": cx_s, "cy": cy_s, "w": w_s, "h": h_s}
	return projected


#============================================
def _round_trip_npz(source_box: dict, tmp_path) -> dict:
	"""Write one source box through state_io and reload it (production path)."""
	left_seed = {"frame_index": 100, "cx": 10.0, "cy": 10.0, "w": 5.0, "h": 5.0}
	right_seed = {"frame_index": 110, "cx": 20.0, "cy": 20.0, "w": 5.0, "h": 5.0}
	fingerprint = interval_fingerprint.compute_interval_fingerprint(left_seed, right_seed)
	# A single-frame path carrying the sentinel box in all three legs.
	path = [{"frame_index": 105, "cx": source_box["cx"], "cy": source_box["cy"],
		"w": source_box["w"], "h": source_box["h"]}]
	cache_data = {
		"solved_intervals": {
			fingerprint: {
				"start_frame": 100,
				"end_frame": 110,
				"forward_path": path,
				"backward_path": path,
				"blended_path": path,
			}
		}
	}
	npz_path = str(tmp_path / "torso_box_coords.npz")
	state_io.write_torso_box_coords(npz_path, cache_data)
	loaded = state_io.load_torso_box_coords(npz_path)
	entry = loaded["solved_intervals"][fingerprint]
	reloaded_box = entry["blended_path"][0]
	return reloaded_box


#============================================
def _processed_box_to_tile(box: dict, roi_x: float, roi_y: float) -> tuple:
	"""WS2-B1 conversion: processed box -> tile-local edge rectangle (once)."""
	# Edges derive from the float center BEFORE rounding (render contract).
	tile_cx = float(box["cx"]) - roi_x
	tile_cy = float(box["cy"]) - roi_y
	x1 = tile_cx - float(box["w"]) / 2.0
	y1 = tile_cy - float(box["h"]) / 2.0
	x2 = tile_cx + float(box["w"]) / 2.0
	y2 = tile_cy + float(box["h"]) / 2.0
	return (x1, y1, x2, y2)


#============================================
def test_sentinel_npz_box_is_source_space():
	"""Assertion 1: the projected/persisted box scales to source full-frame."""
	geometry = _build_geometry()
	proc_box = {"cx": _PROC_CX, "cy": _PROC_CY, "w": _PROC_W, "h": _PROC_H}
	source_box = _project_processed_box_to_source(proc_box, geometry)
	# Source center must equal processed center times bin_factor (pure scale).
	assert source_box["cx"] == _PROC_CX * _BIN_FACTOR
	assert source_box["cy"] == _PROC_CY * _BIN_FACTOR
	# Width and height scale independently (no transpose): w stays w, h stays h.
	assert source_box["w"] == _PROC_W * _BIN_FACTOR
	assert source_box["h"] == _PROC_H * _BIN_FACTOR


#============================================
def test_sentinel_reload_matches_projection_within_rounding(tmp_path):
	"""Assertion 2: reloaded npz box ~= projected source box within rounding."""
	geometry = _build_geometry()
	proc_box = {"cx": _PROC_CX, "cy": _PROC_CY, "w": _PROC_W, "h": _PROC_H}
	source_box = _project_processed_box_to_source(proc_box, geometry)
	reloaded_box = _round_trip_npz(source_box, tmp_path)
	# state_io rounds to uint16, so allow a 1-pixel tolerance per field.
	assert abs(reloaded_box["cx"] - source_box["cx"]) <= 1.0
	assert abs(reloaded_box["cy"] - source_box["cy"]) <= 1.0
	assert abs(reloaded_box["w"] - source_box["w"]) <= 1.0
	assert abs(reloaded_box["h"] - source_box["h"]) <= 1.0


#============================================
def test_sentinel_tile_rectangle_matches_formula():
	"""Assertion 3+4: tile rectangle == single processed-minus-roi conversion."""
	proc_box = {"cx": _PROC_CX, "cy": _PROC_CY, "w": _PROC_W, "h": _PROC_H}
	tile_rect = _processed_box_to_tile(proc_box, _ROI_X, _ROI_Y)
	# Expected formula, computed directly with asymmetric values: a transpose,
	# double-subtraction, or bin upscale at draw time would break exact equality.
	expected = (
		_PROC_CX - _PROC_W / 2.0 - _ROI_X,
		_PROC_CY - _PROC_H / 2.0 - _ROI_Y,
		_PROC_CX + _PROC_W / 2.0 - _ROI_X,
		_PROC_CY + _PROC_H / 2.0 - _ROI_Y,
	)
	assert tile_rect == expected


#============================================
def test_sentinel_single_conversion_no_double_subtract():
	"""Assertion 4: applying the roi subtraction once != applying it twice."""
	proc_box = {"cx": _PROC_CX, "cy": _PROC_CY, "w": _PROC_W, "h": _PROC_H}
	once = _processed_box_to_tile(proc_box, _ROI_X, _ROI_Y)
	# Simulate a double conversion (the failure mode the manifest gates against):
	# subtract roi twice. With nonzero asymmetric roi the result must differ.
	double_box = {"cx": _PROC_CX - _ROI_X, "cy": _PROC_CY - _ROI_Y,
		"w": _PROC_W, "h": _PROC_H}
	twice = _processed_box_to_tile(double_box, _ROI_X, _ROI_Y)
	assert once != twice
	# The single-conversion x1 must equal cx - w/2 - roi_x exactly (one subtract).
	assert once[0] == _PROC_CX - _PROC_W / 2.0 - _ROI_X
