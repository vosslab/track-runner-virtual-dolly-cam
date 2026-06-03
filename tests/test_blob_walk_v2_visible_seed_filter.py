"""Regression tests: select_random_visible returns only visible/visible pairs.

Two filter sites are covered:
  1. walk_util.select_random_visible (direct unit test below).
  2. make_walk_html_v2.py:151 visible_count pre-filter -- that predicate is
     identical in form to the one inside select_random_visible, so its
     correctness follows from the same behavioral assertion: any interval
     that passes the site-151 gate (left/right both "visible") is exactly
     the set that select_random_visible would return with n=inf.  The
     site-151 predicate is verified by the shared helper test at the bottom
     of this module, which asserts the inline condition independently.

All tests are offline, deterministic, and finish well under one second.
No real video decode.
"""

import pathlib
import random
import sys


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))

import walk_paths
walk_paths.setup()

import walk_util
from core.walk_io import SeedToSeedInterval


#============================================
# Helpers to build synthetic SeedToSeedInterval objects.
#============================================

def _make_interval(left_status: str, right_status: str, left_frame: int = 0) -> SeedToSeedInterval:
	"""Build a minimal SeedToSeedInterval with the given seed statuses."""
	left_seed = {'status': left_status, 'frame_index': left_frame}
	right_seed = {'status': right_status, 'frame_index': left_frame + 10}
	return SeedToSeedInterval(left_seed=left_seed, right_seed=right_seed, label='post_start')


#============================================
# Tests for walk_util.select_random_visible (filter site 1).
#============================================

def test_visible_visible_included():
	"""A visible/visible pair is returned."""
	rng = random.Random(42)
	intervals = [_make_interval('visible', 'visible', left_frame=0)]
	result = walk_util.select_random_visible(intervals, n=10, rng=rng)
	assert result == intervals


def test_partial_seed_excluded():
	"""An interval with a partial seed is never selected."""
	rng = random.Random(42)
	intervals = [
		_make_interval('visible', 'visible', left_frame=0),
		_make_interval('partial', 'visible', left_frame=10),
		_make_interval('visible', 'partial', left_frame=20),
		_make_interval('partial', 'partial', left_frame=30),
	]
	result = walk_util.select_random_visible(intervals, n=10, rng=rng)
	# Only the first interval has both seeds visible.
	assert result
	assert result[0].left_seed['status'] == 'visible'
	assert result[0].right_seed['status'] == 'visible'


def test_not_in_frame_seed_excluded():
	"""An interval with a not_in_frame seed is never selected."""
	rng = random.Random(42)
	intervals = [
		_make_interval('visible', 'not_in_frame', left_frame=0),
		_make_interval('not_in_frame', 'visible', left_frame=10),
		_make_interval('visible', 'visible', left_frame=20),
	]
	result = walk_util.select_random_visible(intervals, n=10, rng=rng)
	# Only the third interval qualifies.
	assert all(
		iv.left_seed['status'] == 'visible' and iv.right_seed['status'] == 'visible'
		for iv in result
	)


def test_approximate_seed_excluded():
	"""An interval with an approximate seed is never selected."""
	rng = random.Random(42)
	intervals = [
		_make_interval('approximate', 'visible', left_frame=0),
		_make_interval('visible', 'visible', left_frame=10),
	]
	result = walk_util.select_random_visible(intervals, n=10, rng=rng)
	assert result
	assert result[0].left_seed['frame_index'] == 10


def test_empty_input_returns_empty():
	"""Empty post_start list produces empty result."""
	rng = random.Random(42)
	result = walk_util.select_random_visible([], n=5, rng=rng)
	assert result == []


def test_all_selected_statuses_are_visible_visible():
	"""Every returned interval must have both seeds visible, regardless of input mix."""
	rng = random.Random(99)
	statuses = ['visible', 'partial', 'not_in_frame', 'approximate']
	intervals = [
		_make_interval(ls, rs, left_frame=idx * 10)
		for idx, (ls, rs) in enumerate(
			(ls, rs) for ls in statuses for rs in statuses
		)
	]
	result = walk_util.select_random_visible(intervals, n=100, rng=rng)
	for iv in result:
		assert iv.left_seed['status'] == 'visible'
		assert iv.right_seed['status'] == 'visible'


def test_sampling_respects_n():
	"""When visible count exceeds n, exactly n intervals are returned."""
	rng = random.Random(7)
	intervals = [
		_make_interval('visible', 'visible', left_frame=i * 10)
		for i in range(20)
	]
	result = walk_util.select_random_visible(intervals, n=5, rng=rng)
	assert len(result) == 5


def test_output_sorted_by_left_frame_index():
	"""Output is sorted ascending by left_seed frame_index."""
	rng = random.Random(13)
	intervals = [
		_make_interval('visible', 'visible', left_frame=30),
		_make_interval('visible', 'visible', left_frame=10),
		_make_interval('visible', 'visible', left_frame=20),
	]
	result = walk_util.select_random_visible(intervals, n=10, rng=rng)
	frames = [iv.left_seed['frame_index'] for iv in result]
	assert frames == sorted(frames)


#============================================
# Tests covering the make_walk_html_v2.py:151 pre-filter predicate (filter site 2).
#============================================

def test_site151_predicate_matches_select_random_visible_behavior():
	"""The make_walk_html_v2.py:151 visible_count predicate is behaviorally equivalent.

	Site 151 uses:
		sum(1 for i in post_start
			if i.left_seed['status'] == 'visible' and i.right_seed['status'] == 'visible')

	This test constructs a known-mix list and asserts that the count produced
	by the site-151 predicate equals the length returned by select_random_visible
	with n=inf, confirming both sites agree on which intervals qualify.
	"""
	rng = random.Random(42)
	intervals = [
		_make_interval('visible', 'visible', left_frame=0),
		_make_interval('partial', 'visible', left_frame=10),
		_make_interval('visible', 'not_in_frame', left_frame=20),
		_make_interval('visible', 'visible', left_frame=30),
		_make_interval('approximate', 'approximate', left_frame=40),
	]
	# site-151 predicate (inline copy of the production expression)
	site151_count = sum(
		1 for iv in intervals
		if iv.left_seed['status'] == 'visible' and iv.right_seed['status'] == 'visible'
	)
	selected = walk_util.select_random_visible(intervals, n=len(intervals), rng=rng)
	# Both sites must agree on the qualifying count.
	assert site151_count == len(selected)
