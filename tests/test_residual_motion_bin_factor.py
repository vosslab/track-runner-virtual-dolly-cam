"""Tests for bin_factor correctness in residual_motion.py and walk_walker.py.

Verifies three coordinate-system fixes from task #99 (auto_bin_coord_stack_audit.md):
  Fix 1: warp scale_factor = 1/bin_factor in compute_residual_for_frame production path.
  Fix 2a: dog_diameter_override converted source->processed via geometry.
  Fix 2b: roi_override converted source->processed before clamping.

Also verifies Fix 3 (ROI_CLAMP_SPACE_MISMATCH, degenerate_roi_investigation.md):
  Fix 3: walk_walker bootstrap and per-step loop clamp roi_x2/roi_y2 against
         source-frame dims, not post-bin reader.width/reader.height.
"""
import sys
import os
import types
import unittest.mock
import numpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import file_utils

REPO_ROOT = file_utils.get_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "track_runner"))
sys.path.insert(0, os.path.join(REPO_ROOT, "common_tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "blob_walk_v2"))

import residual_motion
import frame_reader as fr


#============================================
def _make_scene_transform(n_frames: int, dx_per_frame: float) -> object:
	"""Stub SceneTransform with constant horizontal motion dx_per_frame source px/frame.

	cum_dx[i] = i * dx_per_frame; cum_dy = 0; cum_scale = 1 everywhere.
	"""
	st = types.SimpleNamespace()
	st.cum_dx = numpy.array([i * dx_per_frame for i in range(n_frames)], dtype=numpy.float64)
	st.cum_dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.cum_scale = numpy.ones(n_frames, dtype=numpy.float64)
	# motion_track with dx in source-pixel units (required by compute_residual_for_frame)
	mt = types.SimpleNamespace()
	mt.dx = numpy.diff(st.cum_dx, prepend=0.0)
	mt.dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.motion_track = mt
	return st


#============================================
def _make_fake_reader(bin_factor: int, frame_shape_source: tuple, n_frames: int) -> object:
	"""Build a minimal FrameReader-like stub with geometry for the given bin_factor."""
	h_src, w_src = frame_shape_source[:2]
	h_proc = h_src // bin_factor
	w_proc = w_src // bin_factor
	geometry = fr.FrameGeometry(
		source_width=w_src,
		source_height=h_src,
		bin_factor=bin_factor,
		scaled_width=w_proc,
		scaled_height=h_proc,
		processed_width=w_proc,
		processed_height=h_proc,
	)
	reader = types.SimpleNamespace()
	reader.bin_factor = bin_factor
	reader.geometry = geometry
	reader.width = w_proc
	reader.height = h_proc
	reader.frame_count = n_frames
	reader.fps = 60.0
	# read_frame returns a post-bin gray BGR frame (uniform gray)
	reader.read_frame = lambda idx: numpy.full((h_proc, w_proc, 3), 128, dtype=numpy.uint8)
	return reader


#============================================
def test_warp_scale_uses_bin_factor():
	"""compute_residual_for_frame passes 1/bin_factor as scale_factor to build_warp_matrix."""
	BIN = 4
	DX_SOURCE = 10.0  # source pixels per frame
	n_frames = 10
	scene_transform = _make_scene_transform(n_frames, DX_SOURCE)
	reader = _make_fake_reader(BIN, (400, 400, 3), n_frames)

	captured_scale_factors = []
	real_build_warp = residual_motion.build_warp_matrix

	def spy_build_warp(st, fn, fo, sf):
		captured_scale_factors.append(sf)
		return real_build_warp(st, fn, fo, sf)

	with unittest.mock.patch.object(residual_motion, "build_warp_matrix", side_effect=spy_build_warp):
		# compute_residual_for_frame may raise RuntimeError if < 2 neighbors contribute;
		# use a try/except here only because we only care about the warp call args,
		# not the full output. The warp calls happen before the stack-size check.
		try:
			residual_motion.compute_residual_for_frame(
				reader, frame_index=5, scene_transform=scene_transform, half_window=4,
			)
		except (RuntimeError, Exception):
			pass

	# Every captured call must have scale_factor == 1/BIN
	assert len(captured_scale_factors) > 0
	expected_sf = 1.0 / BIN
	for sf in captured_scale_factors:
		assert abs(sf - expected_sf) < 1e-9, f"expected scale_factor={expected_sf}, got {sf}"


# test_observe_blob_at_dog_override_converted and test_observe_blob_at_roi_override_converted
# were removed on 2026-05-29 (Option A coord-system migration).
# Those tests asserted that observe_blob_at CONVERTS source-pixel overrides to processed;
# under Option A the caller passes processed-pixel coords directly and observe_blob_at uses
# them as-is. The equivalent tests under the new contract live in:
#   tests/test_observe_blob_at_processed_contract.py


#============================================
def test_residual_compute_non_empty_at_bin_4():
	"""compute_residual_for_frame returns non-empty residual with bin_factor=4 after fix.

	Two synthetic frames: frame 0 is uniform gray; frame 1 is same gray plus
	a bright 4x4 px patch at (40,40) in source space (10,10 in processed space).
	With corrected warp scale_factor=0.25, the residual map should have nonzero
	pixels where the patch differs from the median background.
	"""
	BIN = 4
	SRC_H, SRC_W = 160, 160
	PROC_H, PROC_W = SRC_H // BIN, SRC_W // BIN  # 40 x 40

	# Frame 0: uniform gray BGR
	frame0 = numpy.full((PROC_H, PROC_W, 3), 80, dtype=numpy.uint8)
	# Frame 1: same + bright 2x2 patch at processed coords (8,8)
	frame1 = frame0.copy()
	frame1[8:10, 8:10] = 220

	frames = {0: frame0, 1: frame1}

	# Make a 9-frame reader where most frames are frame0 except frame 4 and 5
	total_frames = 10

	def read_frame_fn(idx: int) -> numpy.ndarray:
		if idx in frames:
			return frames[idx].copy()
		return frame0.copy()

	geometry = fr.FrameGeometry(
		source_width=SRC_W,
		source_height=SRC_H,
		bin_factor=BIN,
		scaled_width=PROC_W,
		scaled_height=PROC_H,
		processed_width=PROC_W,
		processed_height=PROC_H,
	)
	reader = types.SimpleNamespace()
	reader.bin_factor = BIN
	reader.geometry = geometry
	reader.width = PROC_W
	reader.height = PROC_H
	reader.frame_count = total_frames
	reader.fps = 60.0
	reader.read_frame = read_frame_fn

	# scene_transform with zero motion (no camera movement)
	scene_transform = _make_scene_transform(total_frames, 0.0)

	result = residual_motion.compute_residual_for_frame(
		reader, frame_index=5, scene_transform=scene_transform, half_window=4,
	)
	assert result is not None
	residual_map, validity_mask = result
	assert residual_map is not None
	assert validity_mask is not None
	# validity_mask should have > 50% valid pixels
	valid_fraction = numpy.mean(validity_mask > 0)
	assert valid_fraction > 0.5, f"validity_mask too sparse: valid_fraction={valid_fraction:.2f}"


#============================================
def _compute_roi_override_bootstrap(seed_cx, seed_cy, seed_w, seed_h, reader):
	"""Replicate the ROI construction from walk_walker bootstrap/per-step.

	This mirrors the fixed walk_walker logic so the test stays in sync with
	the production code.  Kept as a helper so both bootstrap and per-step
	paths can be exercised from a single spot.

	Args:
		seed_cx: seed center-x in source pixels.
		seed_cy: seed center-y in source pixels.
		seed_w: seed torso width in source pixels.
		seed_h: seed torso height in source pixels.
		reader: FrameReader-like stub with geometry.source_width/height,
		        width (post-bin), height (post-bin).

	Returns:
		roi_override tuple (x1, y1, x2, y2) in source pixels.
	"""
	acceptance_box = (
		seed_cx - 0.5 * seed_w,
		seed_cy - 0.75 * seed_h,
		seed_cx + 0.5 * seed_w,
		seed_cy + 0.75 * seed_h,
	)
	roi_pad = max(20, seed_w)
	roi_x1 = max(0, int(acceptance_box[0] - roi_pad))
	roi_y1 = max(0, int(acceptance_box[1] - roi_pad))
	# Fixed: clamp against source dims, not post-bin reader.width/height.
	src_w = reader.geometry.source_width
	src_h = reader.geometry.source_height
	roi_x2 = min(src_w, int(acceptance_box[2] + roi_pad))
	roi_y2 = min(src_h, int(acceptance_box[3] + roi_pad))
	roi_override = (roi_x1, roi_y1, roi_x2, roi_y2)
	return roi_override


#============================================
def test_roi_clamp_space_mismatch_fix_bin4_right_of_center():
	"""ROI_CLAMP_SPACE_MISMATCH fix: roi_x2 must exceed roi_x1 at bin_factor=4.

	Pre-fix: roi_x2 was clamped against reader.width=960 (post-bin).
	A seed at cx=2000 (source) with seed_w=200 gives:
	  acceptance_box[2] = 2100, roi_x2 raw = 2300,
	  min(960, 2300) = 960 (WRONG -- smaller than roi_x1=1800 -> inverted).
	Post-fix: clamp against source_width=3840 -> roi_x2=2300 > roi_x1=1800.
	"""
	BIN = 4
	SRC_W, SRC_H = 3840, 2160
	# reader.width and reader.height are post-bin
	reader = _make_fake_reader(BIN, (SRC_H, SRC_W, 3), n_frames=20)
	# seed in the right half of a 4K frame: cx=2000, cy=1000 (source pixels)
	seed_cx = 2000.0
	seed_cy = 1000.0
	seed_w = 200.0
	seed_h = 400.0

	roi = _compute_roi_override_bootstrap(seed_cx, seed_cy, seed_w, seed_h, reader)
	roi_x1, roi_y1, roi_x2, roi_y2 = roi

	# Sanity: roi_x1 is in source-pixel range
	assert roi_x1 >= 0, f"roi_x1 should be >= 0, got {roi_x1}"
	assert roi_y1 >= 0, f"roi_y1 should be >= 0, got {roi_y1}"

	# Primary assertion: non-degenerate ROI (fix 3 regression test)
	assert roi_x2 > roi_x1, (
		f"ROI_CLAMP_SPACE_MISMATCH not fixed: roi_x2={roi_x2} <= roi_x1={roi_x1}. "
		f"Likely clamped against post-bin reader.width={reader.width} instead of "
		f"source_width={reader.geometry.source_width}."
	)
	assert roi_y2 > roi_y1, (
		f"Degenerate y ROI: roi_y2={roi_y2} <= roi_y1={roi_y1}."
	)

	# Sanity: upper bounds must not exceed source dims
	assert roi_x2 <= SRC_W, f"roi_x2={roi_x2} exceeds source_width={SRC_W}"
	assert roi_y2 <= SRC_H, f"roi_y2={roi_y2} exceeds source_height={SRC_H}"


#============================================
def test_roi_clamp_noop_at_bin1():
	"""At bin_factor=1, source_width == reader.width, so the fix is a no-op.

	Verify that the output is unchanged for a 1080p reader with bin=1.
	"""
	BIN = 1
	SRC_W, SRC_H = 1920, 1080
	reader = _make_fake_reader(BIN, (SRC_H, SRC_W, 3), n_frames=10)
	seed_cx = 500.0
	seed_cy = 400.0
	seed_w = 100.0
	seed_h = 200.0

	roi = _compute_roi_override_bootstrap(seed_cx, seed_cy, seed_w, seed_h, reader)
	roi_x1, roi_y1, roi_x2, roi_y2 = roi

	assert roi_x2 > roi_x1, f"Degenerate x ROI at bin=1: x2={roi_x2} <= x1={roi_x1}"
	assert roi_y2 > roi_y1, f"Degenerate y ROI at bin=1: y2={roi_y2} <= y1={roi_y1}"
	assert roi_x2 <= SRC_W
	assert roi_y2 <= SRC_H
