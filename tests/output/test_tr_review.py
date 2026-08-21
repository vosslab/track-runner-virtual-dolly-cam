"""Unit tests for severity classification and interval ranking.

Tests only behavioral invariants that do not depend on tunable
thresholds. Synthetic interval dicts are used; no video, disk I/O, or
mocking.
"""

# local repo modules
import review


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
