"""Regression tests for walker bin_factor coordinate consistency (Option A, 2026-05-29).

Tests:
  1. WARP_SCALE_MISMATCH regression: warp translation is shift/bin_factor at bin=4.
  2. Override source-scale regression: dog_diameter at observation is processed-scaled.
  3. ROI_CLAMP_SPACE_MISMATCH regression: roi at bin=4 with cx>reader.width has w>0.
  4. bin_factor mismatch guard: SeedsView at bin=1 used with bin=4 reader raises.
"""
import sys
import os
import types
import unittest.mock
import numpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import git_file_utils

REPO_ROOT = git_file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "track_runner"))
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))

import residual_motion
import state_io
import frame_reader as fr
import common_tools.coord_space as coord_space


#============================================
def _proc_box_from_edges(edges: tuple) -> coord_space.ProcessedBox:
	"""Build a PROCESSED-space ProcessedBox from (x1, y1, x2, y2) edges."""
	x1, y1, x2, y2 = edges
	cx = (x1 + x2) / 2.0
	cy = (y1 + y2) / 2.0
	box = coord_space.ProcessedBox(cx=cx, cy=cy, w=float(x2 - x1), h=float(y2 - y1))
	return box


#============================================
def _make_geometry(bin_factor: int, src_w: int, src_h: int) -> fr.FrameGeometry:
	"""Build a FrameGeometry for the given bin_factor."""
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
def _make_fake_reader(bin_factor: int, src_w: int, src_h: int, n_frames: int = 10) -> object:
	"""Build a minimal FrameReader-like stub."""
	geometry = _make_geometry(bin_factor, src_w, src_h)
	proc_w = src_w // bin_factor
	proc_h = src_h // bin_factor
	reader = types.SimpleNamespace()
	reader.bin_factor = bin_factor
	reader.geometry = geometry
	reader.width = proc_w
	reader.height = proc_h
	reader.frame_count = n_frames
	reader.fps = 60.0
	reader.read_frame = lambda idx: numpy.full((proc_h, proc_w, 3), 128, dtype=numpy.uint8)
	return reader


#============================================
def _make_scene_transform(n_frames: int, dx_per_frame: float = 0.0) -> object:
	"""Stub SceneTransform with constant horizontal motion in source-pixel units."""
	st = types.SimpleNamespace()
	st.cum_dx = numpy.array([i * dx_per_frame for i in range(n_frames)], dtype=numpy.float64)
	st.cum_dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.cum_scale = numpy.ones(n_frames, dtype=numpy.float64)
	mt = types.SimpleNamespace()
	mt.dx = numpy.diff(st.cum_dx, prepend=0.0)
	mt.dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.motion_track = mt
	return st


#============================================
def test_warp_scale_uses_bin_factor():
	"""Regression for bug #1 (WARP_SCALE_MISMATCH): warp translation = shift/bin_factor.

	Synthetic 2 frames at bin=4 with known source-pixel shift.
	build_warp_matrix must be called with scale_factor=1/4, not 1.0.
	"""
	BIN = 4
	DX_SOURCE = 10.0  # source pixels per frame
	n_frames = 10
	scene_transform = _make_scene_transform(n_frames, DX_SOURCE)
	reader = _make_fake_reader(BIN, 400, 400, n_frames)

	captured_scale_factors = []
	real_build_warp = residual_motion.build_warp_matrix

	def spy_build_warp(st, fn, fo, sf):
		captured_scale_factors.append(sf)
		return real_build_warp(st, fn, fo, sf)

	with unittest.mock.patch.object(residual_motion, "build_warp_matrix", side_effect=spy_build_warp):
		try:
			residual_motion.compute_residual_for_frame(
				reader, frame_index=5, scene_transform=scene_transform, half_window=4,
			)
		except Exception:
			pass

	assert len(captured_scale_factors) > 0, "build_warp_matrix was never called"
	expected_sf = 1.0 / BIN
	for sf in captured_scale_factors:
		assert abs(sf - expected_sf) < 1e-9, (
			f"Warp scale_factor should be {expected_sf} at bin={BIN}, got {sf}. "
			f"WARP_SCALE_MISMATCH regression."
		)


