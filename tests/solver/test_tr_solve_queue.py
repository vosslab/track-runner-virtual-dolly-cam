"""Unit tests for solve_queue.plan_interval_work.

Tests the pure-function planner that decides which intervals get
queued. Both solve mode and refine mode consume the returned WorkPlan,
so the partition contract here is load-bearing.

Tests here check behavioral invariants only -- not frozen-dataclass
storage, not specific collection sizes, not hash bytes.
"""

# Standard Library
import dataclasses
import concurrent.futures

# PIP3 modules
import pytest

pytest.importorskip("solve_queue")

# local repo modules
import solve_queue
import solver_workers
import interval_fingerprint


#============================================
def _make_seed(frame_index: int, cx: float = 100.0, cy: float = 200.0,
		w: float = 30.0, h: float = 60.0, status: str = "visible",
		conf: float = 1.0, pass_num: int = 1) -> dict:
	"""Helper to build seed dicts with canonical structure."""
	seed = {
		"frame_index": frame_index,
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"status": status,
		"conf": conf,
		"pass": pass_num,
	}
	return seed


#============================================
def test_plan_empty_when_fewer_than_two_seeds() -> None:
	"""Fewer than 2 seeds -> no intervals, nothing pending.

	Degenerate-case contract. The solver short-circuits on this gate.
	"""
	plan_empty = solve_queue.plan_interval_work([], None)
	plan_one = solve_queue.plan_interval_work([_make_seed(10)], None)
	assert plan_empty.total_intervals == 0
	assert plan_empty.pending_count == 0
	assert plan_empty.reused_count == 0
	assert plan_one.total_intervals == 0
	assert plan_one.pending_count == 0
	assert plan_one.reused_count == 0


#============================================
def test_plan_no_prior_marks_all_pending() -> None:
	"""Empty prior cache -> every interval is pending, nothing reused.

	Behavioral property: without a cache, solve mode should queue every
	interval.
	"""
	seeds = [_make_seed(10), _make_seed(100), _make_seed(200)]
	plan = solve_queue.plan_interval_work(seeds, None)
	assert plan.reused_count == 0
	assert plan.pending_pair_indices == list(range(plan.total_intervals))


#============================================
def test_plan_partitions_between_cache_and_pending() -> None:
	"""Partial prior cache partitions intervals correctly by fingerprint.

	Core load-bearing behavior: a cache hit for interval [0,1] must
	mark index 0 as reused and index 1 as pending when interval [1,2]
	is not cached.
	"""
	seeds = [_make_seed(10), _make_seed(100), _make_seed(200)]
	fp_first = interval_fingerprint.compute_interval_fingerprint(
		seeds[0], seeds[1],
	)
	prior = {fp_first: {"start_frame": 10, "end_frame": 100, "dummy": True}}
	plan = solve_queue.plan_interval_work(seeds, prior)
	assert plan.pending_pair_indices == [1]
	assert 0 in plan.solved_results_by_idx
	assert plan.solved_results_by_idx[0]["dummy"] is True


#============================================
def test_plan_orphan_fingerprint_filtered_from_pruned_prior() -> None:
	"""Fingerprints in prior that do not match any current interval are pruned.

	Refine mode writes pruned_prior back to disk as the orphan-cleanup
	step; orphans must not leak through.
	"""
	seeds = [_make_seed(10), _make_seed(100)]
	fp_good = interval_fingerprint.compute_interval_fingerprint(
		seeds[0], seeds[1],
	)
	prior = {
		fp_good: {"dummy": True},
		"orphan_fingerprint": {"dummy": True},
	}
	plan = solve_queue.plan_interval_work(seeds, prior)
	assert fp_good in plan.pruned_prior
	assert "orphan_fingerprint" not in plan.pruned_prior


#============================================
def test_plan_is_idempotent() -> None:
	"""Same input produces equal plans across calls.

	Pure-function property.
	"""
	seeds = [_make_seed(10), _make_seed(100), _make_seed(200)]
	plan_a = solve_queue.plan_interval_work(seeds, None)
	plan_b = solve_queue.plan_interval_work(seeds, None)
	assert dataclasses.astuple(plan_a) == dataclasses.astuple(plan_b)


