"""Unit tests for --top flag in target mode (Patch 6).

Tests that the --top=N flag slices intervals to the worst N by rank_key,
and validates that zero or negative N is rejected.
"""

# Standard Library
import argparse

# local repo modules
import cli_args
import review


#============================================
def _make_interval(
	start_frame=1000,
	end_frame=1050,
	motion_cue_acceptance=0.5,
	agreement=0.5,
	confidence_tier="fair",
):
	"""Build a minimal interval dict for testing rank_key sorting.

	Args:
		start_frame: Start frame index.
		end_frame: End frame index.
		motion_cue_acceptance: Motion fusion acceptance rate [0, 1].
		agreement: FWD/BWD agreement aggregate [0, 1].
		confidence_tier: Confidence tier string.

	Returns:
		Interval dict with interval_score sub-dict.
	"""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"interval_score": {
			"motion_cue_acceptance": motion_cue_acceptance,
			"agreement": agreement,
			"confidence_tier": confidence_tier,
			"failure_reasons": [],
		},
	}


#============================================
def test_top_n_slices_sorted_intervals():
	"""Test that --top=N returns the N worst intervals by rank_key.

	Builds synthetic intervals with varying motion_cue_acceptance
	(primary sort key in rank_key), verifies that sorted slicing
	returns the lowest-motion intervals first.
	"""
	# build intervals with increasing motion acceptance (worst to best)
	interval_list = [
		_make_interval(start_frame=0, end_frame=100, motion_cue_acceptance=0.1),
		_make_interval(start_frame=100, end_frame=200, motion_cue_acceptance=0.3),
		_make_interval(start_frame=200, end_frame=300, motion_cue_acceptance=0.5),
		_make_interval(start_frame=300, end_frame=400, motion_cue_acceptance=0.7),
		_make_interval(start_frame=400, end_frame=500, motion_cue_acceptance=0.9),
	]

	# sort worst-first by rank_key
	sorted_ivs = sorted(interval_list, key=review.rank_key)
	top_3 = sorted_ivs[:3]

	# verify we got exactly 3 intervals
	assert len(top_3) == 3

	# verify they are sorted by motion acceptance (ascending)
	motion_values = [
		iv["interval_score"]["motion_cue_acceptance"]
		for iv in top_3
	]
	assert motion_values == [0.1, 0.3, 0.5]


#============================================
def test_top_n_rejects_zero_or_negative():
	"""Test that --top=0 and --top=-5 are rejected by argparse validation.

	Invokes the parser with invalid top_n values and verifies
	that a SystemExit or ArgumentTypeError is raised.
	"""
	parser = cli_args.parse_args

	# test --top=0 (should fail)
	try:
		# directly call parse_args with sys.argv mocked
		# we use the public CLI entry mechanism: track_runner.py -i dummy.mp4 target --top 0
		import sys
		old_argv = sys.argv
		sys.argv = ["track_runner.py", "-i", "/tmp/dummy.mp4", "target", "--top", "0"]
		try:
			cli_args.parse_args()
			# if we reach here, validation failed to catch it
			assert False, "Expected SystemExit for --top=0"
		except SystemExit as e:
			# validation correctly rejected it
			assert e.code != 0
		finally:
			sys.argv = old_argv
	except AssertionError:
		raise

	# test --top=-5 (should also fail)
	try:
		old_argv = sys.argv
		sys.argv = ["track_runner.py", "-i", "/tmp/dummy.mp4", "target", "--top", "-5"]
		try:
			cli_args.parse_args()
			assert False, "Expected SystemExit for --top=-5"
		except SystemExit as e:
			assert e.code != 0
		finally:
			sys.argv = old_argv
	except AssertionError:
		raise
