"""WS2-F: the typed processed->tile-local render conversion (single subtract).

The render path draws seed/solved torso boxes that arrive in PROCESSED pixels.
TILE-LOCAL is the render-only third coordinate flavor: PROCESSED minus the
per-tile ROI origin (the tile is a crop of the processed frame).  This was kept
a render-local typed helper (walk_draw.processed_box_to_tile_local) rather than
a new pipeline space, because docs/COORDINATE_SPACES.md fixes the pipeline at
exactly two spaces (SOURCE, PROCESSED).

These tests pin the contract, not brittle values:
  1. the helper subtracts the ROI origin exactly once, so the tile-local edges
     equal the PROCESSED box edges minus the ROI origin (single subtraction);
  2. this holds at bin_factor=4 with a nonzero asymmetric ROI origin, the case
     where prior coordinate bugs hid (a SOURCE box treated as PROCESSED, or a
     double roi subtraction);
  3. the helper rejects a non-ProcessedBox (a SourceBox) loudly, so a space
     mismatch fails at the render boundary instead of drawing wrong geometry.

All offline, deterministic, well under one second.
"""

import sys
import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_RENDER_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2' / 'render'
if str(_RENDER_DIR) not in sys.path:
	sys.path.insert(0, str(_RENDER_DIR))
import walk_paths
walk_paths.setup()

import common_tools.coord_space
import walk_draw


# Asymmetric sentinel values: nonzero ROI origin, w != h, center not on the bin
# grid.  bin_factor is irrelevant to the tile-local subtraction itself (it is a
# PROCESSED->PROCESSED-minus-origin shift), but the test is framed at bin=4
# because that is where SOURCE-vs-PROCESSED confusion previously surfaced.
_ROI_X = 37
_ROI_Y = 91
_PROC_CX = 213.0
_PROC_CY = 157.0
_PROC_W = 46.0
_PROC_H = 74.0


#============================================
def test_tile_local_edges_equal_processed_edges_minus_roi_bin4():
	"""Tile-local edges == PROCESSED box edges minus the ROI origin (once)."""
	proc_box = common_tools.coord_space.ProcessedBox(
		cx=_PROC_CX, cy=_PROC_CY, w=_PROC_W, h=_PROC_H
	)
	roi_origin = (_ROI_X, _ROI_Y)
	tile_edges = walk_draw.processed_box_to_tile_local(proc_box, roi_origin)
	# Independent reference: the PROCESSED edges from the float center, then a
	# single ROI-origin subtraction applied to the x/y edges.
	px1, py1, px2, py2 = proc_box.edges()
	expected = (px1 - _ROI_X, py1 - _ROI_Y, px2 - _ROI_X, py2 - _ROI_Y)
	assert tile_edges == expected
	# And the explicit closed form, so a transpose or width/height swap surfaces.
	closed_form = (
		_PROC_CX - _PROC_W / 2.0 - _ROI_X,
		_PROC_CY - _PROC_H / 2.0 - _ROI_Y,
		_PROC_CX + _PROC_W / 2.0 - _ROI_X,
		_PROC_CY + _PROC_H / 2.0 - _ROI_Y,
	)
	assert tile_edges == closed_form


#============================================
def test_tile_local_single_subtract_not_double():
	"""Subtracting the ROI origin once differs from subtracting it twice."""
	proc_box = common_tools.coord_space.ProcessedBox(
		cx=_PROC_CX, cy=_PROC_CY, w=_PROC_W, h=_PROC_H
	)
	once = walk_draw.processed_box_to_tile_local(proc_box, (_ROI_X, _ROI_Y))
	# A box already shifted by the ROI origin, then converted again, simulates
	# the double-subtraction failure mode the manifest conversion_count gates.
	double_box = common_tools.coord_space.ProcessedBox(
		cx=_PROC_CX - _ROI_X, cy=_PROC_CY - _ROI_Y, w=_PROC_W, h=_PROC_H
	)
	twice = walk_draw.processed_box_to_tile_local(double_box, (_ROI_X, _ROI_Y))
	assert once != twice


#============================================
def test_tile_local_rejects_wrong_space():
	"""A SourceBox handed to the processed->tile-local helper fails loud."""
	source_box = common_tools.coord_space.SourceBox(
		cx=_PROC_CX, cy=_PROC_CY, w=_PROC_W, h=_PROC_H
	)
	with pytest.raises(ValueError):
		walk_draw.processed_box_to_tile_local(source_box, (_ROI_X, _ROI_Y))
