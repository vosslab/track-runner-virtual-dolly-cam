"""Behavioral tests for observe_blob_at's SOURCE-space return under bin.

These tests do not assert on internal cache shapes or hardcoded
implementation values. They check the property:
"BlobObservation.center_pixel is a coord_space.SourcePoint (SOURCE
space) regardless of FrameReader.bin_factor."

Under the Option A contract (2026-05-29) the INPUTS are PROCESSED and
the RETURN is SOURCE. The typed boundary (M2/WS2-B) makes inputs
coord_space.ProcessedPoint / ProcessedBox and the return centroid a
coord_space.SourcePoint. The mechanism is verified by mocking the inner
pipeline (residual, DoG, blob extraction) so we can place a synthetic
blob at a known PROCESSED-frame centroid and confirm observe_blob_at
converts it to the SOURCE-frame value the caller expects.
"""

# PIP3 modules
import numpy
import pytest

# local repo modules
import residual_motion
import common_tools.frame_reader
import common_tools.coord_space as coord_space


_BIN_2_GEOMETRY = common_tools.frame_reader.FrameGeometry(
	source_width=512,
	source_height=512,
	bin_factor=2,
	scaled_width=256,
	scaled_height=256,
	processed_width=256,
	processed_height=256,
)
_BIN_1_GEOMETRY = common_tools.frame_reader.FrameGeometry(
	source_width=512,
	source_height=512,
	bin_factor=1,
	scaled_width=512,
	scaled_height=512,
	processed_width=512,
	processed_height=512,
)


#============================================
class _StubReader:
	"""Minimal reader stub matching the observe_blob_at contract."""

	def __init__(self, geometry: common_tools.frame_reader.FrameGeometry):
		self.geometry = geometry
		self.width = geometry.processed_width
		self.height = geometry.processed_height
		self.fps = 30.0
		self.frame_count = 100

	def read_frame(self, idx):  # pragma: no cover - never called via stubs
		return numpy.zeros((self.height, self.width, 3), dtype=numpy.uint8)


#============================================
def _patch_inner_pipeline(monkeypatch, blob_proc_xy, blob_score=0.7):
	"""Stub residual_motion's inner pipeline to deliver a single blob.

	`blob_proc_xy` is the (x, y) centroid in PROCESSED-FRAME pixels --
	exactly what extract_frame_blobs would return after the
	roi-offset restoration.
	"""
	# compute_residual_for_frame returns (residual, validity_mask)
	def _stub_residual(reader, frame_index, scene_transform,
		half_window, frame_read_cache, roi,
		fps=None, stride=None):
		h = reader.height
		w = reader.width
		residual = numpy.zeros((h, w), dtype=numpy.float32)
		validity = numpy.ones((h, w), dtype=numpy.uint8)
		return residual, validity

	# dog filter is a no-op for the test
	def _stub_dog(residual, pred_w, k):
		return residual

	# extract_frame_blobs returns a single blob at (x, y) IN ROI COORDS
	# (caller adds roi_x1/roi_y1). To land on a known full-frame
	# processed centroid (bx, by), we return ROI-relative
	# (bx - roi_x1, by - roi_y1) so observe_blob_at's offset-restore
	# yields (bx, by).
	bx, by = blob_proc_xy
	def _stub_extract(dog, validity, threshold):
		# We accept any ROI; observe_blob_at will set roi[0],roi[1]
		# from _compute_roi. For the test we use a static synthetic
		# blob and rely on filter_blobs_to_corridor to pass it.
		return [{
			"centroid_x": float(bx) - 0.0,  # roi offset added by caller
			"centroid_y": float(by) - 0.0,
			"area": 50,
			"integrated_mag": 5000.0,
			"label_id": 1,
			"bbox": (0, 0, 8, 8),
			"small_blob": False,
			"max_intensity": 10.0,
			"mean_intensity": 5.0,
		}]

	# corridor filter passes the single blob through with a
	# cross/along of zero (the test does not depend on these,
	# but they must be set so they survive the exit conversion).
	def _stub_corridor(blobs, cx, cy, tangent, radius):
		for b in blobs:
			b["cross_track"] = 0.0
			b["along_track"] = 0.0
		return blobs

	def _stub_confidence(blob, cx, cy, w, h, tangent=None):
		blob["total_score"] = blob_score
		blob["strength_score"] = blob_score
		blob["proximity_score"] = blob_score
		blob["size_score"] = 0.0
		blob["dist_h"] = 0.0
		return blob_score

	monkeypatch.setattr(residual_motion, "compute_residual_for_frame", _stub_residual)
	monkeypatch.setattr(residual_motion, "dog_filter_blob_scale", _stub_dog)
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", _stub_extract)
	monkeypatch.setattr(residual_motion, "filter_blobs_to_corridor", _stub_corridor)
	monkeypatch.setattr(residual_motion, "compute_cue_confidence", _stub_confidence)


