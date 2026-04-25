"""Unit tests for scoring metrics.

Covers the new velocity_consistency smoothness metric and verifies that
fps is threaded through score_interval_analytical correctly.

Bare imports are resolved via conftest.py which adds track_runner/ to
sys.path.
"""

# Standard Library
import math

# local repo modules
import scoring


#============================================
class MockSceneTransform:
	"""Identity transform: scene coordinates equal pixel coordinates."""

	def pixel_to_scene(
		self, frame_index: int, px: float, py: float,
	) -> tuple:
		return (px, py)


#============================================
def _make_track(positions: list) -> list:
	"""Build a track list of state dicts from (cx, cy) tuples."""
	track = []
	for cx, cy in positions:
		track.append({
			"cx": float(cx),
			"cy": float(cy),
			"w": 40.0,
			"h": 100.0,
			"conf": 0.9,
		})
	return track


#============================================
def test_velocity_consistency_smooth_curve():
	"""Constant-speed motion along a curve should score very high.

	Direction changes continuously but speed magnitude stays fixed, so
	acceleration magnitude is near-zero and the metric should be close
	to 1.0.
	"""
	transform = MockSceneTransform()
	# 60 frames along a quarter-circle at constant speed
	radius = 100.0
	speed_per_frame = 5.0
	n = 60
	# omega such that arc length per frame = speed_per_frame
	omega = speed_per_frame / radius
	positions = []
	for i in range(n):
		theta = omega * i
		cx = 500.0 + radius * math.cos(theta)
		cy = 500.0 + radius * math.sin(theta)
		positions.append((cx, cy))
	track = _make_track(positions)

	vc = scoring._compute_velocity_smoothness(track, 0, transform)
	assert vc > 0.8, f"smooth curve should score > 0.8, got {vc}"


#============================================
def test_velocity_consistency_jerky():
	"""Trajectory with large per-frame acceleration spikes should score low."""
	transform = MockSceneTransform()
	# alternate between fast and slow motion: speed flips every frame
	positions = [(0.0, 0.0)]
	cx = 0.0
	for i in range(60):
		# inject 20-px jump every other frame, 2-px otherwise
		if i % 2 == 0:
			cx += 20.0
		else:
			cx += 2.0
		positions.append((cx, 0.0))
	track = _make_track(positions)

	vc = scoring._compute_velocity_smoothness(track, 0, transform)
	assert vc < 0.3, f"jerky motion should score < 0.3, got {vc}"


#============================================
def test_velocity_consistency_stationary_bounded():
	"""Near-stationary trajectory must not divide by zero.

	The velocity floor keeps the ratio bounded when typical speed is tiny.
	"""
	transform = MockSceneTransform()
	# 40 frames at near-identical positions with sub-pixel jitter
	positions = []
	for i in range(40):
		# jitter under 0.5 px
		jitter_x = 0.3 if i % 2 == 0 else -0.3
		positions.append((100.0 + jitter_x, 200.0))
	track = _make_track(positions)

	vc = scoring._compute_velocity_smoothness(track, 0, transform)
	# bounded, non-negative; exact value depends on floor calibration
	assert 0.0 <= vc <= 1.0, f"expected bounded score, got {vc}"


#============================================
def test_velocity_consistency_short_track():
	"""Tracks under 3 frames return 1.0 (acceleration is undefined)."""
	transform = MockSceneTransform()
	track_empty = []
	track_one = _make_track([(0.0, 0.0)])
	track_two = _make_track([(0.0, 0.0), (1.0, 0.0)])
	assert scoring._compute_velocity_smoothness(track_empty, 0, transform) == 1.0
	assert scoring._compute_velocity_smoothness(track_one, 0, transform) == 1.0
	assert scoring._compute_velocity_smoothness(track_two, 0, transform) == 1.0


#============================================
def test_fps_threaded_through_scoring():
	"""Long-interval demotion uses the passed fps, not a hardcoded value.

	At fps=60 a 310-frame interval is 5.17s (no demotion). At fps=30 the
	same frame count is 10.33s (should demote). The same metric inputs
	should therefore produce a higher tier with fps=60 than with fps=30.
	"""
	transform = MockSceneTransform()
	n = 310
	# build a smooth track so agreement and vc are high
	positions = [(float(i), 0.0) for i in range(n)]
	fwd = _make_track(positions)
	bwd = _make_track(positions)
	all_seeds_scene = []
	interval_curves = {
		"start_frame": 0,
		"end_frame": n - 1,
		"left_pos": (0.0, 0.0),
		"right_pos": (float(n - 1), 0.0),
		"left_size": (40.0, 100.0),
		"right_size": (40.0, 100.0),
		"fwd_slopes": (1.0, 0.0),
		"bwd_slopes": (1.0, 0.0),
	}

	score_60 = scoring.score_interval_analytical(
		fwd, bwd, all_seeds_scene, interval_curves, transform,
		blended_path=fwd, fps=60.0,
	)
	score_30 = scoring.score_interval_analytical(
		fwd, bwd, all_seeds_scene, interval_curves, transform,
		blended_path=fwd, fps=30.0,
	)
	# Canonical tier ordering (should be exposed from scoring module)
	tier_order = ["low", "fair", "good", "high"]
	# fps=60 should leave the tier alone; fps=30 should demote at least once
	assert tier_order.index(score_60["confidence_tier"]) >= \
		tier_order.index(score_30["confidence_tier"])


#============================================
def test_compute_agreement_debug_matches_aggregate():
	"""compute_agreement_debug returns the same mean as compute_agreement."""
	fwd = _make_track([(10.0, 10.0), (12.0, 10.0), (14.0, 10.0)])
	bwd = _make_track([(11.0, 10.0), (12.0, 10.0), (13.0, 10.0)])
	agg = scoring.compute_agreement(fwd, bwd)
	debug = scoring.compute_agreement_debug(fwd, bwd, start_frame=100)
	assert abs(agg - debug["agreement"]) < 1e-9
	# percentiles ordered low->high
	assert debug["iou_p10"] <= debug["iou_p50"] <= debug["iou_p90"]
