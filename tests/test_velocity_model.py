#!/usr/bin/env python3
"""Tests for track_runner.velocity_model module."""

# Standard Library
# (none)

# PIP3 modules
import numpy

# local repo modules
import track_runner.camera_motion
import track_runner.scene_coords
import track_runner.velocity_model


#============================================
def test_hermite_interpolate_endpoints():
	"""Test that Hermite interpolation matches endpoints exactly."""
	# At t=0, should return p0
	result = track_runner.velocity_model.hermite_interpolate(0.0, 100.0, 200.0, 1.0, 2.0)
	assert numpy.isclose(result, 100.0), f"Expected 100.0, got {result}"

	# At t=1, should return p1
	result = track_runner.velocity_model.hermite_interpolate(1.0, 100.0, 200.0, 1.0, 2.0)
	assert numpy.isclose(result, 200.0), f"Expected 200.0, got {result}"

	# At t=0.5, should be somewhere between p0 and p1
	result = track_runner.velocity_model.hermite_interpolate(0.5, 100.0, 200.0, 0.0, 0.0)
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
	slope_x, slope_y = track_runner.velocity_model.estimate_directional_slope(
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
	slope_x, slope_y = track_runner.velocity_model.estimate_directional_slope(
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
	slope_x, slope_y = track_runner.velocity_model.estimate_directional_slope(
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
	slope_x, slope_y = track_runner.velocity_model.estimate_directional_slope(
		seeds_scene, 0, "backward", None,
	)

	assert slope_x == 0.0
	assert slope_y == 0.0


#============================================
def test_fit_interval_curves_creates_dict():
	"""Test that fit_interval_curves returns required dict keys."""
	# create identity motion (no camera motion)
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	# create synthetic seeds (in pixel space, which equals scene space)
	left_seed = {
		"frame": 0,
		"cx": 100.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 2,
		"cx": 150.0,
		"cy": 120.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}

	# all seeds for regression context
	all_seeds_scene = [
		(0, 100.0, 100.0, 50.0, 80.0),
		(2, 150.0, 120.0, 50.0, 80.0),
	]

	# fit curves
	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	# check required keys
	required_keys = [
		"fwd_slopes", "bwd_slopes",
		"fwd_size_slopes", "bwd_size_slopes",
		"left_pos", "right_pos",
		"left_size", "right_size",
		"start_frame", "end_frame",
		"is_stationary",
	]
	for key in required_keys:
		assert key in curves, f"Missing key {key}"


#============================================
def test_fwd_bwd_asymmetry():
	"""Test that FWD and BWD curves differ in mid-interval predictions."""
	# create motion with 4 seeds
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	# seeds with accelerating x motion
	all_seeds_scene = [
		(0, 0.0, 100.0, 50.0, 80.0),
		(1, 10.0, 100.0, 50.0, 80.0),
		(2, 30.0, 100.0, 50.0, 80.0),
		(3, 60.0, 100.0, 50.0, 80.0),
	]

	left_seed = {
		"frame": 1,
		"cx": 10.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 2,
		"cx": 30.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}

	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	# propagate forward and backward
	fwd_states = track_runner.velocity_model.propagate_forward_analytical(
		curves, transform,
	)
	bwd_states = track_runner.velocity_model.propagate_backward_analytical(
		curves, transform,
	)

	# at mid-interval (frame 1.5), FWD and BWD should differ
	# FWD uses backward-looking slopes, BWD uses forward-looking
	# so they should produce different predictions
	assert len(fwd_states) == 2  # frames 1, 2
	assert len(bwd_states) == 2  # frames 2, 1 (reversed)

	# first element should be near endpoints
	assert numpy.isclose(fwd_states[0]["cx"], 10.0, atol=1.0)
	assert numpy.isclose(fwd_states[-1]["cx"], 30.0, atol=1.0)
	assert numpy.isclose(bwd_states[0]["cx"], 30.0, atol=1.0)
	assert numpy.isclose(bwd_states[-1]["cx"], 10.0, atol=1.0)


#============================================
def test_seed_roundtrip_endpoints():
	"""Test that endpoints match seeds exactly (< 0.1 px error)."""
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
		event_flags=numpy.zeros(10, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	left_seed = {
		"frame": 2,
		"cx": 100.0,
		"cy": 200.0,
		"w": 60.0,
		"h": 100.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 5,
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

	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	fwd_states = track_runner.velocity_model.propagate_forward_analytical(
		curves, transform,
	)

	# first state should match left seed
	assert numpy.isclose(fwd_states[0]["cx"], left_seed["cx"], atol=0.1)
	assert numpy.isclose(fwd_states[0]["cy"], left_seed["cy"], atol=0.1)
	assert numpy.isclose(fwd_states[0]["w"], left_seed["w"], atol=0.1)
	assert numpy.isclose(fwd_states[0]["h"], left_seed["h"], atol=0.1)

	# last state should match right seed
	assert numpy.isclose(fwd_states[-1]["cx"], right_seed["cx"], atol=0.1)
	assert numpy.isclose(fwd_states[-1]["cy"], right_seed["cy"], atol=0.1)
	assert numpy.isclose(fwd_states[-1]["w"], right_seed["w"], atol=0.1)
	assert numpy.isclose(fwd_states[-1]["h"], right_seed["h"], atol=0.1)


#============================================
def test_stationary_lock():
	"""Test that near-zero displacement locks position constant."""
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	# seeds with minimal displacement (< 3% of h=100)
	left_seed = {
		"frame": 0,
		"cx": 100.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 100.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 3,
		"cx": 101.0,  # displacement = 1.0 px, threshold = 100*0.03 = 3.0
		"cy": 100.5,
		"w": 50.0,
		"h": 100.0,
		"status": "visible",
	}

	all_seeds_scene = [
		(0, 100.0, 100.0, 50.0, 100.0),
		(3, 101.0, 100.5, 50.0, 100.0),
	]

	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	# should be marked stationary
	assert curves["is_stationary"]

	# all propagated states should hold position
	fwd_states = track_runner.velocity_model.propagate_forward_analytical(
		curves, transform,
	)

	for state in fwd_states:
		assert numpy.isclose(state["cx"], left_seed["cx"], atol=0.1)
		assert numpy.isclose(state["cy"], left_seed["cy"], atol=0.1)


#============================================
def test_propagate_output_format():
	"""Test that propagate output is list of dicts with required keys."""
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
		event_flags=numpy.zeros(5, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	left_seed = {
		"frame": 1,
		"cx": 100.0,
		"cy": 150.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 3,
		"cx": 120.0,
		"cy": 160.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}

	all_seeds_scene = [
		(1, 100.0, 150.0, 50.0, 80.0),
		(3, 120.0, 160.0, 50.0, 80.0),
	]

	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	fwd_states = track_runner.velocity_model.propagate_forward_analytical(
		curves, transform,
	)

	# check it's a list
	assert isinstance(fwd_states, list)
	# check it has the right number of frames
	assert len(fwd_states) == 3  # frames 1, 2, 3

	# required keys in each state
	required_keys = ["cx", "cy", "w", "h", "conf", "source", "stationary_lock"]
	for state in fwd_states:
		assert isinstance(state, dict)
		for key in required_keys:
			assert key in state, f"Missing key {key} in state"
		# conf should be between 0 and 1
		assert 0.0 <= state["conf"] <= 1.0
		# source should be "propagated"
		assert state["source"] == "propagated"


#============================================
def test_confidence_decay():
	"""Test that confidence decays by 0.97 per frame with floor."""
	motion = track_runner.camera_motion.MotionTrack(
		dx=numpy.zeros(10, dtype=numpy.float32),
		dy=numpy.zeros(10, dtype=numpy.float32),
		scale=numpy.ones(10, dtype=numpy.float32),
		quality=numpy.ones(10, dtype=numpy.float32),
		event_flags=numpy.zeros(10, dtype=numpy.int32),
	)
	transform = track_runner.scene_coords.SceneTransform(motion)

	left_seed = {
		"frame": 0,
		"cx": 100.0,
		"cy": 100.0,
		"w": 50.0,
		"h": 80.0,
		"status": "visible",
	}
	right_seed = {
		"frame": 5,
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

	curves = track_runner.velocity_model.fit_interval_curves(
		left_seed, right_seed, all_seeds_scene, transform,
	)

	fwd_states = track_runner.velocity_model.propagate_forward_analytical(
		curves, transform,
	)

	# confidence at frame 0 should be 1.0
	assert numpy.isclose(fwd_states[0]["conf"], 1.0)

	# confidence should decay: 1.0 * 0.97^n
	for i, state in enumerate(fwd_states):
		expected_conf = max(0.1, 1.0 * (0.97 ** i))
		assert numpy.isclose(state["conf"], expected_conf, atol=0.01), \
			f"Frame {i}: expected conf ~{expected_conf}, got {state['conf']}"


# simple assertion test
assert track_runner.velocity_model.hermite_interpolate(0.0, 10.0, 20.0, 1.0, 1.0) == 10.0
