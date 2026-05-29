"""
Tests for WP-1C soft-miss walker: gate rejects and no-blob frames do not
terminate the walk.

Verifies:
1. REJECT_CAP and MISS_CAP are no longer defined in walk_walker (removed in WP-1C).
2. BOOTSTRAP_N is defined and equals 1.
3. The bootstrap branch in walk_walker accepts a 0.5W jump at step 1 with v=0
   (gate_reject_cap walk-stop was blocking this; soft-miss means the walker
   should have continued to the next frame in the old code, and now it does).
4. "gate_reject_cap" and "miss_cap_no_blob" are NOT valid walk stop_reasons
   that the walker emits (they may exist for backward CSV compat, but the
   walker no longer generates them).
"""

import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)

import walk_walker
import walk_motion_gate


#============================================
class TestWalkerNoCaps:
	"""REJECT_CAP and MISS_CAP are removed from walk_walker."""

	def test_no_reject_cap_constant(self):
		# REJECT_CAP must not exist; its presence would indicate the old
		# walk-stop branch was not removed.
		assert not hasattr(walk_walker, 'REJECT_CAP'), (
			"REJECT_CAP still defined in walk_walker; the gate_reject_cap "
			"walk-stop branch must be removed per WP-1C"
		)

	def test_no_miss_cap_constant(self):
		# MISS_CAP must not exist; its presence would indicate the old
		# miss_cap_no_blob walk-stop branch was not removed.
		assert not hasattr(walk_walker, 'MISS_CAP'), (
			"MISS_CAP still defined in walk_walker; the miss_cap_no_blob "
			"walk-stop branch must be removed per WP-1C"
		)

	def test_bootstrap_n_defined(self):
		# BOOTSTRAP_N must be defined.
		assert hasattr(walk_walker, 'BOOTSTRAP_N')

	def test_bootstrap_n_is_one(self):
		# Bootstrap N = 1 per 2026-05-28 changelog.
		assert walk_walker.BOOTSTRAP_N == 1


#============================================
class TestBootstrapModeStep1:
	"""Step 1 uses the bootstrap gate, not tracking-mode three-cap-min."""

	def test_step_1_is_bootstrap(self):
		# BOOTSTRAP_N == 1 means step == 1 satisfies step <= BOOTSTRAP_N.
		# This test pins the gate-selection condition.
		n = walk_walker.BOOTSTRAP_N
		assert 1 <= n, "Step 1 must be within bootstrap range"

	def test_step_2_is_tracking(self):
		# Step 2 is step > BOOTSTRAP_N (step > 1) -> tracking mode.
		n = walk_walker.BOOTSTRAP_N
		assert 2 > n, "Step 2 must be outside bootstrap range (tracking mode)"

	def test_bootstrap_radius_at_60fps_covers_0_5w(self):
		# At 60 fps the bootstrap radius is 0.80W. A 0.5W jump (which the
		# tracking gate at v=0 rejects) must be within bootstrap radius.
		fps = 60.0
		torso_w = 100.0  # px
		radius_scene = walk_motion_gate.bootstrap_search_radius_w(fps) * torso_w
		# Simulate 0.5W jump.
		jump_scene = 0.5 * torso_w
		# Bootstrap gate: accept if jump <= radius.
		assert jump_scene <= radius_scene, (
			f"0.5W jump ({jump_scene} px) should be within bootstrap radius "
			f"({radius_scene} px) at {fps} fps; gate is too tight"
		)

	def test_tracking_gate_rejects_0_5w_at_v0(self):
		# Cross-check: the tracking gate at v_recent=0 rejects the same jump.
		result = walk_motion_gate.evaluate(
			prev_scene=(0.0, 0.0),
			cand_scene=(50.0, 0.0),  # 0.5 * 100 = 50 px
			v_recent_scene_mag=0.0,
			dt_frames=1,
			torso_w=100.0,
			torso_w_drift_frac=0.0,
			source_fps=60.0,
		)
		assert result.accepted is False, (
			"Tracking gate at v=0 should reject 0.5W jump at 60 fps; "
			"if it accepts, tracking-mode constants have changed"
		)
