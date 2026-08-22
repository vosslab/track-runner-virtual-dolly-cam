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
import pathlib

import pytest

# local repo modules
import cli_args
import common_tools.coord_space
import interval_fingerprint
import modes.predictions as mode_predictions
import modes.target as target_mode
import state_io
import torso_box_coords_io


#============================================
def _current_video_identity() -> dict:
	"""Build a current source identity for temporary coordinate storage."""
	identity = {
		"width": 640,
		"height": 480,
		"frame_count": 30,
	}
	return identity


#============================================
def _parse_target_args(argv: list) -> object:
	"""Parse target-mode args through the centralized argparse tree."""
	parser = cli_args._build_parser()
	args = parser.parse_args(["-i", "x.MOV", "target"] + argv)
	return args


#============================================
def test_target_parser_high_shortcut_sets_high_severity() -> None:
	args = _parse_target_args(["-H"])
	assert args.severity == "high"


def test_target_parser_low_shortcut_sets_low_severity() -> None:
	args = _parse_target_args(["--low"])
	assert args.severity == "low"


def test_target_parser_from_analyze_shortcut() -> None:
	args = _parse_target_args(["-A"])
	assert args.target_from_analyze is True


def test_target_parser_rejects_conflicting_severity_shortcuts() -> None:
	with pytest.raises(SystemExit):
		_parse_target_args(["--high", "--low"])


#============================================
def test_target_risk_view_orders_by_risk_then_start_frame() -> None:
	"""Target placement uses current promotion risk, not legacy confidence rank."""
	risk_view = {
		(10, 20): {
			"risk": 1.0, "severity": "high",
		},
		(30, 40): {
			"risk": 2.0, "severity": "low",
		},
		(50, 60): {
			"risk": 2.0, "severity": "medium",
		},
		(70, 80): {
			"risk": 0.0, "severity": "high",
		},
		(90, 100): {
			"risk": 3.0, "severity": "pre_race",
		},
	}
	intervals = target_mode.review.target_intervals_from_risk_view(risk_view)
	assert intervals == [(2.0, 30, 40), (2.0, 50, 60), (1.0, 10, 20)]
	assert target_mode.review.target_intervals_from_risk_view(
		risk_view, top_n=4,
	) == [(2.0, 30, 40), (2.0, 50, 60), (1.0, 10, 20), (0.0, 70, 80)]


#============================================
def test_build_predictions_with_both_fwd_bwd() -> None:
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

	predictions = mode_predictions.build_predictions_from_solved_intervals(diagnostics)

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
		assert isinstance(pred["forward"], common_tools.coord_space.SourceBox)
		assert pred["consensus"].cx == 101.0


