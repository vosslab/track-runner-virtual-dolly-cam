"""Tests for bounded Stage-4 promotion selection."""

# Standard Library
import math

# local repo modules
import interval_solver
import scoring


#============================================
class _ScaleSceneTransform:
	"""Minimal scene transform whose scale makes unit conversion observable."""

	#============================================
	def __init__(self, scale: float = 1.0) -> None:
		self.scale = scale

	#============================================
	def pixel_box_to_scene(
		self, unused_frame: int, cx: float, cy: float, w: float, h: float,
	) -> tuple:
		return (cx / self.scale, cy / self.scale, w / self.scale, h / self.scale)


#============================================
def _seed(frame_index: int, cx: float, width: float = 20.0) -> dict:
	"""Build one visible endpoint seed for Stage-4 selection."""
	return {
		"frame_index": frame_index,
		"cx": cx,
		"cy": 0.0,
		"w": width,
		"h": width * 2.0,
	}


#============================================
def _score(**changes: float) -> dict:
	"""Build a Stage-3 score with no retained promotion predicate by default."""
	result = {
		"confidence_tier": "high",
		"motion_quality": 1.0,
		"occlusion_fraction": 0.0,
		"size_consistency": 1.0,
	}
	result.update(changes)
	return result


#============================================
def _result(start_frame: int, end_frame: int, **score_changes: float) -> dict:
	"""Build one Stage-3 interval result."""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"interval_score": _score(**score_changes),
	}


#============================================
def _select(
	interval_results: list,
	seeds: list,
	video_frame_count: int = 100,
	fps: float = 30.0,
	candidate_indices: list | None = None,
) -> list:
	"""Call promotion selection with the normal no-pre-race configuration."""
	promoted = interval_solver.select_promoted_intervals(
		interval_results, seeds, _ScaleSceneTransform(), fps, video_frame_count,
		0, candidate_indices=candidate_indices,
	)
	return promoted


#============================================
def test_select_promoted_intervals_promotes_retained_risk_and_holds_benign() -> None:
	"""A retained scoring failure promotes even when the old tier was high."""
	seeds = [_seed(0, 0.0), _seed(5, 20.0), _seed(10, 40.0)]
	interval_results = [
		_result(0, 5, motion_quality=0.4),
		_result(5, 10),
	]

	promoted = _select(interval_results, seeds)

	assert promoted == [0]


#============================================
def test_select_promoted_intervals_excludes_pre_race() -> None:
	"""Pre-race intervals remain ineligible even when their risk is positive."""
	seeds = [_seed(0, 0.0), _seed(5, 20.0), _seed(10, 40.0)]
	pre_race = _result(0, 5, motion_quality=0.4)
	pre_race["source"] = "pre_race_reference"
	interval_results = [pre_race, _result(5, 10, motion_quality=0.4)]

	promoted = _select(interval_results, seeds)

	assert promoted == [1]


#============================================
def test_select_promoted_intervals_respects_refine_candidate_scope() -> None:
	"""Refine promotion cannot re-touch an out-of-scope solved interval (C6)."""
	seeds = [_seed(0, 0.0), _seed(5, 20.0), _seed(10, 40.0)]
	interval_results = [
		_result(0, 5, motion_quality=0.4),
		_result(5, 10, motion_quality=0.4),
	]

	promoted = _select(interval_results, seeds, candidate_indices=[1])

	assert promoted == [1]


#============================================
def test_select_promoted_intervals_skips_nonfitting_interval_and_continues() -> None:
	"""Whole-interval FFD skips an oversized risk leader and uses later capacity."""
	seeds = [_seed(0, 0.0), _seed(6, 20.0), _seed(11, 40.0)]
	interval_results = [
		_result(0, 6, motion_quality=0.4, occlusion_fraction=0.4, size_consistency=0.4),
		_result(6, 11, motion_quality=0.4),
	]

	promoted = _select(interval_results, seeds, video_frame_count=60, fps=1.0)

	assert promoted == [1]


#============================================
def test_select_promoted_intervals_breaks_risk_ties_by_lower_start_frame() -> None:
	"""When only one equal-risk interval fits, the earlier interval wins."""
	seeds = [_seed(0, 0.0), _seed(3, 20.0), _seed(6, 40.0)]
	interval_results = [
		_result(0, 3, motion_quality=0.4),
		_result(3, 6, motion_quality=0.4),
	]

	promoted = _select(interval_results, seeds, video_frame_count=40)

	assert promoted == [0]


#============================================
def test_select_promoted_intervals_uses_ten_percent_floor_when_measured_is_zero() -> None:
	"""The policy floor leaves room for risk promotion without low/fair intervals."""
	seeds = [_seed(0, 0.0), _seed(5, 20.0)]
	interval_results = [_result(0, 5, motion_quality=0.4)]

	promoted = _select(interval_results, seeds, video_frame_count=60)

	assert promoted == [0]


#============================================
def test_chord_risk_uses_scene_torso_widths() -> None:
	"""The Branch-B threshold is invariant to a uniform pixel-to-scene scale."""
	seed_start = _seed(0, 0.0, width=40.0)
	seed_end = _seed(5, 400.0, width=40.0)
	chord_span_widths = interval_solver._endpoint_chord_span_widths(
		seed_start, seed_end, _ScaleSceneTransform(scale=2.0),
	)
	risk = scoring.compute_promotion_risk(
		_score(), 0, 5, 30.0, chord_span_widths=chord_span_widths,
	)

	assert math.isclose(chord_span_widths, 10.0)
	assert risk == 1.0
