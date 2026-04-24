"""Tests for race_start module (M2 rewrite).

Tests core pre-race frame range analysis and race-start boundary detection:
- Stage 1: seed-pair displacement bracket detection
- Stage 2: fine-grained velocity onset detection within bracket
- Pre-race reference averaging (torso dimensions, scene-anchored position)
- Contract C2 implementation (averaged geometry for pre-race frames)
"""

# Standard Library
import math

# PIP3 modules
import pytest

# local repo modules
import race_start


#============================================
# Test helpers
#============================================

def _mk_seed(
	frame_index: int,
	cx: float = 100.0,
	cy: float = 100.0,
	w: float = 30.0,
	h: float = 60.0,
	status: str = "visible",
) -> dict:
	"""Build a minimal seed dict for test fixtures.

	Args:
		frame_index: Frame number for the seed.
		cx: Center X in pixel space.
		cy: Center Y in pixel space.
		w: Width in pixel space.
		h: Height in pixel space.
		status: Seed status ("visible", "partial", "approximate", "not_in_frame").

	Returns:
		Seed dict with all required fields.
	"""
	seed = {
		"frame_index": frame_index,
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"status": status,
		"torso_box": None,
	}
	# torso_box present for visible/partial seeds
	if status in ("visible", "partial"):
		seed["torso_box"] = [int(cx - w / 2), int(cy - h / 2), int(w), int(h)]
	return seed


class FakeSceneTransform:
	"""Minimal SceneTransform mock for tests.

	Matches scene_coords.SceneTransform interface but with deterministic
	panning behavior for testing. Scene x increases linearly per frame.

	Note: The real SceneTransform.pixel_box_to_scene returns a 4-tuple
	(scene_cx, scene_cy, scene_w, scene_h). This mock does the same.
	"""

	def __init__(self, pan_rate: float = 0.5):
		"""Initialize with linear panning rate.

		Args:
			pan_rate: Scene displacement per frame in x (pixels/frame).
		"""
		self.pan_rate = pan_rate

	def pixel_box_to_scene(
		self,
		frame_index: int,
		cx: float,
		cy: float,
		w: float,
		h: float,
	) -> tuple[float, float, float, float]:
		"""Convert pixel box to scene coords (with panning).

		Signature matches scene_coords.SceneTransform.pixel_box_to_scene.

		Args:
			frame_index: Frame index.
			cx: Center X in pixels.
			cy: Center Y in pixels.
			w: Width in pixels.
			h: Height in pixels.

		Returns:
			(scene_cx, scene_cy, scene_w, scene_h)
		"""
		# Scene pans: scene_x = pixel_x + frame_index * pan_rate
		scene_cx = cx + frame_index * self.pan_rate
		scene_cy = cy
		scene_w = w
		scene_h = h
		return (scene_cx, scene_cy, scene_w, scene_h)

	def scene_to_pixel(
		self,
		frame_index: int,
		sx: float,
		sy: float,
	) -> tuple[float, float]:
		"""Convert scene coords back to pixel space.

		Args:
			frame_index: Frame index.
			sx: X in scene space.
			sy: Y in scene space.

		Returns:
			(pixel_x, pixel_y)
		"""
		px = sx - frame_index * self.pan_rate
		py = sy
		return (px, py)


#============================================
# Stage 1: bracket detection tests
#============================================

