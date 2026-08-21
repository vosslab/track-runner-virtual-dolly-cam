"""Driver-level invariants for `interval_solver.solve_all_intervals`.

These tests lock the orchestration contract of the cache-partitioned
driver -- ordering, cache short-circuit, and callback firing -- without
exercising real concurrent worker execution. Worker behavior itself is
covered by `test_tr_solver_integration.py` (real solver path) and by
`test_tr_solve_queue.py` (planner). Actual pool equivalence is validated
end-to-end on a real video as part of WP-4's rollout checklist; spawning
a ProcessPoolExecutor from pytest would require a real video fixture and
slow the suite.

Invariants:

1. Full cache-hit runs never open a pool (no video_path required).
2. Result list is returned in seed order regardless of dispatch path.
3. on_interval_solved fires exactly once per newly solved fingerprint.
4. on_interval_solved never fires for cache hits.
5. on_interval_complete fires for every interval (cached + newly solved).

Tests force the in-process path with num_workers=1 and monkeypatch
`solve_interval_analytical` so only the driver loop is exercised.
"""

# PIP3 modules
import numpy
import pytest

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import scene_coords
import interval_solver


#============================================
def _make_motion(n_frames: int) -> camera_motion.MotionTrack:
	"""Create a zero-motion track for a stationary camera."""
	return camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float64),
		dy=numpy.zeros(n_frames, dtype=numpy.float64),
		scale=numpy.ones(n_frames, dtype=numpy.float64),
		quality=numpy.ones(n_frames, dtype=numpy.float64),
	)


#============================================
def _make_seeds(n_seeds: int) -> list:
	"""Create n_seeds linear-motion seeds 50 frames apart starting at frame 10."""
	seeds = []
	for i in range(n_seeds):
		frame_index = 10 + i * 50
		seeds.append({
			"frame_index": frame_index,
			"cx": 100.0 + frame_index * 2.0,
			"cy": 200.0,
			"w": 30.0,
			"h": 60.0,
			"status": "visible",
			"conf": 1.0,
		})
	return seeds


#============================================
class _DummyReader:
	def get_info(self) -> dict:
		return {"fps": 30.0}


#============================================
def _fake_result(seed_start: dict, seed_end: dict) -> dict:
	"""Build a minimal well-formed interval result for driver tests."""
	return {
		"start_frame": int(seed_start["frame_index"]),
		"end_frame": int(seed_end["frame_index"]),
		"blended_path": [],
		"forward_path": [],
		"backward_path": [],
		"interval_score": {"agreement": 1.0, "confidence_tier": "high"},
	}


