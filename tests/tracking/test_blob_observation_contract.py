"""Observable-contract tests for residual_motion.observe_blob_at.

Scope: EXTRACTION LAYER ONLY. These tests exercise the blob extraction
pipeline (raw blob list assembly, candidate filtering, winner pick) and
the tagged reject_reason for None-return paths.

These tests do NOT exercise the heat-map / residual-magnitude pipeline.
The residual_cache is pre-populated with synthetic raw_blobs so that
observe_blob_at skips compute_residual_for_frame entirely (cache hit
branch). This isolates the extraction layer under test.

2026-05-28 re-scope: Extraction returns raw blob centroids in source
pixels. No torso re-anchor or direction-aware geometric filter, no
silent discard based on guessed runner motion. center_pixel IS the raw
winner-blob centroid. See dump_step1/BLOB_EXTRACTION_CODE_AUDIT.md.
"""

# Standard Library
import types

# local repo modules (bare imports resolved by conftest.py)
import blob_trace
import residual_motion
import common_tools.coord_space as coord_space


#============================================
def _make_reader(width: int = 1920, height: int = 1080, fps: float = 30.0) -> object:
	"""Build a minimal reader stub for observe_blob_at."""
	reader = types.SimpleNamespace(
		width=width,
		height=height,
		fps=fps,
		geometry=None,
	)
	return reader


#============================================
def _seed_cache(
	residual_cache: dict,
	frame_index: int,
	pred_cx: float,
	pred_cy: float,
	pred_h: float,
	frame_w: int,
	frame_h: int,
	blobs: list,
) -> tuple:
	"""Pre-populate residual_cache so observe_blob_at hits the cache."""
	roi = residual_motion._compute_roi(
		pred_cx, pred_cy, pred_h, frame_w, frame_h,
	)
	cache_key = (frame_index, roi)
	residual_cache[cache_key] = {
		"raw_blobs_processed": blobs,
		"residual_pre_dog": None,
		"dog_residual": None,
		"validity_mask": None,
	}
	return cache_key


#============================================
def _make_blob(cx: float, cy: float, area: int = 600, mag: float = 5000.0) -> dict:
	"""Build a synthetic raw-blob dict with the keys observe_blob_at needs."""
	blob = {
		"centroid_x": cx,
		"centroid_y": cy,
		"area": area,
		"bbox": (int(cx) - 4, int(cy) - 4, 8, 8),
		"integrated_mag": mag,
		"label_id": int(cx * 1000 + cy),
		"small_blob": area < residual_motion.MIN_BLOB_AREA,
	}
	return blob


#============================================
def test_center_pixel_equals_raw_centroid() -> None:
	"""center_pixel IS the raw winner-blob centroid (no re-anchor)."""
	pred_cx = 800.0
	pred_cy = 400.0
	pred_w = 50.0
	pred_h = 100.0
	frame_idx = 200
	residual_cache = {}
	# Offset blob: +10 in x, +5 in y. The new contract returns this raw.
	blob_x = pred_cx + 10.0
	blob_y = pred_cy + 5.0
	blobs = [_make_blob(blob_x, blob_y)]
	reader = _make_reader()
	_seed_cache(
		residual_cache, frame_idx, pred_cx, pred_cy, pred_h,
		reader.width, reader.height, blobs,
	)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_idx,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
		scene_transform=None,
		reader=reader,
		residual_cache=residual_cache,
	)
	assert obs is not None
	# Raw centroid: both axes are preserved.
	assert abs(obs.center_pixel.cx - blob_x) < 1e-6
	assert abs(obs.center_pixel.cy - blob_y) < 1e-6


#============================================

def test_unfiltered_blob_is_returned() -> None:
	"""A raw blob is returned when no explicit acceptance box is supplied.
	"""
	pred_cx = 500.0
	pred_cy = 500.0
	pred_w = 40.0
	pred_h = 80.0
	frame_idx = 500
	# The far offset confirms extraction does not invent a geometric filter.
	blob_x = pred_cx
	blob_y = pred_cy + 200.0
	residual_cache = {}
	blobs = [_make_blob(blob_x, blob_y)]
	reader = _make_reader()
	_seed_cache(
		residual_cache, frame_idx, pred_cx, pred_cy, pred_h,
		reader.width, reader.height, blobs,
	)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_idx,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
		scene_transform=None,
		reader=reader,
		residual_cache=residual_cache,
	)
	assert obs is not None
	assert abs(obs.center_pixel.cx - blob_x) < 1e-6
	assert abs(obs.center_pixel.cy - blob_y) < 1e-6


#============================================
def test_small_blob_is_exposed_as_feature_not_filter() -> None:
	"""extract_frame_blobs marks small_blob but does not discard."""
	# Use a real residual array so extract_frame_blobs runs.
	import numpy
	# Build a 32x32 magnitude image with a single connected region of
	# 5 pixels (well below MIN_BLOB_AREA=25). Threshold = 1.0.
	mag = numpy.zeros((32, 32), dtype=numpy.float32)
	for px in [(10, 10), (10, 11), (11, 10), (11, 11), (12, 10)]:
		mag[px[1], px[0]] = 5.0
	validity = numpy.full((32, 32), 255, dtype=numpy.uint8)
	blobs = residual_motion.extract_frame_blobs(mag, validity, threshold=1.0)
	# The small blob (area=5) is RETURNED, not discarded, and tagged.
	assert len(blobs) == 1
	assert blobs[0]["area"] == 5
	assert blobs[0]["small_blob"] is True


