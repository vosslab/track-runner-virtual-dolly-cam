"""Regression tests for Track Runner contract C6.

C6 requires that refine mode only re-solves intervals whose seed endpoints
changed. Intervals whose bracketing seeds are unchanged must be retained
from the prior solved-result store.

These tests drive `solve_queue.plan_interval_work` directly -- no video
I/O and no solver execution. They assert on the partition between
`plan.solved_results_by_idx` (retained) and `plan.pending_pair_indices`
(re-solve) for the edit / add / remove / no-change / pre-race-edit
scenarios.

Also covers the C7 boundary: a disguised full solve preserves the artifact.
"""

import copy
import json
import os
import pathlib
import unittest.mock

import pytest

import numpy

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import camera_motion
import interval_fingerprint
import interval_solver
import modes.shared as mode_shared
import scene_coords
import solve_queue


#============================================
def _make_seed(frame_index: int, cx: float, cy: float) -> dict:
	# minimal seed dict accepted by filter_usable_seeds_sorted and
	# interval_fingerprint
	seed = {
		"frame_index": frame_index,
		"cx": cx,
		"cy": cy,
		"w": 30.0,
		"h": 60.0,
		"status": "visible",
		"pass": 1,
	}
	return seed


#============================================
def _build_seed_list() -> list:
	# 8 seeds total: 3 pre-race (0, 5, 10) and 5 post-race (50, 70, 90, 110, 130)
	seeds = [
		_make_seed(0, 100.0, 200.0),
		_make_seed(5, 101.0, 201.0),
		_make_seed(10, 102.0, 202.0),
		_make_seed(50, 150.0, 250.0),
		_make_seed(70, 200.0, 280.0),
		_make_seed(90, 260.0, 310.0),
		_make_seed(110, 330.0, 340.0),
		_make_seed(130, 410.0, 370.0),
	]
	return seeds


#============================================
def _prior_cache_for(seeds: list) -> dict:
	# build a fake cache keyed by the current fingerprint of every adjacent
	# seed pair. Values are sentinel dicts so identity is traceable.
	prior = {}
	for i in range(len(seeds) - 1):
		fp = interval_fingerprint.compute_interval_fingerprint(seeds[i], seeds[i + 1])
		prior[fp] = {"cached_from_pair_idx": i, "source": "fwd_bwd"}
	return prior


#============================================
def test_no_change_retains_all_intervals() -> None:
	# unchanged seeds -> every interval is cache-hit, nothing pending.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	plan = solve_queue.plan_interval_work(seeds, prior)
	assert plan.pending_count == 0
	assert plan.reused_count == plan.total_intervals
	assert plan.total_intervals == len(seeds) - 1


#============================================
def test_edit_post_race_seed_only_touches_adjacent_intervals() -> None:
	# moving seed at index 4 (frame 70) changes fingerprints for interval
	# (3,4) and (4,5) only; the other 5 intervals must be retained.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	edited = copy.deepcopy(seeds)
	edited[4]["cx"] = 205.5
	edited[4]["cy"] = 285.5
	plan = solve_queue.plan_interval_work(edited, prior)
	# exactly two intervals must be pending -- the two adjacent to the
	# edited seed
	assert len(plan.pending_pair_indices) == 2
	# every other pair_idx must be a cache hit
	retained = set(plan.solved_results_by_idx.keys())
	assert len(retained) == plan.total_intervals - 2


#============================================
def test_add_post_race_seed_solves_exactly_two_new_intervals() -> None:
	# adding one seed between existing seeds splits one interval into two.
	# both halves are new (no prior fingerprint). The 6 unrelated intervals
	# must be retained.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	added = copy.deepcopy(seeds)
	# insert a new seed between frames 70 and 90 at frame 80
	added.append(_make_seed(80, 230.0, 295.0))
	plan = solve_queue.plan_interval_work(added, prior)
	# total intervals grows by 1
	assert plan.total_intervals == len(seeds)
	# pending count is exactly 2 (the two halves of the split interval)
	assert plan.pending_count == 2
	assert plan.reused_count == plan.total_intervals - 2


#============================================
def test_remove_post_race_seed_solves_exactly_one_new_interval() -> None:
	# removing one seed merges two intervals into one new interval. That
	# new interval has no prior fingerprint. Every other interval must be
	# retained.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	removed = [s for s in seeds if s["frame_index"] != 70]
	plan = solve_queue.plan_interval_work(removed, prior)
	assert plan.total_intervals == len(seeds) - 2
	assert plan.pending_count == 1
	assert plan.reused_count == plan.total_intervals - 1


