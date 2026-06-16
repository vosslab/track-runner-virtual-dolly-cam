"""Reuse proof on a synthetic legacy-store fixture.

Proves that a store whose keys carry a trailing `/bin<B>` segment fully
reuses after `migrate_legacy_fingerprints` strips that segment.

The synthetic store has three intervals keyed with the legacy bin suffix.
After migration, `plan_interval_work` finds every interval in the migrated
store and reports full reuse -- no external video needed.

This is pure dict/fingerprint logic, runs in well under one second, and
needs no video files or network access.
"""

# local repo modules
import interval_fingerprint
import solve_queue


#============================================
# Three minimal seed dicts representing four named frames.
# These are SOURCE-frame seeds (cx, cy, w, h all in source pixels).
# status, pass, conf are required by filter_usable_seeds_sorted.
_SEEDS = [
	{
		"frame_index": 10,
		"cx": 800.0, "cy": 600.0, "w": 55.0, "h": 70.0,
		"status": "visible", "pass": 1, "conf": 1.0,
	},
	{
		"frame_index": 40,
		"cx": 820.0, "cy": 605.0, "w": 56.0, "h": 71.0,
		"status": "visible", "pass": 1, "conf": 1.0,
	},
	{
		"frame_index": 90,
		"cx": 845.0, "cy": 610.0, "w": 57.0, "h": 72.0,
		"status": "visible", "pass": 1, "conf": 1.0,
	},
	{
		"frame_index": 150,
		"cx": 870.0, "cy": 615.0, "w": 58.0, "h": 73.0,
		"status": "visible", "pass": 1, "conf": 1.0,
	},
]


#============================================
def _build_legacy_store(bin_suffix: str) -> dict:
	"""Build a synthetic solved-results dict keyed with a trailing bin suffix.

	Computes the current bin-invariant fingerprint for each adjacent seed
	pair, then appends the legacy `/bin<B>` segment so the key shape matches
	stores written before the bin-invariant migration.  Each entry holds a minimal solved-result payload
	(start_frame / end_frame) sufficient for plan_interval_work.

	Args:
		bin_suffix: e.g. "/bin4" or "/bin3".

	Returns:
		Dict mapping legacy fingerprint -> solved-result payload.
	"""
	store = {}
	for i in range(len(_SEEDS) - 1):
		seed_start = _SEEDS[i]
		seed_end = _SEEDS[i + 1]
		# compute the current bin-invariant key first
		fresh_key = interval_fingerprint.compute_interval_fingerprint(
			seed_start, seed_end,
		)
		# append the legacy bin segment to simulate a pre-migration store
		legacy_key = fresh_key + bin_suffix
		store[legacy_key] = {
			"start_frame": int(seed_start["frame_index"]),
			"end_frame": int(seed_end["frame_index"]),
		}
	return store


#============================================
def test_legacy_store_fully_reuses_after_migration_bin4():
	"""A store keyed at bin4 fully reuses after migration, no intervals pending.

	Migrated synthetic store yields reused_count == total_intervals,
	pending_count == 0 at bin 4.
	"""
	legacy_store = _build_legacy_store("/bin4")
	migrated_store, n_changed = interval_fingerprint.migrate_legacy_fingerprints(
		legacy_store,
	)
	# migration must have rewritten every key
	assert n_changed == len(_SEEDS) - 1

	plan = solve_queue.plan_interval_work(_SEEDS, migrated_store)

	assert plan.total_intervals == len(_SEEDS) - 1
	assert plan.reused_count == plan.total_intervals
	assert plan.pending_count == 0


#============================================
def test_legacy_store_fully_reuses_after_migration_bin1():
	"""A store keyed at bin1 fully reuses after migration, no intervals pending.

	The same reuse guarantee holds at bin 1. bin 1 is the refine-default
	for wide sources; bin 4 is common for 4K. Both must produce identical
	bin-invariant keys and achieve full reuse.
	"""
	legacy_store = _build_legacy_store("/bin1")
	migrated_store, n_changed = interval_fingerprint.migrate_legacy_fingerprints(
		legacy_store,
	)
	# bin1 keys all had the suffix stripped
	assert n_changed == len(_SEEDS) - 1

	plan = solve_queue.plan_interval_work(_SEEDS, migrated_store)

	assert plan.total_intervals == len(_SEEDS) - 1
	assert plan.reused_count == plan.total_intervals
	assert plan.pending_count == 0


#============================================
def test_bin4_and_bin1_migrate_to_same_keys():
	"""Legacy keys at different bins become byte-identical after migration.

	A solve at bin 4 and a refine at bin 1 produce the same store key after
	migration, so a bin-change between solve and refine does not force a
	re-solve. This is the core bin-invariance guarantee.
	"""
	store_bin4 = _build_legacy_store("/bin4")
	store_bin1 = _build_legacy_store("/bin1")

	migrated_bin4, _ = interval_fingerprint.migrate_legacy_fingerprints(store_bin4)
	migrated_bin1, _ = interval_fingerprint.migrate_legacy_fingerprints(store_bin1)

	# both migrated stores must carry identical key sets
	assert set(migrated_bin4.keys()) == set(migrated_bin1.keys())