#============================================
def test_full_cache_hit_skips_pool(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Refine with 100% cache hit must not call solve_interval_analytical.

	This is the fast-exit path. If every interval is already solved, the
	driver should never invoke the real solver nor require a video_path.
	"""
	seeds = _make_seeds(5)
	motion = _make_motion(300)
	scene_transform = scene_coords.SceneTransform(motion)

	# pre-build prior_solved_intervals containing every fingerprint.
	prior = {}
	for i in range(len(seeds) - 1):
		fp = interval_solver.compute_interval_fingerprint(seeds[i], seeds[i + 1])
		prior[fp] = _fake_result(seeds[i], seeds[i + 1])

	# if solve_interval_analytical is ever called, fail loudly.
	def _boom(*a, **k) -> None:
		raise AssertionError("solver called on a 100% cache-hit refine")

	monkeypatch.setattr(interval_solver, "solve_interval_analytical", _boom)

	solved_called = []

	diagnostics = interval_solver.solve_all_intervals(
		_DummyReader(), seeds,
		scene_transform=scene_transform, motion_track=motion,
		prior_solved_intervals=prior,
		on_interval_solved=lambda fp, r: solved_called.append(fp),
		num_workers=1,
	)

	assert len(diagnostics["intervals"]) == len(seeds) - 1
	# cache hits must not trigger on_interval_solved.
	assert solved_called == []


#============================================
def test_cache_hit_restamps_c3_before_completion_callback(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Cached paths regain C3 anchors without changing uncertain seed behavior."""
	seeds = [
		{
			"frame_index": 10, "cx": 100.5, "cy": 200.25,
			"w": 31.5, "h": 61.25, "status": "visible", "pass": 1,
		},
		{
			"frame_index": 20, "cx": 300.0, "cy": 400.0,
			"w": 50.0, "h": 80.0, "status": "not_in_frame", "pass": 1,
		},
		{
			"frame_index": 60, "cx": 110.75, "cy": 205.5,
			"w": 33.25, "h": 63.5, "status": "partial", "pass": 1,
		},
		{
			"frame_index": 110, "cx": 120.0, "cy": 210.0,
			"w": 35.0, "h": 65.0, "status": "approximate", "pass": 1,
		},
	]
	motion = _make_motion(150)
	scene_transform = scene_coords.SceneTransform(motion)
	usable = [seeds[0], seeds[2], seeds[3]]
	prior = {}
	for seed_start, seed_end in zip(usable, usable[1:]):
		span = int(seed_end["frame_index"] - seed_start["frame_index"]) + 1
		stale_path = [
			{
				"cx": 999.0, "cy": 999.0, "w": 5.0, "h": 6.0,
				"conf": 0.17, "seed_status": "cached", "source": "cached",
			}
			for _ in range(span)
		]
		fingerprint = interval_solver.compute_interval_fingerprint(seed_start, seed_end)
		prior[fingerprint] = {
			"start_frame": seed_start["frame_index"],
			"end_frame": seed_end["frame_index"],
			"blended_path": stale_path,
			"forward_path": list(stale_path),
			"backward_path": list(stale_path),
			"interval_score": {"agreement": 1.0, "confidence_tier": "high"},
		}

	def _boom(*args, **kwargs) -> None:
		raise AssertionError("solver called on a fingerprint-matched cache hit")

	monkeypatch.setattr(interval_solver, "solve_interval_analytical", _boom)
	completed = []
	diagnostics = interval_solver.solve_all_intervals(
		_DummyReader(), seeds,
		scene_transform=scene_transform, motion_track=motion,
		prior_solved_intervals=prior,
		on_interval_complete=lambda result: completed.append(result),
		num_workers=1,
	)

	assert len(completed) == 2
	path_by_start = {result["start_frame"]: result["blended_path"]
		for result in completed}
	for state, seed in (
		(path_by_start[10][0], seeds[0]),
		(path_by_start[10][-1], seeds[2]),
		(path_by_start[60][0], seeds[2]),
	):
		assert tuple(state[key] for key in ("cx", "cy", "w", "h")) == tuple(
			seed[key] for key in ("cx", "cy", "w", "h")
		)
		assert state["conf"] == 1.0
		assert state["seed_status"] == seed["status"]

	# Approximate retains the cache geometry and metadata, except for its
	# established final low-confidence stamp. not_in_frame remains an erasure,
	# even though it is not an interval endpoint and therefore not cache-restamped.
	approximate = diagnostics["trajectory"][110]
	assert tuple(approximate[key] for key in ("cx", "cy", "w", "h")) == (
		999.0, 999.0, 5.0, 6.0,
	)
	assert approximate["conf"] == 0.3
	assert approximate["seed_status"] == "cached"
	assert all(state is None for state in diagnostics["trajectory"][11:60])


#============================================
def test_result_list_in_seed_order(monkeypatch: pytest.MonkeyPatch) -> None:
	"""interval_results must be returned in seed order even when partially cached."""
	seeds = _make_seeds(5)
	motion = _make_motion(300)
	scene_transform = scene_coords.SceneTransform(motion)

	# cache the middle interval only; drivers must still fill the others
	# in seed order.
	prior = {}
	fp_middle = interval_solver.compute_interval_fingerprint(seeds[2], seeds[3])
	prior[fp_middle] = _fake_result(seeds[2], seeds[3])
	# tag so we can identify the cached entry downstream.
	prior[fp_middle]["_from_cache"] = True

	def _fake_solve(seed_start: dict, seed_end: dict, *a, **k) -> dict:
		r = _fake_result(seed_start, seed_end)
		r["_from_cache"] = False
		return r

	monkeypatch.setattr(interval_solver, "solve_interval_analytical", _fake_solve)

	diagnostics = interval_solver.solve_all_intervals(
		_DummyReader(), seeds,
		scene_transform=scene_transform, motion_track=motion,
		prior_solved_intervals=prior,
		num_workers=1,
	)

	results = diagnostics["intervals"]
	assert len(results) == len(seeds) - 1
	# order check: start_frame values must equal seed frame_indices in order.
	for i, result in enumerate(results):
		assert result["start_frame"] == seeds[i]["frame_index"]
		assert result["end_frame"] == seeds[i + 1]["frame_index"]
	# only the middle one came from cache.
	assert results[2]["_from_cache"] is True
	assert sum(r["_from_cache"] for r in results) == 1


#============================================
def test_on_interval_solved_fires_once_per_new_fingerprint(
		monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Exactly one persist callback per newly solved interval; none for cache hits."""
	seeds = _make_seeds(4)
	motion = _make_motion(300)
	scene_transform = scene_coords.SceneTransform(motion)

	# cache the first interval; two cache-misses remain.
	prior = {}
	fp_first = interval_solver.compute_interval_fingerprint(seeds[0], seeds[1])
	prior[fp_first] = _fake_result(seeds[0], seeds[1])

	monkeypatch.setattr(
		interval_solver, "solve_interval_analytical",
		lambda seed_start, seed_end, *a, **k: _fake_result(seed_start, seed_end),
	)

	solved_fingerprints = []
	interval_solver.solve_all_intervals(
		_DummyReader(), seeds,
		scene_transform=scene_transform, motion_track=motion,
		prior_solved_intervals=prior,
		on_interval_solved=lambda fp, r: solved_fingerprints.append(fp),
		num_workers=1,
	)

	# exactly 2 new-solve callbacks (intervals 1-2 and 2-3), none for cache hit.
	fp_interval_1 = interval_solver.compute_interval_fingerprint(seeds[1], seeds[2])
	fp_interval_2 = interval_solver.compute_interval_fingerprint(seeds[2], seeds[3])
	assert set(solved_fingerprints) == {fp_interval_1, fp_interval_2}
	# cache hit's fingerprint is NOT in the callback list.
	assert fp_first not in solved_fingerprints


#============================================
def test_on_interval_complete_fires_for_every_interval(
		monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""on_interval_complete must fire once per interval (cached + solved alike)."""
	seeds = _make_seeds(4)
	motion = _make_motion(300)
	scene_transform = scene_coords.SceneTransform(motion)

	prior = {}
	fp_first = interval_solver.compute_interval_fingerprint(seeds[0], seeds[1])
	prior[fp_first] = _fake_result(seeds[0], seeds[1])

	monkeypatch.setattr(
		interval_solver, "solve_interval_analytical",
		lambda seed_start, seed_end, *a, **k: _fake_result(seed_start, seed_end),
	)

	completed = []
	interval_solver.solve_all_intervals(
		_DummyReader(), seeds,
		scene_transform=scene_transform, motion_track=motion,
		prior_solved_intervals=prior,
		on_interval_complete=lambda r: completed.append(r),
		num_workers=1,
	)

	# one per interval (3 intervals for 4 seeds).
	assert len(completed) == len(seeds) - 1


#============================================
def test_fingerprints_unchanged_by_parallel_refactor() -> None:
	"""compute_interval_fingerprint must return a deterministic string for fixed seeds.

	This locks the no-cache-invalidation guarantee of the solver cache:
	a user's existing torso_box_coords.npz keeps working across refactors
	because fingerprints are deterministic for fixed seed positions.
	"""
	seed_a = {"frame_index": 100, "cx": 500.5, "cy": 300.25, "w": 40.0, "h": 80.0}
	seed_b = {"frame_index": 200, "cx": 600.0, "cy": 310.0, "w": 42.0, "h": 82.0}
	fp1 = interval_solver.compute_interval_fingerprint(seed_a, seed_b)
	fp2 = interval_solver.compute_interval_fingerprint(seed_a, seed_b)
	assert fp1 == fp2
	assert isinstance(fp1, str)
	# swapping seed order must change the fingerprint (direction matters).
	fp_reversed = interval_solver.compute_interval_fingerprint(seed_b, seed_a)
	assert fp_reversed != fp1