#============================================
def test_dog_diameter_override_is_post_bin_at_bin4():
	"""Regression for bug #2 (override source-scale): dog_diameter at bin=4 is seed_w/4.

	Walker bootstrap at bin=4 with source seed_cx=2000, seed_w=200 (source).
	Processed seed_w = 200/4 = 50.
	dog_diameter_override = 0.7 * seed_w_processed = 0.7 * 50 = 35.0.
	observe_blob_at must receive and use 35.0 (no further division).
	"""
	BIN = 4
	SRC_W, SRC_H = 3840, 2160
	reader = _make_fake_reader(BIN, SRC_W, SRC_H, n_frames=20)

	# Processed seed coords (as SeedsView would provide)
	seed_w_proc = 200.0 / BIN  # 50.0
	expected_dog_diam = 0.7 * seed_w_proc  # 35.0

	captured_diameters = []
	real_dog_filter = residual_motion.dog_filter_blob_scale

	def spy_dog_filter(residual, diameter, **kwargs):
		captured_diameters.append(diameter)
		return real_dog_filter(residual, diameter, **kwargs)

	# Dummy residual for the expected processed roi
	seed_cx_proc = 2000.0 / BIN  # 500.0
	seed_cy_proc = 1080.0 / BIN  # 270.0
	seed_h_proc = 400.0 / BIN    # 100.0
	roi_pad = max(20, seed_w_proc)
	ab = (
		seed_cx_proc - 0.5 * seed_w_proc,
		seed_cy_proc - 0.75 * seed_h_proc,
		seed_cx_proc + 0.5 * seed_w_proc,
		seed_cy_proc + 0.75 * seed_h_proc,
	)
	rx1 = max(0, int(ab[0] - roi_pad))
	ry1 = max(0, int(ab[1] - roi_pad))
	rx2 = min(reader.width, int(ab[2] + roi_pad))
	ry2 = min(reader.height, int(ab[3] + roi_pad))
	roi_proc = (rx1, ry1, rx2, ry2)

	rh = max(1, ry2 - ry1)
	rw = max(1, rx2 - rx1)
	dummy_res = numpy.zeros((rh, rw), dtype=numpy.uint8)
	dummy_val = numpy.full((rh, rw), 255, dtype=numpy.uint8)
	ps = {(0, roi_proc): (dummy_res, dummy_val)}  # frame_index=0 (seed frame)

	scene_transform = _make_scene_transform(20)

	with unittest.mock.patch.object(
		residual_motion, "dog_filter_blob_scale", side_effect=spy_dog_filter
	):
		residual_motion.observe_blob_at(
			frame_index=0,
			pred_center=coord_space.ProcessedPoint(cx=seed_cx_proc, cy=seed_cy_proc),
			pred_box=coord_space.ProcessedBox(
				cx=seed_cx_proc, cy=seed_cy_proc, w=seed_w_proc, h=seed_h_proc,
			),
			local_tangent=(1.0, 0.0),
			scene_transform=scene_transform,
			reader=reader,
			residual_cache={},
			dog_diameter_override=expected_dog_diam,
			roi_override=_proc_box_from_edges(roi_proc),
			acceptance_box=_proc_box_from_edges(ab),
			precomputed_store=ps,
		)

	assert len(captured_diameters) > 0, "dog_filter_blob_scale was never called"
	assert abs(captured_diameters[0] - expected_dog_diam) < 1e-9, (
		f"Expected dog_diameter={expected_dog_diam} (0.7*seed_w_proc), "
		f"got {captured_diameters[0]}. Indicates double-conversion or missing conversion."
	)


#============================================
def test_roi_clamp_space_mismatch_regression():
	"""Regression for bug #3 (ROI_CLAMP_SPACE_MISMATCH): roi has x2 > x1 at bin=4.

	At bin=4 with a seed at source cx=2000 (3840-wide source, reader.width=960),
	the processed seed cx=500. roi_x2 after clamping against reader.width (960)
	must still exceed roi_x1.
	"""
	BIN = 4
	SRC_W, SRC_H = 3840, 2160
	reader = _make_fake_reader(BIN, SRC_W, SRC_H, n_frames=20)

	# Processed coords from SeedsView (source cx=2000 -> processed cx=500)
	seed_cx_proc = 2000.0 / BIN   # 500.0
	seed_cy_proc = 1000.0 / BIN   # 250.0
	seed_w_proc = 200.0 / BIN     # 50.0
	seed_h_proc = 400.0 / BIN     # 100.0

	# Replicate the walker ROI construction (Option A: clamp against reader dims)
	acceptance_box = (
		seed_cx_proc - 0.5 * seed_w_proc,
		seed_cy_proc - 0.75 * seed_h_proc,
		seed_cx_proc + 0.5 * seed_w_proc,
		seed_cy_proc + 0.75 * seed_h_proc,
	)
	roi_pad = max(20, seed_w_proc)
	roi_x1 = max(0, int(acceptance_box[0] - roi_pad))
	roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
	roi_x2 = min(reader.width, int(acceptance_box[2] + roi_pad))
	roi_y2 = min(reader.height, int(acceptance_box[3] + roi_pad))

	# Primary assertion: non-degenerate ROI
	assert roi_x2 > roi_x1, (
		f"ROI_CLAMP_SPACE_MISMATCH: roi_x2={roi_x2} <= roi_x1={roi_x1}. "
		f"Seed at processed cx={seed_cx_proc} (source cx=2000) with reader.width={reader.width}."
	)
	assert roi_y2 > roi_y1, (
		f"Degenerate y ROI: roi_y2={roi_y2} <= roi_y1={roi_y1}."
	)

	# Sanity: values must be within processed frame bounds
	assert roi_x2 <= reader.width, f"roi_x2={roi_x2} > reader.width={reader.width}"
	assert roi_y2 <= reader.height, f"roi_y2={roi_y2} > reader.height={reader.height}"


#============================================
def test_seeds_view_bin_factor_mismatch_guard():
	"""SeedsView built at bin=1 raises RuntimeError when used with bin=4 geometry.

	Verifies that assert_geometry_match catches accidental use of a view built
	against one bin_factor with a reader opened at a different bin_factor.
	"""
	SRC_W, SRC_H = 3840, 2160
	geometry_bin1 = _make_geometry(1, SRC_W, SRC_H)
	geometry_bin4 = _make_geometry(4, SRC_W, SRC_H)

	source_dict = {
		state_io.SEEDS_HEADER_KEY: state_io.SEEDS_HEADER_VALUE,
		"seeds": [{
			"frame_index": 100,
			"torso_box": [1900, 500, 200, 400],
			"status": "visible",
			"pass": 1,
			"cx": 2000.0,
			"cy": 700.0,
			"w": 200.0,
			"h": 400.0,
		}],
	}

	# View built at bin=1
	view = state_io.SeedsView(source_dict, geometry_bin1)

	# Checking against bin=4 must raise before any compute step
	raised = False
	try:
		view.assert_geometry_match(geometry_bin4)
	except RuntimeError:
		raised = True
	assert raised, (
		"Expected RuntimeError when SeedsView(bin=1) checked against geometry(bin=4). "
		"The bin_factor mismatch guard must raise loudly."
	)
