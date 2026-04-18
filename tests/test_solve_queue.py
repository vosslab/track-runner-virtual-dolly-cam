"""Unit tests for solve_queue.plan_interval_work.

Tests the pure-function planner that decides which intervals get
queued. Both solve mode and refine mode consume the returned WorkPlan,
so the partition contract here is load-bearing.

Tests here check behavioral invariants only -- not frozen-dataclass
storage, not specific collection sizes, not hash bytes.
"""

# Standard Library
import dataclasses

# PIP3 modules
import pytest

pytest.importorskip("solve_queue")

# local repo modules
import solve_queue
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
def test_plan_empty_when_fewer_than_two_seeds():
	"""Fewer than 2 seeds -> no intervals, nothing pending.

	Degenerate-case contract. The solver short-circuits on this gate.
	"""
	plan_empty = solve_queue.plan_interval_work([], None)
	plan_one = solve_queue.plan_interval_work([_make_seed(10)], None)
	assert plan_empty.total_intervals == 0
	assert plan_empty.pending_count == 0
	assert plan_one.total_intervals == 0
	assert plan_one.pending_count == 0


#============================================
def test_plan_no_prior_marks_all_pending():
	"""Empty prior cache -> every interval is pending, nothing reused.

	Behavioral property: without a cache, solve mode should queue every
	interval.
	"""
	seeds = [_make_seed(10), _make_seed(100), _make_seed(200)]
	plan = solve_queue.plan_interval_work(seeds, None)
	assert plan.reused_count == 0
	assert plan.pending_pair_indices == list(range(plan.total_intervals))


#============================================
def test_plan_partitions_between_cache_and_pending():
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
	assert 0 in plan.cached_results_by_idx
	assert plan.cached_results_by_idx[0]["dummy"] is True


#============================================
def test_plan_orphan_fingerprint_filtered_from_pruned_prior():
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
def test_plan_is_idempotent():
	"""Same input produces equal plans across calls.

	Pure-function property.
	"""
	seeds = [_make_seed(10), _make_seed(100), _make_seed(200)]
	plan_a = solve_queue.plan_interval_work(seeds, None)
	plan_b = solve_queue.plan_interval_work(seeds, None)
	assert dataclasses.astuple(plan_a) == dataclasses.astuple(plan_b)
