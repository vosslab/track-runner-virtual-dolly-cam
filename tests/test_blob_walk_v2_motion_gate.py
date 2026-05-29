"""
Unit tests for tools/blob_walk_v2/walk_motion_gate.py.

Tests cover cold-start, centroid jitter, cap-driven rejection, and parameter
scaling. Per-frame motion budget at 60 fps:
    per_step_cap_per_frame = MAX_RUNNER_SPEED_W_PER_S / source_fps
                           = 30.0 / 60 = 0.5 W/frame
So at torso_w = 100 px the per_step_cap at dt=1 is 50 px.
"""

import pytest
from tools.blob_walk_v2.walk_motion_gate import (
	evaluate,
	MotionGateResult,
	MAX_RUNNER_SPEED_W_PER_S,
	MIN_RUNNER_SPEED_W_PER_S,
	BOOTSTRAP_UNCERTAINTY_W,
	max_runner_jump_per_frame,
	clamp_velocity_w_per_s,
	bootstrap_search_radius_w,
)


class TestPhysicalConstants:
	"""Pin physical envelope constants and derivations."""

	def test_max_speed_30_w_per_s(self):
		assert MAX_RUNNER_SPEED_W_PER_S == 30.0

	def test_min_speed_7_3_w_per_s(self):
		assert MIN_RUNNER_SPEED_W_PER_S == 7.3

	def test_bootstrap_slack_pinned(self):
		assert BOOTSTRAP_UNCERTAINTY_W == 0.30

	def test_per_frame_derivation(self):
		assert max_runner_jump_per_frame(30.0) == pytest.approx(1.0)
		assert max_runner_jump_per_frame(60.0) == pytest.approx(0.5)
		assert max_runner_jump_per_frame(120.0) == pytest.approx(0.25)

	def test_clamp_below_min(self):
		assert clamp_velocity_w_per_s(1.0) == 7.3

	def test_clamp_above_max(self):
		assert clamp_velocity_w_per_s(99.0) == 30.0

	def test_clamp_within_range(self):
		assert clamp_velocity_w_per_s(15.0) == 15.0

	def test_bootstrap_search_radius_60fps(self):
		# Constant radius: 30/60 + 0.30 = 0.5 + 0.30 = 0.80 W (all steps).
		assert bootstrap_search_radius_w(60.0) == pytest.approx(0.80)

	def test_bootstrap_search_radius_30fps(self):
		# 30/30 + 0.30 = 1.0 + 0.30 = 1.30 W (all steps).
		assert bootstrap_search_radius_w(30.0) == pytest.approx(1.30)


class TestMotionGateResult:
	"""Test MotionGateResult dataclass."""

	def test_result_is_dataclass(self):
		"""MotionGateResult has required fields."""
		result = MotionGateResult(
			accepted=True,
			expected_jump=10.0,
			allowed_jump=20.0,
			actual_jump=15.0,
			v_recent_scene_mag=5.0,
			dt_for_gate=1,
			reject_reason="",
		)
		assert result.accepted is True
		assert result.expected_jump == 10.0
		assert result.allowed_jump == 20.0
		assert result.actual_jump == 15.0
		assert result.v_recent_scene_mag == 5.0
		assert result.dt_for_gate == 1
		assert result.reject_reason == ""


class TestColdStartAccept:
	"""Test cold-start accept under chord-velocity seed."""

	def test_cold_start_accept_chord_velocity(self):
		"""
		Accept cold-start motion under seed-chord velocity.

		After bootstrap, the walker seeds v_recent_scene_mag from the
		seed-to-neighbor chord distance over frame count.
		Test: typical 100-frame interval, 400 px chord, v_recent = 4 px/frame.
		Torso 100 px, dt=1, should accept reasonable candidate near expected position.
		"""
		prev_scene = (100.0, 200.0)
		cand_scene = (104.0, 202.0)  # +4 px in x, +2 px in y = ~4.47 px total
		v_recent_scene_mag = 4.0  # pixels per frame
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		assert result.accepted is True
		assert result.reject_reason == ""
		assert result.dt_for_gate == 1


