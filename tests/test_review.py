"""Unit tests for severity classification and interval ranking in review module.

Tests the three-band motion-cue gate rule and lexicographic ranking function
introduced in Patch 4 (severity rework plan). Uses synthetic interval dicts
without video, disk I/O, or mocking.

Imports follow the conftest.py pattern: bare module imports are resolved by
adding track_runner/ to sys.path.
"""

# Standard Library
# (none needed beyond builtins)

# local repo modules
import review


#============================================
def _make_interval(
	start_frame=1000,
	end_frame=1050,
	motion_cue_acceptance=0.5,
	agreement=0.5,
	confidence_tier="fair",
	failure_reasons=None,
):
	"""Build a minimal interval dict for testing severity classification.

	Args:
		start_frame: Start frame index (default 1000).
		end_frame: End frame index (default 1050 = 50 frames ~1.7s at 30fps).
		motion_cue_acceptance: Motion fusion acceptance rate [0, 1].
		agreement: FWD/BWD agreement aggregate [0, 1].
		confidence_tier: Confidence tier string ("low", "fair", "good", "high").
		failure_reasons: List of failure reason strings (or None for empty).

	Returns:
		Interval dict with interval_score sub-dict containing all fields.
	"""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"interval_score": {
			"motion_cue_acceptance": motion_cue_acceptance,
			"agreement": agreement,
			"confidence_tier": confidence_tier,
			"failure_reasons": failure_reasons or [],
		},
	}


#============================================
# Severity classification tests (seven total)
#============================================

def test_severity_identity_swap_overrides():
	"""Identity swap is always high severity, hard-override rule.

	Even with strong motion corroboration (0.85) and good agreement
	(0.50), presence of likely_identity_swap in failure_reasons
	forces the severity to "high" because motion-cue fusion has no
	identity check.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.85,
		agreement=0.50,
		confidence_tier="fair",
		failure_reasons=["likely_identity_swap"],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "high"


def test_severity_strong_corroboration_low():
	"""Strong motion acceptance + passing agreement sanity check -> low.

	motion >= 0.70 AND agreement >= 0.15 is the strong corroboration
	band: fusion accepted the majority of frames and FWD/BWD broadly
	agree, so the interval is working and needs no seed.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.85,
		agreement=0.50,
		confidence_tier="fair",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "low"


def test_severity_motion_high_agreement_bad():
	"""Motion >= 0.70 but agreement < 0.15 -> medium (not low).

	Strong motion acceptance alone is not enough; agreement sanity
	check must pass (>= 0.15). When motion is high but FWD/BWD
	disagree, motion-only corroboration could be misleading, so
	severity stays medium.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.85,
		agreement=0.05,
		confidence_tier="fair",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "medium"


def test_severity_mid_band_medium():
	"""Motion in [0.40, 0.70) band -> medium.

	The moderate corroboration band: fusion accepted a meaningful
	fraction (40-70%) of frames. Severity is medium regardless of
	confidence tier or agreement, signaling "ambiguous, might be
	worth checking".
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.55,
		agreement=0.2,  # default, fine for this test
		confidence_tier="low",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "medium"


def test_severity_catastrophic_high():
	"""Motion < 0.40 + low confidence + low agreement -> high.

	Below 40% acceptance, the only high-severity path requires BOTH
	confidence AND agreement to be catastrophic. This is the only
	case where very low motion-cue acceptance alone triggers high.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.20,
		agreement=0.10,
		confidence_tier="low",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "high"


def test_severity_low_tier_decent_agreement():
	"""Motion < 0.40 + low confidence but agreement decent -> medium.

	Even though confidence is low, agreement is 0.45 (decent), so the
	interval is ambiguous, not broken. Asymmetric rule: both must fail
	for high. Returns medium.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.20,
		agreement=0.45,
		confidence_tier="low",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "medium"


def test_severity_fair_tier_low_agreement():
	"""Motion < 0.40 + fair confidence + low agreement -> medium.

	Fair confidence (not low) is not enough to reach high severity,
	even with low agreement. Fair + low = borderline + borderline =
	uncertain, not broken. Asymmetric rule requires low confidence.
	Returns medium.
	"""
	interval = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.20,
		agreement=0.10,
		confidence_tier="fair",
		failure_reasons=[],
	)
	severity = review.classify_interval_severity(interval, fps=30.0)
	assert severity == "medium"


#============================================
# Rank key ordering tests (three total)
#============================================

def test_rank_motion_dominates():
	"""Lower motion_cue_acceptance sorts first (worse-first ordering).

	When two intervals have identical agreement and confidence_tier,
	the interval with lower motion acceptance is the worse one and
	sorts first in ascending (worst-first) order.
	"""
	iv_low_motion = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.30,
		agreement=0.40,
		confidence_tier="fair",
	)
	iv_high_motion = _make_interval(
		start_frame=1100,
		end_frame=1150,
		motion_cue_acceptance=0.70,
		agreement=0.40,
		confidence_tier="fair",
	)
	sorted_list = sorted(
		[iv_high_motion, iv_low_motion],
		key=review.rank_key,
	)
	assert sorted_list[0] is iv_low_motion
	assert sorted_list[1] is iv_high_motion


def test_rank_agreement_tiebreaks_motion():
	"""When motion is tied, lower agreement sorts first (worse-first).

	Agreement is the second element of the rank key tuple. Two intervals
	with identical motion but different agreement are ordered by
	agreement ascending.
	"""
	iv_low_agreement = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.50,
		agreement=0.20,
		confidence_tier="fair",
	)
	iv_high_agreement = _make_interval(
		start_frame=1100,
		end_frame=1150,
		motion_cue_acceptance=0.50,
		agreement=0.60,
		confidence_tier="fair",
	)
	sorted_list = sorted(
		[iv_high_agreement, iv_low_agreement],
		key=review.rank_key,
	)
	assert sorted_list[0] is iv_low_agreement
	assert sorted_list[1] is iv_high_agreement


def test_rank_confidence_tier_final_tiebreak():
	"""When motion and agreement tied, lower confidence_tier sorts first.

	Confidence tier rank follows the order: "low" (0) < "fair" (1) <
	"good" (2) < "high" (3). Two intervals with identical motion and
	agreement but different confidence tiers are ordered by tier rank.
	"""
	iv_low_tier = _make_interval(
		start_frame=1000,
		end_frame=1050,
		motion_cue_acceptance=0.50,
		agreement=0.40,
		confidence_tier="low",
	)
	iv_fair_tier = _make_interval(
		start_frame=1100,
		end_frame=1150,
		motion_cue_acceptance=0.50,
		agreement=0.40,
		confidence_tier="fair",
	)
	iv_good_tier = _make_interval(
		start_frame=1200,
		end_frame=1250,
		motion_cue_acceptance=0.50,
		agreement=0.40,
		confidence_tier="good",
	)
	iv_high_tier = _make_interval(
		start_frame=1300,
		end_frame=1350,
		motion_cue_acceptance=0.50,
		agreement=0.40,
		confidence_tier="high",
	)
	sorted_list = sorted(
		[iv_high_tier, iv_fair_tier, iv_good_tier, iv_low_tier],
		key=review.rank_key,
	)
	assert sorted_list[0] is iv_low_tier
	assert sorted_list[1] is iv_fair_tier
	assert sorted_list[2] is iv_good_tier
	assert sorted_list[3] is iv_high_tier