#============================================
def test_edit_pre_race_seed_only_touches_adjacent_intervals() -> None:
	# moving seed at index 1 (frame 5, pre-race) changes fingerprints for
	# interval (0,1) and (1,2) only; the 5 post-race intervals must stay
	# cached even when a race_start_interval is passed.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	edited = copy.deepcopy(seeds)
	edited[1]["cx"] = 105.5
	edited[1]["cy"] = 206.5
	# simulate Stage 1 having identified the pre-race/race boundary as
	# seed_frame=10 -> seed_frame=50
	plan = solve_queue.plan_interval_work(
		edited, prior, race_start_interval=(10, 50),
	)
	assert len(plan.pending_pair_indices) == 2
	retained = set(plan.solved_results_by_idx.keys())
	assert len(retained) == plan.total_intervals - 2


#============================================
def test_guard_detects_full_solve_fallthrough() -> None:
	# when the prior cache has zero overlap with current fingerprints but
	# intervals exist, refine would be doing a full solve. The guard
	# condition in modes.refine.run fires on this pattern.
	seeds = _build_seed_list()
	# build a prior cache from a totally different seed set so no
	# fingerprint matches
	other_seeds = [_make_seed(1000 + i * 10, 1.0, 1.0) for i in range(4)]
	prior = _prior_cache_for(other_seeds)
	plan = solve_queue.plan_interval_work(seeds, prior)
	# exactly the condition guarded by _mode_refine
	assert plan.total_intervals > 0
	assert plan.reused_count == 0
	assert plan.pending_count == plan.total_intervals


#============================================
def test_prune_drops_orphans_but_keeps_current_entries() -> None:
	# an orphaned fingerprint in prior (not matching any current pair) is
	# dropped from pruned_prior. Current fingerprints must remain.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	orphan_key = "9999|0.00|0.00|0.00|0.00|9998|0.00|0.00|0.00|0.00||orphan-tag"
	prior_with_orphan = dict(prior)
	prior_with_orphan[orphan_key] = {"cached_from_pair_idx": -1}
	plan = solve_queue.plan_interval_work(seeds, prior_with_orphan)
	assert orphan_key not in plan.pruned_prior
	# every current fingerprint survives the prune
	for fp in plan.expected_fingerprints:
		assert fp in plan.pruned_prior


#============================================
def test_zero_interval_plan_when_fewer_than_two_seeds() -> None:
	# fewer than 2 usable seeds -> plan yields total_intervals == 0.
	# a non-empty prior store does NOT influence the plan: the plan
	# simply has no intervals to walk. This is the plan-level condition
	# that triggers the degenerate early-exit branch in _mode_refine.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	# pass only one seed -> plan has no intervals
	one_seed = seeds[:1]
	plan = solve_queue.plan_interval_work(one_seed, prior)
	assert plan.total_intervals == 0
	assert plan.pending_count == 0
	assert plan.reused_count == 0


#============================================
def _minimal_seeds_json(tmp_path: str, seeds: list) -> str:
	"""Write a minimal seeds JSON file and return its path."""
	seeds_path = os.path.join(tmp_path, "seeds.json")
	# track_runner_seeds header key is the v3 canonical format accepted by state_io.load_seeds
	data = {"track_runner_seeds": 3, "seeds": seeds}
	with open(seeds_path, "w") as fh:
		json.dump(data, fh)
	return seeds_path


#============================================
def _minimal_intervals_file(solved_intervals: dict) -> dict:
	"""Return a minimal intervals_file dict with the given solved_intervals."""
	intervals_file = {
		"track_runner_intervals": len(solved_intervals),
		"solve_complete": True,
		"video_identity": {},
		"solved_intervals": solved_intervals,
	}
	return intervals_file


#============================================
def _artifact_intervals_for(seeds: list) -> dict:
	"""Build minimal artifact entries for every adjacent seed pair."""
	solved = {}
	for idx in range(len(seeds) - 1):
		start_seed = seeds[idx]
		end_seed = seeds[idx + 1]
		fingerprint = interval_fingerprint.compute_interval_fingerprint(
			start_seed, end_seed,
		)
		solved[fingerprint] = {
			"start_frame": start_seed["frame_index"],
			"end_frame": end_seed["frame_index"],
			"forward_path": [],
			"backward_path": [],
			"blended_path": [],
		}
	return solved


#============================================
def _seed_records_for_disk(seeds: list) -> list:
	"""Convert in-memory geometry fixtures to canonical on-disk seed records."""
	records = []
	for seed in seeds:
		x = int(seed["cx"] - seed["w"] / 2.0)
		y = int(seed["cy"] - seed["h"] / 2.0)
		records.append({
			"frame_index": seed["frame_index"],
			"torso_box": [x, y, int(seed["w"]), int(seed["h"])],
			"status": seed["status"],
			"pass": seed["pass"],
		})
	return records


