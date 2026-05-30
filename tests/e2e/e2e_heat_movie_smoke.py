#!/usr/bin/env python3
"""E2E smoke for --heat-movie spill->ffmpeg->copy->cleanup pipeline (M2-C).

Exercises encode_heat_movie_for_direction end-to-end with a synthetic reader
and a small direction_path (5 frames). Asserts:
  - the destination .mkv exists and is non-empty after the run
  - every .bgr file is exactly roiW*roiH*3 bytes (constant ROI shape)
  - no run-scoped scratch directory remains after the run (no post-run leak)
  - the returned dest_path is a non-empty string (movie path reported)

Skips cleanly (exit 0) when ffmpeg is absent.

Run directly:
  source source_me.sh && python3 tests/e2e/e2e_heat_movie_smoke.py
"""

# Standard Library
import os
import sys
import glob
import shutil
import pathlib

# Resolve repo root: tests/e2e/ -> two levels up
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BLOB_WALK_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
# Put blob_walk_v2 root on sys.path so walk_paths resolves by bare name, then
# call setup() to wire up track_runner, repo root, core/, and render/.
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)

import walk_paths
walk_paths.setup()

# PIP3 modules
import numpy

# local repo modules (available after walk_paths.setup())
import residual_motion
import residual_heat_map
import common_tools.coord_space
import heat_movie_frame
import heat_movie_encode

# Stable scratch root under /tmp: reuse between runs, overwriting output.
SMOKE_OUTPUT_DIR = pathlib.Path('/tmp/output_smoke_heat_movie')  # nosec B108 - E2E scratch under /tmp per E2E_TESTS.md convention

# Stable scratch parent for the encode spill frames (injectable).
SCRATCH_PARENT = '/tmp/output_smoke_heat_movie_scratch'  # nosec B108 - E2E scratch under /tmp per E2E_TESTS.md convention


#============================================
class _FakeReader:
	"""Minimal VideoReader stub sufficient for build_heat_movie_frame.

	compute_heat_map_roi is patched out below, so the reader's decode path
	is never exercised. The width/height attributes let the frame compositor
	compute frame-boundary clamping.
	"""
	width = 1280
	height = 720


#============================================
class _FakeSceneTransform:
	"""Minimal scene_transform stub (passed through to patched compute_heat_map_roi)."""


#============================================
def _fake_compute_heat_map_roi(
	reader, frame_index, scene_transform,
	pred_center, pred_box, fps,
	threshold=residual_motion.DEFAULT_THRESHOLD,
	out_arrays=None,
):
	"""Return a small non-black composite, populating out_arrays.

	Replaces residual_heat_map.compute_heat_map_roi so the E2E runs without
	a real video file. Returns the same structure the real function would:
	(composite, origin) tuple where composite is a (comp_h, comp_w, 3) BGR
	array and origin is (ox, oy) in PROCESSED pixels.
	"""
	comp_h, comp_w = 40, 30
	# Mid-grey composite so the output .bgr frames are non-trivially black.
	composite = numpy.full((comp_h, comp_w, 3), 128, dtype=numpy.uint8)
	origin_x = int(pred_center[0]) - comp_w // 2
	origin_y = int(pred_center[1]) - comp_h // 2
	if out_arrays is not None:
		# Above-threshold residual everywhere -> in-box hot-mean has real data.
		residual_dog = numpy.full(
			(comp_h, comp_w), threshold + 5.0, dtype=numpy.float32,
		)
		validity_mask = numpy.ones((comp_h, comp_w), dtype=numpy.uint8)
		out_arrays['residual_dog'] = residual_dog
		out_arrays['validity_mask'] = validity_mask
		out_arrays['roi_bounds'] = (
			origin_x, origin_y, origin_x + comp_w, origin_y + comp_h,
		)
		out_arrays['dog_k'] = residual_motion.DOG_K_FACTOR_DEFAULT
		out_arrays['threshold'] = threshold
	return (composite, (origin_x, origin_y))


