"""Behavior tests for the stateless per-frame blob snap in velocity_model.

These tests enforce the design invariants from
~/.claude/plans/happy-forging-valiant.md:

  - gates read raw_pred only, never snap_pred (no indirect state);
  - a blob accepted at frame t cannot influence decisions at t+1;
  - blob evidence at a seed frame never moves the seed;
  - missing blobs fall through to raw Hermite with no exception;
  - displacement clamp caps per-frame shift.

The observer is monkey-patched to return deterministic BlobObservation
instances for specific frames, keeping tests synthetic and fast.
"""

# Standard Library
import math

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import interval_solver
import residual_motion
import scene_coords
import state_io
import velocity_model


#============================================
class _StubReader:
	"""Minimal video-reader stub satisfying observe_blob_at's attribute checks."""

	def __init__(self, width: int = 1920, height: int = 1080, frame_count: int = 1000):
		self.width = width
		self.height = height
		self.frame_count = frame_count

	def read_frame(self, frame_index: int):
		# never actually called: the observer is monkey-patched to a stub.
		return None

	def get_info(self) -> dict:
		return {"fps": 30.0}


def _make_motion_track(n_frames: int) -> camera_motion.MotionTrack:
	return camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)


def _make_curves_linear() -> dict:
	"""Build fit_interval_curves dict for a 20-frame linear-motion interval."""
	transform = scene_coords.SceneTransform(_make_motion_track(30))
	all_seeds_scene = [
		(0, 0.0, 100.0, 40.0, 60.0),
		(10, 100.0, 100.0, 40.0, 60.0),
		(20, 200.0, 100.0, 40.0, 60.0),
		(30, 300.0, 100.0, 40.0, 60.0),
	]
	left_seed = {
		"frame_index": 10, "cx": 100.0, "cy": 100.0,
		"w": 40.0, "h": 60.0, "status": "visible",
	}
	right_seed = {
		"frame_index": 20, "cx": 200.0, "cy": 100.0,
		"w": 40.0, "h": 60.0, "status": "visible",
	}
	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)
	return curves, transform


#============================================
def test_delete_test_no_observer_equals_pure_hermite():
	"""reader=None yields identical trajectory to when the observer returns None every frame.

	This is the plan's delete-test: removing the blob-observer call must
	revert to pre-patch propagation exactly.
	"""
	curves, transform = _make_curves_linear()

	# baseline: no reader (pure Hermite path)
	fwd_none = velocity_model.propagate_forward_analytical(curves, transform)

	# observer stubbed to always return None
	reader = _StubReader()
	cache = {}

	def _always_none(*args, **kwargs):
		return None

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _always_none
	try:
		fwd_stubbed = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache=cache,
		)
	finally:
		residual_motion.observe_blob_at = original

	for a, b in zip(fwd_none, fwd_stubbed):
		assert numpy.isclose(a["cx"], b["cx"], atol=1e-9)
		assert numpy.isclose(a["cy"], b["cy"], atol=1e-9)
		# sources must both be plain "propagated" (no snap was applied)
		assert b["source"] == "propagated"