#============================================
def _video_context() -> object:
	"""Return the routed-video stub used by refine mode tests."""
	import fastread_video

	selection = fastread_video.VideoSelection(
		path="/fake/video.mkv",
		role="working_decode",
		using_fastread=False,
		reason=fastread_video.REASON_NO_FASTREAD_ORIGINAL,
	)
	context = fastread_video.VideoContext(
		original_video_path="/fake/video.mkv",
		working_decode=selection,
		final_encode=selection,
		metadata_identity=selection,
	)
	return context


#============================================
def _artifact_score_inputs() -> tuple:
	"""Build a small durable artifact score-reconstruction case."""
	seeds = [_make_seed(0, 10.0, 50.0), _make_seed(2, 30.0, 50.0)]
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(100, dtype=numpy.float32),
		dy=numpy.zeros(100, dtype=numpy.float32),
		scale=numpy.ones(100, dtype=numpy.float32),
		quality=numpy.zeros(100, dtype=numpy.float32),
	)
	path = [
		{"cx": 10.0 + 10.0 * idx, "cy": 50.0, "w": 30.0, "h": 60.0}
		for idx in range(3)
	]
	entry = {
		"start_frame": 0, "end_frame": 2,
		"forward_path": None, "backward_path": None,
		"conf": [0.2, 0.4, 0.6], "blended_path": path,
	}
	return (seeds, motion, entry)


#============================================
def test_mode_refine_zero_intervals_prints_diagnostic_and_preserves_store(
	tmp_path: pathlib.Path,
	capsys: pytest.CaptureFixture[str],
) -> None:
	# When the current seeds yield 0 solvable intervals (only 1 usable seed)
	# but the loaded store has 2 prior-solved intervals, _mode_refine must:
	# - print the degenerate-case diagnostic naming the likely cause
	# - return cleanly (no exception)
	# - write NOTHING to disk (store file mtime unchanged, write never called)
	import modes.refine as refine_mode

	# two dummy intervals with known frame ranges
	dummy_fp_a = "sentinel-fp-a"
	dummy_fp_b = "sentinel-fp-b"
	dummy_interval_a = {
		"start_frame": 50,
		"end_frame": 70,
		"forward_path": [],
		"backward_path": [],
		"blended_path": [],
	}
	dummy_interval_b = {
		"start_frame": 70,
		"end_frame": 90,
		"forward_path": [],
		"backward_path": [],
		"blended_path": [],
	}
	prior_solved_intervals = {
		dummy_fp_a: dummy_interval_a,
		dummy_fp_b: dummy_interval_b,
	}
	intervals_file = _minimal_intervals_file(prior_solved_intervals)

	# Diagnostics are intentionally absent. Reuse is owned by the solve artifact.
	diag_path = os.path.join(str(tmp_path), "interval_scores.json")

	# write a seeds file with exactly ONE usable seed (< 2 -> 0 intervals)
	one_usable_seed = {
		"frame_index": 50,
		"torso_box": [100, 200, 30, 60],
		"status": "visible",
		"pass": 1,
	}
	seeds_path = _minimal_seeds_json(str(tmp_path), [one_usable_seed])

	# create a placeholder intervals file on disk (content irrelevant;
	# _mode_refine checks existence then delegates loading to the mock)
	intervals_path = os.path.join(str(tmp_path), "intervals.npz")
	with open(intervals_path, "w") as fh:
		fh.write("placeholder")

	# Patch the artifact I/O:
	# - NPZ loader: return the in-memory dict (no real NPZ needed)
	# - NPZ writer: must never be called by the new early-exit
	with unittest.mock.patch(
		"torso_box_coords_io.load_torso_box_coords",
		return_value=intervals_file,
	) as mock_load:
		with unittest.mock.patch(
			"torso_box_coords_io.write_torso_box_coords",
		) as mock_write:
			refine_mode.run(
				args=None,
				cfg={},
				video_info={},
				seeds_path=seeds_path,
				diag_path=diag_path,
				intervals_path=intervals_path,
				video_context=_video_context(),
				video_identity={"width": 640, "height": 480, "frame_count": 100},
			)

	# the diagnostic must state that no solvable intervals were found and
	# that the existing store is preserved unchanged
	captured = capsys.readouterr()
	assert "no solvable intervals" in captured.out
	assert "preserved unchanged" in captured.out

	# write must never have been called by the new degenerate early-exit path
	mock_write.assert_not_called()
	_ = mock_load  # used; suppress linter warning