class TestCentroidJitter:
	"""Test acceptance of pure centroid jitter under MEASUREMENT_ALLOWANCE_W."""

	def test_jitter_under_allowance_stationary(self):
		"""
		Accept stationary runner's centroid jitter without motion.

		Zero velocity, zero radial drift, tiny blob offset < MEASUREMENT_ALLOWANCE_W.
		"""
		prev_scene = (100.0, 200.0)
		cand_scene = (100.8, 200.4)  # ~0.9 px offset
		v_recent_scene_mag = 0.0
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		# measurement_allowance = 0.10 * 100 = 10 px
		# actual_jump ~ 0.9 px < 10 px -> accept
		assert result.accepted is True
		assert result.reject_reason == ""


class TestAbsoluteCapReject:
	"""Test rejection when actual_jump > ABSOLUTE_MAX_JUMP_W * torso_w."""

	def test_reject_absolute_cap(self):
		"""
		Reject when actual displacement exceeds absolute_cap.

		Set v_recent huge to bypass velocity gate, but actual_jump > 1.5 * torso_w.
		"""
		prev_scene = (0.0, 0.0)
		# actual_jump = sqrt(400^2 + 0^2) = 400 px
		cand_scene = (400.0, 0.0)
		v_recent_scene_mag = 1000.0  # huge velocity
		dt_frames = 1
		torso_w = 200.0  # absolute_cap = 1.5 * 200 = 300 px
		torso_w_drift_frac = 0.0

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		assert result.accepted is False
		assert result.reject_reason == "absolute_cap"
		assert result.actual_jump > result.allowed_jump


class TestVelocityToleranceReject:
	"""Test rejection when actual_jump exceeds velocity_tolerance * expected_jump."""

	def test_reject_velocity_tolerance(self):
		"""
		Reject when actual_jump > VELOCITY_TOLERANCE * expected_jump,
		but actual_jump < per_step_cap and < absolute_cap.
		"""
		prev_scene = (0.0, 0.0)
		cand_scene = (50.0, 0.0)  # actual_jump = 50 px
		v_recent_scene_mag = 10.0  # pixels per frame
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		# expected_jump = 10 * 1 + 10 + 0 = 20 px
		# per_step_cap = 0.75 * 100 * 1 = 75 px
		# absolute_cap = 1.5 * 100 = 150 px
		# allowed_jump = min(150, 75, 20 * 1.75) = min(150, 75, 35) = 35 px
		# actual_jump = 50 px > 35 px -> reject velocity_tolerance

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		assert result.accepted is False
		assert result.reject_reason == "velocity_tolerance"


class TestPerStepCapReject:
	"""Test rejection when actual_jump > per_step_cap (but < absolute_cap)."""

	def test_reject_per_step_cap(self):
		"""
		Reject when per_step_cap is the binding constraint.

		Set dt_frames high (large per_step_cap allowance) and actual_jump
		between per_step_cap and absolute_cap.
		"""
		prev_scene = (0.0, 0.0)
		cand_scene = (200.0, 0.0)  # actual_jump = 200 px
		v_recent_scene_mag = 100.0  # high velocity
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		# per_step_cap = 0.75 * 100 * 1 = 75 px
		# absolute_cap = 1.5 * 100 = 150 px
		# expected_jump = 100 * 1 + 10 + 0 = 110 px
		# allowed_jump = min(150, 75, 110 * 1.75) = min(150, 75, 192.5) = 75 px
		# actual_jump = 200 px > 150 px absolute_cap -> reject absolute_cap (first hit)

		# Adjust to land between per_step and absolute caps:
		# Let's set actual_jump = 120 px (between 75 and 150).
		cand_scene = (120.0, 0.0)

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		# actual_jump = 120 px
		# per_step_cap = 75 px < 120 px
		# absolute_cap = 150 px >= 120 px
		# So per_step_cap is hit first.
		assert result.accepted is False
		assert result.reject_reason == "per_step_cap"


class TestDtForGateClamped:
	"""Test that dt_for_gate clamps at DT_GATE_CAP."""

	def test_dt_for_gate_clamped_to_3(self):
		"""Pass dt_frames = 10 and verify dt_for_gate == 3 in result."""
		prev_scene = (0.0, 0.0)
		cand_scene = (10.0, 0.0)  # small motion
		v_recent_scene_mag = 1.0
		dt_frames = 10  # DT_GATE_CAP = 3
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		assert result.dt_for_gate == 3


