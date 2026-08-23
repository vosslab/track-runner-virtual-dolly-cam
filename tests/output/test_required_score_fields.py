"""Required interval-score fields surface as errors where they go missing.

`scoring.py` produces all five numeric score fields on every branch, so a
consumer reading one of them is entitled to fail when it is absent rather than
continue on a substituted number.
"""

# Standard Library
import pathlib

# PIP3 modules
import pytest

# local repo modules
import encode_analysis
import review
import state_io
import modes.predictions


#============================================
def _complete_score() -> dict:
	"""Build one interval score carrying every required field."""
	score = {
		"agreement": 0.8,
		"velocity_consistency": 0.7,
		"size_consistency": 0.6,
		"motion_quality": 0.9,
		"occlusion_fraction": 0.1,
		"confidence_tier": "good",
	}
	return score


#============================================
def _interval(score: dict) -> dict:
	"""Wrap a score in a minimal interval record."""
	interval = {"start_frame": 0, "end_frame": 30, "interval_score": score}
	return interval


#============================================
def test_interval_scores_writer_requires_agreement(tmp_path: pathlib.Path) -> None:
	"""Writing solver scores fails when agreement is absent."""
	score = _complete_score()
	del score["agreement"]
	solve_data = {
		"intervals": [_interval(score)],
		"video_identity": {"width": 1920, "height": 1080, "frame_count": 100},
	}
	out_path = str(tmp_path / "interval_scores.json")
	with pytest.raises(KeyError):
		state_io.write_solver_interval_scores(solve_data, out_path, 60.0)


#============================================
def test_rank_key_requires_agreement() -> None:
	"""Ranking an interval fails when agreement is absent."""
	score = _complete_score()
	del score["agreement"]
	with pytest.raises(KeyError):
		review.rank_key(_interval(score))


#============================================
def test_predictions_require_confidence_tier() -> None:
	"""Building predictions fails when confidence_tier is absent."""
	score = _complete_score()
	del score["confidence_tier"]
	solved_data = {"fps": 60.0, "intervals": [_interval(score)]}
	with pytest.raises(KeyError):
		modes.predictions.build_predictions_from_solved_intervals(solved_data)


#============================================
def test_solver_context_requires_motion_quality() -> None:
	"""Solver-context analysis fails when motion_quality is absent."""
	score = _complete_score()
	del score["motion_quality"]
	seeds = [{"frame_index": 0}, {"frame_index": 30}]
	with pytest.raises(KeyError):
		encode_analysis.analyze_solver_context([_interval(score)], seeds, 60.0)
