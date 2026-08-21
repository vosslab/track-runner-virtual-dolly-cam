"""Unit tests for solve_queue interval result formatters.

Locks the per-interval line format for Stage 3 and Stage 4 output.
Tests use synthetic result dicts so no real solver run is required.
"""

# local repo modules (bare imports resolved by conftest.py)
import solve_queue


#============================================
def _make_stage3_result(
	start_frame: int,
	end_frame: int,
	agreement: float,
	velocity_consistency: float,
	size_consistency: float,
	confidence_tier: str,
	failure_reasons: list,
) -> dict:
	"""Build a minimal Stage 3 v3 interval result dict."""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"blended_path": [],
		"forward_path": [],
		"backward_path": [],
		"interval_score": {
			"confidence_tier": confidence_tier,
			"agreement": agreement,
			"velocity_consistency": velocity_consistency,
			"size_consistency": size_consistency,
			"failure_reasons": failure_reasons,
		},
	}


#============================================
def _make_stage4_result(
	start_frame: int,
	end_frame: int,
	agreement: float,
	velocity_consistency: float,
	size_consistency: float,
	confidence_tier: str,
	failure_reasons: list,
) -> dict:
	"""Build a minimal Stage 4 v3 interval result dict."""
	return {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"blended_path": [],
		"forward_path": [],
		"backward_path": [],
		"interval_score": {
			"confidence_tier": confidence_tier,
			"agreement": agreement,
			"velocity_consistency": velocity_consistency,
			"size_consistency": size_consistency,
			"failure_reasons": failure_reasons,
		},
	}


#============================================
def test_stage3_line_has_no_blob_accept_and_ends_with_stage3_tag() -> None:
	"""Stage 3 formatter must not emit blob_accept and must end with [stage3]."""
	result = _make_stage3_result(
		start_frame=16380,
		end_frame=16800,
		agreement=0.01,
		velocity_consistency=1.0,
		size_consistency=0.35,
		confidence_tier="low",
		failure_reasons=["overlap"],
	)
	line = solve_queue._format_interval_result(result, fps=60.0)
	# blob_accept must not appear: Stage 3 is Hermite-only, no real blob data
	assert "blob_accept" not in line
	# must carry the stage tag so solve log is scannable
	assert line.endswith("[stage3]")
	# must identify the interval by frame range
	assert "interval 16380-16800" in line
	assert "agreement=0.01" in line
	assert "overlap=" not in line


#============================================
def test_stage4_line_format() -> None:
	"""Stage 4 formatter must include delta, confidence label, [stage4] tag."""
	result = _make_stage4_result(
		start_frame=16380,
		end_frame=16800,
		agreement=0.42,
		velocity_consistency=1.00,
		size_consistency=0.71,
		confidence_tier="good",
		failure_reasons=[],
	)
	# Stage 3 baseline with lower scores
	baseline_score = {
		"confidence_tier": "low",
		"agreement": 0.01,
		"velocity_consistency": 1.0,
		"size_consistency": 0.35,
	}
	line = solve_queue._format_stage4_interval_result(result, baseline_score, fps=60.0)
	# must start with interval prefix (leading spaces are formatting)
	assert line.lstrip().startswith("interval ")
	# must carry the stage4 tag
	assert line.endswith("[stage4]")
	# agreement delta: 0.42 - 0.01 = +0.41
	assert "(+0.41)" in line
	assert "agreement=0.42" in line
	# confidence label for "good" tier
	assert "[GOOD]" in line


#============================================
def test_stage4_line_omits_delta_when_baseline_missing() -> None:
	"""Stage 4 formatter must omit delta parentheticals when baseline is None."""
	result = _make_stage4_result(
		start_frame=16380,
		end_frame=16800,
		agreement=0.42,
		velocity_consistency=1.00,
		size_consistency=0.71,
		confidence_tier="good",
		failure_reasons=[],
	)
	line = solve_queue._format_stage4_interval_result(result, None, fps=60.0)
	# no delta parentheticals when baseline is unavailable
	assert "(+" not in line and "(-" not in line
	# tag must still appear
	assert line.endswith("[stage4]")
