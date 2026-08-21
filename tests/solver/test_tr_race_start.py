"""Tests for race_start module (M2 rewrite).

Tests core pre-race frame range analysis and race-start boundary detection:
- Stage 1: seed-pair displacement bracket detection
- Stage 2: fine-grained velocity onset detection within bracket
- Pre-race reference averaging (torso dimensions, scene-anchored position)
- Contract C2 implementation (averaged geometry for pre-race frames)
"""

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

	def __init__(self, pan_rate: float = 0.5) -> None:
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
# Stage 1: interval detection tests
#============================================

def test_locate_interval_picks_coherent_transition() -> None:
	"""Stationary cluster followed by coherent motion -> bracket is the
	transition pair.

	Seeds @30,60,90 stationary; seeds @120,150 moving in one direction.
	The first confirmed coherent window is [usable[2..4]] = @90,@120,@150;
	its next window [@120,@150,@180] is also coherent. Transition pair is
	the largest in the first window = (@90, @120). Interval = (90, 120).
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
	interval = race_start.locate_race_start_interval(seeds, transform, fps=30.0)
	assert interval == (90, 120)


def test_locate_interval_dense_pre_race_cluster_not_triggered() -> None:
	"""Adjacent-frame pre-race seeds are debounced (total annotation noise
	across a cluster stays below the motion threshold); bracket is found at
	the first post-cluster coherent motion.

	Uses dense seeds at frames 0-3 with tiny pixel jitter, then coherent motion
	starting at frame 60.
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
	interval = race_start.locate_race_start_interval(seeds, transform, fps=60.0)
	# First coherent window is [@3, @60, @120]; transition pair is the
	# largest disp in that window = (@3, @60). Interval endpoints are the
	# last pre-race seed and the first moving seed.
	assert interval == (3, 60)


def test_locate_interval_oneoff_jump_not_triggered() -> None:
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
	assert race_start.locate_race_start_interval(seeds, transform, fps=30.0) is None


def test_locate_interval_torso_scale_invariance() -> None:
	"""Same absolute scene displacement triggers for a small torso but not
	for a large torso, since the threshold is in torso widths (C1).

	Uses two fixtures with identical seed positions but different torso
	widths. Small-torso fixture must trigger; large-torso fixture must not.
	"""
	def _run(w: float) -> tuple[int, int] | None:
		seeds = [
			_mk_seed(30, cx=100.0, w=w),
			_mk_seed(60, cx=100.0, w=w),
			_mk_seed(90, cx=100.0, w=w),
			_mk_seed(120, cx=130.0, w=w),  # +30 px motion
			_mk_seed(150, cx=160.0, w=w),
			_mk_seed(180, cx=190.0, w=w),
		]
		transform = FakeSceneTransform(pan_rate=0.0)
		return race_start.locate_race_start_interval(seeds, transform, fps=30.0)
	# Small torso (w=10): 30 px = 3 torso widths >> 0.75 threshold. Triggers.
	assert _run(10.0) == (90, 120)
	# Large torso (w=200): 30 px = 0.15 torso widths << 0.75 threshold. No trigger.
	assert _run(200.0) is None


def test_locate_interval_all_stationary_returns_none() -> None:
	"""All seeds at same scene position -> None (no coherent window)."""
	seeds = [_mk_seed(i * 30, cx=100.0) for i in range(6)]
	transform = FakeSceneTransform(pan_rate=0.0)
	assert race_start.locate_race_start_interval(seeds, transform, fps=30.0) is None


def test_locate_interval_all_moving_returns_none() -> None:
	"""First seed pair already moving (no pre-race seed) -> None.

	Every seed pair is a big directional step, so the first window triggers
	at i=0 and the transition pair is (usable[0], usable[1]); without a
	pre-race seed the detector reports no pre-race phase.
	"""
	seeds = [
		_mk_seed(0, cx=100.0),
		_mk_seed(30, cx=500.0),
		_mk_seed(60, cx=900.0),
		_mk_seed(90, cx=1300.0),
	]
	transform = FakeSceneTransform(pan_rate=0.0)
	assert race_start.locate_race_start_interval(seeds, transform, fps=30.0) is None


