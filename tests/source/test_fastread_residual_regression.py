"""test_fastread_residual_regression.py

Regression tests for the off-frame soft-miss and fps-mismatch changes
landed in the A1/A2/B1/B2 work.

Plan items covered:
  4. _build_rois_for_frame: off-frame predicted center yields no ROI;
     in-frame center yields a normal ROI; near-edge positive test.
  5. Frame-885 regression: observe_blob_at with off-frame predicted center
     returns None with reject_reason "off_frame" via trace_sink.
  6. Invariant still trips: a degenerate (SOURCE-fed-as-PROCESSED) roi
     tuple passed to compute_residual_for_frame raises ValueError.

All tests are fast, deterministic, require no real video, and write no files.
"""

# Standard Library
import types

# PIP3 modules
import numpy
import pytest

import residual_motion
import residual_pre_pass
import frame_reader as fr
import common_tools.coord_space as coord_space
import blob_trace

#============================================
# Shared helpers
#============================================

def _make_geometry(
	bin_factor: int,
	src_w: int = 1920,
	src_h: int = 1080,
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

def _make_fake_reader(
	width: int = 1920,
	height: int = 1080,
	frame_count: int = 1000,
	fps: float = 60.0,
	geometry: object = None,
) -> object:
	"""Build a minimal reader stub exposing the required attributes."""
	reader = types.SimpleNamespace()
	reader.width = width
	reader.height = height
	reader.frame_count = frame_count
	reader.fps = fps
	reader.geometry = geometry
	# read_frame returns a constant uint8 BGR frame; used only when residual
	# is actually computed (Plan item 6 test triggers this path)
	reader.read_frame = lambda idx: numpy.full((height, width, 3), 128, dtype=numpy.uint8)
	return reader

def _make_scene_transform(n_frames: int = 1000) -> object:
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
# Plan item 4: _build_rois_for_frame off-frame / in-frame / near-edge
#============================================

def test_build_rois_offframe_center_yields_no_roi() -> None:
	"""Off-frame predicted center (cx >= frame_w) produces no ROI entry.

	Regression guard for the B1 fix: an off-frame prediction previously
	reached _compute_roi and built a degenerate clamped ROI with zero
	width. After B1 the skip happens before that call so no ROI is added.
	"""
	frame_w = 1920
	frame_h = 1080
	# FWD center far past the right edge; BWD absent
	fwd_by_frame = {10: (float(frame_w + 200), 300.0, 100.0)}
	bwd_by_frame = {}
	rois = residual_pre_pass._build_rois_for_frame(
		start_frame=10,
		end_frame=10,
		fwd_by_frame=fwd_by_frame,
		bwd_by_frame=bwd_by_frame,
		geometry=None,
		frame_w=frame_w,
		frame_h=frame_h,
	)
	# off-frame frame should not be in the result at all
	assert 10 not in rois, (
		"off-frame FWD center must not produce an ROI entry; "
		f"got rois[10]={rois.get(10)}"
	)

def test_build_rois_inframe_center_yields_roi() -> None:
	"""In-frame predicted center produces a normal ROI entry."""
	frame_w = 1920
	frame_h = 1080
	# FWD center well inside the frame
	fwd_by_frame = {10: (800.0, 400.0, 100.0)}
	bwd_by_frame = {}
	rois = residual_pre_pass._build_rois_for_frame(
		start_frame=10,
		end_frame=10,
		fwd_by_frame=fwd_by_frame,
		bwd_by_frame=bwd_by_frame,
		geometry=None,
		frame_w=frame_w,
		frame_h=frame_h,
	)
	assert 10 in rois, "in-frame FWD center must produce an ROI entry"

def test_build_rois_near_edge_positive() -> None:
	"""A center one pixel inside the frame edge still produces a valid ROI.

	Guards against an over-strict off-frame skip that would drop on-frame
	centers near the boundary. x == frame_w - 1 is the last valid column.
	"""
	frame_w = 1920
	frame_h = 1080
	# cx is exactly one pixel inside the right edge
	fwd_by_frame = {10: (float(frame_w - 1), 300.0, 80.0)}
	bwd_by_frame = {}
	rois = residual_pre_pass._build_rois_for_frame(
		start_frame=10,
		end_frame=10,
		fwd_by_frame=fwd_by_frame,
		bwd_by_frame=bwd_by_frame,
		geometry=None,
		frame_w=frame_w,
		frame_h=frame_h,
	)
	assert 10 in rois, (
		"near-edge in-frame center (cx=frame_w-1) must not be skipped as off-frame"
	)

def test_build_rois_binned_geometry_inframe() -> None:
	"""With bin_factor=2 geometry, an in-frame processed center yields a ROI.

	The geometry path uses coord_space.ProcessedPoint.in_bounds. Verify
	that an ordinary in-frame processed position passes through correctly.
	"""
	src_w, src_h = 3840, 2160
	bin_factor = 2
	geometry = _make_geometry(bin_factor, src_w, src_h)
	proc_w = src_w // bin_factor
	proc_h = src_h // bin_factor
	# source center (400, 300) -> processed (200, 150); clearly in-frame
	fwd_by_frame = {20: (400.0, 300.0, 200.0)}
	bwd_by_frame = {}
	rois = residual_pre_pass._build_rois_for_frame(
		start_frame=20,
		end_frame=20,
		fwd_by_frame=fwd_by_frame,
		bwd_by_frame=bwd_by_frame,
		geometry=geometry,
		frame_w=proc_w,
		frame_h=proc_h,
	)
	assert 20 in rois, (
		"binned geometry: in-frame source center must produce an ROI entry"
	)

def test_build_rois_binned_geometry_offframe_yields_no_roi() -> None:
	"""With bin_factor=2 geometry, a source center past processed bounds yields no ROI.

	A source center at (8000, 300) converts to processed (4000, 150) which is
	far past processed_width=1920, so the geometry-path in_bounds check must
	skip it.
	"""
	src_w, src_h = 3840, 2160
	bin_factor = 2
	geometry = _make_geometry(bin_factor, src_w, src_h)
	proc_w = src_w // bin_factor
	proc_h = src_h // bin_factor
	# source center well past processed_width after conversion
	fwd_by_frame = {20: (8000.0, 300.0, 200.0)}
	bwd_by_frame = {}
	rois = residual_pre_pass._build_rois_for_frame(
		start_frame=20,
		end_frame=20,
		fwd_by_frame=fwd_by_frame,
		bwd_by_frame=bwd_by_frame,
		geometry=geometry,
		frame_w=proc_w,
		frame_h=proc_h,
	)
	assert 20 not in rois, (
		"binned geometry: off-processed center must yield no ROI entry"
	)

#============================================
# Plan item 5: frame-885 off-frame regression
#============================================

class _TraceSinkHolder:
	"""Minimal trace_sink compatible with observe_blob_at's _set_reject_reason."""
	observer_trace: blob_trace.BlobObserverTrace | None = None

def test_observe_blob_at_offframe_returns_none_with_reject_reason() -> None:
	"""Off-frame predicted center returns None and sets reject_reason='off_frame'.

	Regression guard for B2: before the guard was added, passing a center
	at cx >= frame_w caused a degenerate ROI and downstream crash. After B2
	observe_blob_at returns None with reject_reason 'off_frame' via trace_sink.
	"""
	frame_w = 1920
	frame_h = 1080
	reader = _make_fake_reader(width=frame_w, height=frame_h)
	scene_transform = _make_scene_transform()

	# predicted center one pixel past the right edge -> off-frame
	pred_center = coord_space.ProcessedPoint(cx=float(frame_w), cy=300.0)
	pred_box = coord_space.ProcessedBox(cx=float(frame_w), cy=300.0, w=60.0, h=120.0)

	sink = _TraceSinkHolder()
	result = residual_motion.observe_blob_at(
		frame_index=885,
		pred_center=pred_center,
		pred_box=pred_box,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache={},
		fps=60.0,
		stride=1,
		trace_sink=sink,
	)
	# must return None, not raise
	assert result is None, (
		f"off-frame center must return None, got {result!r}"
	)
	# trace_sink must carry the reject_reason
	assert sink.observer_trace is not None, (
		"trace_sink.observer_trace must be populated for off-frame misses"
	)
	assert sink.observer_trace.reject_reason == "off_frame", (
		f"expected reject_reason='off_frame', got {sink.observer_trace.reject_reason!r}"
	)

def test_observe_blob_at_offframe_negative_cy_returns_none() -> None:
	"""Negative cy (above frame top) also returns None with reject_reason='off_frame'."""
	frame_w = 1920
	frame_h = 1080
	reader = _make_fake_reader(width=frame_w, height=frame_h)
	scene_transform = _make_scene_transform()

	# predicted center above the top edge
	pred_center = coord_space.ProcessedPoint(cx=800.0, cy=-50.0)
	pred_box = coord_space.ProcessedBox(cx=800.0, cy=-50.0, w=60.0, h=120.0)

	sink = _TraceSinkHolder()
	result = residual_motion.observe_blob_at(
		frame_index=885,
		pred_center=pred_center,
		pred_box=pred_box,
		scene_transform=scene_transform,
		reader=reader,
		residual_cache={},
		fps=60.0,
		stride=1,
		trace_sink=sink,
	)
	assert result is None
	assert sink.observer_trace is not None
	assert sink.observer_trace.reject_reason == "off_frame"

#============================================
# Plan item 6: degenerate SOURCE-fed-as-PROCESSED input raises ValueError
#============================================

def test_compute_residual_degenerate_roi_raises_value_error() -> None:
	"""A degenerate (zero-area) roi to compute_residual_for_frame raises ValueError.

	The guard at residual_motion.py:589 must fire on a roi produced by a
	SOURCE-space center passed where PROCESSED coords are expected: the
	un-divided source coordinate clamps to a zero-extent roi, which cv2
	cannot handle. The ValueError at the explicit guard is the correct
	behavior rather than a deeper crash inside warpAffine.
	"""
	frame_w = 1920
	frame_h = 1080
	reader = _make_fake_reader(width=frame_w, height=frame_h, frame_count=100)
	scene_transform = _make_scene_transform(n_frames=100)

	# roi that clamps to zero width: x1 == x2 == frame_w (clamped)
	# This arises when a SOURCE-space center (far past processed bounds) is
	# fed directly; the clamp in _compute_roi produces x1==x2==frame_w.
	degenerate_roi = (frame_w, 0, frame_w, frame_h)

	with pytest.raises(ValueError, match="degenerate ROI"):
		residual_motion.compute_residual_for_frame(
			reader=reader,
			frame_index=10,
			scene_transform=scene_transform,
			roi=degenerate_roi,
			fps=60.0,
			stride=1,
		)


#============================================
# Byte-bounded residual pre-pass result store
#============================================

def test_byte_bounded_lru_store_keeps_recently_read_entry() -> None:
	"""A recently read residual survives eviction while the older one misses."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=16)
	entry = (
		numpy.zeros((2, 2), dtype=numpy.uint8),
		numpy.ones((2, 2), dtype=numpy.uint8),
	)
	store.store_result((1, (0, 0, 2, 2)), entry)
	store.store_result((2, (0, 0, 2, 2)), entry)
	_ = store[(1, (0, 0, 2, 2))]
	store.store_result((3, (0, 0, 2, 2)), entry)

	assert (
		(1, (0, 0, 2, 2)) in store
		and (2, (0, 0, 2, 2)) not in store
	)
	assert store.total_bytes <= store.max_bytes


def test_byte_bounded_lru_store_replacement_reuses_old_entry_bytes() -> None:
	"""Replacing an entry updates byte accounting without evicting another key."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=12)
	large_entry = (
		numpy.zeros((2, 2), dtype=numpy.uint8),
		numpy.ones((2, 2), dtype=numpy.uint8),
	)
	small_entry = (
		numpy.zeros((1, 2), dtype=numpy.uint8),
		numpy.ones((1, 2), dtype=numpy.uint8),
	)
	store.store_result((1, (0, 0, 2, 2)), large_entry)
	store.store_result((2, (0, 0, 2, 2)), small_entry)
	store.store_result((1, (0, 0, 2, 2)), small_entry)

	assert (
		(1, (0, 0, 2, 2)) in store
		and (2, (0, 0, 2, 2)) in store
	)
	assert store.total_bytes == 8


def test_byte_bounded_lru_construction_lookup_does_not_record_miss() -> None:
	"""Construction duplicate checks do not affect consumer cache metrics."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=8)
	entry = (
		numpy.zeros((2, 2), dtype=numpy.uint8),
		numpy.ones((2, 2), dtype=numpy.uint8),
	)
	store.store_result((1, (0, 0, 2, 2)), entry)
	before = (store.lookup_count, store.miss_count)
	_ = store.contains_without_accounting((2, (0, 0, 2, 2)))

	assert (store.lookup_count, store.miss_count) == before


def test_byte_bounded_lru_oversized_replacement_keeps_valid_entry() -> None:
	"""An oversized replacement leaves the prior cache entry available."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=8)
	entry = (
		numpy.zeros((2, 2), dtype=numpy.uint8),
		numpy.ones((2, 2), dtype=numpy.uint8),
	)
	oversized_entry = (
		numpy.zeros((3, 2), dtype=numpy.uint8),
		numpy.ones((3, 2), dtype=numpy.uint8),
	)
	key = (1, (0, 0, 2, 2))
	store.store_result(key, entry)
	store.store_result(key, oversized_entry)

	assert store[key] is entry
	assert store.total_bytes == entry[0].nbytes + entry[1].nbytes


def test_byte_bounded_lru_replacement_after_read_evicts_oldest_entry() -> None:
	"""Replacing a previously LRU entry promotes it before the next eviction."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=12)
	entry = (
		numpy.zeros((1, 2), dtype=numpy.uint8),
		numpy.ones((1, 2), dtype=numpy.uint8),
	)
	first_key = (1, (0, 0, 2, 2))
	second_key = (2, (0, 0, 2, 2))
	third_key = (3, (0, 0, 2, 2))
	fourth_key = (4, (0, 0, 2, 2))
	store.store_result(first_key, entry)
	store.store_result(second_key, entry)
	store.store_result(third_key, entry)
	# Touch second so first is now the least-recently-used entry.
	_ = store[second_key]
	# Replacing first must make it most recent without a read of first.
	store.store_result(first_key, entry)
	store.store_result(fourth_key, entry)

	assert first_key in store and second_key in store
	assert third_key not in store and fourth_key in store


def test_byte_bounded_lru_total_matches_stored_array_bytes() -> None:
	"""The byte counter matches the residual and validity arrays still stored."""
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=16)
	store.store_result(
		(1, (0, 0, 2, 2)),
		(
			numpy.zeros((2, 2), dtype=numpy.uint8),
			numpy.ones((2, 2), dtype=numpy.uint8),
		),
	)
	store.store_result(
		(2, (0, 0, 2, 2)),
		(
			numpy.zeros((1, 2), dtype=numpy.uint8),
			numpy.ones((1, 2), dtype=numpy.uint8),
		),
	)
	expected_bytes = sum(
		value[0].nbytes + value[1].nbytes
		for value in store.values()
	)

	assert store.total_bytes == expected_bytes


#============================================
def test_precomputed_float_residual_hit_preserves_fraction_and_skips_compute(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""A pre-pass hit feeds the exact float32 residual into DoG extraction."""
	reader = _make_fake_reader()
	pred_center = coord_space.ProcessedPoint(cx=400.0, cy=300.0)
	pred_box = coord_space.ProcessedBox(cx=400.0, cy=300.0, w=40.0, h=80.0)
	roi = residual_motion._compute_roi(400.0, 300.0, 80.0, reader.width, reader.height)
	residual = numpy.array([[0.25, 1.75]], dtype=numpy.float32)
	validity = numpy.full(residual.shape, 255, dtype=numpy.uint8)
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=64)
	store.store_result((17, roi), (residual, validity))
	seen = []

	def fail_compute(*args, **kwargs) -> object:
		raise AssertionError("cache hit must not call direct residual compute")

	def capture_dog(value: object, *args, **kwargs) -> object:
		seen.append(value.copy())
		return value.copy()

	monkeypatch.setattr(residual_motion, "compute_residual_for_frame", fail_compute)
	monkeypatch.setattr(residual_motion, "dog_filter_blob_scale", capture_dog)
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", lambda *args, **kwargs: [])

	result = residual_motion.observe_blob_at(
		frame_index=17,
		pred_center=pred_center,
		pred_box=pred_box,
		scene_transform=None,
		reader=reader,
		residual_cache={},
		precomputed_store=store,
	)

	assert result is None
	assert len(seen) == 1
	assert seen[0].dtype == numpy.float32
	assert numpy.array_equal(seen[0], residual)
	assert store.lookup_count == 1
	assert store.miss_count == 0


