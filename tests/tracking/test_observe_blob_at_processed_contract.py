"""Tests for observe_blob_at processed-pixel contract (Option A, 2026-05-29).

Verifies that roi_override and dog_diameter_override are used as-is
(no internal conversion) after the contract flip.

  1. roi_override_processed_no_conversion: roi_override in processed coords
     used as-is; no source->processed divide inside observe_blob_at.
  2. dog_diameter_override_processed_no_conversion: dog_diameter_override
     in processed coords passed directly to dog_filter_blob_scale.
  3. at_bin1_no_change: behavior identical at bin=1 (source==processed).
"""
import types
import unittest.mock
import numpy

import residual_motion
import frame_reader as fr
import common_tools.coord_space as coord_space


#============================================
def _proc_box_from_edges(edges: tuple[int, int, int, int]) -> coord_space.ProcessedBox:
	"""Build a PROCESSED-space ProcessedBox from (x1, y1, x2, y2) edges."""
	x1, y1, x2, y2 = edges
	cx = (x1 + x2) / 2.0
	cy = (y1 + y2) / 2.0
	box = coord_space.ProcessedBox(cx=cx, cy=cy, w=float(x2 - x1), h=float(y2 - y1))
	return box


#============================================
def _make_geometry(
	bin_factor: int,
	src_w: int = 1600,
	src_h: int = 1600,
) -> fr.FrameGeometry:
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
def _make_fake_reader(bin_factor: int, src_w: int = 1600, src_h: int = 1600) -> object:
	"""Build a minimal FrameReader-like stub."""
	geometry = _make_geometry(bin_factor, src_w, src_h)
	proc_w = src_w // bin_factor
	proc_h = src_h // bin_factor
	reader = types.SimpleNamespace()
	reader.bin_factor = bin_factor
	reader.geometry = geometry
	reader.width = proc_w
	reader.height = proc_h
	reader.frame_count = 10
	reader.fps = 60.0
	reader.read_frame = lambda idx: numpy.full((proc_h, proc_w, 3), 128, dtype=numpy.uint8)
	return reader


#============================================
def _make_scene_transform(n_frames: int) -> object:
	"""Stub SceneTransform with zero camera motion."""
	st = types.SimpleNamespace()
	st.cum_dx = numpy.zeros(n_frames, dtype=numpy.float64)
	st.cum_dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.cum_scale = numpy.ones(n_frames, dtype=numpy.float64)
	mt = types.SimpleNamespace()
	mt.dx = numpy.zeros(n_frames, dtype=numpy.float64)
	mt.dy = numpy.zeros(n_frames, dtype=numpy.float64)
	st.motion_track = mt
	return st


#============================================
def _make_precomputed_store(roi: tuple[int, int, int, int], proc_h: int, proc_w: int) -> dict:
	"""Build a precomputed_store with a dummy residual for the given processed roi."""
	roi_h = roi[3] - roi[1]
	roi_w = roi[2] - roi[0]
	if roi_h <= 0 or roi_w <= 0:
		roi_h = 1
		roi_w = 1
	dummy_residual = numpy.zeros((roi_h, roi_w), dtype=numpy.uint8)
	dummy_validity = numpy.full((roi_h, roi_w), 255, dtype=numpy.uint8)
	return {(5, roi): (dummy_residual, dummy_validity)}


#============================================
def test_roi_override_processed_no_conversion() -> None:
	"""roi_override in processed coords is used as-is (no source->processed divide).

	At bin=4, passing roi_override=(10, 10, 200, 200) in processed coords
	should result in a cache key with roi=(10, 10, 200, 200), NOT (2, 2, 50, 50)
	which would indicate an extra division by bin_factor.
	"""
	BIN = 4
	SRC_W = SRC_H = 1600
	reader = _make_fake_reader(BIN, SRC_W, SRC_H)
	scene_transform = _make_scene_transform(10)

	# roi_override in PROCESSED coords
	roi_proc = (10, 10, 200, 200)

	captured_cache_keys = []

	class _SpyCache(dict):
		def get(self, key: object, default: object = None) -> object:
			captured_cache_keys.append(key)
			return super().get(key, default)

	spy_cache = _SpyCache()
	# Inject precomputed store so computation doesn't run
	ps = _make_precomputed_store(roi_proc, reader.height, reader.width)

	residual_motion.observe_blob_at(
		frame_index=5,
		pred_center=coord_space.ProcessedPoint(cx=100.0, cy=100.0),
		pred_box=coord_space.ProcessedBox(cx=100.0, cy=100.0, w=50.0, h=100.0),
		scene_transform=scene_transform,
		reader=reader,
		residual_cache=spy_cache,
		roi_override=_proc_box_from_edges(roi_proc),
		precomputed_store=ps,
	)

	# The cache key must use the roi as-is (no extra divide by bin_factor)
	assert (5, roi_proc) in captured_cache_keys, (
		f"Expected cache key (5, {roi_proc}), got: {captured_cache_keys}"
	)


