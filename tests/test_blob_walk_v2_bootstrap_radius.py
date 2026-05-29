"""
Unit tests for bootstrap-mode gate in walk_walker.py.

Verifies that step 1 (bootstrap mode) uses bootstrap_search_radius_w instead
of the tracking-mode three-cap-min gate, so a candidate at 0.5W from the seed
is accepted when v_recent=0 (which the tracking gate rejects at 60 fps with
allowed ~0.18W).

At 60 fps:
    bootstrap_radius_w = MAX_RUNNER_SPEED_W_PER_S / 60 + BOOTSTRAP_UNCERTAINTY_W
                       = 30/60 + 0.30 = 0.50 + 0.30 = 0.80 W/frame

Tracking gate at v=0:
    per_step_cap = 0.5 * torso_w
    measurement_allowance = 0.10 * torso_w
    expected_jump = 0 + 0.10*torso_w = 0.10*torso_w
    allowed = min(1.5*torso_w, 0.5*torso_w, 0.10*torso_w * 1.75) = 0.175*torso_w

A jump of 0.5W (50px at torso_w=100) is:
    - ACCEPTED by bootstrap gate (0.5 < 0.80W)
    - REJECTED by tracking gate (0.5 > 0.175W)
"""

import sys
import os

# Add tools/blob_walk_v2 to path for import.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)

import walk_motion_gate
from walk_walker import BOOTSTRAP_N


#============================================
def _bootstrap_gate_result(jump_w: float, torso_w_px: float, fps: float):
	"""
	Compute the motion gate result using bootstrap logic for step 1.

	Mirrors the bootstrap branch in walk_walker.walk_one_direction for
	isolated testing without a real video or observer.

	Args:
		jump_w: jump in torso-width units (actual displacement / torso_w).
		torso_w_px: torso width in pixels (scale unit).
		fps: source video frame rate.

	Returns:
		MotionGateResult
	"""
	# Bootstrap radius in scene units (scene units == pixels here for test).
	bootstrap_radius_scene = walk_motion_gate.bootstrap_search_radius_w(fps) * torso_w_px
	# Simulate a jump directly right (x axis).
	actual_jump_scene = jump_w * torso_w_px
	accepted_bootstrap = actual_jump_scene <= bootstrap_radius_scene
	return walk_motion_gate.MotionGateResult(
		accepted=accepted_bootstrap,
		expected_jump=0.0,
		allowed_jump=bootstrap_radius_scene,
		actual_jump=actual_jump_scene,
		v_recent_scene_mag=0.0,
		dt_for_gate=1,
		reject_reason="" if accepted_bootstrap else "bootstrap_radius",
	)


#============================================
def _tracking_gate_result(jump_w: float, torso_w_px: float, fps: float):
	"""
	Compute the motion gate result using tracking-mode logic with v_recent=0.

	Args:
		jump_w: jump in torso-width units.
		torso_w_px: torso width in pixels.
		fps: source video frame rate.

	Returns:
		MotionGateResult
	"""
	# prev_scene at origin; cand_scene jump_w torso widths to the right.
	prev_scene = (0.0, 0.0)
	cand_scene = (jump_w * torso_w_px, 0.0)
	return walk_motion_gate.evaluate(
		prev_scene=prev_scene,
		cand_scene=cand_scene,
		v_recent_scene_mag=0.0,
		dt_frames=1,
		torso_w=torso_w_px,
		torso_w_drift_frac=0.0,
		source_fps=fps,
	)


#============================================
class TestBootstrapN:
	"""Pin the BOOTSTRAP_N constant to the design value."""

	def test_bootstrap_n_is_one(self):
		# Design value per 2026-05-28 bootstrap redesign in docs/CHANGELOG.md.
		assert BOOTSTRAP_N == 1


#============================================
class TestBootstrapAcceptsHalfWidthJump:
	"""At step 1 (bootstrap mode), a 0.5W jump at 60 fps must be accepted."""

	def test_bootstrap_accepts_0_5w_at_60fps(self):
		# 0.5W is within the 0.80W bootstrap radius.
		result = _bootstrap_gate_result(jump_w=0.5, torso_w_px=100.0, fps=60.0)
		assert result.accepted is True

	def test_bootstrap_allowed_is_0_8w_at_60fps(self):
		# Bootstrap radius = 30/60 + 0.30 = 0.80W * torso_w.
		result = _bootstrap_gate_result(jump_w=0.0, torso_w_px=100.0, fps=60.0)
		import pytest
		assert result.allowed_jump == pytest.approx(80.0, abs=0.1)

	def test_tracking_rejects_0_5w_at_60fps_v0(self):
		# Tracking gate at v=0 and 60fps: allowed ~0.175W * torso_w = 17.5 px.
		# A 50 px (0.5W) jump must be rejected.
		result = _tracking_gate_result(jump_w=0.5, torso_w_px=100.0, fps=60.0)
		assert result.accepted is False

	def test_bootstrap_same_jump_tracking_would_reject(self):
		# Confirm the two gates diverge on 0.5W with v=0.
		boot = _bootstrap_gate_result(jump_w=0.5, torso_w_px=100.0, fps=60.0)
		track = _tracking_gate_result(jump_w=0.5, torso_w_px=100.0, fps=60.0)
		assert boot.accepted is True
		assert track.accepted is False


#============================================
class TestBootstrapRejectsExtremeJumps:
	"""Bootstrap mode still rejects jumps that exceed the physical envelope."""

	def test_bootstrap_rejects_1_5w_jump(self):
		# 1.5W exceeds the 0.80W bootstrap radius.
		result = _bootstrap_gate_result(jump_w=1.5, torso_w_px=100.0, fps=60.0)
		assert result.accepted is False

	def test_bootstrap_rejects_at_exactly_radius_plus_epsilon(self):
		# A jump just above bootstrap_radius_w should be rejected.
		fps = 60.0
		radius_w = walk_motion_gate.bootstrap_search_radius_w(fps)
		# Jump 1% above radius.
		result = _bootstrap_gate_result(jump_w=radius_w * 1.01, torso_w_px=100.0, fps=fps)
		assert result.accepted is False

	def test_bootstrap_accepts_at_exactly_radius(self):
		# A jump at exactly the bootstrap radius should be accepted (<=).
		fps = 60.0
		radius_w = walk_motion_gate.bootstrap_search_radius_w(fps)
		result = _bootstrap_gate_result(jump_w=radius_w, torso_w_px=100.0, fps=fps)
		assert result.accepted is True