#============================================
def test_blob_accepted_at_frame_t_does_not_influence_frame_t_plus_one():
	"""A blob snap at frame t must not leak into the decision at t+1.

	Gates read raw_pred[t-1..t+1] only. Accepting a blob at t+2 should
	produce the same result whether or not t+5 also has a blob.
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()

	# scenario A: blob accepted at t_index=5 only
	# scenario B: blob accepted at t_index=5 AND t_index=6
	# in a stateful system, the extra blob at 6 would shift the "base" for
	# subsequent frames. in ours, frame 7's decision must be identical in
	# both scenarios because frame 7 reads raw_pred[6..8], never snap_pred.

	def _make_observer(accept_frames: set):
		def _obs(frame_index, pred_center, pred_box, *args, **kwargs):
			if frame_index in accept_frames:
				# return a blob exactly at the prediction (no actual shift)
				return residual_motion.BlobObservation(
					center_pixel=(pred_center[0] + 1.0, pred_center[1]),
					cross_track=0.0,
					along_track=1.0,
					confidence=0.8,
				)
			return None
		return _obs

	original = residual_motion.observe_blob_at
	try:
		residual_motion.observe_blob_at = _make_observer({15})
		cache_a = {}
		fwd_a = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache=cache_a,
		)

		residual_motion.observe_blob_at = _make_observer({15, 16})
		cache_b = {}
		fwd_b = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache=cache_b,
		)
	finally:
		residual_motion.observe_blob_at = original

	# frame 17 (index 7 in the 11-state output, frames 10..20) must match.
	# this is the frame one step after both test frames diverge.
	idx_17 = 17 - curves["start_frame"]
	assert numpy.isclose(fwd_a[idx_17]["cx"], fwd_b[idx_17]["cx"], atol=1e-9)
	assert numpy.isclose(fwd_a[idx_17]["cy"], fwd_b[idx_17]["cy"], atol=1e-9)


#============================================
def test_seed_endpoints_never_moved_by_blob():
	"""Blob observations at or near seed frames never shift the seed positions.

	Propagator explicitly skips snap on the first and last frame of each
	pass (the two seed endpoints). Runs a full solve with an observer
	that always offers a large-shift blob, and checks that endpoint
	positions match the seeds exactly.
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()

	def _always_far_blob(frame_index, pred_center, pred_box, *args, **kwargs):
		# huge offset; any pass would fail gates, but for the endpoint we
		# should not even consult the observer
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 500.0, pred_center[1] + 500.0),
			cross_track=250.0,
			along_track=250.0,
			confidence=1.0,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _always_far_blob
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	# endpoints match left_pos and right_pos in pixel space (motion track is
	# identity so scene == pixel)
	assert numpy.isclose(fwd[0]["cx"], 100.0, atol=1.0)
	assert fwd[0]["blob_gate"] == "skipped"
	assert numpy.isclose(fwd[-1]["cx"], 200.0, atol=1.0)
	assert fwd[-1]["blob_gate"] == "skipped"


#============================================
def test_proximity_gate_rejects_far_blob():
	"""A blob far beyond ALPHA * box_height is rejected.

	Offset scales with BLOB_SNAP_ALPHA so the test survives future
	tuning: whatever the alpha, "far" is "well past the cap".
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()
	# 4x the proximity cap is "definitely far" at any plausible ALPHA.
	far_offset = 4.0 * velocity_model.BLOB_SNAP_ALPHA * 60.0

	def _far_observer(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + far_offset, pred_center[1]),
			cross_track=0.0, along_track=far_offset, confidence=0.8,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _far_observer
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	# behavioral invariant: no accepted frames; all observed frames rejected
	accepted = sum(1 for s in fwd if s["blob_gate"] == "accepted")
	assert accepted == 0
	# positions unchanged from pure Hermite (round-trip invariant)
	baseline = velocity_model.propagate_forward_analytical(curves, transform)
	for a, b in zip(fwd, baseline):
		assert numpy.isclose(a["cx"], b["cx"], atol=1e-9)


#============================================
def test_accepted_blob_displacement_clamped():
	"""Per-frame snap shift is bounded by alpha_eff * max_shift regardless of blob offset.

	Bound uses the same constants the implementation uses, so the test
	survives tuning of ALPHA_MAX or MAX_SHIFT_FRACTION. Offset position
	is placed just inside the proximity cap so the blob passes gates
	and the displacement clamp is exercised.
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()
	box_h = 60.0
	proximity_cap = velocity_model.BLOB_SNAP_ALPHA * box_h
	# sit 10% inside the proximity cap so the blob is accepted at any
	# reasonable ALPHA; along-track so direction gate is trivially met
	offset = 0.9 * proximity_cap

	def _at_edge_observer(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + offset, pred_center[1]),
			cross_track=0.0, along_track=offset,
			confidence=0.99,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _at_edge_observer
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	baseline = velocity_model.propagate_forward_analytical(curves, transform)
	# clamp bound derives from the same constants the solver uses
	max_expected_shift = (
		velocity_model.BLOB_SNAP_ALPHA_MAX
		* velocity_model.BLOB_SNAP_MAX_SHIFT_FRACTION
		* box_h
	)
	for a, b in zip(fwd, baseline):
		dx = a["cx"] - b["cx"]
		dy = a["cy"] - b["cy"]
		shift = math.hypot(dx, dy)
		assert shift <= max_expected_shift + 1e-6


