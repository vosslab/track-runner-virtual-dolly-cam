"""Tests for track_runner.velocity_model module."""

# Standard Library
import unittest.mock

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import scene_coords
import velocity_model


#============================================
def test_hermite_interpolate_endpoints():
	"""Test that Hermite interpolation matches endpoints exactly."""
	# At t=0, should return p0
	result = velocity_model.hermite_interpolate(0.0, 100.0, 200.0, 1.0, 2.0)
	assert numpy.isclose(result, 100.0), f"Expected 100.0, got {result}"

	# At t=1, should return p1
	result = velocity_model.hermite_interpolate(1.0, 100.0, 200.0, 1.0, 2.0)
	assert numpy.isclose(result, 200.0), f"Expected 200.0, got {result}"

	# At t=0.5, should be somewhere between p0 and p1
	result = velocity_model.hermite_interpolate(0.5, 100.0, 200.0, 0.0, 0.0)
	assert 100.0 < result < 200.0, f"t=0.5 result {result} not between endpoints"


#============================================
def test_directional_slope_backward():
	"""Test slope estimation from backward (left) neighbors."""
	# create synthetic seeds: steady linear motion
	# seeds at frames 0, 10, 20, 30, 40
	# positions: (0, 0), (10, 10), (20, 20), (30, 30), (40, 40)
	seeds_scene = [
		(0, 0.0, 0.0, 10.0, 10.0),
		(10, 10.0, 10.0, 10.0, 10.0),
		(20, 20.0, 20.0, 10.0, 10.0),
		(30, 30.0, 30.0, 10.0, 10.0),
		(40, 40.0, 40.0, 10.0, 10.0),
	]

	# estimate slope at frame 20 from backward neighbors
	# should get slope of (20-0)/(20-0) = 1.0 per frame
	slope_x, slope_y = velocity_model.estimate_directional_slope(
		seeds_scene, 2, "backward", None,
	)

	# slope should be approximately 1.0 (x and y per frame)
	assert numpy.isclose(slope_x, 1.0, atol=0.01), f"Expected slope_x ~1.0, got {slope_x}"
	assert numpy.isclose(slope_y, 1.0, atol=0.01), f"Expected slope_y ~1.0, got {slope_y}"


#============================================
def test_directional_slope_forward():
	"""Test slope estimation from forward (right) neighbors."""
	seeds_scene = [
		(0, 0.0, 0.0, 10.0, 10.0),
		(10, 10.0, 10.0, 10.0, 10.0),
		(20, 20.0, 20.0, 10.0, 10.0),
		(30, 30.0, 30.0, 10.0, 10.0),
		(40, 40.0, 40.0, 10.0, 10.0),
	]

	# estimate slope at frame 20 from forward neighbors
	slope_x, slope_y = velocity_model.estimate_directional_slope(
		seeds_scene, 2, "forward", None,
	)

	# with forward neighbors at 30 and 40, should still get slope ~1.0
	assert numpy.isclose(slope_x, 1.0, atol=0.01), f"Expected slope_x ~1.0, got {slope_x}"
	assert numpy.isclose(slope_y, 1.0, atol=0.01), f"Expected slope_y ~1.0, got {slope_y}"


#============================================
def test_directional_slope_sparse_fallback():
	"""Test sparse fallback: single neighbor uses finite difference."""
	seeds_scene = [
		(0, 0.0, 0.0, 10.0, 10.0),
		(10, 5.0, 10.0, 10.0, 10.0),
	]

	# estimate slope at frame 10 from backward
	# only 1 neighbor, should use finite difference
	slope_x, slope_y = velocity_model.estimate_directional_slope(
		seeds_scene, 1, "backward", None,
	)

	# finite difference: (5-0) / (10-0) = 0.5
	assert numpy.isclose(slope_x, 0.5, atol=0.01), f"Expected slope_x ~0.5, got {slope_x}"
	assert numpy.isclose(slope_y, 1.0, atol=0.01), f"Expected slope_y ~1.0, got {slope_y}"