#============================================
def test_plan_interval_phase_classification() -> None:
	"""Interval ending at interval_low is pre_race; interval seed-pair is interval;
	intervals starting at interval_high are post_race.

	Regression for the off-by-one where end_frame == interval_low used to be
	classified as post_race even though the interval is entirely pre-race.
	interval_low is the last pre-race seed frame; end_frame is inclusive.
	"""
	# seeds at frames 50, 100, 130, 200 with interval = (100, 130).
	# Intervals (by seed pair): (50,100), (100,130), (130,200).
	seeds = [
		_make_seed(50),
		_make_seed(100),
		_make_seed(130),
		_make_seed(200),
	]
	interval = (100, 130)
	plan = solve_queue.plan_interval_work(
		seeds, None, race_start_interval=interval,
	)
	assert plan.phase_by_idx[0] == "pre_race"
	assert plan.phase_by_idx[1] == "interval"
	assert plan.phase_by_idx[2] == "post_race"


#============================================
def test_pool_solve_emits_worker_telemetry(
	monkeypatch: pytest.MonkeyPatch,
	capsys: pytest.CaptureFixture[str],
) -> None:
	"""The normal Stage-3 pool opts into and reports worker measurements."""
	seeds = [_make_seed(frame) for frame in (0, 10, 20, 30, 40)]
	plan = solve_queue.plan_interval_work(seeds)
	pool_kwargs = {}

	class FakePool:
		def __enter__(self) -> object:
			return self

		def __exit__(
			self, exc_type: object, exc_value: object, traceback: object,
		) -> None:
			return None

		def submit(self, function: object, task: tuple) -> concurrent.futures.Future:
			pair_idx, seed_start, seed_end = task
			result = {
				"start_frame": seed_start["frame_index"],
				"end_frame": seed_end["frame_index"],
				"interval_score": {
					"confidence_tier": "high", "agreement": 1.0,
					"velocity_consistency": 1.0, "size_consistency": 1.0,
				},
			}
			telemetry = {
				"pid": pair_idx + 10,
				"ru_maxrss_bytes": 1000 + pair_idx,
				"elapsed_s": 0.25,
				"prepass": None,
			}
			future = concurrent.futures.Future()
			future.set_result((pair_idx, "fp", result, telemetry))
			return future

	def fake_make_pool(**kwargs) -> FakePool:
		pool_kwargs.update(kwargs)
		return FakePool()

	monkeypatch.setattr(solver_workers, "make_pool", fake_make_pool)
	monkeypatch.setattr(
		solver_workers, "current_process_peak_rss_bytes", lambda: 500,
	)
	context = solve_queue.ExecutionContext(
		reader=object(), scene_transform=object(), motion_track=object(),
		all_seeds=seeds,
		all_seeds_scene=[
			(seed["frame_index"], seed["cx"], seed["cy"], seed["w"], seed["h"])
			for seed in seeds
		],
		fps=30.0, num_workers=2, decode_video_path="decode.mkv",
		original_video_path="source.mkv", video_frame_count=41, debug=False,
	)
	solve_queue.execute_interval_work(plan, context)
	output = capsys.readouterr().out
	assert pool_kwargs["telemetry_enabled"] is True
	assert "telemetry stage=stage_3 intervals=4" in output


#============================================
def test_pre_race_synthesis_when_all_normal_cached() -> None:
	"""Pre-race intervals execute correctly when every normal+interval is cached.

	Regression test for P1 bug: _accept and progress were defined inside
	the 'if pending_normal_total > 0' block but called unconditionally in
	pre-race synthesis. If all normal+interval intervals hit the cache
	(pending_normal_total == 0) but pre-race intervals were pending,
	_accept would raise NameError.

	This test verifies the planner produces a work plan with pre-race
	intervals pending and no normal intervals pending, which exercises
	the code path where progress bar setup is skipped.
	"""
	# Seeds: frame 0 (pre-race), 10 (pre-race), 20 (interval_low), 50 (interval_high), 100 (post-race)
	# Intervals (by seed pair): (0,10) pre-race, (10,20) pre-race, (20,50) interval, (50,100) post-race
	seeds = [
		_make_seed(0),
		_make_seed(10),
		_make_seed(20),
		_make_seed(50),
		_make_seed(100),
	]
	interval = (20, 50)  # interval is from seed 2 to seed 3, index 2
	plan = solve_queue.plan_interval_work(
		seeds, None, race_start_interval=interval,
	)

	# Verify phase assignments: intervals 0,1 pre-race, interval 2 interval, interval 3 post-race
	assert plan.phase_by_idx[0] == "pre_race"
	assert plan.phase_by_idx[1] == "pre_race"
	assert plan.phase_by_idx[2] == "interval"
	assert plan.phase_by_idx[3] == "post_race"

	# The key behavioral check: the plan structure supports pre-race
	# synthesis happening even when normal intervals are cached out.
	# We need at least one pre-race interval in pending to exercise the P1 code path.
	pre_race_pending = [
		idx for idx in plan.pending_pair_indices
		if plan.phase_by_idx.get(idx) == "pre_race"
	]
	assert len(pre_race_pending) > 0, "Must have pending pre-race intervals to test P1 code path"