def test_locate_bracket_picks_coherent_transition():
	"""Stationary cluster followed by coherent motion -> bracket is the
	transition pair.

	Seeds @30,60,90 stationary; seeds @120,150 moving in one direction.
	The first confirmed coherent window is [usable[2..4]] = @90,@120,@150;
	its next window [@120,@150,@180] is also coherent. Transition pair is
	the largest in the first window = (@90, @120). Bracket = (90, 120).
	"""
	seeds = [
		_mk_seed(30, cx=100.0),
		_mk_seed(60, cx=100.0),
		_mk_seed(90, cx=100.0),
		_mk_seed(120, cx=500.0),
		_mk_seed(150, cx=900.0),
		_mk_seed(180, cx=1300.0),
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	bracket = race_start.locate_race_start_bracket(seeds, transform, fps=30.0)
	assert bracket == (90, 120)


def test_locate_dense_pre_race_cluster_not_triggered():
	"""Adjacent-frame pre-race seeds are debounced (total annotation noise
	across a cluster stays below the motion threshold); bracket is found at
	the first post-cluster coherent motion.

	Mimics IMG_3627's pre-race pattern: dense seeds at frames 0-3 with tiny
	pixel jitter, then real motion starting at frame 60.
	"""
	seeds = [
		_mk_seed(0, cx=100.0),
		_mk_seed(1, cx=101.0),
		_mk_seed(2, cx=100.0),
		_mk_seed(3, cx=99.0),
		_mk_seed(60, cx=500.0),
		_mk_seed(120, cx=900.0),
		_mk_seed(180, cx=1300.0),
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	bracket = race_start.locate_race_start_bracket(seeds, transform, fps=60.0)
	# First coherent window is [@3, @60, @120]; transition pair is the
	# largest disp in that window = (@3, @60). Bracket endpoints are the
	# last pre-race seed and the first moving seed.
	assert bracket == (3, 60)


def test_locate_oneoff_jump_not_triggered():
	"""A single big annotation jump followed by return is rejected by the
	next-window confirmation step.

	Seeds stationary, one large outlier, then back to stationary. Window
	spanning the outlier triggers in isolation but the next window (which
	sees the return) does not. Detector therefore reports no race start.
	"""
	seeds = [
		_mk_seed(30, cx=100.0),
		_mk_seed(60, cx=100.0),
		_mk_seed(90, cx=900.0),     # one-off jump
		_mk_seed(120, cx=100.0),    # returns to baseline
		_mk_seed(150, cx=100.0),
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_bracket(seeds, transform, fps=30.0)


def test_locate_torso_scale_invariance():
	"""Same absolute scene displacement triggers for a small torso but not
	for a large torso, since the threshold is in torso widths (C1).

	Uses two fixtures with identical seed positions but different torso
	widths. Small-torso fixture must trigger; large-torso fixture must not.
	"""
	def _run(w):
		seeds = [
			_mk_seed(30, cx=100.0, w=w),
			_mk_seed(60, cx=100.0, w=w),
			_mk_seed(90, cx=100.0, w=w),
			_mk_seed(120, cx=130.0, w=w),  # +30 px motion
			_mk_seed(150, cx=160.0, w=w),
			_mk_seed(180, cx=190.0, w=w),
		]
		transform = FakeSceneTransform(pan_rate=0.0)
		return race_start.locate_race_start_bracket(seeds, transform, fps=30.0)
	# Small torso (w=10): 30 px = 3 torso widths >> 0.75 threshold. Triggers.
	assert _run(10.0) == (90, 120)
	# Large torso (w=200): 30 px = 0.15 torso widths << 0.75 threshold. No trigger.
	with pytest.raises(RuntimeError):
		_run(200.0)


def test_locate_all_stationary_raises():
	"""All seeds at same scene position -> RuntimeError (no coherent window)."""
	seeds = [_mk_seed(i * 30, cx=100.0) for i in range(6)]
	transform = FakeSceneTransform(pan_rate=0.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_bracket(seeds, transform, fps=30.0)


def test_locate_all_moving_raises():
	"""First seed pair already moving (no pre-race seed) -> RuntimeError.

	Every seed pair is a big directional step, so the first window triggers
	at i=0 and the transition pair is (usable[0], usable[1]).
	"""
	seeds = [
		_mk_seed(0, cx=100.0),
		_mk_seed(30, cx=500.0),
		_mk_seed(60, cx=900.0),
		_mk_seed(90, cx=1300.0),
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_bracket(seeds, transform, fps=30.0)


def test_locate_fewer_than_min_window_seeds_raises():
	"""Fewer usable seeds than window size -> RuntimeError."""
	transform = FakeSceneTransform(pan_rate=0.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_bracket([_mk_seed(0)], transform, fps=30.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_bracket(
			[_mk_seed(0), _mk_seed(30)], transform, fps=30.0,
		)


#============================================
# Pre-race reference computation tests
#============================================

def test_compute_pre_race_reference_averages_w_and_h():
	"""Torso w/h averaged from qualifying seeds."""
	seeds = [
		_mk_seed(0, w=30.0, h=60.0),
		_mk_seed(10, w=32.0, h=62.0),
		_mk_seed(20, w=34.0, h=64.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
	)
	assert abs(ref["torso_w"] - 32.0) < 0.5
	assert abs(ref["torso_h"] - 62.0) < 0.5
	assert ref["source_count"] == 3


def test_compute_pre_race_reference_excludes_approximate():
	"""Approximate seeds excluded; source_count drops accordingly."""
	seeds = [
		_mk_seed(0, w=30.0, status="visible"),
		_mk_seed(10, w=999.0, status="approximate"),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
	)
	assert ref["source_count"] == 1
	assert abs(ref["torso_w"] - 30.0) < 0.5


def test_compute_pre_race_reference_excludes_not_in_frame():
	"""not_in_frame seeds excluded."""
	seeds = [
		_mk_seed(0, w=30.0, status="visible"),
		_mk_seed(10, status="not_in_frame"),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
	)
	assert ref["source_count"] == 1


def test_compute_pre_race_reference_excludes_post_boundary_seeds():
	"""Seeds at frame_index >= race_start_frame excluded."""
	seeds = [
		_mk_seed(0, w=30.0),
		_mk_seed(50, w=999.0),
		_mk_seed(60, w=999.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
	)
	assert ref["source_count"] == 1
	assert abs(ref["torso_w"] - 30.0) < 0.5


def test_compute_pre_race_reference_raises_when_no_qualifying():
	"""No visible/partial pre-race seeds -> RuntimeError."""
	seeds = [
		_mk_seed(0, status="approximate"),
		_mk_seed(5, status="not_in_frame"),
	]
	race_start_frame = 50
	transform = FakeSceneTransform(pan_rate=0.0)

	with pytest.raises(RuntimeError) as exc_info:
		race_start.compute_pre_race_reference(
			seeds, race_start_frame, transform
		)
	assert "visible or partial" in str(exc_info.value).lower()


def test_compute_pre_race_reference_scene_anchored():
	"""Scene-anchor is mean of qualifying seeds' scene coords."""
	seeds = [
		_mk_seed(0, cx=100.0, cy=200.0),
		_mk_seed(10, cx=100.0, cy=200.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
	)
	# pan_rate=0 so scene == pixel; anchor = (100, 200)
	assert abs(ref["scene_anchor_x"] - 100.0) < 0.5
	assert abs(ref["scene_anchor_y"] - 200.0) < 0.5


#============================================
# Detect race start in bracket (Stage 2)
#============================================

def test_detect_race_start_in_bracket_raises_on_none():
	"""Detector returns None for bracket trajectory -> RuntimeError."""
	# Build a bracket trajectory with no clear motion onset
	# (all frames stationary). The detector should return None
	# and we should raise RuntimeError.
	bracket_trajectory = [
		{"cx": 100.0, "cy": 100.0, "w": 30.0, "h": 60.0}
		for _ in range(10)
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	fps = 30.0
	bracket_start_frame = 0

	# This test depends on race_phases.detect_race_start returning None
	# when there is no motion. We trust that contract and expect RuntimeError.
	with pytest.raises(RuntimeError) as exc_info:
		race_start.detect_race_start_in_bracket(
			bracket_trajectory, transform, fps, bracket_start_frame
		)
	assert "velocity onset" in str(exc_info.value).lower() or \
		   "none" in str(exc_info.value).lower()


#============================================
# Print summary
#============================================

def test_print_race_phase_summary_none():
	"""print_race_phase_summary handles None gracefully."""
	# Should not raise
	race_start.print_race_phase_summary(None)


def test_print_race_phase_summary_with_result():
	"""print_race_phase_summary prints frame and source count."""
	result = {
		"race_start_frame": 120,
		"source_count": 3,
		"warnings": [],
	}
	# Should not raise
	race_start.print_race_phase_summary(result)