#============================================
def test_dog_diameter_override_processed_no_conversion() -> None:
	"""dog_diameter_override in processed coords is passed directly to dog filter.

	At bin=4, passing dog_diameter_override=10.0 (processed) should result in
	dog_filter_blob_scale receiving diameter=10.0, NOT 10.0/4=2.5 which
	would indicate an extra conversion inside observe_blob_at.
	"""
	BIN = 4
	SRC_W = SRC_H = 1600
	reader = _make_fake_reader(BIN, SRC_W, SRC_H)
	scene_transform = _make_scene_transform(10)

	# dog_diameter in PROCESSED coords (0.7 * seed_w_processed)
	dog_proc = 10.0
	roi_proc = (0, 0, reader.width, reader.height)

	captured_diameters = []
	real_dog_filter = residual_motion.dog_filter_blob_scale

	def spy_dog_filter(residual: object, diameter: float, **kwargs) -> object:
		captured_diameters.append(diameter)
		return real_dog_filter(residual, diameter, **kwargs)

	ps = _make_precomputed_store(roi_proc, reader.height, reader.width)

	with unittest.mock.patch.object(
		residual_motion, "dog_filter_blob_scale", side_effect=spy_dog_filter
	):
		residual_motion.observe_blob_at(
			frame_index=5,
			pred_center=coord_space.ProcessedPoint(cx=100.0, cy=100.0),
			pred_box=coord_space.ProcessedBox(cx=100.0, cy=100.0, w=50.0, h=100.0),
			scene_transform=scene_transform,
			reader=reader,
			residual_cache={},
			dog_diameter_override=dog_proc,
			roi_override=_proc_box_from_edges(roi_proc),
			precomputed_store=ps,
		)

	assert len(captured_diameters) > 0, "dog_filter_blob_scale was not called"
	assert abs(captured_diameters[0] - dog_proc) < 1e-9, (
		f"Expected dog diameter {dog_proc} (no conversion), got {captured_diameters[0]}. "
		f"Possible double-conversion (divided by bin_factor={BIN} inside observe_blob_at)."
	)


#============================================
def test_at_bin1_no_change() -> None:
	"""At bin=1, source==processed; behavior is identical for any input convention."""
	BIN = 1
	SRC_W = SRC_H = 400
	reader = _make_fake_reader(BIN, SRC_W, SRC_H)
	scene_transform = _make_scene_transform(10)

	# At bin=1, source==processed; same values work under either convention
	roi = (50, 50, 300, 300)
	dog_diam = 30.0

	captured_diameters = []
	real_dog_filter = residual_motion.dog_filter_blob_scale

	def spy_dog_filter(residual: object, diameter: float, **kwargs) -> object:
		captured_diameters.append(diameter)
		return real_dog_filter(residual, diameter, **kwargs)

	ps = _make_precomputed_store(roi, reader.height, reader.width)

	with unittest.mock.patch.object(
		residual_motion, "dog_filter_blob_scale", side_effect=spy_dog_filter
	):
		residual_motion.observe_blob_at(
			frame_index=5,
			pred_center=coord_space.ProcessedPoint(cx=150.0, cy=150.0),
			pred_box=coord_space.ProcessedBox(cx=150.0, cy=150.0, w=60.0, h=120.0),
			scene_transform=scene_transform,
			reader=reader,
			residual_cache={},
			dog_diameter_override=dog_diam,
			roi_override=_proc_box_from_edges(roi),
			precomputed_store=ps,
		)

	assert len(captured_diameters) > 0, "dog_filter_blob_scale was not called"
	# At bin=1 the diameter is used as-is
	assert abs(captured_diameters[0] - dog_diam) < 1e-9, (
		f"At bin=1, expected diameter={dog_diam}, got {captured_diameters[0]}"
	)