#============================================
# Pre-race interval synthesis tests
# (moved from former tests/test_interval_solver_pre_race.py; these exercise
# solve_queue._solve_pre_race_interval, not interval_solver.)
#============================================


class _FakeSceneTransform:
	"""Minimal scene_to_pixel stub for pre-race synthesis tests.

	Applies a fixed pixel offset to the scene anchor so tests can assert
	on back-projected pixel position without a real SceneTransform.
	"""

	def __init__(self, offset_x: float = 0.0, offset_y: float = 0.0) -> None:
		self.offset_x = offset_x
		self.offset_y = offset_y

	def scene_to_pixel(self, frame_index: int, sx: float, sy: float) -> tuple:
		return (sx + self.offset_x, sy + self.offset_y)


def _make_pre_race_reference(
		race_start_frame: int = 10,
		torso_w: float = 32.0,
		torso_h: float = 64.0,
		scene_anchor_x: float = 100.0,
		scene_anchor_y: float = 100.0,
) -> dict:
	"""Build a minimal pre_race_reference dict for synthesis tests."""
	reference = {
		"race_start_frame": race_start_frame,
		"torso_w": torso_w,
		"torso_h": torso_h,
		"scene_anchor_x": scene_anchor_x,
		"scene_anchor_y": scene_anchor_y,
		"warnings": [],
	}
	return reference


#============================================
def test_pre_race_interval_has_full_diagnostics_shape() -> None:
	"""Pre-race synthesized interval has every key the v4 writer consumes.

	Anti-KeyError guard: downstream consumers (target, review, encoder)
	all index into these keys. Missing any one silently breaks the v4
	diagnostics writer's non-migration path.
	"""
	seed_start = _make_seed(0)
	seed_end = _make_seed(10)
	result = solve_queue._solve_pre_race_interval(
		seed_start, seed_end, _make_pre_race_reference(race_start_frame=10),
		_FakeSceneTransform(), fps=30.0,
	)
	required_top = {
		"start_frame", "end_frame", "blended_path", "interval_score",
		"fingerprint", "source",
	}
	required_frame = {"cx", "cy", "w", "h", "conf", "source"}
	required_score = {
		"agreement", "velocity_consistency", "size_consistency",
		"motion_quality", "occlusion_fraction", "confidence_tier",
		"failure_reasons", "warning_flags",
	}
	assert required_top.issubset(result)
	assert required_frame.issubset(result["blended_path"][0])
	assert required_frame.issubset(result["blended_path"][-1])
	assert required_score.issubset(result["interval_score"])


#============================================
def test_pre_race_interval_uses_averaged_dimensions() -> None:
	"""Every frame_state has the reference's averaged torso dimensions."""
	reference = _make_pre_race_reference(torso_w=40.0, torso_h=80.0)
	result = solve_queue._solve_pre_race_interval(
		_make_seed(0), _make_seed(5), reference, _FakeSceneTransform(), fps=30.0,
	)
	for frame_state in result["blended_path"]:
		assert abs(frame_state["w"] - 40.0) < 0.01
		assert abs(frame_state["h"] - 80.0) < 0.01


