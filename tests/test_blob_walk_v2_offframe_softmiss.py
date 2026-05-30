"""WS2-C: walker off-frame soft-miss and typed observe boundary.

Covers the two WS2-C behaviors that are not exercised by the e2e/xfail
bug_101 integration gate:

  1. An off-frame PROCESSED anchor makes _compute_roi_and_observe return a
     (None, trace_sink_holder) soft-miss WITHOUT calling observe_blob_at,
     using the typed coord_space.ProcessedPoint.in_bounds predicate. This
     is the graceful replacement for the old degenerate-ROI crash (#101).
  2. An on-frame anchor calls observe_blob_at with typed PROCESSED
     primitives: pred_center is a ProcessedPoint, pred_box / roi_override /
     acceptance_box are ProcessedBoxes, and the roi_override / acceptance_box
     edges equal the pixel rectangle the walker builds today.

Offline, deterministic, well under one second. observe_blob_at is replaced
with a capture stub so no video decode happens.
"""

import sys
import pathlib


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))
import walk_paths
walk_paths.setup()

import common_tools.coord_space as coord_space
import common_tools.frame_reader
import walk_walker


# bin_factor=4 origin-preserving geometry; processed frame is 960 x 540.
_BIN_FACTOR = 4
_SOURCE_WIDTH = 3840
_SOURCE_HEIGHT = 2160


#============================================
def _build_geometry() -> common_tools.frame_reader.FrameGeometry:
	"""Construct a bin_factor=4 origin-preserving FrameGeometry."""
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
class _FakeReader:
	"""Minimal reader exposing PROCESSED dims and a FrameGeometry."""
	def __init__(self, geometry):
		self.geometry = geometry
		self.width = geometry.processed_width
		self.height = geometry.processed_height
		self.frame_count = 10000
		self.fps = 30.0


#============================================
def test_offframe_anchor_is_softmiss_without_observe(monkeypatch):
	"""Off-frame processed anchor returns (None, holder); observe not called."""
	geometry = _build_geometry()
	reader = _FakeReader(geometry)

	calls = []
	def _spy(**kwargs):
		calls.append(kwargs)
		return object()
	monkeypatch.setattr(walk_walker.residual_motion, "observe_blob_at", _spy)

	# anchor_cx well past processed_width (960) -> off-frame.
	obs, holder = walk_walker._compute_roi_and_observe(
		frame_f=500,
		anchor_cx=5000.0,
		anchor_cy=200.0,
		seed_w=40.0,
		seed_h=80.0,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=None,
		reader=reader,
		residual_cache={},
		fps=30.0,
		stride=1,
	)
	assert obs is None
	assert holder is not None
	# observe must NOT have been called on an off-frame anchor.
	assert calls == []


#============================================
def test_onframe_anchor_calls_observe_with_typed_processed_args(monkeypatch):
	"""On-frame anchor calls observe with typed PROCESSED primitives."""
	geometry = _build_geometry()
	reader = _FakeReader(geometry)

	captured = {}
	def _spy(**kwargs):
		captured.update(kwargs)
		return object()
	monkeypatch.setattr(walk_walker.residual_motion, "observe_blob_at", _spy)

	anchor_cx = 213.0
	anchor_cy = 157.0
	seed_w = 46.0
	seed_h = 74.0
	obs, holder = walk_walker._compute_roi_and_observe(
		frame_f=500,
		anchor_cx=anchor_cx,
		anchor_cy=anchor_cy,
		seed_w=seed_w,
		seed_h=seed_h,
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=None,
		reader=reader,
		residual_cache={},
		fps=30.0,
		stride=1,
	)
	assert obs is not None

	# pred_center and pred_box are typed PROCESSED primitives.
	assert isinstance(captured["pred_center"], coord_space.ProcessedPoint)
	assert isinstance(captured["pred_box"], coord_space.ProcessedBox)
	assert captured["pred_center"].cx == anchor_cx
	assert captured["pred_center"].cy == anchor_cy
	assert captured["pred_box"].w == seed_w
	assert captured["pred_box"].h == seed_h

	# acceptance_box is a ProcessedBox whose edges equal the walker's pixel
	# acceptance rectangle (cx +/- 0.5w, cy +/- 0.75h).
	assert isinstance(captured["acceptance_box"], coord_space.ProcessedBox)
	ab_x1, ab_y1, ab_x2, ab_y2 = captured["acceptance_box"].edges()
	assert ab_x1 == anchor_cx - 0.5 * seed_w
	assert ab_y1 == anchor_cy - 0.75 * seed_h
	assert ab_x2 == anchor_cx + 0.5 * seed_w
	assert ab_y2 == anchor_cy + 0.75 * seed_h

	# roi_override is a ProcessedBox whose edges equal the padded, clamped ROI.
	assert isinstance(captured["roi_override"], coord_space.ProcessedBox)
	roi_pad = max(20, seed_w)
	expected_roi = (
		max(0, int(anchor_cx - 0.5 * seed_w - roi_pad)),
		max(0, int(anchor_cy - 0.75 * seed_h - roi_pad)),
		min(reader.width, int(anchor_cx + 0.5 * seed_w + roi_pad)),
		min(reader.height, int(anchor_cy + 0.75 * seed_h + roi_pad)),
	)
	assert captured["roi_override"].edges() == expected_roi

	# dog_diameter_override stays the processed scalar 0.7 * seed_w.
	assert captured["dog_diameter_override"] == 0.7 * seed_w
