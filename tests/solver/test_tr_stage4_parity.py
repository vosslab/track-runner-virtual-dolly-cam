"""Tests for Stage-4 interval promotion selection.

Covers select_promoted_intervals and PROMOTION_TIERS: which intervals are
re-solved in Stage 4 based on confidence tier and pre-race exclusion.
"""

# local repo modules
import interval_solver


#============================================
def test_select_promoted_intervals_filters_low_fair() -> None:
	"""select_promoted_intervals picks only low and fair confidence tiers."""
	interval_results = [
		{"start_frame": 0, "end_frame": 10, "interval_score": {"confidence_tier": "high"}},
		{"start_frame": 10, "end_frame": 20, "interval_score": {"confidence_tier": "fair"}},
		{"start_frame": 20, "end_frame": 30, "interval_score": {"confidence_tier": "low"}},
		{"start_frame": 30, "end_frame": 40, "interval_score": {"confidence_tier": "good"}},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral property: every promoted index has a low or fair tier.
	for idx in promoted:
		tier = interval_results[idx]["interval_score"]["confidence_tier"]
		assert tier in {"low", "fair"}, f"index {idx} has tier {tier}"
	# Behavioral property: no high/good tier slipped in.
	for idx, result in enumerate(interval_results):
		if result["interval_score"]["confidence_tier"] not in {"low", "fair"}:
			assert idx not in promoted


#============================================
def test_select_promoted_intervals_excludes_pre_race() -> None:
	"""Pre-race intervals are never promoted (Contract C4)."""
	interval_results = [
		{
			"start_frame": 0, "end_frame": 5,
			"source": "pre_race_reference",
			"interval_score": {"confidence_tier": "low"},
		},
		{
			"start_frame": 5, "end_frame": 15,
			"interval_score": {"confidence_tier": "low"},
		},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral: no pre-race interval was promoted; the non-pre-race one was.
	for idx in promoted:
		assert interval_results[idx].get("source") != "pre_race_reference"
	assert 1 in promoted


#============================================
def test_select_promoted_intervals_skips_none() -> None:
	"""None entries (e.g. quit in progress) are skipped without raising."""
	interval_results = [
		{"start_frame": 0, "end_frame": 10, "interval_score": {"confidence_tier": "low"}},
		None,
		{"start_frame": 20, "end_frame": 30, "interval_score": {"confidence_tier": "fair"}},
	]

	promoted = interval_solver.select_promoted_intervals(interval_results)

	# Behavioral: None index is not promoted; valid low/fair entries are.
	assert 1 not in promoted
	for idx in promoted:
		assert interval_results[idx] is not None
		tier = interval_results[idx]["interval_score"]["confidence_tier"]
		assert tier in {"low", "fair"}


#============================================
def test_promotion_tiers_contains_low_and_fair() -> None:
	"""PROMOTION_TIERS includes the two tiers that should be re-solved."""
	assert "low" in interval_solver.PROMOTION_TIERS
	assert "fair" in interval_solver.PROMOTION_TIERS