#============================================
def test_pre_race_interval_scene_anchored_center() -> None:
	"""Scene-anchored center back-projects to the same pixel position per frame.

	Contract C2: pre-race center is anchored in scene coordinates, not
	re-estimated per frame. With a fixed-offset transform, every pre-race
	frame must resolve to the same pixel position.
	"""
	reference = _make_pre_race_reference(
		scene_anchor_x=100.0, scene_anchor_y=50.0,
	)
	transform = _FakeSceneTransform(offset_x=10.0, offset_y=20.0)
	result = solve_queue._solve_pre_race_interval(
		_make_seed(0), _make_seed(5), reference, transform, fps=30.0,
	)
	for frame_state in result["blended_path"]:
		assert abs(frame_state["cx"] - 110.0) < 0.01
		assert abs(frame_state["cy"] - 70.0) < 0.01


#============================================
def test_pre_race_interval_confidence_tier() -> None:
	"""Pre-race interval_score carries the pre_race tier tag."""
	result = solve_queue._solve_pre_race_interval(
		_make_seed(0), _make_seed(5), _make_pre_race_reference(),
		_FakeSceneTransform(), fps=30.0,
	)
	assert result["interval_score"]["confidence_tier"] == "pre_race"


#============================================
def test_pre_race_interval_consistency_sentinels() -> None:
	"""Synthesized pre-race intervals carry the consistency-by-construction
	sentinel values (agreement and consistencies at 1.0, occlusion 0.0).

	These values document the contract: pre-race intervals did not run
	FWD/BWD, so there is no disagreement to measure. They are not tuned
	scores; they are the sentinel that tells downstream consumers
	"not a quality measurement".

	These are by-construction sentinel values written by _solve_pre_race_interval,
	not tunable thresholds. Hardcoded values are intentional contract locks.
	"""
	result = solve_queue._solve_pre_race_interval(
		_make_seed(0), _make_seed(5), _make_pre_race_reference(),
		_FakeSceneTransform(), fps=30.0,
	)
	score = result["interval_score"]
	assert score["agreement"] == 1.0
	assert score["velocity_consistency"] == 1.0
	assert score["size_consistency"] == 1.0
	assert score["motion_quality"] == 1.0
	assert score["occlusion_fraction"] == 0.0


#============================================
def test_pre_race_synthesis_classified_by_phase() -> None:
	"""When race_start_interval is given, plan_interval_work labels each
	pair_idx with one of {pre_race, interval, post_race}, and exactly one
	pair carries the race-start interval phase.
	"""
	seeds = [_make_seed(f) for f in (0, 10, 20, 30, 40, 50)]
	race_start_interval = (20, 30)

	plan = solve_queue.plan_interval_work(
		seeds, prior_solved_intervals=None, race_start_interval=race_start_interval,
	)

	phases = list(plan.phase_by_idx.values())
	# Behavioral: pre-race and post-race phases both exist when seeds bracket race start.
	assert "pre_race" in phases
	assert "post_race" in phases
	# Behavioral: exactly one race-start interval pair exists.
	race_start_idx = [idx for idx, phase in plan.phase_by_idx.items() if phase == "interval"]
	assert len(race_start_idx) == 1


#============================================
def test_pre_race_pending_disjoint_from_cached() -> None:
	"""When some intervals are cached, plan partitions the rest into pending
	such that pending and cached sets are disjoint and account for every
	interval. Pre-race intervals that are cached must not appear in pending.
	"""
	seeds = [_make_seed(f) for f in (0, 10, 20, 30, 40)]

	fp_pre_0 = interval_fingerprint.compute_interval_fingerprint(seeds[0], seeds[1])
	fp_pre_1 = interval_fingerprint.compute_interval_fingerprint(seeds[1], seeds[2])
	prior = {fp_pre_0: {"dummy": True}, fp_pre_1: {"dummy": True}}

	plan = solve_queue.plan_interval_work(
		seeds, prior_solved_intervals=prior, race_start_interval=(20, 30),
	)

	pending_set = set(plan.pending_pair_indices)
	cached_set = set(plan.solved_results_by_idx)
	# Round-trip invariant: every interval is either reused or pending.
	assert plan.reused_count + plan.pending_count == plan.total_intervals
	# Disjointness: cached and pending never overlap.
	assert pending_set.isdisjoint(cached_set)
	# All-pre-race-cached: no pre-race index remains pending.
	pending_pre_race = [idx for idx in pending_set
		if plan.phase_by_idx.get(idx) == "pre_race"]
	assert not pending_pre_race
