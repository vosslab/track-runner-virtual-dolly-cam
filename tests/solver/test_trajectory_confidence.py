"""Tests for raw-pass torso-unit confidence ownership."""

# Standard Library
import math

# local repo modules
import trajectory_confidence


#============================================
def _state(cx: float, cy: float, width: float = 20.0) -> dict:
	"""Build a raw pass state with deliberately irrelevant output confidence."""
	return {"cx": cx, "cy": cy, "w": width, "h": 100.0, "conf": 0.01}


#============================================
def test_frame_confidence_is_invariant_to_raw_pass_scale() -> None:
	"""The same center separation in torso widths has the same confidence."""
	base = trajectory_confidence.frame_confidence(_state(0.0, 0.0, 20.0), _state(10.0, 0.0, 20.0))
	scaled = trajectory_confidence.frame_confidence(_state(0.0, 0.0, 80.0), _state(40.0, 0.0, 80.0))
	assert math.isclose(base, scaled)
	assert math.isclose(base, math.exp(-0.5))


#============================================
def test_frame_confidence_uses_mean_raw_width_and_rejects_invalid_width() -> None:
	"""Raw widths, rather than a blended box or raw pixel distance, set scale."""
	confidence = trajectory_confidence.frame_confidence(
		_state(0.0, 0.0, 20.0), _state(30.0, 0.0, 40.0),
	)
	assert math.isclose(confidence, math.exp(-1.0))
	assert trajectory_confidence.frame_confidence(_state(0.0, 0.0, 0.0), _state(0.0, 0.0)) == 0.0


#============================================
def test_interval_agreement_and_derived_confidence_use_only_raw_passes() -> None:
	"""Raw-path agreement ignores conflicting blended output and handles pre-race."""
	fwd = [_state(0.0, 0.0), _state(20.0, 0.0)]
	bwd = [_state(0.0, 0.0), _state(40.0, 0.0)]
	agreement = trajectory_confidence.interval_agreement(fwd, bwd)
	assert math.isclose(agreement, (1.0 + math.exp(-1.0)) / 2.0)
	results = [
		{
			"start_frame": 1,
			"end_frame": 2,
			"forward_path": fwd,
			"backward_path": bwd,
			"blended_path": [_state(999.0, 999.0), _state(999.0, 999.0)],
		},
		{"start_frame": 4, "end_frame": 5},
	]
	assert trajectory_confidence.derive_per_frame_confidence(results, 7) == [
		0.0, 1.0, math.exp(-1.0), 0.0, 1.0, 1.0, 0.0,
	]


#============================================
def test_apply_confidence_replaces_stale_stored_values() -> None:
	"""The shared consumer boundary replaces cached output confidence."""
	fwd = [_state(0.0, 0.0), _state(20.0, 0.0)]
	bwd = [_state(0.0, 0.0), _state(40.0, 0.0)]
	results = [{"start_frame": 0, "end_frame": 1,
		"forward_path": fwd, "backward_path": bwd}]
	trajectory = [_state(999.0, 999.0), _state(999.0, 999.0)]
	trajectory_confidence.apply_per_frame_confidence(results, trajectory)
	assert [state["conf"] for state in trajectory] == [
		1.0, math.exp(-1.0),
	]