#============================================
def test_occlusion_fallback_equals_raw_hermite():
	"""Frames with no blob candidate fall through to pure Hermite exactly."""
	curves, transform = _make_curves_linear()
	reader = _StubReader()
	baseline = velocity_model.propagate_forward_analytical(curves, transform)

	# observer returns None for every 2nd frame (50% occlusion simulation)
	def _half_observer(frame_index, pred_center, pred_box, *args, **kwargs):
		if frame_index % 2 == 0:
			return None
		# odd frames get a large off-corridor blob (will be rejected anyway)
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 200.0, pred_center[1] + 200.0),
			cross_track=200.0, along_track=0.0, confidence=0.9,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _half_observer
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	# on even-index (absent-blob) frames, output must match pure Hermite
	for i, (a, b) in enumerate(zip(fwd, baseline)):
		frame_index = curves["start_frame"] + i
		if frame_index % 2 == 0:
			assert numpy.isclose(a["cx"], b["cx"], atol=1e-9)
			assert numpy.isclose(a["cy"], b["cy"], atol=1e-9)


#============================================
def test_coverage_split_is_none_when_no_candidate_blobs():
	"""Per-pass coverage fields are None when no candidates exist."""
	seeds = [
		{"frame_index": 10, "cx": 100.0, "cy": 100.0, "w": 40.0, "h": 60.0, "status": "visible"},
		{"frame_index": 50, "cx": 500.0, "cy": 100.0, "w": 40.0, "h": 60.0, "status": "visible"},
	]
	motion = _make_motion_track(60)
	transform = scene_coords.SceneTransform(motion)
	reader = _StubReader(frame_count=60)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = lambda *a, **k: None
	try:
		diagnostics = interval_solver.solve_all_intervals(
			reader, seeds, detector=None, config={},
			scene_transform=transform, motion_track=motion,
		)
	finally:
		residual_motion.observe_blob_at = original

	intervals = diagnostics["intervals"]
	score = intervals[0]["interval_score"]
	assert score["blob_coverage_fwd"] is None
	assert score["blob_coverage_bwd"] is None
	assert score["no_candidate_blobs"] is True


#============================================
def test_coverage_split_reports_per_pass():
	"""Per-pass coverage is reported independently (diagnostic purity)."""
	seeds = [
		{"frame_index": 10, "cx": 100.0, "cy": 100.0, "w": 40.0, "h": 60.0, "status": "visible"},
		{"frame_index": 40, "cx": 400.0, "cy": 100.0, "w": 40.0, "h": 60.0, "status": "visible"},
	]
	motion = _make_motion_track(50)
	transform = scene_coords.SceneTransform(motion)
	reader = _StubReader(frame_count=50)

	# observer returns a blob exactly at prediction; gates are trivially
	# satisfied so every non-endpoint frame accepts.
	def _accept_at_pred(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 1.0, pred_center[1]),
			cross_track=0.0, along_track=1.0, confidence=0.9,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _accept_at_pred
	try:
		diagnostics = interval_solver.solve_all_intervals(
			reader, seeds, detector=None, config={},
			scene_transform=transform, motion_track=motion,
		)
	finally:
		residual_motion.observe_blob_at = original

	score = diagnostics["intervals"][0]["interval_score"]
	for key in ("blob_coverage_fwd", "blob_coverage_bwd"):
		value = score[key]
		assert value is not None, f"{key} should have a value"
		assert 0.0 <= value <= 1.0
	# each pass visited the same non-endpoint frames, so their candidate
	# counts should match when observer semantics are symmetric.
	assert score["candidate_frame_count_fwd"] > 0
	assert score["candidate_frame_count_bwd"] > 0


#============================================
def test_solver_fingerprint_includes_geometry_schema_tag():
	"""The fingerprint wrapper appends GEOMETRY_TAG so refine busts on upgrade."""
	seed_a = {"frame_index": 0, "cx": 0.0, "cy": 0.0, "w": 40.0, "h": 60.0}
	seed_b = {"frame_index": 100, "cx": 100.0, "cy": 0.0, "w": 40.0, "h": 60.0}

	plain = state_io.interval_fingerprint(seed_a, seed_b)
	tagged = interval_solver.compute_interval_fingerprint(seed_a, seed_b)
	assert tagged != plain
	assert tagged.startswith(plain)
	assert "geometry_schema_v" in tagged