#============================================
def test_total_score_is_metadata_only() -> None:
	"""Winner is by integrated_mag, not by total_score."""
	pred_cx = 600.0
	pred_cy = 600.0
	pred_w = 40.0
	pred_h = 80.0
	frame_idx = 700
	# Two blobs. Blob A is closer to predicted (higher proximity_score,
	# thus higher total_score) but has LOWER integrated_mag. Blob B is
	# farther but has HIGHER integrated_mag. Winner must be B.
	blob_a = _make_blob(pred_cx + 1.0, pred_cy + 1.0, area=100, mag=1000.0)
	blob_b = _make_blob(pred_cx + 30.0, pred_cy + 30.0, area=100, mag=9000.0)
	residual_cache = {}
	reader = _make_reader()
	_seed_cache(
		residual_cache, frame_idx, pred_cx, pred_cy, pred_h,
		reader.width, reader.height, [blob_a, blob_b],
	)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_idx,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
		scene_transform=None,
		reader=reader,
		residual_cache=residual_cache,
	)
	assert obs is not None
	# Winner is blob_b (higher integrated_mag), not blob_a (higher
	# total_score from proximity).
	assert abs(obs.center_pixel.cx - (pred_cx + 30.0)) < 1e-6
	assert abs(obs.center_pixel.cy - (pred_cy + 30.0)) < 1e-6


#============================================
def test_reject_reason_no_raw_blobs() -> None:
	"""Empty raw_blobs -> trace.reject_reason == 'no_raw_blobs'."""
	pred_cx = 960.0
	pred_cy = 540.0
	pred_w = 40.0
	pred_h = 80.0
	frame_idx = 400
	residual_cache = {}
	reader = _make_reader()
	_seed_cache(
		residual_cache, frame_idx, pred_cx, pred_cy, pred_h,
		reader.width, reader.height, [],
	)
	trace_sink = types.SimpleNamespace(observer_trace=None)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_idx,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
		scene_transform=None,
		reader=reader,
		residual_cache=residual_cache,
		trace_sink=trace_sink,
	)
	assert obs is None
	assert isinstance(trace_sink.observer_trace, blob_trace.BlobObserverTrace)
	assert trace_sink.observer_trace.reject_reason == "no_raw_blobs"


#============================================
def test_reject_reason_acceptance_box_empty() -> None:
	"""Acceptance-box arm: no blob inside ROI -> 'acceptance_box_empty'."""
	import numpy
	pred_cx = 800.0
	pred_cy = 400.0
	pred_w = 40.0
	pred_h = 80.0
	frame_idx = 600
	residual_cache = {}
	# Blob far outside the acceptance box.
	blobs = [_make_blob(pred_cx + 500.0, pred_cy + 500.0)]
	reader = _make_reader()
	# roi_override / acceptance_box are PROCESSED-space ProcessedBox now;
	# build them center-size from the same edge extents the test used.
	roi_override = coord_space.ProcessedBox(
		cx=pred_cx, cy=pred_cy, w=200.0, h=200.0,
	)
	acceptance_box = coord_space.ProcessedBox(
		cx=pred_cx, cy=pred_cy, w=50.0, h=50.0,
	)
	orig_compute = residual_motion.compute_residual_for_frame
	orig_extract = residual_motion.extract_frame_blobs
	orig_dog = residual_motion.dog_filter_blob_scale

	def fake_compute(*args: object, **kwargs: object) -> tuple[object, object]:
		return numpy.zeros((4, 4), dtype=numpy.float32), numpy.full((4, 4), 255, dtype=numpy.uint8)

	def fake_dog(arr: object, diameter: object, k: float = 1.0) -> object:
		return arr

	def fake_extract(
		mag: object, mask: object, threshold: object, top_k: int = 10,
	) -> list:
		return list(blobs)

	residual_motion.compute_residual_for_frame = fake_compute
	residual_motion.dog_filter_blob_scale = fake_dog
	residual_motion.extract_frame_blobs = fake_extract
	try:
		trace_sink = types.SimpleNamespace(observer_trace=None)
		obs = residual_motion.observe_blob_at(
			frame_index=frame_idx,
			pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
			pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
			scene_transform=None,
			reader=reader,
			residual_cache=residual_cache,
			trace_sink=trace_sink,
			roi_override=roi_override,
			acceptance_box=acceptance_box,
		)
	finally:
		residual_motion.compute_residual_for_frame = orig_compute
		residual_motion.extract_frame_blobs = orig_extract
		residual_motion.dog_filter_blob_scale = orig_dog
	assert obs is None
	assert trace_sink.observer_trace.reject_reason == "acceptance_box_empty"


#============================================
def test_dist_to_pred_px_is_logged_feature() -> None:
	"""dist_to_pred_px is logged on the trace, not used to filter."""
	pred_cx = 700.0
	pred_cy = 500.0
	pred_w = 40.0
	pred_h = 80.0
	frame_idx = 800
	# Single blob offset 30/40 from prediction -> dist=50 px.
	blob = _make_blob(pred_cx + 30.0, pred_cy + 40.0)
	residual_cache = {}
	reader = _make_reader()
	_seed_cache(
		residual_cache, frame_idx, pred_cx, pred_cy, pred_h,
		reader.width, reader.height, [blob],
	)
	trace_sink = types.SimpleNamespace(observer_trace=None)
	obs = residual_motion.observe_blob_at(
		frame_index=frame_idx,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=pred_w, h=pred_h),
		scene_transform=None,
		reader=reader,
		residual_cache=residual_cache,
		trace_sink=trace_sink,
	)
	assert obs is not None
	trace = trace_sink.observer_trace
	assert trace is not None
	# Logged dist_to_pred_px on the raw_blobs trace; not a filter.
	assert abs(trace.raw_blobs[0]["dist_to_pred_px"] - 50.0) < 1e-6
