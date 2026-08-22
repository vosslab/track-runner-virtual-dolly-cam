"""Unit tests for severity classification and interval ranking.

Tests only behavioral invariants that do not depend on tunable
thresholds. Synthetic interval dicts are used; no video, disk I/O, or
mocking.
"""

# PIP3 modules
import numpy

# local repo modules
import camera_motion
import review
import scene_coords
import scoring


#============================================
def _make_interval(
	start_frame: int = 1000,
	end_frame: int = 1050,
	agreement: float = 0.5,
	confidence_tier: str = "fair",
	failure_reasons: list[str] | None = None,
) -> dict:
	"""Build a minimal interval dict for severity and rank testing."""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"interval_score": {
			"agreement": agreement,
			"confidence_tier": confidence_tier,
			"failure_reasons": failure_reasons or [],
		},
	}


#============================================
def test_rank_lower_agreement_sorts_first() -> None:
	"""Lower agreement sorts before higher agreement (worst-first).

	Behavioral property on relative ordering, not a threshold value.
	"""
	iv_worse = _make_interval(agreement=0.20, confidence_tier="fair")
	iv_better = _make_interval(agreement=0.60, confidence_tier="fair")
	sorted_list = sorted([iv_better, iv_worse], key=review.rank_key)
	assert sorted_list[0] is iv_worse
	assert sorted_list[1] is iv_better


#============================================
def test_rank_confidence_tier_orders_ties() -> None:
	"""With agreement tied, confidence tier orders low < fair < good < high.

	Behavioral property on relative ordering across tiers.
	"""
	iv_low = _make_interval(agreement=0.40, confidence_tier="low")
	iv_fair = _make_interval(agreement=0.40, confidence_tier="fair")
	iv_good = _make_interval(agreement=0.40, confidence_tier="good")
	iv_high = _make_interval(agreement=0.40, confidence_tier="high")
	sorted_list = sorted(
		[iv_high, iv_fair, iv_good, iv_low],
		key=review.rank_key,
	)
	assert sorted_list[0] is iv_low
	assert sorted_list[-1] is iv_high


#============================================
def test_classify_severity_skips_pre_race() -> None:
	"""classify_interval_severity returns None for pre_race intervals.

	Pre-race intervals are synthesized with perfect consistency metrics
	and are not quality-ranked. The function must return None to signal
	callers to skip severity classification for pre_race tiers.
	"""
	pre_race_interval = _make_interval(
		agreement=1.0,
		confidence_tier="pre_race",
	)
	result = review.classify_interval_severity(pre_race_interval, fps=30.0)
	assert result is None


#============================================
def test_review_flagging_skips_pre_race() -> None:
	"""identify_weak_spans excludes pre_race intervals from suggestions."""
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
	# No suggestion may fall inside the pre-race interval frame range.
	for suggestion in suggestions:
		assert not (100 <= suggestion["frame_index"] <= 150)


#============================================
def test_rank_key_sorts_pre_race_to_end() -> None:
	"""Sorting by rank_key places pre_race intervals last."""
	intervals = [
		{"interval_score": {"agreement": 0.9, "confidence_tier": "high"}},
		{"interval_score": {"agreement": 0.1, "confidence_tier": "low"}},
		{"interval_score": {"agreement": 1.0, "confidence_tier": "pre_race"}},
	]
	sorted_intervals = sorted(intervals, key=review.rank_key)
	assert sorted_intervals[-1]["interval_score"]["confidence_tier"] == "pre_race"


#============================================
def _artifact_seed(frame_index: int, cx: float, status: str = "visible") -> dict:
	"""Build one durable human seed for artifact-adapter coverage."""
	return {
		"frame_index": frame_index, "cx": cx, "cy": 50.0,
		"w": 10.0, "h": 20.0, "status": status, "pass": 1,
	}


#============================================
def _artifact_motion(frame_count: int) -> object:
	"""Build an identity-motion track with deliberately low quality."""
	return camera_motion.MotionTrack(
		dx=numpy.zeros(frame_count, dtype=numpy.float32),
		dy=numpy.zeros(frame_count, dtype=numpy.float32),
		scale=numpy.ones(frame_count, dtype=numpy.float32),
		quality=numpy.zeros(frame_count, dtype=numpy.float32),
	)


