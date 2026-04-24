"""Unit tests for cli target mode validators and partition helpers.

Tests the strict validator _validate_diagnostics_confidence and the
mode-aware partition helper _partition_intervals_by_validity used in
target mode to skip stale intervals rather than failing hard.

Per docs/PYTHON_STYLE.md PYTEST guidance: behavioral tests only, no
video decode, no full annotator setup.
"""

# PIP3 modules
import pytest

# local repo modules
import cli
import state_io


#============================================
def test_validator_raises_on_stale(tmp_path):
	"""_validate_diagnostics_confidence raises on missing interval_score.

	Strict validator must fail hard when an entry lacks interval_score,
	signaling corruption that requires re-solve.
	"""
	# build a diagnostics dict with a stale entry (no interval_score)
	diagnostics = {
		state_io.DIAGNOSTICS_HEADER_KEY: 3,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				# missing interval_score entirely
			}
		]
	}

	diag_path = str(tmp_path / "diag.json")
	with pytest.raises(RuntimeError) as exc_info:
		cli._validate_diagnostics_confidence(diagnostics, diag_path)
	assert "re-solve" in str(exc_info.value).lower()


#============================================
def test_partition_intervals_valid_vs_stale(tmp_path):
	"""_partition_intervals_by_validity separates valid from stale entries.

	Valid entries have nested interval_score with confidence_tier.
	Stale entries are missing interval_score or confidence_tier.
	"""
	intervals_list = [
		{
			"start_frame": 10,
			"end_frame": 100,
			"interval_score": {
				"agreement": 0.8,
				"confidence_tier": "high",
				"failure_reasons": [],
				"warning_flags": [],
			},
		},
		{
			"start_frame": 101,
			"end_frame": 200,
			# missing interval_score entirely
		},
		{
			"start_frame": 201,
			"end_frame": 300,
			"interval_score": {
				"agreement": 0.5,
				# missing confidence_tier
				"failure_reasons": [],
				"warning_flags": [],
			},
		},
	]

	valid, stale_count = cli._partition_intervals_by_validity(intervals_list)
	assert len(valid) == 1
	assert stale_count == 2
	assert valid[0]["start_frame"] == 10


#============================================
def test_partition_skips_entries_missing_confidence(tmp_path):
	"""_partition_intervals_by_validity skips entries lacking confidence fields.

	Directly test the partition helper on an interval missing confidence_tier
	and confidence (the condition that makes an entry stale for target mode).
	"""
	intervals_list = [
		{
			"start_frame": 10,
			"end_frame": 100,
			"interval_score": {
				"agreement": 0.8,
				"confidence_tier": "high",
				"failure_reasons": [],
				"warning_flags": [],
			},
		},
		{
			"start_frame": 101,
			"end_frame": 200,
			"interval_score": {
				"agreement": 0.5,
				# missing both confidence_tier and confidence
				"failure_reasons": [],
				"warning_flags": [],
			},
		},
	]

	valid, stale_count = cli._partition_intervals_by_validity(intervals_list)
	assert len(valid) == 1
	assert stale_count == 1
	assert valid[0]["start_frame"] == 10


#============================================
# Pre-race tier audit tests (merged from former test_confidence_tier_pre_race.py)
#============================================

def test_review_flagging_skips_pre_race():
	"""identify_weak_spans excludes pre_race intervals from suggestions."""
	import review
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 0,
				"end_frame": 100,
				"interval_score": {
					"agreement": 0.3,
					"confidence_tier": "low",
					"failure_reasons": ["low_agreement"],
					"warning_flags": [],
				},
				"blended_path": [
					{"cx": 100.0, "cy": 100.0, "w": 30.0, "h": 60.0}
					for _ in range(101)
				],
			},
			{
				"start_frame": 100,
				"end_frame": 150,
				"interval_score": {
					"agreement": 1.0,
					"confidence_tier": "pre_race",
					"failure_reasons": [],
					"warning_flags": [],
				},
				"blended_path": [
					{"cx": 100.0, "cy": 100.0, "w": 30.0, "h": 60.0}
					for _ in range(51)
				],
			},
		]
	}
	suggestions = review.identify_weak_spans(diagnostics)
	# No suggestion may fall inside the pre-race interval frame range
	for suggestion in suggestions:
		assert not (100 <= suggestion["frame_index"] <= 150)