#============================================
def test_directional_slope_no_neighbors():
	"""Test that no neighbors returns (0, 0)."""
	seeds_scene = [
		(0, 0.0, 0.0, 10.0, 10.0),
	]

	# estimate slope at frame 0 from backward (no left neighbors)
	slope_x, slope_y = velocity_model.estimate_directional_slope(
		seeds_scene, 0, "backward", None,
	)

	assert slope_x == 0.0
	assert slope_y == 0.0


#============================================
def test_fwd_bwd_endpoint_alignment():
	"""Both FWD and BWD map slot 0 to start_frame and slot -1 to end_frame.

	Post-fix invariant: forward_path[i] and backward_path[i] both refer
	to the same absolute frame (start_frame + i). The two passes differ
	in internal Hermite slopes, not in array ordering.
	"""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	# seeds with accelerating x motion
	all_seeds_scene = [
		(0, 0.0, 100.0, 50.0, 80.0),
		(1, 10.0, 100.0, 50.0, 80.0),
		(2, 30.0, 100.0, 50.0, 80.0),
		(3, 60.0, 100.0, 50.0, 80.0),
	]

	left_seed = {
		"frame_index": 1,
		"cx": 10.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 2,
		"cx": 30.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	fwd_states = velocity_model.propagate_forward_analytical(
		curves, transform, blob_snap_enabled=False,
	)
	bwd_states = velocity_model.propagate_backward_analytical(
		curves, transform, blob_snap_enabled=False,
	)

	# both passes anchor slot 0 at left_seed, slot -1 at right_seed
	assert numpy.isclose(fwd_states[0]["cx"], 10.0, atol=1.0)
	assert numpy.isclose(fwd_states[-1]["cx"], 30.0, atol=1.0)
	assert numpy.isclose(bwd_states[0]["cx"], 10.0, atol=1.0)
	assert numpy.isclose(bwd_states[-1]["cx"], 30.0, atol=1.0)


#============================================
def test_seed_roundtrip_endpoints():
	"""Test that endpoints match seeds exactly (< 0.1 px error)."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	left_seed = {
		"frame_index": 2,
		"cx": 100.0,
		"cy": 200.0,
		"w": 60.0,
		"h": 100.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 5,
		"cx": 150.0,
		"cy": 220.0,
		"w": 60.0,
		"h": 100.0,
		"status": "visible",
	}

	all_seeds_scene = [
		(2, 100.0, 200.0, 60.0, 100.0),
		(5, 150.0, 220.0, 60.0, 100.0),
	]

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	fwd_states = velocity_model.propagate_forward_analytical(
		curves, transform, blob_snap_enabled=False,
	)
	bwd_states = velocity_model.propagate_backward_analytical(
		curves, transform, blob_snap_enabled=False,
	)

	# both passes' endpoints match seeds in center position
	# (chronological slot storage: slot 0 = start_frame, slot -1 = end_frame)
	assert numpy.isclose(fwd_states[0]["cx"], left_seed["cx"], atol=0.1)
	assert numpy.isclose(fwd_states[0]["cy"], left_seed["cy"], atol=0.1)
	assert numpy.isclose(fwd_states[-1]["cx"], right_seed["cx"], atol=0.1)
	assert numpy.isclose(fwd_states[-1]["cy"], right_seed["cy"], atol=0.1)
	assert numpy.isclose(bwd_states[0]["cx"], left_seed["cx"], atol=0.1)
	assert numpy.isclose(bwd_states[0]["cy"], left_seed["cy"], atol=0.1)
	assert numpy.isclose(bwd_states[-1]["cx"], right_seed["cx"], atol=0.1)
	assert numpy.isclose(bwd_states[-1]["cy"], right_seed["cy"], atol=0.1)


#============================================
def _make_decay_fixture():
	"""Shared six-frame interval fixture for confidence-decay tests."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	left_seed = {
		"frame_index": 0,
		"cx": 100.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 5,
		"cx": 120.0,
		"cy": 110.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}

	all_seeds_scene = [
		(0, 100.0, 100.0, 50.0, 80.0),
		(5, 120.0, 110.0, 50.0, 80.0),
	]

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)
	return curves, transform