#============================================
def test_precomputed_store_miss_records_and_uses_direct_compute(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""A genuine store miss is counted once and uses direct computation."""
	reader = _make_fake_reader()
	pred_center = coord_space.ProcessedPoint(cx=400.0, cy=300.0)
	pred_box = coord_space.ProcessedBox(cx=400.0, cy=300.0, w=40.0, h=80.0)
	store = residual_pre_pass._ByteBoundedLruStore(max_bytes=64)
	called = []

	def fake_compute(*args, **kwargs) -> tuple:
		called.append(True)
		return numpy.zeros((2, 2), dtype=numpy.float32), numpy.full((2, 2), 255, dtype=numpy.uint8)

	monkeypatch.setattr(residual_motion, "compute_residual_for_frame", fake_compute)
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", lambda *args, **kwargs: [])

	result = residual_motion.observe_blob_at(
		frame_index=18,
		pred_center=pred_center,
		pred_box=pred_box,
		scene_transform=None,
		reader=reader,
		residual_cache={},
		precomputed_store=store,
	)

	assert result is None
	assert called == [True]
	assert store.lookup_count == 1
	assert store.miss_count == 1


#============================================
def test_walker_startup_prepass_hits_exact_roi_and_misses_later_anchor(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Only exact startup ROI keys hit; later adaptive anchors use fallback."""
	reader = _make_fake_reader(frame_count=20)
	seed_start = {"frame_index": 0, "cx": 400.0, "cy": 300.0, "w": 40.0, "h": 80.0}
	seed_end = {"frame_index": 10, "cx": 500.0, "cy": 300.0, "w": 40.0, "h": 80.0}
	rois = residual_pre_pass.build_walker_initial_rois(
		seed_start, seed_end, reader, stride=1, window_frames=1,
	)
	compute_calls = []

	def fake_compute(*args, **kwargs) -> tuple:
		frame_index = kwargs["frame_index"] if "frame_index" in kwargs else args[1]
		compute_calls.append(frame_index)
		return numpy.full((3, 3), 0.25, dtype=numpy.float32), numpy.full((3, 3), 255, dtype=numpy.uint8)

	monkeypatch.setattr(residual_motion, "compute_residual_for_frame", fake_compute)
	store = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=None,
		seed_start=seed_start,
		seed_end=seed_end,
		fwd_curve=[],
		bwd_curve=[],
		half_window=1,
		fps=60.0,
		stride=1,
		rois_for_frame=rois,
	)
	assert all(value[0].dtype == numpy.float32 for value in store.values())
	compute_calls.clear()
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", lambda *args, **kwargs: [])

	startup_roi = next(iter(rois[0]))
	x1, y1, x2, y2 = startup_roi
	roi_box = coord_space.ProcessedBox(
		cx=0.5 * (x1 + x2), cy=0.5 * (y1 + y2), w=x2 - x1, h=y2 - y1,
	)
	pred_center = coord_space.ProcessedPoint(cx=400.0, cy=300.0)
	pred_box = coord_space.ProcessedBox(cx=400.0, cy=300.0, w=40.0, h=80.0)

	# Bootstrap key is exact and bypasses direct residual computation.
	residual_motion.observe_blob_at(
		frame_index=0, pred_center=pred_center, pred_box=pred_box,
		scene_transform=None,
		reader=reader, residual_cache={}, precomputed_store=store,
		roi_override=roi_box, dog_diameter_override=28.0, acceptance_box=roi_box,
	)
	assert compute_calls == []

	# Frame 2 is after this test's one-fill startup window. Even with the
	# same geometry it has no key, so an adaptive anchor cannot use a wrong ROI.
	residual_motion.observe_blob_at(
		frame_index=2, pred_center=pred_center, pred_box=pred_box,
		scene_transform=None,
		reader=reader, residual_cache={}, precomputed_store=store,
		roi_override=roi_box, dog_diameter_override=28.0, acceptance_box=roi_box,
	)
	assert compute_calls == [2]
	assert store.lookup_count == 2
	assert store.miss_count == 1


