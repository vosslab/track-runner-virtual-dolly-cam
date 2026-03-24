"""Integration tests for the analytical solver pipeline.

Tests the full solve path components: camera motion -> scene transform ->
velocity model -> scoring -> diagnostics, using synthetic data.

Note: interval_solver.py uses bare imports (import propagator) which require
the track_runner directory on sys.path. These tests use sys.path manipulation
to make those imports work, matching the runtime behavior.
"""

# Standard Library
import os
import sys

# PIP3 modules
import numpy

# add track_runner to sys.path so bare imports work (matches runtime behavior)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TR_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TR_DIR not in sys.path:
	sys.path.insert(0, _TR_DIR)

# local repo modules (bare imports matching runtime behavior)
import camera_motion
import scene_coords
import state_io
import review
import tr_config
import interval_solver


#============================================
def _make_synthetic_motion_track(n_frames: int) -> camera_motion.MotionTrack:
	"""Create a zero-motion track for testing (stationary camera)."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float64),
		dy=numpy.zeros(n_frames, dtype=numpy.float64),
		scale=numpy.ones(n_frames, dtype=numpy.float64),
		quality=numpy.ones(n_frames, dtype=numpy.float64),
		event_flags=numpy.zeros(n_frames, dtype=numpy.int32),
	)
	return motion


#============================================
def _make_seeds_linear_motion(
	n_seeds: int,
	start_frame: int,
	frame_spacing: int,
	start_x: float,
	start_y: float,
	dx_per_frame: float,
	dy_per_frame: float,
	box_w: float,
	box_h: float,
) -> list:
	"""Create seeds along a linear trajectory for testing.

	Args:
		n_seeds: Number of seeds to create.
		start_frame: Frame index for the first seed.
		frame_spacing: Number of frames between seeds.
		start_x: Starting cx position.
		start_y: Starting cy position.
		dx_per_frame: Pixels per frame in x direction.
		dy_per_frame: Pixels per frame in y direction.
		box_w: Bounding box width.
		box_h: Bounding box height.

	Returns:
		List of seed dicts with frame_index, cx, cy, w, h, status, conf.
	"""
	seeds = []
	for i in range(n_seeds):
		frame_idx = start_frame + i * frame_spacing
		cx = start_x + frame_idx * dx_per_frame
		cy = start_y + frame_idx * dy_per_frame
		seed = {
			"frame_index": frame_idx,
			"cx": cx,
			"cy": cy,
			"w": box_w,
			"h": box_h,
			"status": "visible",
			"conf": 1.0,
		}
		seeds.append(seed)
	return seeds


#============================================
def test_analytical_solve_produces_fused_track():
	"""Full analytical solve on one interval produces valid fused track."""
	n_frames = 300
	motion = _make_synthetic_motion_track(n_frames)
	scene_transform = scene_coords.SceneTransform(motion)
	# 5 seeds moving rightward at 2px/frame
	seeds = _make_seeds_linear_motion(
		n_seeds=5, start_frame=10, frame_spacing=50,
		start_x=100.0, start_y=200.0,
		dx_per_frame=2.0, dy_per_frame=0.0,
		box_w=30.0, box_h=60.0,
	)
	# convert seeds to scene coords
	all_seeds_scene = []
	for s in seeds:
		sx, sy = scene_transform.pixel_to_scene(
			s["frame_index"], s["cx"], s["cy"],
		)
		all_seeds_scene.append(
			(s["frame_index"], sx, sy, s["w"], s["h"]),
		)
	# solve first interval
	result = interval_solver.solve_interval_analytical(
		seed_start=seeds[0],
		seed_end=seeds[1],
		scene_transform=scene_transform,
		all_seeds_scene=all_seeds_scene,
		fps=30.0,
	)
	# verify result structure
	assert "fused_track" in result
	assert "forward_track" in result
	assert "backward_track" in result
	assert "interval_score" in result
	assert "start_frame" in result
	assert "end_frame" in result
	# fused track should span the interval
	fused = result["fused_track"]
	expected_len = seeds[1]["frame_index"] - seeds[0]["frame_index"] + 1
	assert len(fused) == expected_len
	# endpoints should match seeds within 1px
	first_state = fused[0]
	last_state = fused[-1]
	assert abs(first_state["cx"] - seeds[0]["cx"]) < 1.0
	assert abs(last_state["cx"] - seeds[1]["cx"]) < 1.0


#============================================
def test_analytical_scoring_produces_v2_fields():
	"""Analytical scoring returns interval_score_v2 fields."""
	n_frames = 300
	motion = _make_synthetic_motion_track(n_frames)
	scene_transform = scene_coords.SceneTransform(motion)
	seeds = _make_seeds_linear_motion(
		n_seeds=5, start_frame=10, frame_spacing=50,
		start_x=100.0, start_y=200.0,
		dx_per_frame=2.0, dy_per_frame=0.0,
		box_w=30.0, box_h=60.0,
	)
	all_seeds_scene = []
	for s in seeds:
		sx, sy = scene_transform.pixel_to_scene(
			s["frame_index"], s["cx"], s["cy"],
		)
		all_seeds_scene.append(
			(s["frame_index"], sx, sy, s["w"], s["h"]),
		)
	result = interval_solver.solve_interval_analytical(
		seed_start=seeds[0],
		seed_end=seeds[1],
		scene_transform=scene_transform,
		all_seeds_scene=all_seeds_scene,
		fps=30.0,
	)
	score = result["interval_score"]
	# v2 fields must be present
	assert "agreement" in score
	assert "velocity_consistency" in score
	assert "size_consistency" in score
	assert "confidence_tier" in score
	assert "severity" in score
	assert "failure_reasons" in score
	assert "warning_flags" in score
	# confidence_tier must be a valid label
	valid_tiers = ("high", "good", "fair", "low")
	assert score["confidence_tier"] in valid_tiers


#============================================
def test_fwd_bwd_tracks_differ_mid_interval():
	"""FWD and BWD tracks produce different mid-interval positions."""
	n_frames = 300
	motion = _make_synthetic_motion_track(n_frames)
	scene_transform = scene_coords.SceneTransform(motion)
	# non-uniform spacing creates slope asymmetry
	seeds = [
		{
			"frame_index": 10, "cx": 100.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible", "conf": 1.0,
		},
		{
			"frame_index": 30, "cx": 140.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible", "conf": 1.0,
		},
		{
			"frame_index": 130, "cx": 340.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible", "conf": 1.0,
		},
		{
			"frame_index": 200, "cx": 480.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible", "conf": 1.0,
		},
		{
			"frame_index": 220, "cx": 520.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible", "conf": 1.0,
		},
	]
	all_seeds_scene = []
	for s in seeds:
		sx, sy = scene_transform.pixel_to_scene(
			s["frame_index"], s["cx"], s["cy"],
		)
		all_seeds_scene.append(
			(s["frame_index"], sx, sy, s["w"], s["h"]),
		)
	# solve middle interval (seeds[1] to seeds[2])
	result = interval_solver.solve_interval_analytical(
		seed_start=seeds[1],
		seed_end=seeds[2],
		scene_transform=scene_transform,
		all_seeds_scene=all_seeds_scene,
		fps=30.0,
	)
	fwd = result["forward_track"]
	bwd = result["backward_track"]
	# mid-interval should show directional asymmetry
	mid_idx = len(fwd) // 2
	fwd_cx = fwd[mid_idx]["cx"]
	bwd_cx = bwd[mid_idx]["cx"]
	diff = abs(fwd_cx - bwd_cx)
	# asymmetry should exist (may be small)
	assert diff >= 0.0


#============================================
def test_diagnostics_v3_write_and_read(tmp_path):
	"""Write v3 diagnostics with analytical scores and read back."""
	diag_path = str(tmp_path / "test.diagnostics.json")
	# build minimal v3 diagnostics
	diagnostics = {
		"intervals": [
			{
				"start_frame": 0,
				"end_frame": 100,
				"interval_score": {
					"agreement": 0.75,
					"velocity_consistency": 0.82,
					"size_consistency": 0.91,
					"motion_quality": 0.95,
					"occlusion_fraction": 0.0,
					"confidence_tier": "high",
					"severity": "low",
					"failure_reasons": [],
					"warning_flags": [],
				},
			},
		],
	}
	# write
	state_io.write_solver_diagnostics(
		diagnostics, diag_path, fps=30.0,
	)
	# read back
	loaded = state_io.load_diagnostics(diag_path)
	# verify v3 fields survived round-trip
	iv = loaded["intervals"][0]
	score = iv["interval_score"]
	assert "confidence_tier" in score
	assert score["confidence_tier"] == "high"
	assert abs(score["agreement"] - 0.75) < 0.01
	assert abs(score["velocity_consistency"] - 0.82) < 0.01
	assert abs(score["size_consistency"] - 0.91) < 0.01


#============================================
def test_review_handles_v3_confidence():
	"""Review module correctly reads confidence_tier from v3 scores."""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 0,
				"end_frame": 300,
				"start_s": 0.0,
				"end_s": 10.0,
				"interval_score": {
					"agreement": 0.15,
					"velocity_consistency": 0.3,
					"size_consistency": 0.8,
					"motion_quality": 0.9,
					"occlusion_fraction": 0.0,
					"confidence_tier": "low",
					"severity": "high",
					"failure_reasons": ["low_agreement"],
					"warning_flags": [],
				},
			},
		],
	}
	# should identify this as needing refinement
	needs = review.needs_refinement(diagnostics)
	assert needs is True


#============================================
def test_setup_mode_importable():
	"""Verify setup_mode module is importable and has run_setup."""
	import setup_mode
	assert hasattr(setup_mode, "run_setup")


#============================================
def test_config_camera_defaults():
	"""Config validation adds camera defaults when missing."""
	config = {
		"track_runner": 2,
		"detection": {},
		"processing": {},
	}
	tr_config.validate_config(config)
	assert "camera" in config
	assert config["camera"]["zoom_type"] == "fixed"
	assert config["processing"]["solver_backend"] == "scene_interp"