#============================================
def test_fwd_confidence_decays_from_start():
	"""FWD confidence peaks at slot 0 and monotonically decreases."""
	curves, transform = _make_decay_fixture()

	fwd_states = velocity_model.propagate_forward_analytical(
		curves, transform, blob_snap_enabled=False,
	)

	confs = [s["conf"] for s in fwd_states]
	# peak at the start_frame anchor (slot 0)
	assert confs[0] == max(confs)
	# monotonically non-increasing away from the anchor
	for i in range(1, len(confs)):
		assert confs[i] <= confs[i - 1] + 1e-9


#============================================
def test_bwd_confidence_decays_from_end():
	"""BWD confidence peaks at slot n-1 and monotonically decreases.

	Locks the "BWD math unchanged, storage flipped" rule: the BWD anchor
	is the right (end_frame) seed, so confidence must peak at the last
	slot (chronological end) even though the array is stored
	chronologically.
	"""
	curves, transform = _make_decay_fixture()

	bwd_states = velocity_model.propagate_backward_analytical(
		curves, transform, blob_snap_enabled=False,
	)

	confs = [s["conf"] for s in bwd_states]
	# peak at the end_frame anchor (slot -1)
	assert confs[-1] == max(confs)
	# monotonically non-increasing as we walk away from the anchor
	for i in range(len(confs) - 1):
		assert confs[i] <= confs[i + 1] + 1e-9


#============================================
def test_fwd_bwd_slot_alignment():
	"""Both passes: slot i corresponds to absolute frame start_frame + i.

	Central invariant of the chronological-BWD fix. Uses the private
	_compute_raw_pred_* helpers directly because they expose the frame
	index in tuple[0]; the public wrappers strip it.
	"""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	left_seed = {
		"frame_index": 2,
		"cx": 100.0,
		"cy": 200.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 7,
		"cx": 150.0,
		"cy": 220.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	all_seeds_scene = [
		(2, 100.0, 200.0, 50.0, 80.0),
		(7, 150.0, 220.0, 50.0, 80.0),
	]

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)
	raw_fwd = velocity_model._compute_raw_pred_forward(curves, transform)
	raw_bwd = velocity_model._compute_raw_pred_backward(curves, transform)

	start_frame = left_seed["frame_index"]
	for i in range(len(raw_fwd)):
		assert int(raw_fwd[i][0]) == start_frame + i
		assert int(raw_bwd[i][0]) == start_frame + i


#============================================
def test_bwd_gate_tangent_chronological():
	"""BWD gate-time tangent points in the same chronological direction as FWD.

	Pre-fix, raw_bwd was stored reverse-chronologically, so v_prev =
	raw_bwd[i][1:3] - raw_bwd[i-1][1:3] pointed backward through time.
	Post-fix, both passes store chronologically, so both tangents share
	sign orientation. Monotonic-x motion fixture; sign-only check.
	"""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	left_seed = {
		"frame_index": 0,
		"cx": 50.0,
		"cy": 100.0,
		"w": 40.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 6,
		"cx": 200.0,
		"cy": 100.0,
		"w": 40.0,
		"h": 80.0,
		"status": "visible",
	}
	all_seeds_scene = [
		(0, 50.0, 100.0, 40.0, 80.0),
		(6, 200.0, 100.0, 40.0, 80.0),
	]

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)
	raw_fwd = velocity_model._compute_raw_pred_forward(curves, transform)
	raw_bwd = velocity_model._compute_raw_pred_backward(curves, transform)

	# on monotonic-x motion, v_prev.x must be positive for both passes
	# at every interior slot
	for i in range(1, len(raw_fwd) - 1):
		fwd_vx = raw_fwd[i][1] - raw_fwd[i - 1][1]
		bwd_vx = raw_bwd[i][1] - raw_bwd[i - 1][1]
		assert fwd_vx > 0.0
		assert bwd_vx > 0.0


