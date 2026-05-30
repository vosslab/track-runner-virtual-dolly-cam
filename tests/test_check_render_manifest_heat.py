"""Fixture-based tests for the heat-present fraction REPORT in check_render_manifest.

Three synthetic intervals verify the eligibility rules and the exit-code contract:
  1. Warm interval: all eligible tiles are heat-present -> fraction 1.0.
  2. Computed-cold interval: eligible (computed=true) but present=false -> fraction 0.0.
     Exit code must be UNCHANGED (PASS) because cold tiles are not a gate failure.
  3. Not-computed interval: computed=false tiles are excluded from the denominator.
     Zero eligible tiles -> fraction reported as n/a; exit code UNCHANGED.

Also verifies that older manifests (no in_box_heat_computed field) skip the heat
report cleanly without crashing.

All tests are offline, deterministic, and finish in well under one second.
"""

# Standard Library
import sys
import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / 'tools' / 'blob_walk_v2'
if str(_TOOLS_DIR) not in sys.path:
	sys.path.insert(0, str(_TOOLS_DIR))

import check_render_manifest


#============================================

def _base_record(frame_index: int, direction: str = "fwd") -> dict:
	"""Return a minimal valid manifest record with the two existing gate fields."""
	return {
		"frame_index": frame_index,
		"direction": direction,
		"conversion_count": 1,
		"seed_box_present": False,
		"solved_box_present": True,
		"non_seed_missing_solved_box": False,
	}


def _with_heat(record: dict, computed: bool, present: bool, threshold: float = 10.0) -> dict:
	"""Add the M1-B heat fields to a record dict."""
	record["in_box_heat_computed"] = computed
	record["in_box_heat_present"] = present
	record["in_box_hot_count"] = 3 if present else 0
	record["in_box_hot_mean"] = 15.2 if present else None
	record["heat_threshold_used"] = threshold
	return record


#============================================

def test_warm_interval_fraction_is_correct(capsys):
	"""All eligible tiles are heat-present: fraction should be 1.0 (100%)."""
	records = [
		_with_heat(_base_record(10), computed=True, present=True),
		_with_heat(_base_record(11), computed=True, present=True),
		_with_heat(_base_record(12), computed=True, present=True),
	]
	failures = check_render_manifest.check_records(records, "fake/manifest.json")
	captured = capsys.readouterr()
	# No gate failures.
	assert failures == []
	# The heat report line must show 3/3 eligible and 100% fraction.
	assert "3/3 eligible" in captured.out
	assert "100.0%" in captured.out


def test_computed_cold_fraction_is_zero_exit_unaffected(capsys):
	"""Computed-cold tiles (computed=true, present=false) stay in denominator.

	Fraction is 0/2 (0.0%). Exit code is unchanged -- check_records still returns
	an empty failure list, so the process would exit 0.
	"""
	records = [
		_with_heat(_base_record(20), computed=True, present=False),
		_with_heat(_base_record(21), computed=True, present=False),
	]
	failures = check_render_manifest.check_records(records, "fake/manifest.json")
	captured = capsys.readouterr()
	# Cold tiles do NOT produce gate failures -- exit code is unchanged.
	assert failures == []
	# Report line must show 0/2 eligible (denominator includes cold tiles).
	assert "0/2 eligible" in captured.out
	assert "0.0%" in captured.out


def test_not_computed_excluded_from_denominator(capsys):
	"""Not-computed tiles (computed=false) are excluded from the denominator.

	With zero eligible tiles the fraction is reported as n/a and no failures are
	produced (exit code unchanged).
	"""
	records = [
		_with_heat(_base_record(30), computed=False, present=False),
		_with_heat(_base_record(31), computed=False, present=False),
	]
	failures = check_render_manifest.check_records(records, "fake/manifest.json")
	captured = capsys.readouterr()
	# Not-computed tiles do NOT produce gate failures.
	assert failures == []
	# Report line must mention 0 eligible tiles.
	assert "0/0 eligible" in captured.out or "n/a" in captured.out
	# Both tiles should appear in the skipped (not-computed) count.
	assert "2 not-computed" in captured.out


def test_older_manifest_without_heat_fields_skips_cleanly(capsys):
	"""Manifests without in_box_heat_computed skip the heat report without crashing."""
	# These records have no heat fields at all (pre-M1-B manifest).
	records = [
		_base_record(40),
		_base_record(41),
	]
	failures = check_render_manifest.check_records(records, "fake/old_manifest.json")
	captured = capsys.readouterr()
	# No failures from the two existing gates.
	assert failures == []
	# No HEAT REPORT line should appear (older manifest silently skips).
	assert "HEAT REPORT" not in captured.out


def test_existing_gate_failures_unaffected_by_heat(capsys):
	"""The two existing gate failures are produced regardless of heat fields.

	Specifically: conversion_count != 1 still fires and is not silenced by the
	heat report being present.
	"""
	# One record with bad conversion_count and valid heat fields.
	record = _with_heat(_base_record(50), computed=True, present=True)
	record["conversion_count"] = 2
	failures = check_render_manifest.check_records([record], "fake/manifest.json")
	# The conversion-count gate must still fire.
	assert len(failures) == 1
	assert "conversion_count=2" in failures[0]
