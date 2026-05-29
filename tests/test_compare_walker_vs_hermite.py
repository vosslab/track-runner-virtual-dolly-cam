"""Tests for tools/blob_walk_v2/compare_walker_vs_hermite.py classifier.

Four tests covering the multi-metric classification logic:
  - test_classifier_walker_better
  - test_classifier_tie
  - test_classifier_inconclusive
  - test_classifier_walker_better_with_regression_in_third

These tests exercise classify_interval() directly with synthetic metrics.
No file I/O; no video decode; no walker re-run.
"""

# Standard Library
import sys
import os

# local repo modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'blob_walk_v2'))
import compare_walker_vs_hermite


#============================================
def _make_metrics(pct: float, var: float, ang: float) -> dict:
	"""Build a minimal walk metrics dict with the three primary plausibility fields.

	Args:
		pct: windowed_speed_in_range_fraction (higher is better).
		var: windowed_vel_mag_variance_median (lower is better).
		ang: windowed_vel_angle_spread_median (lower is better).

	Returns:
		Dict with the three primary metric keys set.
	"""
	return {
		"windowed_speed_in_range_fraction": pct,
		"windowed_vel_mag_variance_median": var,
		"windowed_vel_angle_spread_median": ang,
	}


#============================================
def test_classifier_walker_better():
	"""Walker improves all 3 metrics; expect walker_better."""
	# Walker: higher pct, lower var, lower ang (all improvements by >> tolerance)
	walker = _make_metrics(pct=0.80, var=0.10, ang=0.20)
	hermite = _make_metrics(pct=0.40, var=0.50, ang=0.60)
	result = compare_walker_vs_hermite.classify_interval(walker, hermite)
	assert result == "walker_better", f"Expected 'walker_better', got '{result}'"


#============================================
def test_classifier_tie():
	"""Each solver improves exactly 1 metric; expect tie.

	Walker improves pct (higher is better).
	Hermite improves var (lower is better).
	ang is within tolerance (tie on the third metric).
	Neither side reaches 2+ improvements.
	"""
	# Walker improves pct by 0.2 (>> tol=0.05)
	# Hermite improves var by 0.2 (>> tol)
	# ang: walker=0.30, hermite=0.30 -> tie
	walker = _make_metrics(pct=0.80, var=0.50, ang=0.30)
	hermite = _make_metrics(pct=0.60, var=0.30, ang=0.30)
	result = compare_walker_vs_hermite.classify_interval(walker, hermite)
	assert result == "tie", f"Expected 'tie', got '{result}'"


#============================================
def test_classifier_inconclusive():
	"""Hermite metrics are nan (trajectory missing); expect inconclusive."""
	walker = _make_metrics(pct=0.80, var=0.10, ang=0.20)
	# Simulate missing hermite data: all nan
	hermite = _make_metrics(pct=float("nan"), var=float("nan"), ang=float("nan"))
	result = compare_walker_vs_hermite.classify_interval(walker, hermite)
	assert result == "inconclusive", f"Expected 'inconclusive', got '{result}'"


#============================================
def test_classifier_walker_better_with_regression_in_third():
	"""Walker improves 2 metrics but regresses 3rd by 10% (above 5% tolerance).

	Walker improves pct (+0.30) and var (-0.20).
	Walker regresses ang by +0.10 (hermite has lower ang by 0.10 >> tol=0.05).
	Expect tie (regression in the non-improved metric blocks walker_better).
	"""
	# pct: walker=0.80, hermite=0.50 -> walker improves pct (delta=0.30)
	# var: walker=0.10, hermite=0.30 -> walker improves var (delta=-0.20)
	# ang: walker=0.50, hermite=0.40 -> hermite better on ang by 0.10 > tol(0.05)
	#      => walker regresses ang beyond tolerance
	walker = _make_metrics(pct=0.80, var=0.10, ang=0.50)
	hermite = _make_metrics(pct=0.50, var=0.30, ang=0.40)
	result = compare_walker_vs_hermite.classify_interval(walker, hermite)
	# Walker improves 2 metrics but worsens the 3rd by 0.10 > tolerance(0.05),
	# so the walker_better condition fails. Expected: tie.
	assert result in ("tie", "inconclusive"), (
		f"Expected 'tie' or 'inconclusive' (regression blocks walker_better), got '{result}'"
	)