#============================================
def test_severity_is_independent_of_blob_coverage():
	"""Severity classification reads agreement and tier, not blob coverage.

	The coverage fields are diagnostic-only. Two intervals with identical
	agreement and tier but wildly different coverage values must produce
	the same severity.
	"""
	# local import to avoid polluting module-level imports with review module
	import review

	def _make_interval(**score_overrides):
		score = {
			"confidence_tier": "good",
			"agreement": 0.25,
			"failure_reasons": [],
		}
		score.update(score_overrides)
		return {
			"interval_score": score,
			"start_frame": 0,
			"end_frame": 50,
		}

	iv_high_coverage = _make_interval(
		blob_coverage_fwd=0.95, blob_coverage_bwd=0.95,
	)
	iv_low_coverage = _make_interval(
		blob_coverage_fwd=0.05, blob_coverage_bwd=0.05,
	)
	iv_no_coverage = _make_interval(
		blob_coverage_fwd=None, blob_coverage_bwd=None,
	)

	sev_high = review.classify_interval_severity(iv_high_coverage, fps=30.0)
	sev_low = review.classify_interval_severity(iv_low_coverage, fps=30.0)
	sev_none = review.classify_interval_severity(iv_no_coverage, fps=30.0)
	assert sev_high == sev_low == sev_none, (
		f"severity must be independent of coverage, got "
		f"{sev_high} / {sev_low} / {sev_none}"
	)