def test_rank_key_sorts_pre_race_to_end():
	"""Sorting by rank_key places pre_race intervals last."""
	import review
	intervals = [
		{"interval_score": {"agreement": 0.9, "confidence_tier": "high"}},
		{"interval_score": {"agreement": 0.1, "confidence_tier": "low"}},
		{"interval_score": {"agreement": 1.0, "confidence_tier": "pre_race"}},
	]
	sorted_intervals = sorted(intervals, key=review.rank_key)
	assert sorted_intervals[-1]["interval_score"]["confidence_tier"] == "pre_race"


def test_classify_interval_severity_skips_pre_race():
	"""classify_interval_severity returns None for pre_race intervals.

	Pre-race intervals are synthesized with perfect consistency metrics
	and are not quality-ranked. The function must return None to signal
	callers to skip severity classification for pre_race tiers.
	"""
	import review
	pre_race_interval = {
		"start_frame": 0,
		"end_frame": 100,
		"interval_score": {
			"agreement": 1.0,
			"confidence_tier": "pre_race",
			"failure_reasons": [],
			"warning_flags": [],
		},
	}
	result = review.classify_interval_severity(pre_race_interval, fps=30.0)
	assert result is None


#============================================
# Race-start target mode tests
#============================================

def test_target_race_start_frame_selection():
	"""Test race-start frame selection with known inputs."""
	# Build a fake diagnostics dict with known race-start data
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 100,
			"race_start_interval": [50, 150],
		},
	}

	fps = 30.0
	frame_count = 1000

	target_frames = cli._generate_race_start_target_frames(
		diagnostics, fps, frame_count
	)

	# Verify properties
	# Should contain interval endpoints
	assert 50 in target_frames
	assert 150 in target_frames

	# Should be sorted
	assert target_frames == sorted(target_frames)

	# Should have no duplicates
	assert len(target_frames) == len(set(target_frames))

	# Should have clamped frames (within valid range)
	assert all(0 <= f < frame_count for f in target_frames)


def test_target_race_start_clamped():
	"""Test clamping when race_start_frame is near the beginning."""
	# race_start_frame near start; -0.5s offset would go negative
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 10,  # Very early
			"race_start_interval": [5, 50],
		},
	}

	fps = 30.0
	frame_count = 1000

	target_frames = cli._generate_race_start_target_frames(
		diagnostics, fps, frame_count
	)

	# Verify no negative frames
	assert all(f >= 0 for f in target_frames)

	# Verify interval endpoints are still present
	assert 5 in target_frames
	assert 50 in target_frames


def test_target_race_start_missing_schema_raises():
	"""Test error when diagnostics schema is outdated."""
	# Schema version 4 (pre-unification)
	diagnostics = {
		"track_runner_diagnostics": 4,
		"pre_race_reference": {
			"race_start_frame": 100,
			"race_start_interval": [50, 150],
		},
	}

	fps = 30.0
	frame_count = 1000

	with pytest.raises(RuntimeError) as exc_info:
		cli._generate_race_start_target_frames(diagnostics, fps, frame_count)

	assert "schema" in str(exc_info.value).lower()
	assert "solve" in str(exc_info.value).lower()


def test_target_race_start_missing_reference_raises():
	"""Test error when pre_race_reference is missing entirely."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		# Missing pre_race_reference
	}

	fps = 30.0
	frame_count = 1000

	with pytest.raises(RuntimeError) as exc_info:
		cli._generate_race_start_target_frames(diagnostics, fps, frame_count)

	assert "pre_race_reference" in str(exc_info.value).lower()


def test_target_race_start_missing_interval_raises():
	"""Test error when race_start_interval is missing."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 100,
			# Missing race_start_interval
		},
	}

	fps = 30.0
	frame_count = 1000

	# Strict indexing: a schema-5 diagnostics dict without race_start_interval
	# is an internal invariant violation and surfaces as KeyError.
	with pytest.raises((KeyError, RuntimeError)) as exc_info:
		cli._generate_race_start_target_frames(diagnostics, fps, frame_count)

	assert "interval" in str(exc_info.value).lower()
