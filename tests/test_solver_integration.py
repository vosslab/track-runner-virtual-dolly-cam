"""Integration tests for the analytical solver pipeline.

Tests the full solve path components: camera motion -> scene transform ->
velocity model -> scoring -> diagnostics, using synthetic data.

Bare imports (e.g. import scoring) are resolved via conftest.py which
adds track_runner/ to sys.path.
"""

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import scene_coords
import state_io
import review
import interval_solver


#============================================
def _make_synthetic_motion_track(n_frames: int) -> camera_motion.MotionTrack:
	"""Create a zero-motion track for testing (stationary camera)."""
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float64),
		dy=numpy.zeros(n_frames, dtype=numpy.float64),
		scale=numpy.ones(n_frames, dtype=numpy.float64),
		quality=numpy.ones(n_frames, dtype=numpy.float64),
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
		frame_index = start_frame + i * frame_spacing
		cx = start_x + frame_index * dx_per_frame
		cy = start_y + frame_index * dy_per_frame
		seed = {
			"frame_index": frame_index,
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
	# endpoints should match seeds within 1px
	fused = result["fused_track"]
	assert abs(fused[0]["cx"] - seeds[0]["cx"]) < 1.0
	assert abs(fused[-1]["cx"] - seeds[1]["cx"]) < 1.0


#============================================
def test_diagnostics_v3_write_and_read(tmp_path):
	"""Write v3 diagnostics with analytical scores and read back."""
	diag_path = str(tmp_path / "test.interval_scores.json")
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
	# verify numeric round-trip
	score = loaded["intervals"][0]["interval_score"]
	assert score["confidence_tier"] == "high"
	assert abs(score["agreement"] - 0.75) < 0.01


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
def test_solve_all_intervals_calls_on_interval_solved():
	"""Analytical solve persists each interval through the callback hook."""
	n_frames = 300
	motion = _make_synthetic_motion_track(n_frames)
	scene_transform = scene_coords.SceneTransform(motion)
	seeds = _make_seeds_linear_motion(
		n_seeds=4, start_frame=10, frame_spacing=50,
		start_x=100.0, start_y=200.0,
		dx_per_frame=2.0, dy_per_frame=0.0,
		box_w=30.0, box_h=60.0,
	)

	class _DummyReader:
		def get_info(self) -> dict:
			return {"fps": 30.0}

	captured = []

	def _on_interval_solved(fingerprint: str, result: dict) -> None:
		captured.append((fingerprint, result))

	diagnostics = interval_solver.solve_all_intervals(
		_DummyReader(),
		seeds,
		detector=None,
		config={},
		scene_transform=scene_transform,
		motion_track=motion,
		on_interval_solved=_on_interval_solved,
	)

	# callback was invoked for each interval with matching payload shape
	assert len(captured) == len(diagnostics["intervals"])
	for fingerprint, result in captured:
		assert isinstance(fingerprint, str)
		assert "start_frame" in result


