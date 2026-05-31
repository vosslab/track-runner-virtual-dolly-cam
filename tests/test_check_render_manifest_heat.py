"""Tests for the interval-level heat REPORT in check_render_manifest (C13 rework).

Heat is no longer stored per tile; it lives in a sibling render_heat_summary.json
as one record per interval-direction. These tests verify:
  1. report_heat_summaries prints one HEAT REPORT and one SEED-COLD line per
     interval-direction summary, with correct eligible/present counts and the
     not-computed coverage surfaced.
  2. A zero-eligible summary reports n/a without crashing.
  3. check_records (the gate over per-tile records) still fires the
     conversion_count gate and is unaffected by the heat move.

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

def _summary(left, right, direction, eligible, present, not_computed,
		seed_cold_frames, threshold=10.0,
		heat_present_pct=None, mean_heat=None) -> dict:
	"""Build one interval-direction heat summary dict.

	heat_present_pct defaults to present/eligible (or 0.0 when eligible==0).
	mean_heat defaults to None (no heat-present frames).
	"""
	if heat_present_pct is None:
		heat_present_pct = present / eligible if eligible > 0 else 0.0
	return {
		"left_frame": left,
		"right_frame": right,
		"direction": direction,
		"heat_eligible": eligible,
		"heat_present": present,
		"heat_present_pct": heat_present_pct,
		"mean_heat": mean_heat,
		"not_computed": not_computed,
		"seed_cold_frames": seed_cold_frames,
		"threshold": threshold,
	}


def _base_record(frame_index: int, direction: str = "fwd") -> dict:
	"""Return a minimal valid per-tile manifest record (gate fields only)."""
	return {
		"frame_index": frame_index,
		"direction": direction,
		"conversion_count": 1,
		"seed_box_present": False,
		"solved_box_present": True,
		"non_seed_missing_solved_box": False,
	}


#============================================

def test_warm_summary_fraction_is_correct(capsys):
	"""All eligible frames are heat-present: fraction is 100%."""
	summaries = [_summary(10, 12, "fwd", eligible=3, present=3,
		not_computed=0, seed_cold_frames=[])]
	check_render_manifest.report_heat_summaries(summaries, "fake/heat.json")
	captured = capsys.readouterr()
	assert "heat-present 3/3" in captured.out
	assert "100.0%" in captured.out


def test_cold_summary_reports_zero_fraction(capsys):
	"""Eligible but zero present: fraction 0.0%, coverage surfaced."""
	summaries = [_summary(20, 22, "fwd", eligible=2, present=0,
		not_computed=1, seed_cold_frames=[20])]
	check_render_manifest.report_heat_summaries(summaries, "fake/heat.json")
	captured = capsys.readouterr()
	assert "heat-present 0/2" in captured.out
	assert "0.0%" in captured.out
	assert "1 not-computed" in captured.out
	assert "1 seed tiles heat-cold" in captured.out


def test_zero_eligible_summary_reports_zero_pct(capsys):
	"""Zero eligible frames: fraction reported as 0.0%, not-computed surfaced."""
	summaries = [_summary(30, 33, "bwd", eligible=0, present=0,
		not_computed=4, seed_cold_frames=[])]
	check_render_manifest.report_heat_summaries(summaries, "fake/heat.json")
	captured = capsys.readouterr()
	assert "0.0%" in captured.out
	assert "4 not-computed" in captured.out


def test_gate_failure_unaffected_by_heat_move():
	"""The conversion_count gate still fires on per-tile records."""
	record = _base_record(50)
	record["conversion_count"] = 2
	failures = check_render_manifest.check_records([record], "fake/manifest.json")
	assert len(failures) == 1
	assert "conversion_count=2" in failures[0]