#============================================
def test_motion_path_gate_rejects_off_line_blob():
	"""A blob ON PROXIMITY but OFF the motion line is rejected.

	Previous temporal gate collapsed to a velocity-scaled proximity
	check and accepted any blob inside the velocity-scaled radius. The
	new motion-path gate rejects blobs that are off-axis.

	Perpendicular offset is scaled to sit inside the proximity cap (so
	proximity gate passes) but beyond the motion-path perpendicular cap
	(so the new gate rejects). Scaled from the same constants the
	implementation uses, so the test survives tuning.
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()
	box_h = 60.0
	# inside proximity cap AT ALPHA * h, but far outside the motion-path
	# perpendicular cap of PATH_PERP_FRACTION * |v|. For our linear
	# 10 px/frame fixture, |v| ~= 10 and PERP cap ~= 7.5. Pick 0.9 * ALPHA * h
	# which is >= 0.9 * 0.6 * 60 = 32 px -- well past any plausible PERP cap.
	perp_offset = 0.9 * velocity_model.BLOB_SNAP_ALPHA * box_h

	def _sideways_blob(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0], pred_center[1] + perp_offset),
			cross_track=perp_offset, along_track=0.0, confidence=0.9,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _sideways_blob
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	# behavioral invariant: no accepted frames despite inside-proximity blob
	accepted = sum(1 for s in fwd if s["blob_gate"] == "accepted")
	assert accepted == 0


#============================================
def test_motion_path_gate_accepts_on_line_blob():
	"""A blob on the motion line is accepted, even with a forward offset."""
	curves, transform = _make_curves_linear()
	reader = _StubReader()

	# linear x-motion at ~10 px/frame. Place blob 5 px AHEAD along the
	# motion direction, no perpendicular offset. Should accept.
	def _ahead_on_line_blob(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 5.0, pred_center[1]),
			cross_track=0.0, along_track=5.0, confidence=0.9,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _ahead_on_line_blob
	try:
		fwd = velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	accepted = sum(1 for s in fwd if s["blob_gate"] == "accepted")
	# at least some non-endpoint frames should have accepted
	assert accepted > 0


#============================================
def test_roi_quantization_collapses_subpixel_jitter():
	"""Small pred-center differences share an ROI (and cache entry).

	FWD and BWD at the same frame typically differ by a few pixels due
	to Hermite slope asymmetry. Without quantization each would get a
	distinct ROI tuple and a separate cache entry, doubling residual
	computation even though both are looking at the same image region.
	With quantization, sub-quantum differences collapse to one ROI.
	"""
	box_h = 60.0
	frame_w, frame_h = 1920, 1080
	quant = residual_motion.ROI_QUANT
	# snap to a clean bucket anchor so the "within-bucket" test is robust
	# against banker's rounding at bucket boundaries
	cx_clean = quant * round(500.0 / quant)
	cy_clean = quant * round(300.0 / quant)
	# jitter well under half the bucket width stays in the same bucket
	jitter = 1.0
	roi_a = residual_motion._compute_roi(cx_clean, cy_clean, box_h, frame_w, frame_h)
	roi_b = residual_motion._compute_roi(
		cx_clean + jitter, cy_clean + jitter, box_h, frame_w, frame_h,
	)
	assert roi_a == roi_b, (
		f"sub-quantum jitter should share ROI: {roi_a} vs {roi_b}"
	)
	# a prediction many quanta away gets a distinct ROI
	roi_c = residual_motion._compute_roi(
		cx_clean + 10 * quant, cy_clean, box_h, frame_w, frame_h,
	)
	assert roi_a != roi_c, (
		f"meaningful divergence should split ROI: both were {roi_a}"
	)


#============================================
def test_raw_pred_is_never_mutated_by_snap():
	"""Stage 1's raw_pred list is not modified by Stage 2's blob snap.

	Invariant: the stateless propagator must be decomposable into "pure
	Hermite -> snap(raw)" where raw is frozen input to the snap layer.
	If _apply_blob_snap ever wrote into raw, the second call (BWD reusing
	FWD's raw in some future refactor) would pick up blob-driven edits
	as "pure kinematics" -- exactly the soft-state failure mode the
	design forbids.
	"""
	curves, transform = _make_curves_linear()
	raw_before = velocity_model._compute_raw_pred_forward(curves, transform)
	raw_snapshot = [tuple(entry) for entry in raw_before]

	reader = _StubReader()

	def _offset_blob(frame_index, pred_center, pred_box, *args, **kwargs):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 3.0, pred_center[1]),
			cross_track=0.0, along_track=3.0, confidence=0.9,
		)

	original = residual_motion.observe_blob_at
	residual_motion.observe_blob_at = _offset_blob
	try:
		velocity_model._apply_blob_snap(raw_before, reader, transform, {})
	finally:
		residual_motion.observe_blob_at = original

	# raw is unchanged entry-for-entry
	for actual, expected in zip(raw_before, raw_snapshot):
		assert actual == expected, (
			f"raw_pred entry mutated: {actual!r} vs {expected!r}"
		)


#============================================
def test_observer_inputs_depend_only_on_raw_pred():
	"""Inputs the propagator passes to the observer are deterministic from raw_pred.

	The observer is called with (frame_index, pred_center, pred_box,
	local_tangent, ...) computed purely from raw_pred[i-1..i+1]. A second
	propagation run on identical curves must pass identical inputs to
	the observer at each frame regardless of whether earlier frames
	accepted or rejected a blob.

	Proves this by running the pass twice with different observer outputs:
	run A always returns None, run B always accepts. The observer's
	INPUTS must be byte-for-byte identical at every frame -- the decisions
	differ, but the inputs do not.
	"""
	curves, transform = _make_curves_linear()
	reader = _StubReader()

	def _make_recorder(on_call):
		record = {}

		def _recorder(frame_index, pred_center, pred_box, local_tangent, *args, **kwargs):
			record[frame_index] = (
				round(pred_center[0], 9), round(pred_center[1], 9),
				round(pred_box[0], 9), round(pred_box[1], 9),
				round(local_tangent[0], 9), round(local_tangent[1], 9),
			)
			return on_call(pred_center)

		return record, _recorder

	record_a, recorder_a = _make_recorder(lambda pc: None)

	def _accept_at(pred_center):
		return residual_motion.BlobObservation(
			center_pixel=(pred_center[0] + 1.0, pred_center[1]),
			cross_track=0.0, along_track=1.0, confidence=0.9,
		)

	record_b, recorder_b = _make_recorder(_accept_at)

	original = residual_motion.observe_blob_at
	try:
		residual_motion.observe_blob_at = recorder_a
		velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
		residual_motion.observe_blob_at = recorder_b
		velocity_model.propagate_forward_analytical(
			curves, transform, reader=reader, residual_cache={},
		)
	finally:
		residual_motion.observe_blob_at = original

	# observer saw the same input tuple at every frame in both runs.
	# different return values must not perturb subsequent inputs.
	assert record_a == record_b