#============================================
def test_mode_refine_rejects_full_resolve_without_writing_when_scores_absent(
	tmp_path: pathlib.Path,
) -> None:
	"""A C7 rejection preserves the solve artifact without diagnostics."""
	import modes.refine as refine_mode

	intervals_file = _minimal_intervals_file({
		"unscored": {
			"start_frame": 50,
			"end_frame": 70,
			"forward_path": [],
			"backward_path": [],
			"blended_path": [],
		},
	})
	seeds_path = _minimal_seeds_json(str(tmp_path), [
		{
			"frame_index": 50,
			"torso_box": [100, 200, 30, 60],
			"status": "visible",
			"pass": 1,
		},
		{
			"frame_index": 70,
			"torso_box": [120, 210, 30, 60],
			"status": "visible",
			"pass": 1,
		},
	])
	diag_path = str(tmp_path / "interval_scores.json")
	intervals_path = str(tmp_path / "torso_box_coords.npz")
	pathlib.Path(intervals_path).write_text("placeholder")
	with unittest.mock.patch(
		"torso_box_coords_io.load_torso_box_coords",
		return_value=intervals_file,
	), unittest.mock.patch(
		"torso_box_coords_io.write_torso_box_coords",
	) as mock_write:
		with pytest.raises(RuntimeError, match="full solve"):
			refine_mode.run(
				args=None,
				cfg={},
				video_info={},
				seeds_path=seeds_path,
				diag_path=diag_path,
				intervals_path=intervals_path,
				video_context=_video_context(),
				video_identity={"width": 640, "height": 480, "frame_count": 100},
			)
	mock_write.assert_not_called()


#============================================
def test_cached_artifact_score_participates_in_promotion_without_diagnostics(
	tmp_path: pathlib.Path,
) -> None:
	"""A cached weak interval remains eligible for the normal M6 budget policy."""
	seeds, motion, entry = _artifact_score_inputs()
	fingerprint = interval_fingerprint.compute_interval_fingerprint(
		seeds[0], seeds[1],
	)
	artifact = _minimal_intervals_file({fingerprint: entry})
	transform = scene_coords.SceneTransform(motion)
	with unittest.mock.patch(
		"torso_box_coords_io.load_torso_box_coords", return_value=artifact,
	):
		cached, _callback = mode_shared._load_prior_results(
			str(tmp_path / "torso_box_coords.npz"), {}, seeds, transform,
			motion, 30.0, None,
		)
	promoted = interval_solver.select_promoted_intervals(
		list(cached.values()), seeds, transform, 30.0, 100, 0,
	)
	assert numpy.isclose(cached[fingerprint]["interval_score"]["agreement"], 0.4)
	assert promoted == [0]


#============================================
def test_cached_pre_race_interval_uses_persisted_boundary_only(
	tmp_path: pathlib.Path,
) -> None:
	"""A cached interval before the persisted boundary is excluded as pre-race."""
	seeds, motion, entry = _artifact_score_inputs()
	fingerprint = interval_fingerprint.compute_interval_fingerprint(
		seeds[0], seeds[1],
	)
	artifact = _minimal_intervals_file({fingerprint: entry})
	transform = scene_coords.SceneTransform(motion)
	race_start_reference = {
		"race_start_frame": 1,
		"race_start_interval": [2, 4],
		"torso_w": 30.0, "torso_h": 60.0,
		"scene_anchor_x": 10.0, "scene_anchor_y": 50.0,
		"method": "seed_scene_displacement", "warnings": [],
	}
	with unittest.mock.patch(
		"torso_box_coords_io.load_torso_box_coords", return_value=artifact,
	):
		cached, _callback = mode_shared._load_prior_results(
			str(tmp_path / "torso_box_coords.npz"), {}, seeds, transform,
			motion, 30.0, race_start_reference,
		)
	assert cached[fingerprint]["interval_score"]["confidence_tier"] == "pre_race"


#============================================
def test_cached_interval_without_race_start_remains_post_race(
	tmp_path: pathlib.Path,
) -> None:
	"""An absent artifact block preserves the no-pre-race boundary."""
	seeds, motion, entry = _artifact_score_inputs()
	fingerprint = interval_fingerprint.compute_interval_fingerprint(
		seeds[0], seeds[1],
	)
	artifact = _minimal_intervals_file({fingerprint: entry})
	transform = scene_coords.SceneTransform(motion)
	with unittest.mock.patch(
		"torso_box_coords_io.load_torso_box_coords", return_value=artifact,
	):
		cached, _callback = mode_shared._load_prior_results(
			str(tmp_path / "torso_box_coords.npz"), {}, seeds, transform,
			motion, 30.0, None,
		)
	assert cached[fingerprint]["interval_score"]["confidence_tier"] != "pre_race"