def test_locate_interval_too_few_seeds() -> None:
	"""Fewer than 2 seeds raises; 2 seeds (below window size) returns None."""
	transform = FakeSceneTransform(pan_rate=0.0)
	with pytest.raises(RuntimeError):
		race_start.locate_race_start_interval([_mk_seed(0)], transform, fps=30.0)
	assert race_start.locate_race_start_interval(
		[_mk_seed(0), _mk_seed(30)], transform, fps=30.0,
	) is None


#============================================
# Pre-race reference computation tests
#============================================

def test_compute_pre_race_reference_averages_w_and_h() -> None:
	"""Torso w/h averaged from qualifying seeds."""
	seeds = [
		_mk_seed(0, w=30.0, h=60.0),
		_mk_seed(10, w=32.0, h=62.0),
		_mk_seed(20, w=34.0, h=64.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=(0, 50),
	)
	assert abs(ref["torso_w"] - 32.0) < 0.5
	assert abs(ref["torso_h"] - 62.0) < 0.5
	assert ref["source_count"] >= 1


def test_compute_pre_race_reference_excludes_approximate() -> None:
	"""Approximate seeds excluded; source_count drops accordingly."""
	seeds = [
		_mk_seed(0, w=30.0, status="visible"),
		_mk_seed(10, w=999.0, status="approximate"),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=(0, 50),
	)
	assert ref["source_count"] >= 1
	assert abs(ref["torso_w"] - 30.0) < 0.5


def test_compute_pre_race_reference_excludes_not_in_frame() -> None:
	"""not_in_frame seeds excluded."""
	seeds = [
		_mk_seed(0, w=30.0, status="visible"),
		_mk_seed(10, status="not_in_frame"),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=(0, 50),
	)
	assert ref["source_count"] >= 1


def test_compute_pre_race_reference_excludes_post_boundary_seeds() -> None:
	"""Seeds at frame_index >= race_start_frame excluded."""
	seeds = [
		_mk_seed(0, w=30.0),
		_mk_seed(50, w=999.0),
		_mk_seed(60, w=999.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=(0, 50),
	)
	assert ref["source_count"] == 1
	assert abs(ref["torso_w"] - 30.0) < 0.5


def test_compute_pre_race_reference_raises_when_no_qualifying() -> None:
	"""No visible/partial pre-race seeds -> RuntimeError."""
	seeds = [
		_mk_seed(0, status="approximate"),
		_mk_seed(5, status="not_in_frame"),
	]
	race_start_frame = 50
	transform = FakeSceneTransform(pan_rate=0.0)

	with pytest.raises(RuntimeError) as exc_info:
		race_start.compute_pre_race_reference(
			seeds, race_start_frame, transform, race_start_interval=(0, 50)
		)
	assert "visible or partial" in str(exc_info.value).lower()


def test_compute_pre_race_reference_scene_anchored() -> None:
	"""Scene-anchor is mean of qualifying seeds' scene coords."""
	seeds = [
		_mk_seed(0, cx=100.0, cy=200.0),
		_mk_seed(10, cx=100.0, cy=200.0),
	]
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=50, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=(0, 50),
	)
	# pan_rate=0 so scene == pixel; anchor = (100, 200)
	assert abs(ref["scene_anchor_x"] - 100.0) < 0.5
	assert abs(ref["scene_anchor_y"] - 200.0) < 0.5


def test_compute_pre_race_reference_includes_interval() -> None:
	"""race_start_interval is persisted when provided."""
	seeds = [
		_mk_seed(0, w=30.0, h=60.0),
		_mk_seed(10, w=32.0, h=62.0),
		_mk_seed(20, w=34.0, h=64.0),
	]
	interval = (10, 20)
	ref = race_start.compute_pre_race_reference(
		seeds, race_start_frame=25, scene_transform=FakeSceneTransform(0.0),
		race_start_interval=interval,
	)
	assert ref["race_start_interval"] == [10, 20]


#============================================
# Race-start frame selection (production: midpoint, deactivated Stage 2)
#============================================

def test_pick_race_start_frame_midpoint_even_span() -> None:
	# even span: ceil((10 + 20) / 2) == 15
	assert race_start.pick_race_start_frame_midpoint(10, 20) == 15


def test_pick_race_start_frame_midpoint_odd_span() -> None:
	# odd span: ceil((10 + 21) / 2) == 16, not 15
	assert race_start.pick_race_start_frame_midpoint(10, 21) == 16


def test_pick_race_start_frame_midpoint_rejects_inverted_interval() -> None:
	with pytest.raises(ValueError):
		race_start.pick_race_start_frame_midpoint(20, 10)
	with pytest.raises(ValueError):
		race_start.pick_race_start_frame_midpoint(20, 20)


#============================================
# Frame selection for confirmation contact sheet
#============================================

def test_choose_confirmation_frames_fixed_offsets() -> None:
	"""Tiles span full offset range with center at START."""
	tiles = race_start.choose_race_start_confirmation_frames(
		race_start_frame=100, fps=30.0, video_frame_count=200
	)
	# Behavioral property: tiles exist with specific offset values
	offsets = [t["offset_s"] for t in tiles]
	assert 0.0 in offsets
	assert any(o < 0 for o in offsets)
	assert any(o > 0 for o in offsets)
	# Center tile (offset=0.0) has label START
	center_tiles = [t for t in tiles if t["offset_s"] == 0.0]
	assert len(center_tiles) == 1
	assert center_tiles[0]["label"] == "START"


def test_choose_confirmation_frames_clamping() -> None:
	"""Clamping at frame boundary sets clamped=True, frame_index clamped."""
	tiles = race_start.choose_race_start_confirmation_frames(
		race_start_frame=10, fps=30.0, video_frame_count=200
	)
	# Behavioral property: when requested_frame_index would go negative,
	# it's clamped to 0 and clamped=True is set
	clamped_tiles = [t for t in tiles if t["clamped"]]
	assert len(clamped_tiles) > 0
	for t in clamped_tiles:
		# Clamped frame index must be >= 0
		assert t["frame_index"] >= 0


def test_choose_confirmation_frames_labels() -> None:
	"""Labels are PRE/START/POST based on offset sign."""
	tiles = race_start.choose_race_start_confirmation_frames(
		race_start_frame=100, fps=30.0, video_frame_count=200
	)
	# Behavioral property: offset sign determines label
	pre_tiles = [t for t in tiles if t["offset_s"] < 0]
	start_tiles = [t for t in tiles if t["offset_s"] == 0.0]
	post_tiles = [t for t in tiles if t["offset_s"] > 0]

	# All PRE tiles (offset < 0) have label PRE
	for t in pre_tiles:
		assert t["label"] == "PRE"
	# Center tile (offset == 0) has label START
	assert len(start_tiles) == 1
	assert start_tiles[0]["label"] == "START"
	# All POST tiles (offset > 0) have label POST
	for t in post_tiles:
		assert t["label"] == "POST"


def test_choose_confirmation_frames_rows() -> None:
	"""Rows are top/center/bottom based on label."""
	tiles = race_start.choose_race_start_confirmation_frames(
		race_start_frame=100, fps=30.0, video_frame_count=200
	)
	# Behavioral property: label determines row
	pre_tiles = [t for t in tiles if t["label"] == "PRE"]
	start_tiles = [t for t in tiles if t["label"] == "START"]
	post_tiles = [t for t in tiles if t["label"] == "POST"]

	# All PRE tiles have row = top
	for t in pre_tiles:
		assert t["row"] == "top"
	# All START tiles have row = center
	for t in start_tiles:
		assert t["row"] == "center"
	# All POST tiles have row = bottom
	for t in post_tiles:
		assert t["row"] == "bottom"


#============================================
# Print summary
#============================================

def test_print_race_phase_summary_none() -> None:
	"""print_race_phase_summary handles None gracefully."""
	# Should not raise
	race_start.print_race_phase_summary(None)


def test_print_race_phase_summary_with_result(capsys: pytest.CaptureFixture[str]) -> None:
	"""print_race_phase_summary renders a multi-line block with the
	race_start_frame, the Stage 1 interval, the pre-race anchor,
	and -- when fps is given -- a timestamp.
	"""
	result = {
		"race_start_frame": 120,
		"race_start_interval": [115, 125],
		"source_count": 3,
		"torso_w": 60.0,
		"torso_h": 100.0,
		"warnings": [],
	}
	race_start.print_race_phase_summary(result, fps=60.0)
	captured = capsys.readouterr().out
	assert "RACE-START DETECTION" in captured
	assert "race_start_frame: 120" in captured
	assert "(115, 125)" in captured
	# fps=60.0 -> 120/60 = 2.000 s timestamp
	assert "2.000 s" in captured