#============================================
def _make_direction_path(num_frames: int, cx: float, cy: float, w: float, h: float) -> list:
	"""Build a synthetic per-frame direction path list.

	Each entry matches the structure _spill_direction_frames expects:
	{frame_index, cx, cy, w, h}.
	"""
	frames = []
	for i in range(num_frames):
		frames.append({
			'frame_index': i,
			'cx': cx,
			'cy': cy,
			'w': w,
			'h': h,
		})
	return frames


#============================================
def _install_patch() -> None:
	"""Replace compute_heat_map_roi in all modules that have already imported it.

	heat_movie_frame imports residual_heat_map and calls
	residual_heat_map.compute_heat_map_roi. Patching the attribute on the
	residual_heat_map module object is sufficient because heat_movie_frame
	holds a reference to the module, not a cached function pointer.
	"""
	residual_heat_map.compute_heat_map_roi = _fake_compute_heat_map_roi


#============================================
def run_smoke() -> None:
	"""Drive the full spill->ffmpeg->copy->cleanup pipeline and assert postconditions.

	Exits 0 on success, 1 on assertion failure, skips (exit 0 + message) when
	ffmpeg is absent.
	"""
	# --- Skip if ffmpeg is absent ---
	if shutil.which('ffmpeg') is None:
		print('SKIP: ffmpeg not found in PATH; --heat-movie smoke test skipped.')
		sys.exit(0)

	# Also verify via the module-level check so the function is exercised.
	try:
		heat_movie_encode.check_ffmpeg_available()
	except RuntimeError as exc:
		print(f'SKIP: check_ffmpeg_available raised RuntimeError: {exc}')
		sys.exit(0)

	# --- Patch out real video decode ---
	_install_patch()

	# --- Synthetic inputs ---
	reader = _FakeReader()
	scene_transform = _FakeSceneTransform()
	fps = 30.0
	# Two seed boxes: same cx/cy so the window is well-centered; use height 60
	# to drive a clear ROI size.
	left_seed_box = common_tools.coord_space.ProcessedBox(
		cx=640.0, cy=360.0, w=30.0, h=60.0,
	)
	right_seed_box = common_tools.coord_space.ProcessedBox(
		cx=640.0, cy=360.0, w=30.0, h=60.0,
	)
	# 5-frame interval (small but enough to produce a valid H.264 stream).
	num_frames = 5
	direction_path = _make_direction_path(
		num_frames, cx=640.0, cy=360.0, w=30.0, h=60.0,
	)

	# --- Compute expected .bgr size (must be constant for all frames) ---
	roi_size = heat_movie_frame.compute_fixed_heat_roi_size(left_seed_box, right_seed_box)
	roi_w, roi_h = int(roi_size[0]), int(roi_size[1])
	expected_bgr_bytes = roi_w * roi_h * 3
	print(f'  roi_size = ({roi_w}, {roi_h}), expected_bgr_bytes = {expected_bgr_bytes}')

	# --- Prepare output and scratch dirs ---
	SMOKE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
	output_interval_dir = SMOKE_OUTPUT_DIR / 'fwd_interval'
	output_interval_dir.mkdir(parents=True, exist_ok=True)

	# TemporaryDirectory creates: <SCRATCH_PARENT>/heat_movie_fwd_<random>.

	# --- Intercept .bgr write paths for byte-size assertion ---
	# We capture .bgr paths by temporarily wrapping build_heat_movie_frame.
	# The wrapper records paths then calls the real function unchanged.
	_written_bgr_paths = []
	_real_build_frame = heat_movie_frame.build_heat_movie_frame

	def _capturing_build(reader, frame_index, scene_transform, solved_box,
			roi_size, scratch_dir, fps, threshold=residual_motion.DEFAULT_THRESHOLD):
		path = _real_build_frame(
			reader=reader, frame_index=frame_index,
			scene_transform=scene_transform, solved_box=solved_box,
			roi_size=roi_size, scratch_dir=scratch_dir, fps=fps,
			threshold=threshold,
		)
		_written_bgr_paths.append(path)
		return path

	heat_movie_frame.build_heat_movie_frame = _capturing_build

	# --- Run the encode ---
	dest_path = heat_movie_encode.encode_heat_movie_for_direction(
		direction_label='FWD',
		left_seed_box=left_seed_box,
		right_seed_box=right_seed_box,
		direction_path=direction_path,
		reader=reader,
		scene_transform=scene_transform,
		fps=fps,
		output_interval_dir=output_interval_dir,
		scratch_parent=SCRATCH_PARENT,
	)

	# Restore the real build_heat_movie_frame for cleanliness.
	heat_movie_frame.build_heat_movie_frame = _real_build_frame

	# --- Assertion 1: dest_path is a non-empty string (movie path reported) ---
	assert isinstance(dest_path, str) and len(dest_path) > 0, (
		f'encode_heat_movie_for_direction returned a non-string or empty path: '
		f'{dest_path!r}'
	)
	print(f'  dest_path = {dest_path}')

	# --- Assertion 2: destination .mkv exists and is non-empty ---
	assert os.path.isfile(dest_path), (
		f'destination .mkv not found: {dest_path}'
	)
	dest_size = os.path.getsize(dest_path)
	assert dest_size > 0, f'destination .mkv is empty: {dest_path}'
	print(f'  dest_size = {dest_size} bytes (non-empty, OK)')

	# --- Assertion 3: every .bgr file was the same size roiW*roiH*3 bytes ---
	# The .bgr files are deleted by cleanup after the encode, so we verify their
	# byte count in a separate sub-check below: spill frames directly into a
	# controlled temp dir (no ffmpeg) and stat each file while it still exists.
	assert len(_written_bgr_paths) == num_frames, (
		f'expected {num_frames} .bgr files, got {len(_written_bgr_paths)}'
	)
	print(f'  .bgr path count = {len(_written_bgr_paths)} (matches num_frames={num_frames}, OK)')

	# Sub-check: spill a center box and an edge box into a controlled dir and
	# verify each file is exactly roi_w*roi_h*3 bytes (constant ROI shape,
	# independent of where the solved box sits including off-frame boxes).
	import tempfile
	with tempfile.TemporaryDirectory(prefix='heat_bgr_size_check_', dir='/tmp') as td:  # nosec B108 - E2E scratch under /tmp per E2E_TESTS.md convention
		test_box = common_tools.coord_space.ProcessedBox(
			cx=640.0, cy=360.0, w=30.0, h=60.0,
		)
		# Use the edge-of-frame box to exercise black-fill path.
		edge_box = common_tools.coord_space.ProcessedBox(
			cx=5.0, cy=5.0, w=30.0, h=60.0,
		)
		for frame_idx, box in [(0, test_box), (1, edge_box)]:
			spill_path = _real_build_frame(
				reader=reader, frame_index=frame_idx,
				scene_transform=scene_transform, solved_box=box,
				roi_size=(roi_w, roi_h), scratch_dir=td, fps=fps,
			)
			actual_bytes = os.path.getsize(spill_path)
			assert actual_bytes == expected_bgr_bytes, (
				f'.bgr file size mismatch at frame {frame_idx}: '
				f'expected {expected_bgr_bytes}, got {actual_bytes}'
			)
		print(f'  .bgr byte size OK: {expected_bgr_bytes} bytes for both center and edge boxes')

	# --- Assertion 4: no run-scoped scratch directory remains (no post-run leak) ---
	# TemporaryDirectory names dirs <SCRATCH_PARENT>/heat_movie_fwd_<random>;
	# any surviving subdir matching that pattern is a leak.
	leaked_dirs = glob.glob(os.path.join(SCRATCH_PARENT, 'heat_movie_fwd_*'))
	assert len(leaked_dirs) == 0, (
		f'post-run heat_movie_fwd_* scratch dirs still exist (leak): {leaked_dirs}'
	)
	print('  no heat_movie_fwd_* scratch dirs remain after run (no leak, OK)')

	print('PASS: heat-movie smoke test passed.')
	sys.exit(0)


#============================================
def main() -> None:
	"""Entry point."""
	run_smoke()


if __name__ == '__main__':
	main()
