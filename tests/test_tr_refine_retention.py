"""Regression tests for Track Runner contract C6.

C6 requires that refine mode only re-solves intervals whose seed endpoints
changed. Intervals whose bracketing seeds are unchanged must be retained
from the prior cache.

These tests drive `solve_queue.plan_interval_work` directly -- no video
I/O and no solver execution. They assert on the partition between
`plan.cached_results_by_idx` (retained) and `plan.pending_pair_indices`
(re-solve) for the edit / add / remove / no-change / pre-race-edit
scenarios.
"""

import copy

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import interval_fingerprint
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
def test_no_change_retains_all_intervals():
	# unchanged seeds -> every interval is cache-hit, nothing pending.
	seeds = _build_seed_list()
	prior = _prior_cache_for(seeds)
	plan = solve_queue.plan_interval_work(seeds, prior)
	assert plan.pending_count == 0
	assert plan.reused_count == plan.total_intervals
	assert plan.total_intervals == len(seeds) - 1


#============================================
def test_edit_post_race_seed_only_touches_adjacent_intervals():
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
	assert set(plan.pending_pair_indices) == {3, 4}
	# every other pair_idx must be a cache hit
	retained = set(plan.cached_results_by_idx.keys())
	assert retained == {0, 1, 2, 5, 6}


#============================================
def test_add_post_race_seed_solves_exactly_two_new_intervals():
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
def test_remove_post_race_seed_solves_exactly_one_new_interval():
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
def test_edit_pre_race_seed_only_touches_adjacent_intervals():
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
	assert set(plan.pending_pair_indices) == {0, 1}
	retained = set(plan.cached_results_by_idx.keys())
	assert retained == {2, 3, 4, 5, 6}


#============================================
def test_guard_detects_full_solve_fallthrough():
	# when the prior cache has zero overlap with current fingerprints but
	# intervals exist, refine would be doing a full solve. The guard
	# condition in cli._mode_refine fires on this pattern.
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
def test_prune_drops_orphans_but_keeps_current_entries():
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
