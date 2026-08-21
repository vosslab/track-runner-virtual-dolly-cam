"""Unit tests for count_post_seed_accepts and WalkCoverage.

count_post_seed_accepts is the pure helper that counts accepted frames
excluding the pass's own seed frame. Case shapes are inline synthetic inputs.

The gate that consumes WalkCoverage lives in interval_solver; see
test_walker_stall_fallback.py for gate-decision tests.

"""

# local repo modules (track_runner/ is on sys.path via tests/conftest.py)
import walker_bundle


#============================================
def test_empty_accepts_returns_zero() -> None:
	"""An empty accepts list has no post-seed evidence."""
	result = walker_bundle.count_post_seed_accepts(accepts=[], seed_frame=1080)
	assert result == 0


#============================================
def test_bootstrap_only_returns_zero() -> None:
	"""A seed-only acceptance has no post-seed evidence.

	The bootstrap observes the seed frame, but no windowed step accepts a frame.
	"""
	result = walker_bundle.count_post_seed_accepts(accepts=[1126], seed_frame=1126)
	assert result == 0


#============================================
def test_bootstrap_plus_windowed_accepts() -> None:
	"""Bootstrap plus windowed accepts excludes the bootstrap from the count.

	Total 30 accepted, seed at 1296 (bootstrap), 29 windowed post-seed.
	"""
	# seed frame at 1296, then 29 frames accepted after it
	accepts = [1296] + list(range(1297, 1326))
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1296)
	assert result == 29


#============================================
def test_bootstrap_miss_with_windowed_accept() -> None:
	"""A windowed acceptance counts even if bootstrap missed.

	Total 1 accepted (same as the masked case), post-seed 1 (different gate
	outcome): the single accepted frame is NOT the seed frame.
	"""
	# seed at 1134 was not observed; frame 1137 was accepted by a windowed step
	accepts = [1137]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1134)
	assert result == 1


#============================================
def test_bwd_pass_seed_at_right_endpoint() -> None:
	"""A backward pass counts all accepted frames except its seed.

	Total 3 accepted, bootstrap missed (seed_frame=1134 not in accepts),
	post-seed == 3. Same total as the masked FWD case but healthy gate outcome.
	"""
	# BWD seed is the right seed (frame 1134); bootstrap missed, 3 windowed accepts
	accepts = [1130, 1128, 1127]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=1134)
	assert result == 3


#============================================
def test_duplicate_non_seed_frames_count_each_once() -> None:
	"""Duplicate non-seed entries each count once (counts appearances, not unique frames)."""
	# seed=10; non-seed frames 20 and 30 appear twice each
	accepts = [20, 30, 20, 30]
	result = walker_bundle.count_post_seed_accepts(accepts=accepts, seed_frame=10)
	# 4 entries in accepts, all non-seed -> count is 4
	assert result == 4
