"""Unit tests for count_post_seed_accepts and WalkCoverage.

count_post_seed_accepts is the pure helper that counts accepted frames
excluding the pass's own seed frame. WalkCoverage is the named return type
that carries both accepted_count (total) and post_seed_accepted (post-seed
trajectory evidence count). Case shapes are drawn from the Check 3 per-pass
audit table (Conant-4x400-2026_April_15.mkv, 13 intervals).

The gate that consumes WalkCoverage lives in interval_solver; see
tests/test_walker_stall_fallback.py for gate-decision tests.

All tests assert named fields (never tuple positions) per the P10 plan.
"""

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import walker_bundle


#============================================
def test_empty_accepts_returns_zero():
	"""Empty accepts list: Conant seed_1080_1111 FWD shape (total 0, post-seed 0)."""
	result = walker_bundle.count_post_seed_accepts(accepts=[], seed_frame=1080)
	assert result == 0


#============================================
def test_bootstrap_only_returns_zero():
	"""Seed-only accepts: Conant seed_1126_1134 FWD shape (total 1, post-seed 0).

	This is the P10 masked case: the bootstrap observes the seed frame and
	appends it to accepts; no windowed step accepted any frame.
	"""
	result = walker_bundle.count_post_seed_accepts(accepts=[1126], seed_frame=1126)
	assert result == 0


#============================================
def test_bootstrap_plus_windowed_accepts():
	"""Bootstrap + many windowed accepts: Conant seed_1296_1327 FWD shape.

	Total 30 accepted, seed at 1296 (bootstrap), 29 windowed post-seed.
	"""
	# seed frame at 1296, then 29 frames accepted after it
	accepts = [1296] + list(range(1297, 1326))
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1296)
	assert result == 29


#============================================
def test_bootstrap_miss_with_windowed_accept():
	"""Bootstrap missed, windowed step accepted: Conant seed_1134_1142 FWD shape.

	Total 1 accepted (same as the masked case), post-seed 1 (different gate
	outcome): the single accepted frame is NOT the seed frame.
	"""
	# seed at 1134 was not observed; frame 1137 was accepted by a windowed step
	accepts = [1137]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1134)
	assert result == 1


#============================================
def test_bwd_pass_seed_at_right_endpoint():
	"""BWD pass with seed at right endpoint: Conant seed_1126_1134 BWD shape.

	Total 3 accepted, bootstrap missed (seed_frame=1134 not in accepts),
	post-seed == 3. Same total as the masked FWD case but healthy gate outcome.
	"""
	# BWD seed is the right seed (frame 1134); bootstrap missed, 3 windowed accepts
	accepts = [1130, 1128, 1127]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1134)
	assert result == 3


#============================================
def test_duplicate_non_seed_frames_count_each_once():
	"""Duplicate non-seed entries each count once (counts appearances, not unique frames)."""
	# seed=10; non-seed frames 20 and 30 appear twice each
	accepts = [20, 30, 20, 30]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=10)
	# 4 entries in accepts, all non-seed -> count is 4
	assert result == 4


#============================================
def test_walk_coverage_bootstrap_only_gate_decision():
	"""Gate reads post_seed_accepted == 0 for the P10 bootstrap-only case.

	Conant seed_1126_1134 FWD: accepted_count=1 (seed bootstrap), post_seed=0.
	Under the old gate (accepted_count == 0) this was False (no fallback).
	Under the corrected gate (post_seed_accepted == 0) this is True (fallback).
	"""
	coverage = walker_bundle.WalkCoverage(accepted_count=1, post_seed_accepted=0)
	# the old gate would have been: coverage.accepted_count == 0 -> False (wrong)
	# the corrected gate:
	assert (coverage.post_seed_accepted == 0) is True


#============================================
def test_walk_coverage_healthy_pass_gate_decision():
	"""Gate reads post_seed_accepted >= 1 for a healthy pass (no fallback)."""
	coverage = walker_bundle.WalkCoverage(accepted_count=30, post_seed_accepted=29)
	assert (coverage.post_seed_accepted == 0) is False
