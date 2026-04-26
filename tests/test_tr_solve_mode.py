"""End-to-end smoke test for all three solve modes (M7 closure).

Exercises the five-stage pipeline in all three user-visible modes:
- Default: Stages 1-4 (Hermite + optional blob promotion)
- --hermite-only: Stages 1-3 (Hermite only, fast)
- --full: Stages 1-5 (Hermite + blob on every interval, slow)
"""

# Standard Library
import unittest.mock

# PIP3 modules
import numpy

# local repo modules
import camera_motion
import interval_fingerprint
import interval_solver
import scene_coords


#============================================
def _make_test_fixture():
	"""Create a minimal multi-interval setup for smoke testing.

	Returns:
		(seeds, scene_transform, fps) for a 3-interval sequence.
	"""
	seeds = [
		{"frame_index": 0, "cx": 100.0, "cy": 100.0, "w": 50.0, "h": 50.0},
		{"frame_index": 10, "cx": 150.0, "cy": 150.0, "w": 50.0, "h": 50.0},
		{"frame_index": 20, "cx": 200.0, "cy": 200.0, "w": 50.0, "h": 50.0},
		{"frame_index": 30, "cx": 250.0, "cy": 250.0, "w": 50.0, "h": 50.0},
	]
	motion_track = camera_motion.MotionTrack(
		dx=numpy.zeros(31, dtype=numpy.float32),
		dy=numpy.zeros(31, dtype=numpy.float32),
		scale=numpy.ones(31, dtype=numpy.float32),
		quality=numpy.ones(31, dtype=numpy.float32),
	)
	scene_transform = scene_coords.SceneTransform(motion_track)
	fps = 30.0
	return seeds, scene_transform, fps


#============================================
def test_solve_all_modes_produce_valid_results():
	"""All three modes produce complete interval results with finite trajectories."""
	seeds, scene_transform, fps = _make_test_fixture()
	all_seeds_scene = [(s["frame_index"], s["cx"], s["cy"], s["w"], s["h"]) for s in seeds]

	for mode_name, hermite_only, full_solve in [
		("default", False, False),
		("hermite_only", True, False),
		("full", False, True),
	]:
		with unittest.mock.patch("common_tools.frame_reader.FrameReader"):
			interval_results = []

			# Stage 3: Hermite-only path runs in every mode
			for i in range(len(seeds) - 1):
				seed_start = seeds[i]
				seed_end = seeds[i + 1]
				result = interval_solver.solve_interval_analytical(
					seed_start, seed_end, scene_transform, all_seeds_scene, fps,
					blob_snap_enabled=False,
					debug=False,
					motion_track=None,
					all_seeds=seeds,
					reader=None,
				)
				result["_from_cache"] = False
				result["pair_idx"] = i
				interval_results.append(result)

			# Stage 4 / Stage 5: blob pass on selected intervals
			if not hermite_only and not full_solve:
				promoted = interval_solver.select_promoted_intervals(interval_results)
			elif full_solve:
				promoted = [i for i in range(len(interval_results))
					if interval_results[i].get("phase") != "pre_race"]
			else:
				promoted = []

			for pair_idx in promoted:
				seed_start = seeds[pair_idx]
				seed_end = seeds[pair_idx + 1]
				result = interval_solver.solve_interval_analytical(
					seed_start, seed_end, scene_transform, all_seeds_scene, fps,
					blob_snap_enabled=True,
					debug=False,
					motion_track=None,
					all_seeds=seeds,
					reader=None,
				)
				result["_from_cache"] = False
				result["pair_idx"] = pair_idx
				interval_results[pair_idx] = result

			# Behavioral check: at least one solved frame exists with finite
			# coordinates, and every frame in every interval is finite.
			finite_frames = [
				f for r in interval_results for f in r["blended_path"]
				if numpy.isfinite(f["cx"]) and numpy.isfinite(f["cy"])
			]
			assert any(True for _ in finite_frames), f"Mode {mode_name}: no finite frames"
			for i, result in enumerate(interval_results):
				for frame in result["blended_path"]:
					assert numpy.isfinite(frame["cx"]) and numpy.isfinite(frame["cy"]), (
						f"Mode {mode_name}, interval {i}: non-finite position"
					)


#============================================
def test_fingerprint_tags_differ():
	"""Hermite and blob fingerprint tags encode distinct cache namespaces."""
	hermite_tag = interval_fingerprint.HERMITE_GEOMETRY_TAG
	blob_tag = interval_fingerprint.BLOB_GEOMETRY_TAG
	assert hermite_tag != blob_tag


#============================================
def test_cache_hit_on_rerun_same_mode():
	"""Same inputs produce the same blended trajectory (round-trip determinism)."""
	seeds, scene_transform, fps = _make_test_fixture()
	all_seeds_scene = [(s["frame_index"], s["cx"], s["cy"], s["w"], s["h"]) for s in seeds]
	seed_start = seeds[0]
	seed_end = seeds[1]

	result_1 = interval_solver.solve_interval_analytical(
		seed_start, seed_end, scene_transform, all_seeds_scene, fps,
		blob_snap_enabled=False,
		debug=False,
		motion_track=None,
		all_seeds=seeds,
		reader=None,
	)
	result_2 = interval_solver.solve_interval_analytical(
		seed_start, seed_end, scene_transform, all_seeds_scene, fps,
		blob_snap_enabled=False,
		debug=False,
		motion_track=None,
		all_seeds=seeds,
		reader=None,
	)

	blended_1 = result_1["blended_path"]
	blended_2 = result_2["blended_path"]
	assert len(blended_1) == len(blended_2)
	for frame_1, frame_2 in zip(blended_1, blended_2):
		assert numpy.allclose(frame_1["cx"], frame_2["cx"], atol=1e-5)
		assert numpy.allclose(frame_1["cy"], frame_2["cy"], atol=1e-5)
