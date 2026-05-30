"""M2-A: fixed-ROI heat-movie frame compositor + raw-BGR spill.

These tests pin the memory/shape contract of heat_movie_frame, not brittle
values:

  1. compute_fixed_heat_roi_size derives a single constant (roiW, roiH) from the
     LARGER of the two bracketing seeds (by torso height), and that size does not
     depend on seed order;
  2. build_heat_movie_frame writes a file of EXACTLY roiW*roiH*3 bytes regardless
     of where the solved box sits, including a box pushed off the frame edge so
     black-fill triggers (the window is never shifted or scaled);
  3. the written frame dimensions are constant across several box positions;
  4. when compute_heat_map_roi returns None (residual-unavailable edge frame), the
     file is still the same fixed-shape byte count (black base + box only).

The heat compositor reads frame imagery only through
residual_heat_map.compute_heat_map_roi; here that function is monkeypatched so the
tests stay offline, deterministic, and well under one second (no video decode, no
residual pipeline).

All file writes go to tmp_path.
"""

import sys
import pathlib

import numpy

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_RENDER_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2' / 'render'
if str(_RENDER_DIR) not in sys.path:
	sys.path.insert(0, str(_RENDER_DIR))
import walk_paths
walk_paths.setup()

import residual_motion
import residual_heat_map
import common_tools.coord_space
import heat_movie_frame


#============================================
def _make_seed(cx, cy, w, h):
	"""Build a typed PROCESSED seed box."""
	box = common_tools.coord_space.ProcessedBox(cx=cx, cy=cy, w=w, h=h)
	return box


#============================================
def _install_fake_heat(monkeypatch, return_none=False):
	"""Monkeypatch compute_heat_map_roi to avoid any real video decode.

	When return_none is False, returns a small non-black composite at a fixed
	PROCESSED origin and populates out_arrays with above-threshold residual data
	so the in-box hot-mean path runs. When True, returns None to exercise the
	residual-unavailable edge-frame branch.
	"""
	def fake_compute(reader, frame_index, scene_transform, pred_center,
			pred_box, fps, threshold=residual_motion.DEFAULT_THRESHOLD,
			out_arrays=None):
		if return_none:
			return None
		# Small 30x40 composite placed near the solved center in PROCESSED px.
		comp_h, comp_w = 40, 30
		composite = numpy.full((comp_h, comp_w, 3), 128, dtype=numpy.uint8)
		origin_x = int(pred_center[0]) - comp_w // 2
		origin_y = int(pred_center[1]) - comp_h // 2
		if out_arrays is not None:
			# Above-threshold residual everywhere so the in-box hot-mean is real.
			residual_dog = numpy.full((comp_h, comp_w), threshold + 5.0,
				dtype=numpy.float32)
			validity_mask = numpy.ones((comp_h, comp_w), dtype=numpy.uint8)
			out_arrays['residual_dog'] = residual_dog
			out_arrays['validity_mask'] = validity_mask
			out_arrays['roi_bounds'] = (origin_x, origin_y,
				origin_x + comp_w, origin_y + comp_h)
			out_arrays['dog_k'] = residual_motion.DOG_K_FACTOR_DEFAULT
			out_arrays['threshold'] = threshold
		return (composite, (origin_x, origin_y))

	monkeypatch.setattr(residual_heat_map, 'compute_heat_map_roi', fake_compute)


#============================================
def test_fixed_size_from_larger_seed_is_order_independent():
	"""Window size derives from the larger seed by height, regardless of order."""
	small = _make_seed(100.0, 100.0, 18.0, 30.0)
	large = _make_seed(500.0, 400.0, 40.0, 74.0)
	size_a = heat_movie_frame.compute_fixed_heat_roi_size(small, large)
	size_b = heat_movie_frame.compute_fixed_heat_roi_size(large, small)
	# Same size both orders, and it follows the taller seed (h=74), not the short.
	assert size_a == size_b
	size_small_only = heat_movie_frame.compute_fixed_heat_roi_size(small, small)
	assert size_a[0] > size_small_only[0]


#============================================
def test_frame_byte_count_constant_across_positions(tmp_path, monkeypatch):
	"""Every written frame is exactly roiW*roiH*3 bytes, independent of position."""
	_install_fake_heat(monkeypatch)
	seed1 = _make_seed(640.0, 360.0, 30.0, 60.0)
	seed2 = _make_seed(640.0, 360.0, 30.0, 60.0)
	roi_w, roi_h = heat_movie_frame.compute_fixed_heat_roi_size(seed1, seed2)
	expected_bytes = roi_w * roi_h * 3

	class FakeReader:
		width = 1280
		height = 720

	reader = FakeReader()
	# A centered box and an edge box (window extends past the frame -> black-fill).
	centered = _make_seed(640.0, 360.0, 30.0, 60.0)
	near_edge = _make_seed(5.0, 5.0, 30.0, 60.0)
	for idx, box in ((10, centered), (11, near_edge)):
		path = heat_movie_frame.build_heat_movie_frame(
			reader, idx, scene_transform=None, solved_box=box,
			roi_size=(roi_w, roi_h), scratch_dir=str(tmp_path), fps=30.0,
		)
		written = pathlib.Path(path)
		assert written.stat().st_size == expected_bytes


#============================================
def test_dimensions_constant_via_reload(tmp_path, monkeypatch):
	"""Reloaded raw bytes reshape to the same (roiH, roiW, 3) for several boxes."""
	_install_fake_heat(monkeypatch)
	seed = _make_seed(640.0, 360.0, 30.0, 60.0)
	roi_w, roi_h = heat_movie_frame.compute_fixed_heat_roi_size(seed, seed)

	class FakeReader:
		width = 1280
		height = 720

	reader = FakeReader()
	boxes = [
		_make_seed(640.0, 360.0, 30.0, 60.0),
		_make_seed(1275.0, 715.0, 30.0, 60.0),
		_make_seed(0.0, 0.0, 30.0, 60.0),
	]
	for idx, box in enumerate(boxes):
		path = heat_movie_frame.build_heat_movie_frame(
			reader, idx, scene_transform=None, solved_box=box,
			roi_size=(roi_w, roi_h), scratch_dir=str(tmp_path), fps=30.0,
		)
		raw = pathlib.Path(path).read_bytes()
		arr = numpy.frombuffer(raw, dtype=numpy.uint8).reshape(roi_h, roi_w, 3)
		assert arr.shape == (roi_h, roi_w, 3)


#============================================
def test_none_heat_edge_frame_still_fixed_shape(tmp_path, monkeypatch):
	"""A compute_heat_map_roi-None frame still writes the fixed-shape byte count."""
	_install_fake_heat(monkeypatch, return_none=True)
	seed = _make_seed(640.0, 360.0, 30.0, 60.0)
	roi_w, roi_h = heat_movie_frame.compute_fixed_heat_roi_size(seed, seed)
	expected_bytes = roi_w * roi_h * 3

	class FakeReader:
		width = 1280
		height = 720

	reader = FakeReader()
	box = _make_seed(640.0, 360.0, 30.0, 60.0)
	path = heat_movie_frame.build_heat_movie_frame(
		reader, 0, scene_transform=None, solved_box=box,
		roi_size=(roi_w, roi_h), scratch_dir=str(tmp_path), fps=30.0,
	)
	assert pathlib.Path(path).stat().st_size == expected_bytes