class TestRadialAllowance:
	"""Test radial-allowance contribution from toward/away camera motion."""

	def test_radial_allowance_included(self):
		"""Set torso_w_drift_frac and verify it contributes to expected_jump."""
		prev_scene = (0.0, 0.0)
		cand_scene = (50.0, 0.0)
		v_recent_scene_mag = 0.0  # zero velocity
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.5  # 50% of torso width

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		# expected_jump = 0 * 1 + 10 + 0.5 * 100 = 10 + 50 = 60 px
		# per_step_cap = 0.75 * 100 * 1 = 75 px
		# absolute_cap = 1.5 * 100 = 150 px
		# allowed_jump = min(150, 75, 60 * 1.75) = min(150, 75, 105) = 75 px
		# actual_jump = 50 px < 75 px -> accept
		assert result.accepted is True
		assert result.expected_jump == pytest.approx(60.0, abs=0.1)


class TestPerStepCapScaling:
	"""Test that per_step_cap scales with dt_for_gate."""

	def test_per_step_cap_scaling_dt_frames_1_vs_3(self):
		"""Same torso, different dt_frames; allowed_jump scales by dt_for_gate."""
		prev_scene = (0.0, 0.0)
		cand_scene = (30.0, 0.0)  # reasonable motion
		v_recent_scene_mag = 5.0
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		# dt_frames = 1: dt_for_gate = 1
		# expected = 5 * 1 + 10 + 0 = 15 px
		# per_step_cap = 0.75 * 100 * 1 = 75 px
		# absolute_cap = 1.5 * 100 = 150 px
		# allowed_jump = min(150, 75, 15 * 1.75) = min(150, 75, 26.25) = 26.25 px
		# actual_jump = 30 px > 26.25 px -> reject
		result_dt1 = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames=1,
			torso_w=torso_w,
			torso_w_drift_frac=torso_w_drift_frac,
			source_fps=60.0,
		)

		# dt_frames = 3: dt_for_gate = 3
		# expected = 5 * 3 + 10 + 0 = 25 px
		# per_step_cap = 0.75 * 100 * 3 = 225 px
		# absolute_cap = 1.5 * 100 = 150 px
		# allowed_jump = min(150, 225, 25 * 1.75) = min(150, 225, 43.75) = 43.75 px
		# actual_jump = 30 px < 43.75 px -> accept
		result_dt3 = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames=3,
			torso_w=torso_w,
			torso_w_drift_frac=torso_w_drift_frac,
			source_fps=60.0,
		)

		# Same candidate, dt_frames=1 rejects, dt_frames=3 accepts.
		# This shows per_step_cap scaling with dt_for_gate allows larger jumps
		# when more frames have passed.
		assert result_dt1.accepted is False
		assert result_dt3.accepted is True
		assert result_dt3.allowed_jump > result_dt1.allowed_jump


class TestRejectReasonOrdering:
	"""Test that reject_reason identifies the FIRST cap hit."""

	def test_reject_reason_prefers_absolute_over_per_step(self):
		"""When absolute_cap is exceeded, that is the reject reason."""
		prev_scene = (0.0, 0.0)
		cand_scene = (200.0, 0.0)  # actual_jump = 200 px
		v_recent_scene_mag = 100.0
		dt_frames = 1
		torso_w = 100.0
		torso_w_drift_frac = 0.0

		result = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w,
			torso_w_drift_frac,
			source_fps=60.0,
		)

		# absolute_cap = 150 px < 200 px -> hit absolute first
		assert result.reject_reason == "absolute_cap"


class TestMeasurementAllowanceScaling:
	"""Test that MEASUREMENT_ALLOWANCE_W is a multiple of torso_w."""

	def test_measurement_allowance_scales_with_torso_w(self):
		"""Same scenario at different torso sizes; allowance scales."""
		prev_scene = (0.0, 0.0)
		cand_scene = (0.5, 0.0)  # tiny offset
		v_recent_scene_mag = 0.0
		dt_frames = 1
		torso_w_drift_frac = 0.0

		# Small runner: torso_w = 10 px
		result_small = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w=10.0,
			torso_w_drift_frac=torso_w_drift_frac,
			source_fps=60.0,
		)

		# Large runner: torso_w = 1000 px
		result_large = evaluate(
			prev_scene,
			cand_scene,
			v_recent_scene_mag,
			dt_frames,
			torso_w=1000.0,
			torso_w_drift_frac=torso_w_drift_frac,
			source_fps=60.0,
		)

		# Small runner: measurement_allowance = 0.10 * 10 = 1 px; 0.5 px < 1 px -> accept
		# Large runner: measurement_allowance = 0.10 * 1000 = 100 px; 0.5 px < 100 px -> accept
		assert result_small.accepted is True
		assert result_large.accepted is True
