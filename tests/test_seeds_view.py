"""Tests for state_io.SeedsView and load_seeds_view (Option A, 2026-05-29).

Verifies:
  1. view.seeds at bin=1 returns same coords as source.
  2. view.seeds at bin=4 returns source/4 coords for cx/cy/w/h.
  3. assert_geometry_match raises RuntimeError when bin_factor differs.
  4. view.source returns the original source-pixel seeds dict unchanged.
  5. view.seeds is computed once and cached (lazy evaluation).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import git_file_utils

REPO_ROOT = git_file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "track_runner"))
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))

import state_io
import frame_reader as fr


#============================================
def _make_geometry(bin_factor: int, src_w: int = 3840, src_h: int = 2160) -> fr.FrameGeometry:
	"""Build a minimal FrameGeometry for the given bin_factor."""
	proc_w = src_w // bin_factor
	proc_h = src_h // bin_factor
	return fr.FrameGeometry(
		source_width=src_w,
		source_height=src_h,
		bin_factor=bin_factor,
		scaled_width=proc_w,
		scaled_height=proc_h,
		processed_width=proc_w,
		processed_height=proc_h,
	)


#============================================
def _make_source_seeds(seeds: list) -> dict:
	"""Build a minimal seeds dict in the state_io in-memory format."""
	return {
		state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
		"seeds": seeds,
	}


#============================================
def _seed(frame_index: int, cx: float, cy: float, w: float, h: float) -> dict:
	"""Build a minimal seed dict with derived cx/cy/w/h (as load_seeds returns)."""
	return {
		"frame_index": frame_index,
		"torso_box": [int(cx - w / 2), int(cy - h / 2), int(w), int(h)],
		"status": "visible",
		"pass": 1,
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
	}


#============================================
def test_view_processed_coords_at_bin1():
	"""view.seeds at bin=1 returns same coords as source (source==processed)."""
	geometry = _make_geometry(bin_factor=1)
	src_seed = _seed(100, cx=1000.0, cy=500.0, w=200.0, h=400.0)
	source_dict = _make_source_seeds([src_seed])
	view = state_io.SeedsView(source_dict, geometry)

	proc_seeds = view.seeds
	assert len(proc_seeds) == 1
	ps = proc_seeds[0]
	# at bin=1, source==processed; coords unchanged
	assert abs(ps["cx"] - 1000.0) < 1e-9
	assert abs(ps["cy"] - 500.0) < 1e-9
	assert abs(ps["w"] - 200.0) < 1e-9
	assert abs(ps["h"] - 400.0) < 1e-9


#============================================
def test_view_processed_coords_at_bin4():
	"""view.seeds at bin=4 returns source/4 coords for cx/cy/w/h."""
	geometry = _make_geometry(bin_factor=4)
	src_seed = _seed(200, cx=2346.0, cy=1080.0, w=200.0, h=400.0)
	source_dict = _make_source_seeds([src_seed])
	view = state_io.SeedsView(source_dict, geometry)

	proc_seeds = view.seeds
	assert len(proc_seeds) == 1
	ps = proc_seeds[0]
	# processed = source / bin_factor
	assert abs(ps["cx"] - 2346.0 / 4) < 1e-9, f"expected cx={2346.0/4}, got {ps['cx']}"
	assert abs(ps["cy"] - 1080.0 / 4) < 1e-9, f"expected cy={1080.0/4}, got {ps['cy']}"
	assert abs(ps["w"] - 200.0 / 4) < 1e-9, f"expected w={200.0/4}, got {ps['w']}"
	assert abs(ps["h"] - 400.0 / 4) < 1e-9, f"expected h={400.0/4}, got {ps['h']}"


#============================================
def test_view_assert_geometry_mismatch_raises():
	"""view built at bin=2 + assert against bin=4 geometry raises RuntimeError."""
	geometry_bin2 = _make_geometry(bin_factor=2)
	geometry_bin4 = _make_geometry(bin_factor=4)
	source_dict = _make_source_seeds([_seed(50, cx=500.0, cy=300.0, w=100.0, h=200.0)])
	view = state_io.SeedsView(source_dict, geometry_bin2)

	# Should raise when checked against a different bin_factor
	raised = False
	try:
		view.assert_geometry_match(geometry_bin4)
	except RuntimeError:
		raised = True
	assert raised, "Expected RuntimeError for bin_factor mismatch (bin2 vs bin4)"


#============================================
def test_view_source_accessor_unchanged():
	"""view.source returns the original source-pixel seeds dict unchanged.

	Verifies that building a view does not mutate the source dict, even after
	view.seeds is accessed (which projects coords to processed space).
	"""
	geometry = _make_geometry(bin_factor=4)
	original_cx = 2000.0
	src_seed = _seed(300, cx=original_cx, cy=800.0, w=180.0, h=360.0)
	source_dict = _make_source_seeds([src_seed])
	view = state_io.SeedsView(source_dict, geometry)

	# Access processed seeds to trigger caching
	_ = view.seeds

	# Source seed must remain unchanged
	assert view.source["seeds"][0]["cx"] == original_cx, (
		f"source cx was mutated: expected {original_cx}, "
		f"got {view.source['seeds'][0]['cx']}"
	)


#============================================
def test_view_lazy_evaluation():
	"""view.seeds is computed once and the same list object is returned on repeat access."""
	geometry = _make_geometry(bin_factor=2)
	source_dict = _make_source_seeds([_seed(10, cx=400.0, cy=200.0, w=80.0, h=160.0)])
	view = state_io.SeedsView(source_dict, geometry)

	# _processed_cache starts as None
	assert view._processed_cache is None

	first = view.seeds
	second = view.seeds

	# Same list object returned both times (cached)
	assert first is second, "Expected the same cached list on repeated access"
	# Cache is set after first access
	assert view._processed_cache is not None