#============================================
def test_artifact_score_uses_stored_conf_and_analytical_size_path() -> None:
	"""Reloaded confidence is raw-pass evidence; blended does not score size."""
	seeds = [_artifact_seed(0, 10.0), _artifact_seed(2, 30.0)]
	motion = _artifact_motion(3)
	transform = scene_coords.SceneTransform(motion)
	artifact_interval = {
		"start_frame": 0, "end_frame": 2,
		"forward_path": None, "backward_path": None,
		"conf": [0.2, 0.4, 0.6],
		"blended_path": [
			{"cx": 10.0 + 10.0 * i, "cy": 50.0, "w": 10.0, "h": 200.0}
			for i in range(3)
		],
	}
	score = scoring.score_interval_from_artifact(
		seeds[0], seeds[1], seeds, transform, motion, artifact_interval, 30.0,
	)
	assert numpy.isclose(score["agreement"], 0.4)
	assert score["size_consistency"] == 1.0
	assert score["motion_quality"] == 0.0


#============================================
def test_artifact_score_prefers_stored_conf_over_quantized_raw_paths() -> None:
	"""Schema-15 conf transports agreement even when raw arrays survive."""
	seeds = [_artifact_seed(0, 10.0), _artifact_seed(2, 30.0)]
	motion = _artifact_motion(3)
	transform = scene_coords.SceneTransform(motion)
	forward = [
		{"cx": 10.0 + 10.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(3)
	]
	backward = [
		{"cx": 110.0 + 10.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(3)
	]
	artifact_interval = {
		"start_frame": 0, "end_frame": 2,
		"forward_path": forward, "backward_path": backward,
		"conf": [0.75, 0.75, 0.75], "blended_path": forward,
	}
	score = scoring.score_interval_from_artifact(
		seeds[0], seeds[1], seeds, transform, motion, artifact_interval, 30.0,
	)
	assert score["agreement"] == 0.75


#============================================
def test_artifact_score_does_not_project_geometryless_not_in_frame_seed() -> None:
	"""NIF seeds remain occlusion input but never enter scene geometry support."""
	seeds = [
		_artifact_seed(0, 10.0),
		{"frame_index": 1, "status": "not_in_frame", "pass": 1},
		_artifact_seed(2, 30.0),
	]
	motion = _artifact_motion(3)
	transform = scene_coords.SceneTransform(motion)
	path = [
		{"cx": 10.0 + 10.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(3)
	]
	artifact_interval = {
		"start_frame": 0, "end_frame": 2,
		"forward_path": path, "backward_path": path,
		"blended_path": path,
	}
	score = scoring.score_interval_from_artifact(
		seeds[0], seeds[2], seeds, transform, motion, artifact_interval, 30.0,
	)
	assert score["agreement"] == 1.0


#============================================
def test_risk_view_delegates_to_artifact_scoring_and_m6_policy() -> None:
	"""The view contains scoring-owner results and current M6 allocation."""
	seeds = [_artifact_seed(0, 10.0), _artifact_seed(9, 30.0)]
	motion = _artifact_motion(100)
	transform = scene_coords.SceneTransform(motion)
	forward = [
		{"cx": 10.0 + 2.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(10)
	]
	solve_artifact = {
		"scene_transform": transform,
		"fps": 30.0,
		"video_identity": {"frame_count": 100},
		"solved_intervals": {
			"pair": {
				"start_frame": 0, "end_frame": 9,
				"forward_path": forward, "backward_path": forward,
				"blended_path": forward,
			}
		},
	}
	view = review.build_interval_risk_view(seeds, motion, solve_artifact)
	entry = view[(0, 9)]
	assert entry["risk"] == 1.0
	assert entry["promoted"] is True
	assert "low_motion_quality" in entry["failure_reasons"]
	assert entry["interval_score"]["agreement"] == 1.0


#============================================
def test_risk_view_only_marks_pairs_before_race_start_interval_pre_race() -> None:
	"""The race-start spanning pair remains a normal scored interval."""
	seeds = [
		_artifact_seed(0, 10.0), _artifact_seed(10, 30.0),
		_artifact_seed(20, 50.0),
	]
	motion = _artifact_motion(100)
	transform = scene_coords.SceneTransform(motion)
	path_a = [
		{"cx": 10.0 + 2.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(11)
	]
	path_b = [
		{"cx": 30.0 + 2.0 * i, "cy": 50.0, "w": 10.0, "h": 20.0}
		for i in range(11)
	]
	artifact = {
		"scene_transform": transform, "fps": 30.0,
		"video_identity": {"frame_count": 100},
		"race_start": {"race_start_frame": 15, "race_start_interval": [10, 20]},
		"solved_intervals": {
			"before": {"start_frame": 0, "end_frame": 10,
				"forward_path": path_a, "backward_path": path_a, "blended_path": path_a},
			"spanning": {"start_frame": 10, "end_frame": 20,
				"forward_path": path_b, "backward_path": path_b, "blended_path": path_b},
		},
	}
	view = review.build_interval_risk_view(seeds, motion, artifact)
	assert view[(0, 10)]["severity"] == "pre_race"
	assert view[(10, 20)]["severity"] != "pre_race"
