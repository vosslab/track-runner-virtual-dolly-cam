"""
Unit tests for tools/blob_walk_v2/core/walk_motion_gate.py.

Tests cover cold-start, centroid jitter, cap-driven rejection, and parameter
scaling. Per-frame motion budget at 60 fps:
    per_step_cap_per_frame = MAX_RUNNER_SPEED_W_PER_S / source_fps
                           = 30.0 / 60 = 0.5 W/frame
So at torso_w = 100 px the per_step_cap at dt=1 is 50 px.
"""

import os
import sys

import pytest

# Put the blob_walk_v2 package root on sys.path so the bare import walk_paths
# resolves, then call walk_paths.setup() to add the core/ and render/ subdirs.
# walk_motion_gate now lives under core/, reachable via that bootstrap.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BLOB_WALK_DIR = os.path.join(_REPO_ROOT, "tools", "blob_walk_v2")
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)
import walk_paths
walk_paths.setup()

import walk_motion_gate


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

		result = walk_motion_gate.evaluate(
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

		result = walk_motion_gate.evaluate(
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

		result = walk_motion_gate.evaluate(
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

		result = walk_motion_gate.evaluate(
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

		result = walk_motion_gate.evaluate(
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
		result_dt1 = walk_motion_gate.evaluate(
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
		result_dt3 = walk_motion_gate.evaluate(
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