#============================================
def test_sparse_startup_prepass_skips_unrequested_interval_gap() -> None:
	"""Sparse startup ROIs skip their gap and retain direct residual parity."""
	reader = _make_fake_reader(width=16, height=12, frame_count=100)
	scene_transform = _make_scene_transform(n_frames=100)
	roi = (2, 2, 14, 10)
	rois = {10: {roi}, 90: {roi}}
	read_log = []
	store = residual_pre_pass.precompute_interval_residuals(
		reader=reader,
		scene_transform=scene_transform,
		seed_start={"frame_index": 10},
		seed_end={"frame_index": 90},
		fwd_curve=[],
		bwd_curve=[],
		half_window=2,
		fps=60.0,
		stride=1,
		read_log=read_log,
		rois_for_frame=rois,
	)

	assert read_log == list(range(8, 13)) + list(range(88, 93))
	assert set(store) == {(10, roi), (90, roi)}
	for frame_index in sorted(rois):
		direct_reader = _make_fake_reader(width=16, height=12, frame_count=100)
		expected = residual_motion.compute_residual_for_frame(
			reader=direct_reader,
			frame_index=frame_index,
			scene_transform=scene_transform,
			half_window=2,
			cache={},
			roi=roi,
			fps=60.0,
			stride=1,
		)
		actual = store[(frame_index, roi)]
		assert numpy.array_equal(actual[0], expected[0])
		assert numpy.array_equal(actual[1], expected[1])


#============================================
def test_short_interval_prepass_returns_byte_bounded_store() -> None:
	"""The pre-pass always returns its byte-bounded store when a reader exists."""
	store = residual_pre_pass.precompute_interval_residuals(
		reader=_make_fake_reader(),
		scene_transform=None,
		seed_start={"frame_index": 1},
		seed_end={"frame_index": 2},
		fwd_curve=[],
		bwd_curve=[],
		half_window=1,
		fps=60.0,
	)
	assert isinstance(store, residual_pre_pass._ByteBoundedLruStore)
