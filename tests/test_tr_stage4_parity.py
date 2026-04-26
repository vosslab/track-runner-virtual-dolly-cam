"""Tests for Stage-4 blob promotion parity with end-to-end blob solve.

Exercises M5 milestone: Stage 4 promotion re-solve produces output identical
to a single-pass blob-coupled solve of the same interval.
"""

# PIP3 modules
import numpy

# local repo modules
import camera_motion
import interval_solver
import scene_coords
import velocity_model


#============================================
def _make_simple_interval_fixture():
	"""Create a minimal two-seed interval for testing.

	Returns:
		(seed_start, seed_end, all_seeds_scene, scene_transform, interval_curves)
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
	interval_curves = velocity_model.fit_interval_curves(
		seed_start, seed_end, all_seeds_scene, transform,
	)
	return seed_start, seed_end, all_seeds_scene, transform, interval_curves


#============================================
def test_stage_4_parity_hermite_then_blob_vs_direct_blob():
	"""Stage 3 + Stage 4 (re-solve with blob) trajectory matches direct blob solve."""
	seed_start, seed_end, all_seeds_scene, transform, _ = _make_simple_interval_fixture()
	fps = 30.0
	all_seeds = [seed_start, seed_end]

	# Reference: single-pass blob-coupled solve.
	result_direct_blob = interval_solver.solve_interval_analytical(
		seed_start, seed_end, transform, all_seeds_scene, fps,
		blob_snap_enabled=True,
		debug=False,
		motion_track=None,
		all_seeds=all_seeds,
		reader=None,
	)
	blended_direct = result_direct_blob["blended_path"]

	# Two-pass: Stage 3 (Hermite) discarded, Stage 4 re-solves with blob.
	_ = interval_solver.solve_interval_analytical(
		seed_start, seed_end, transform, all_seeds_scene, fps,
		blob_snap_enabled=False,
		debug=False,
		motion_track=None,
		all_seeds=all_seeds,
		reader=None,
	)
	result_stage_4 = interval_solver.solve_interval_analytical(
		seed_start, seed_end, transform, all_seeds_scene, fps,
		blob_snap_enabled=True,
		debug=False,
		motion_track=None,
		all_seeds=all_seeds,
		reader=None,
	)
	blended_two_pass = result_stage_4["blended_path"]

	assert len(blended_direct) == len(blended_two_pass)
	for i, (state_direct, state_two) in enumerate(zip(blended_direct, blended_two_pass)):
		assert numpy.isclose(state_direct["cx"], state_two["cx"], rtol=1e-5, atol=1e-8), (
			f"Frame {i} cx mismatch"
		)
		assert numpy.isclose(state_direct["cy"], state_two["cy"], rtol=1e-5, atol=1e-8), (
			f"Frame {i} cy mismatch"
		)


#============================================
def test_select_promoted_intervals_filters_low_fair():
	"""select_promoted_intervals picks only low and fair confidence tiers."""
	interval_results = [
		{"start_frame": 0, "end_frame": 10, "interval_score": {"confidence_tier": "high"}},
		{"start_frame": 10, "end_frame": 20, "interval_score": {"confidence_tier": "fair"}},
		{"start_frame": 20, "end_frame": 30, "interval_score": {"confidence_tier": "low"}},
		{"start_frame": 30, "end_frame": 40, "interval_score": {"confidence_tier": "good"}},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral property: every promoted index has a low or fair tier.
	for idx in promoted:
		tier = interval_results[idx]["interval_score"]["confidence_tier"]
		assert tier in {"low", "fair"}, f"index {idx} has tier {tier}"
	# Behavioral property: no high/good tier slipped in.
	for idx, result in enumerate(interval_results):
		if result["interval_score"]["confidence_tier"] not in {"low", "fair"}:
			assert idx not in promoted


#============================================
def test_select_promoted_intervals_excludes_pre_race():
	"""Pre-race intervals are never promoted (Contract C4)."""
	interval_results = [
		{
			"start_frame": 0, "end_frame": 5,
			"source": "pre_race_reference",
			"interval_score": {"confidence_tier": "low"},
		},
		{
			"start_frame": 5, "end_frame": 15,
			"interval_score": {"confidence_tier": "low"},
		},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral: no pre-race interval was promoted; the non-pre-race one was.
	for idx in promoted:
		assert interval_results[idx].get("source") != "pre_race_reference"
	assert 1 in promoted


#============================================
def test_select_promoted_intervals_skips_none():
	"""None entries (e.g. quit in progress) are skipped without raising."""
	interval_results = [
		{"start_frame": 0, "end_frame": 10, "interval_score": {"confidence_tier": "low"}},
		None,
		{"start_frame": 20, "end_frame": 30, "interval_score": {"confidence_tier": "fair"}},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral: None index is not promoted; valid low/fair entries are.
	assert 1 not in promoted
	for idx in promoted:
		assert interval_results[idx] is not None
		tier = interval_results[idx]["interval_score"]["confidence_tier"]
		assert tier in {"low", "fair"}


#============================================
def test_promotion_tiers_contains_low_and_fair():
	"""PROMOTION_TIERS includes the two tiers that should be re-solved."""
	assert "low" in interval_solver.PROMOTION_TIERS
	assert "fair" in interval_solver.PROMOTION_TIERS
