"""Unit tests for severity classification and interval ranking.

Tests only behavioral invariants that do not depend on tunable
thresholds. Synthetic interval dicts are used; no video, disk I/O, or
mocking.
"""

# local repo modules
import review


#============================================
def _make_interval(
	start_frame=1000,
	end_frame=1050,
	agreement=0.5,
	confidence_tier="fair",
	failure_reasons=None,
):
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
def test_severity_identity_swap_overrides():
	"""Identity swap always produces high severity, regardless of other signals.

	Behavioral invariant: the hard-override rule must dominate even
	when agreement and confidence look strong.
	"""
	interval = _make_interval(
		agreement=0.80,
		confidence_tier="high",
		failure_reasons=["likely_identity_swap"],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "high"


#============================================
def test_rank_lower_agreement_sorts_first():
	"""Lower agreement sorts before higher agreement (worst-first).

	Behavioral property on relative ordering, not a threshold value.
	"""
	iv_worse = _make_interval(agreement=0.20, confidence_tier="fair")
	iv_better = _make_interval(agreement=0.60, confidence_tier="fair")
	sorted_list = sorted([iv_better, iv_worse], key=review.rank_key)
	assert sorted_list[0] is iv_worse
	assert sorted_list[1] is iv_better


#============================================
def test_rank_confidence_tier_orders_ties():
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