#============================================
def test_bwd_raw_pred_confidence_peak_at_end():
	"""BWD raw_pred confidence peaks at slot n-1 (end_frame anchor).

	Tests the low-level _compute_raw_pred_backward helper directly.
	Pre-fix, confidence peaked at slot 0 (because iteration order
	coincided with decay axis). Post-fix, iteration is chronological
	but decay still measures distance from end_frame via
	frames_from_end = end_frame - frame_index, so the peak moves to
	slot n-1 and the old "peak at slot 0" assumption would fail.
	"""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	left_seed = {
		"frame_index": 0,
		"cx": 100.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame_index": 5,
		"cx": 120.0,
		"cy": 110.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	all_seeds_scene = [
		(0, 100.0, 100.0, 50.0, 80.0),
		(5, 120.0, 110.0, 50.0, 80.0),
	]

	curves = velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)
	raw_bwd = velocity_model._compute_raw_pred_backward(curves, transform)

	# confidence is field index 5 in the tuple
	confs = [float(row[5]) for row in raw_bwd]
	assert confs[-1] == max(confs)
	for i in range(len(confs) - 1):
		assert confs[i] <= confs[i + 1] + 1e-9


#============================================
def _make_two_seed_interval_fixture():
	"""Build a minimal two-seed interval for blob_snap_enabled parity tests.

	Local copy (deliberate): avoids cross-test imports per the test-cleanup
	plan. A near-identical helper lives in test_tr_stage4_parity.
	"""
	seed_start = {"frame_index": 0, "cx": 100.0, "cy": 100.0, "w": 50.0, "h": 50.0}
	seed_end = {"frame_index": 10, "cx": 200.0, "cy": 200.0, "w": 50.0, "h": 50.0}
	all_seeds_scene = [
		(0, 100.0, 100.0, 50.0, 50.0),
		(10, 200.0, 200.0, 50.0, 50.0),
	]
	motion_track = camera_motion.MotionTrack(
		dx=numpy.zeros(11, dtype=numpy.float32),
		dy=numpy.zeros(11, dtype=numpy.float32),
		scale=numpy.ones(11, dtype=numpy.float32),
		quality=numpy.ones(11, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion_track)
	curves = velocity_model.fit_interval_curves(
		seed_start, seed_end, all_seeds_scene, transform,
	)
	return transform, curves


#============================================
def test_propagator_skips_blob_observer_when_disabled():
	"""blob_snap_enabled=False must never call residual_motion.observe_blob_at on either pass."""
	transform, curves = _make_two_seed_interval_fixture()

	with unittest.mock.patch("residual_motion.observe_blob_at") as mock_observe:
		mock_observe.side_effect = RuntimeError("observe_blob_at should not be called")

		velocity_model.propagate_forward_analytical(
			curves, transform, blob_snap_enabled=False, reader=None,
		)
		velocity_model.propagate_backward_analytical(
			curves, transform, blob_snap_enabled=False, reader=None,
		)

		assert mock_observe.call_count == 0


#============================================
def test_propagator_hermite_only_matches_no_reader_path():
	"""Round-trip invariant: with reader=None, blob_snap_enabled has no observable effect.

	Blob snap falls through to raw_pred when no video data is available, so
	both modes must produce trajectories matching frame-by-frame on FWD and BWD.
	"""
	transform, curves = _make_two_seed_interval_fixture()

	for direction, propagate in (
		("fwd", velocity_model.propagate_forward_analytical),
		("bwd", velocity_model.propagate_backward_analytical),
	):
		hermite_only = propagate(curves, transform, blob_snap_enabled=False, reader=None)
		blob_no_reader = propagate(curves, transform, blob_snap_enabled=True, reader=None)
		for state_h, state_b in zip(hermite_only, blob_no_reader):
			assert numpy.isclose(state_h["cx"], state_b["cx"], atol=1e-6), direction
			assert numpy.isclose(state_h["cy"], state_b["cy"], atol=1e-6), direction

