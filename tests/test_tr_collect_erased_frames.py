"""Tests for interval_solver.collect_erased_frames.

Locks in P1-1 from /Users/vosslab/.claude/plans/rustling-juggling-creek.md:
analyze --plot needs to distinguish user-flagged not_in_frame dropouts from
tracker gaps. The helper mirrors the per-seed radius logic of
_apply_trajectory_erasure without mutating any trajectory.
"""

# local repo modules
import interval_solver


#============================================
def test_no_not_in_frame_seeds_returns_empty_set():
	seeds = [
		{"frame_index": 5, "status": "visible"},
		{"frame_index": 50, "status": "approximate"},
	]
	erased = interval_solver.collect_erased_frames(seeds, fps=30.0, total_frames=100)
	assert erased == set()


def test_not_in_frame_seed_erases_radius_around_seed():
	# At 30 fps and NOT_IN_FRAME_ERASE_RADIUS_S = 1.0s the radius is 30 frames.
	seeds = [{"frame_index": 50, "status": "not_in_frame"}]
	erased = interval_solver.collect_erased_frames(seeds, fps=30.0, total_frames=200)
	# range is inclusive on both ends in _apply_trajectory_erasure
	assert min(erased) == 20
	assert max(erased) == 80
	assert len(erased) == 61


def test_visible_seed_inside_radius_is_protected():
	# visible/partial seeds are pinned by anchor_to_seeds and must not be
	# reported as erased even if they fall inside a not_in_frame radius.
	seeds = [
		{"frame_index": 50, "status": "not_in_frame"},
		{"frame_index": 55, "status": "visible"},
	]
	erased = interval_solver.collect_erased_frames(seeds, fps=30.0, total_frames=200)
	assert 55 not in erased
	assert 54 in erased
	assert 56 in erased


def test_radius_clamps_at_video_bounds():
	# A not_in_frame seed near frame 0 must not produce negative indices.
	seeds = [{"frame_index": 5, "status": "not_in_frame"}]
	erased = interval_solver.collect_erased_frames(seeds, fps=30.0, total_frames=100)
	assert min(erased) == 0
	assert max(erased) == 35
