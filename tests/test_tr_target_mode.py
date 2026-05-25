"""Tests for target mode: prediction rendering, validators, and race-start
target-frame generation.

Covers:
- C10 contract: target mode must never silently drop an interval just
  because FWD/BWD are missing. If blended_path is present, render it.
- Stale-diagnostics validator and partition helper used at target-mode entry.
- The --race-start sub-mode helper that proposes target frames for refining
  the race-start interval.
"""

# PIP3 modules
import pytest

# local repo modules
import cli
import cli_args
import state_io


#============================================
def _parse_target_args(argv: list) -> object:
	"""Parse target-mode args through the centralized argparse tree."""
	parser = cli_args._build_parser()
	args = parser.parse_args(["-i", "x.MOV", "target"] + argv)
	return args


#============================================
def test_target_parser_high_shortcut_sets_high_severity():
	args = _parse_target_args(["-H"])
	assert args.severity == "high"


def test_target_parser_low_shortcut_sets_low_severity():
	args = _parse_target_args(["--low"])
	assert args.severity == "low"


def test_target_parser_from_analyze_shortcut():
	args = _parse_target_args(["-A"])
	assert args.target_from_analyze is True


def test_target_parser_rejects_conflicting_severity_shortcuts():
	with pytest.raises(SystemExit):
		_parse_target_args(["--high", "--low"])


#============================================
def test_build_predictions_with_both_fwd_bwd():
	"""Predictions include FWD/BWD/consensus when both paths are present."""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 20,
				"forward_path": [
					{"cx": 100.0, "cy": 200.0, "w": 30.0, "h": 60.0}
					for _ in range(5)
				],
				"backward_path": [
					{"cx": 102.0, "cy": 202.0, "w": 32.0, "h": 62.0}
					for _ in range(5)
				],
				"blended_path": [
					{"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0}
					for _ in range(5)
				],
				"interval_score": {
					"confidence_tier": "high",
					"agreement": 0.95,
					"velocity_consistency": 0.9,
				},
			}
		],
	}

	predictions = cli._build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: predictions exist for every interior frame of the interval,
	# each carrying all four direction labels.
	expected_frames = set(range(10, 15))
	assert expected_frames.issubset(predictions.keys())
	for frame_idx in expected_frames:
		pred = predictions[frame_idx]
		assert "forward" in pred, f"frame {frame_idx} missing forward"
		assert "backward" in pred, f"frame {frame_idx} missing backward"
		assert "blended" in pred, f"frame {frame_idx} missing blended"
		assert "consensus" in pred, f"frame {frame_idx} missing consensus"
		assert "interval_info" in pred, f"frame {frame_idx} missing interval_info"


#============================================
def test_build_predictions_with_blended_only():
	"""Predictions include blended when FWD/BWD are missing but blended present.

	This is the C10 regression fix: target mode no longer skips intervals
	that lack FWD/BWD but have blended_path. The blended path is rendered.
	"""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 20,
				"forward_path": None,  # missing
				"backward_path": None,  # missing
				"blended_path": [
					{"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0}
					for _ in range(5)
				],
				"interval_score": {
					"confidence_tier": "fair",
					"agreement": 0.75,
					"velocity_consistency": 0.7,
				},
			}
		],
	}

	predictions = cli._build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: every interior frame carries blended (the C10 regression
	# fix) but no FWD/BWD/consensus when those paths are missing.
	expected_frames = set(range(10, 15))
	assert expected_frames.issubset(predictions.keys())
	for frame_idx in expected_frames:
		pred = predictions[frame_idx]
		assert "blended" in pred, f"frame {frame_idx} missing blended"
		assert "interval_info" in pred, f"frame {frame_idx} missing interval_info"
		assert "forward" not in pred, \
			f"frame {frame_idx} should not have forward (FWD is None)"
		assert "backward" not in pred, \
			f"frame {frame_idx} should not have backward (BWD is None)"
		assert "consensus" not in pred, \
			f"frame {frame_idx} should not have consensus (can't compute from missing paths)"


#============================================
def test_build_predictions_skips_empty_intervals():
	"""Intervals with all paths missing are skipped with a warning."""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 20,
				"forward_path": None,
				"backward_path": None,
				"blended_path": None,  # all missing
				"interval_score": {
					"confidence_tier": "low",
					"agreement": 0.5,
					"velocity_consistency": 0.4,
				},
			}
		],
	}

	predictions = cli._build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: nothing rendered when all paths are missing.
	assert not predictions