#============================================
def _observe_at_geometry(monkeypatch, geometry, proc_pred_xy, proc_box_wh, blob_proc_xy):
	"""Helper: run observe_blob_at with the given geometry and stubs.

	proc_pred_xy / proc_box_wh are PROCESSED-space coords (Option A); they
	are wrapped into typed coord_space primitives at the boundary.
	"""
	_patch_inner_pipeline(monkeypatch, blob_proc_xy)
	reader = _StubReader(geometry)
	cache = {}
	# but the stub's _stub_extract returns blob coords WITHOUT the
	# roi offset baked in -- observe_blob_at adds roi_x1/roi_y1.
	# To make the math come out at exactly blob_proc_xy, we need the
	# stub to subtract roi_x1/y1 OR we monkey the offset add. Easier:
	# patch extract_frame_blobs to receive ROI bounds via a closure.
	# (already done above; we rely on the corridor stub to deliver
	# raw_blobs untouched and observe_blob_at to add the ROI offset.
	# That means our stub returns ABSOLUTE blob_proc_xy minus zero;
	# the caller will add roi[0]/roi[1]. For correctness, we override
	# the stub here so it subtracts the actual ROI it sees.)
	bx, by = blob_proc_xy

	def _stub_extract(dog, validity, threshold, _bx=bx, _by=by):
		return [{
			"centroid_x": float(_bx),
			"centroid_y": float(_by),
			"area": 50,
			"integrated_mag": 5000.0,
			"label_id": 1,
			"bbox": (0, 0, 8, 8),
			"small_blob": False,
			"max_intensity": 10.0,
			"mean_intensity": 5.0,
		}]

	# in observe_blob_at, raw_blobs get roi_x1/roi_y1 added; for our
	# test we want the final processed centroid to equal blob_proc_xy.
	# Override _compute_roi so its (x1,y1)==(0,0) -- absolute coords
	# preserved.
	monkeypatch.setattr(
		residual_motion,
		"_compute_roi",
		lambda cx, cy, h, fw, fh: (0, 0, fw, fh),
	)
	monkeypatch.setattr(residual_motion, "extract_frame_blobs", _stub_extract)
	pred_cx, pred_cy = proc_pred_xy
	box_w, box_h = proc_box_wh
	return residual_motion.observe_blob_at(
		frame_index=10,
		pred_center=coord_space.ProcessedPoint(cx=pred_cx, cy=pred_cy),
		pred_box=coord_space.ProcessedBox(cx=pred_cx, cy=pred_cy, w=box_w, h=box_h),
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=None,
		reader=reader,
		residual_cache=cache,
	)


#============================================
def test_observe_blob_at_returns_source_frame_at_bin1(monkeypatch):
	# baseline: at bin=1, src == proc; processed pred=(100,80), a blob at
	# processed (100,80) should produce a SOURCE-space SourcePoint at (100,80).
	obs = _observe_at_geometry(
		monkeypatch,
		geometry=_BIN_1_GEOMETRY,
		proc_pred_xy=(100.0, 80.0),
		proc_box_wh=(40.0, 60.0),
		blob_proc_xy=(100.0, 80.0),
	)
	assert obs is not None
	assert isinstance(obs.center_pixel, coord_space.SourcePoint)
	assert (obs.center_pixel.cx, obs.center_pixel.cy) == pytest.approx((100.0, 80.0))


#============================================
def test_observe_blob_at_returns_source_frame_at_bin2(monkeypatch):
	# under bin=2 the function receives PROCESSED pred=(100,80) and
	# pred_box=(40,60) directly (Option A: inputs are processed). A blob
	# at processed (100,80) MUST come back as a SOURCE SourcePoint at
	# (200,160) -- the single processed->source conversion at exit.
	obs = _observe_at_geometry(
		monkeypatch,
		geometry=_BIN_2_GEOMETRY,
		proc_pred_xy=(100.0, 80.0),
		proc_box_wh=(40.0, 60.0),
		blob_proc_xy=(100.0, 80.0),
	)
	assert obs is not None
	assert isinstance(obs.center_pixel, coord_space.SourcePoint)
	# property: bin_factor scales centroid back to source-frame
	assert (obs.center_pixel.cx, obs.center_pixel.cy) == pytest.approx((200.0, 160.0))


#============================================
def test_observe_blob_at_cache_key_is_processed_frame(monkeypatch):
	# property: raw blobs are stored under a processed-frame key;
	# the public API returns source-frame values.
	_patch_inner_pipeline(monkeypatch, (100.0, 80.0))
	monkeypatch.setattr(
		residual_motion, "_compute_roi",
		lambda cx, cy, h, fw, fh: (0, 0, fw, fh),
	)
	reader = _StubReader(_BIN_2_GEOMETRY)
	cache = {}
	residual_motion.observe_blob_at(
		frame_index=5,
		pred_center=coord_space.ProcessedPoint(cx=100.0, cy=80.0),
		pred_box=coord_space.ProcessedBox(cx=100.0, cy=80.0, w=40.0, h=60.0),
		local_tangent=(1.0, 0.0, 0.0, 1.0),
		scene_transform=None,
		reader=reader,
		residual_cache=cache,
	)
	# at least one cache entry uses the processed-frame key name.
	# (Frames sub-cache uses "_frames"; the per-(frame, roi) entries
	# carry "raw_blobs_processed".)
	per_roi_entries = [
		v for k, v in cache.items() if k != "_frames"
	]
	assert per_roi_entries, "expected at least one (frame, roi) cache entry"
	for entry in per_roi_entries:
		assert "raw_blobs_processed" in entry
		assert "raw_blobs" not in entry