#============================================
def test_build_predictions_with_blended_only() -> None:
	"""Predictions include blended when FWD/BWD are missing but blended present.

	Schema 15 may omit quantization-identical FWD/BWD paths while retaining the
	durable blended path used for the target overlay.
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

	predictions = mode_predictions.build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: every interior frame carries blended but no
	# FWD/BWD/consensus when those paths are missing.
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
def test_live_blend_commitment_review_items_reach_frame_predictions() -> None:
	"""Committed and unavailable blend states have distinct review text."""
	diagnostics = {
		"fps": 30.0,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 13,
				"forward_path": None,
				"backward_path": None,
				"blended_path": [
					{
						"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0,
						"blend_flag": True, "commitment_direction": "fwd",
						"commitment_alpha": 0.5,
					},
					{
						"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0,
						"blend_flag": True, "commitment_direction": "unavailable",
						"commitment_alpha": 0.0,
					},
					{
						"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0,
						"blend_flag": True, "commitment_direction": "bwd",
						"commitment_alpha": 1.0,
					},
				],
				"interval_score": {
					"confidence_tier": "fair",
					"agreement": 0.75,
					"velocity_consistency": 0.7,
				},
			}
		],
	}

	predictions = mode_predictions.build_predictions_from_solved_intervals(diagnostics)

	assert predictions[10]["interval_info"]["commitment_review_item"] == (
		"Blend committed to FWD at 50% transition; residual-motion evidence"
	)
	assert predictions[11]["interval_info"]["commitment_review_item"] == (
		"Blend commitment unavailable; evidence unavailable; baseline retained"
	)
	assert predictions[12]["interval_info"]["commitment_review_item"] == (
		"Blend committed to BWD at 100% transition; residual-motion evidence"
	)


#============================================
def test_reloaded_predictions_allow_absent_advisory_scores(
	tmp_path: pathlib.Path,
) -> None:
	"""Coordinate storage still supplies overlays without advisory scores."""
	intervals_path = tmp_path / "demo.track_runner.torso_box_coords.npz"
	torso_box_coords_io.write_torso_box_coords(
		str(intervals_path),
		{
			"video_identity": _current_video_identity(),
			"solve_complete": True,
			"solved_intervals": {
				"test-fingerprint": {
					"start_frame": 10,
					"end_frame": 10,
					"forward_path": [
						{"cx": 100.0, "cy": 200.0, "w": 30.0, "h": 60.0},
					],
					"backward_path": [
						{"cx": 102.0, "cy": 202.0, "w": 32.0, "h": 62.0},
					],
					"blended_path": [
						{
							"cx": 101.0, "cy": 201.0, "w": 31.0, "h": 61.0,
							"blend_flag": True, "commitment_direction": "fwd",
							"commitment_alpha": 0.5,
						},
					],
				}
			}
		},
	)

	predictions = mode_predictions.predictions_from_torso_box_coords(
		str(intervals_path), str(tmp_path / "missing_scores.json"), 30.0,
	)
	assert predictions[10]["interval_info"]["confidence"] == "unknown"


#============================================
def test_reloaded_predictions_restore_exact_c3_seed_geometry(
	tmp_path: pathlib.Path,
) -> None:
	"""Seed/Target/Edit overlays cannot expose stale cached blend endpoints."""
	seed_start = {
		"frame_index": 10, "cx": 100.5, "cy": 200.25,
		"w": 31.5, "h": 61.25, "status": "visible", "pass": 1,
	}
	seed_end = {
		"frame_index": 12, "cx": 110.75, "cy": 205.5,
		"w": 33.25, "h": 63.5, "status": "partial", "pass": 1,
	}
	fingerprint = interval_fingerprint.compute_interval_fingerprint(
		seed_start, seed_end,
	)
	wrong_path = [
		{"cx": 999.0, "cy": 999.0, "w": 9.0, "h": 9.0}
		for _ in range(3)
	]
	intervals_path = tmp_path / "demo.track_runner.torso_box_coords.npz"
	torso_box_coords_io.write_torso_box_coords(
		str(intervals_path),
		{
			"video_identity": _current_video_identity(),
			"solve_complete": True,
			"solved_intervals": {
			fingerprint: {
				"start_frame": 10, "end_frame": 12,
				"forward_path": wrong_path,
				"backward_path": wrong_path,
				"blended_path": wrong_path,
			},
			},
		},
	)
	scores_path = tmp_path / "demo.track_runner.interval_scores.json"
	state_io.write_interval_scores(str(scores_path), {
		"fps": 30.0,
		"video_identity": _current_video_identity(),
		"intervals": [{
			"start_frame": 10,
			"end_frame": 12,
			"interval_score": {
				"agreement": 1.0,
				"velocity_consistency": 1.0,
				"size_consistency": 1.0,
				"motion_quality": 1.0,
				"occlusion_fraction": 0.0,
				"confidence_tier": "high",
				"failure_reasons": [],
				"warning_flags": [],
			},
		}],
	})

	predictions = mode_predictions.predictions_from_torso_box_coords(
		str(intervals_path), str(scores_path), 30.0,
		seeds=[seed_start, seed_end],
	)

	assert predictions[10]["blended"] == common_tools.coord_space.SourceBox(
		cx=100.5, cy=200.25, w=31.5, h=61.25,
	)
	assert predictions[12]["blended"] == common_tools.coord_space.SourceBox(
		cx=110.75, cy=205.5, w=33.25, h=63.5,
	)


#============================================
def test_build_predictions_skips_empty_intervals() -> None:
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

	predictions = mode_predictions.build_predictions_from_solved_intervals(diagnostics)

	# Behavioral: nothing rendered when all paths are missing.
	assert not predictions


#============================================
# --race-start sub-mode: target-frame generation.
#============================================
def test_target_race_start_frame_selection() -> None:
	"""Race-start frame selection: endpoints present, sorted, unique, in-range."""
	race_start_reference = {"race_start_frame": 100, "race_start_interval": [50, 150]}

	target_frames = target_mode._generate_race_start_target_frames(
		race_start_reference, fps=30.0, frame_count=1000,
	)

	assert 50 in target_frames
	assert 150 in target_frames
	assert target_frames == sorted(target_frames)
	assert target_frames == sorted(set(target_frames))
	assert all(0 <= f < 1000 for f in target_frames)


#============================================
def test_target_race_start_converges_on_tiny_interval() -> None:
	"""On a 4-frame interval, every proposed frame must lie inside the
	interval. Earlier fixed-second offsets (+/-0.5 s) produced frames
	60+ frames outside a 4-frame interval, defeating refinement.
	"""
	race_start_reference = {"race_start_frame": 102, "race_start_interval": [100, 104]}
	target_frames = target_mode._generate_race_start_target_frames(
		race_start_reference, fps=60.0, frame_count=10000,
	)
	assert all(100 <= f <= 104 for f in target_frames)
	assert 100 in target_frames
	assert 104 in target_frames
	# strictly inside the interval (refinement must offer at least one
	# choice that is not already a seed at an endpoint)
	inside = [f for f in target_frames if 100 < f < 104]
	assert inside


#============================================
def test_target_race_start_clamped() -> None:
	"""When race_start_frame is near the start, frames clamp to >= 0 and endpoints survive."""
	race_start_reference = {"race_start_frame": 10, "race_start_interval": [5, 50]}

	target_frames = target_mode._generate_race_start_target_frames(
		race_start_reference, fps=30.0, frame_count=1000,
	)

	assert all(f >= 0 for f in target_frames)
	assert 5 in target_frames
	assert 50 in target_frames


#============================================
def test_target_race_start_missing_reference_raises() -> None:
	"""Missing solve-artifact race_start is a hard error."""
	with pytest.raises((KeyError, RuntimeError)):
		target_mode._generate_race_start_target_frames({}, fps=30.0, frame_count=1000)


#============================================
def test_target_race_start_missing_interval_raises() -> None:
	"""Current solve artifacts require a complete race-start interval."""
	race_start_reference = {"race_start_frame": 100}

	with pytest.raises((KeyError, RuntimeError)):
		target_mode._generate_race_start_target_frames(race_start_reference, fps=30.0, frame_count=1000)