#============================================
def test_build_predictions_mixed_intervals():
	"""Multiple intervals with different path availability are all processed.

	Interval 1: has FWD/BWD/blended -> renders all
	Interval 2: has blended only -> renders blended
	Interval 3: empty -> skipped
	"""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			# Interval 1: complete
			{
				"start_frame": 10,
				"end_frame": 15,
				"forward_path": [
					{"cx": 100.0, "cy": 200.0, "w": 30.0, "h": 60.0}
					for _ in range(3)
				],
				"backward_path": [
					{"cx": 102.0, "cy": 202.0, "w": 32.0, "h": 62.0}
					for _ in range(3)
				],
				"blended_path": [
					{"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0}
					for _ in range(3)
				],
				"interval_score": {
					"confidence_tier": "high",
					"agreement": 0.95,
					"velocity_consistency": 0.9,
				},
			},
			# Interval 2: blended only
			{
				"start_frame": 20,
				"end_frame": 25,
				"forward_path": None,
				"backward_path": None,
				"blended_path": [
					{"cx": 105.0, "cy": 205.0, "w": 35.0, "h": 65.0}
					for _ in range(3)
				],
				"interval_score": {
					"confidence_tier": "fair",
					"agreement": 0.75,
					"velocity_consistency": 0.7,
				},
			},
			# Interval 3: empty
			{
				"start_frame": 30,
				"end_frame": 35,
				"forward_path": None,
				"backward_path": None,
				"blended_path": None,
				"interval_score": {
					"confidence_tier": "low",
					"agreement": 0.5,
					"velocity_consistency": 0.4,
				},
			},
		],
	}

	predictions = cli._build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: predictions span exactly the rendered frame ranges from
	# intervals 1 and 2; interval 3 contributes nothing.
	rendered_frames = set(range(10, 13)) | set(range(20, 23))
	assert set(predictions.keys()) == rendered_frames

	# Interval 1: frames 10-12 should have all paths
	for frame_idx in range(10, 13):
		pred = predictions[frame_idx]
		assert "forward" in pred, f"interval 1 frame {frame_idx} missing forward"
		assert "backward" in pred, f"interval 1 frame {frame_idx} missing backward"
		assert "blended" in pred, f"interval 1 frame {frame_idx} missing blended"
		assert "consensus" in pred, f"interval 1 frame {frame_idx} missing consensus"

	# Interval 2: frames 20-22 should have blended only
	for frame_idx in range(20, 23):
		pred = predictions[frame_idx]
		assert "blended" in pred, f"interval 2 frame {frame_idx} missing blended"
		assert "forward" not in pred, \
			f"interval 2 frame {frame_idx} should not have forward"
		assert "backward" not in pred, \
			f"interval 2 frame {frame_idx} should not have backward"
		assert "consensus" not in pred, \
			f"interval 2 frame {frame_idx} should not have consensus"

	# Interval 3: no frames added
	for frame_idx in range(30, 35):
		assert frame_idx not in predictions, \
			f"interval 3 frame {frame_idx} should not be in predictions"


#============================================
# Stale-diagnostics validator + partition helper (target-mode entry).
#============================================
def test_validator_raises_on_stale(tmp_path):
	"""_validate_diagnostics_confidence raises on missing interval_score.

	Strict validator must fail hard when an entry lacks interval_score,
	signaling corruption that requires re-solve.
	"""
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
def test_partition_intervals_valid_vs_stale():
	"""_partition_intervals_by_validity separates valid from stale entries.

	Valid entries have nested interval_score with confidence_tier; stale
	entries lack interval_score or confidence_tier.
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
	assert stale_count > 0
	assert {s["start_frame"] for s in valid} == {10}


#============================================
def test_partition_skips_entries_missing_confidence():
	"""_partition_intervals_by_validity skips entries lacking confidence fields."""
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
	assert stale_count > 0
	assert {s["start_frame"] for s in valid} == {10}


#============================================
# --race-start sub-mode: target-frame generation.
#============================================
def test_target_race_start_frame_selection():
	"""Race-start frame selection: endpoints present, sorted, unique, in-range."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 100,
			"race_start_interval": [50, 150],
		},
	}

	target_frames = cli._generate_race_start_target_frames(
		diagnostics, fps=30.0, frame_count=1000,
	)

	assert 50 in target_frames
	assert 150 in target_frames
	assert target_frames == sorted(target_frames)
	assert target_frames == sorted(set(target_frames))
	assert all(0 <= f < 1000 for f in target_frames)


#============================================
def test_target_race_start_converges_on_tiny_interval():
	"""On a 4-frame interval, every proposed frame must lie inside the
	interval. Earlier fixed-second offsets (+/-0.5 s) produced frames
	60+ frames outside a 4-frame interval, defeating refinement.
	"""
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 102,
			"race_start_interval": [100, 104],
		},
	}
	target_frames = cli._generate_race_start_target_frames(
		diagnostics, fps=60.0, frame_count=10000,
	)
	assert all(100 <= f <= 104 for f in target_frames)
	assert 100 in target_frames
	assert 104 in target_frames
	# strictly inside the interval (refinement must offer at least one
	# choice that is not already a seed at an endpoint)
	inside = [f for f in target_frames if 100 < f < 104]
	assert inside


#============================================
def test_target_race_start_clamped():
	"""When race_start_frame is near the start, frames clamp to >= 0 and endpoints survive."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 10,
			"race_start_interval": [5, 50],
		},
	}

	target_frames = cli._generate_race_start_target_frames(
		diagnostics, fps=30.0, frame_count=1000,
	)

	assert all(f >= 0 for f in target_frames)
	assert 5 in target_frames
	assert 50 in target_frames


#============================================
def test_target_race_start_missing_schema_raises():
	"""Outdated diagnostics schema raises a re-solve directive."""
	diagnostics = {
		"track_runner_diagnostics": 4,
		"pre_race_reference": {
			"race_start_frame": 100,
			"race_start_interval": [50, 150],
		},
	}

	with pytest.raises(RuntimeError) as exc_info:
		cli._generate_race_start_target_frames(diagnostics, fps=30.0, frame_count=1000)
	assert "schema" in str(exc_info.value).lower()
	assert "solve" in str(exc_info.value).lower()


#============================================
def test_target_race_start_missing_reference_raises():
	"""Missing pre_race_reference is a hard error."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		# Missing pre_race_reference
	}

	with pytest.raises(RuntimeError) as exc_info:
		cli._generate_race_start_target_frames(diagnostics, fps=30.0, frame_count=1000)
	assert "pre_race_reference" in str(exc_info.value).lower()


#============================================
def test_target_race_start_missing_interval_raises():
	"""Schema-5 diagnostics missing race_start_interval is an internal invariant violation."""
	diagnostics = {
		"track_runner_diagnostics": 5,
		"pre_race_reference": {
			"race_start_frame": 100,
			# Missing race_start_interval
		},
	}

	with pytest.raises((KeyError, RuntimeError)):
		cli._generate_race_start_target_frames(diagnostics, fps=30.0, frame_count=1000)
