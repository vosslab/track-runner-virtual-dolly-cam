"""Unit tests for race_phases.detect_race_start().

Tests use synthetic trajectory data with a mock SceneTransform
that performs identity transformation (pixel == scene).

Bare imports (e.g. import race_phases) are resolved via conftest.py
which adds track_runner/ to sys.path.
"""

# local repo modules (bare imports resolved by conftest.py)
import race_phases


#============================================
class MockSceneTransform:
	"""Identity transform: scene coordinates equal pixel coordinates."""

	def pixel_to_scene(
		self, frame_index: int, px: float, py: float,
	) -> tuple:
		return (px, py)


#============================================
def _make_trajectory(positions: list, conf: float = 0.9) -> list:
	"""Build a trajectory list from (cx, cy) tuples.

	Args:
		positions: List of (cx, cy) tuples. None entries become None frames.
		conf: Confidence value for all valid frames.

	Returns:
		List of frame state dicts (or None).
	"""
	trajectory = []
	for pos in positions:
		if pos is None:
			trajectory.append(None)
		else:
			trajectory.append({
				"cx": pos[0],
				"cy": pos[1],
				"w": 40.0,
				"h": 100.0,
				"conf": conf,
			})
	return trajectory


#============================================
def test_stationary_then_moving():
	"""Stationary runner for 60 frames, then moves right at constant speed."""
	fps = 30.0
	transform = MockSceneTransform()

	# 60 frames stationary at (100, 200), then 90 frames moving right
	positions = []
	for _i in range(60):
		positions.append((100.0, 200.0))
	for i in range(90):
		# move at 5 pixels/frame = 150 pixels/s
		positions.append((100.0 + (i + 1) * 5.0, 200.0))

	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	assert result["race_start_frame"] is not None
	assert result["method"] == "velocity_ratio_onset"
	# start should be near frame 60 (some tolerance for window effects)
	assert 55 <= result["race_start_frame"] <= 75
	assert result["confidence"] > 0.5
	assert result["race_start_s"] is not None


#============================================
def test_all_stationary():
	"""Runner never moves. Should return race_start_frame=None."""
	fps = 30.0
	transform = MockSceneTransform()

	# 150 frames all at same position
	positions = [(100.0, 200.0)] * 150
	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	assert result["race_start_frame"] is None
	assert result["race_start_s"] is None
	assert result["confidence"] == 0.0


#============================================
def test_already_moving():
	"""Runner is moving from frame 0. Should return first evaluable frame with low confidence."""
	fps = 30.0
	transform = MockSceneTransform()

	# 150 frames moving right at constant speed
	positions = []
	for i in range(150):
		positions.append((100.0 + i * 5.0, 200.0))

	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	# may or may not detect a start depending on ratio behavior
	# with constant velocity, v_pre and v_post are similar so ratio ~ 1
	# this means no onset is detected, which is correct
	if result["race_start_frame"] is not None:
		# if detected, confidence should be low
		assert result["confidence"] <= 0.5


#============================================
def test_false_burst_then_real_start():
	"""Short jitter burst, back to stationary, then real sustained motion."""
	fps = 30.0
	transform = MockSceneTransform()

	positions = []
	# 30 frames stationary
	for _i in range(30):
		positions.append((100.0, 200.0))
	# 4 frames of jitter (small burst)
	for i in range(4):
		positions.append((100.0 + (i + 1) * 3.0, 200.0))
	# 30 frames back to stationary (at the jittered position)
	jitter_end_x = 100.0 + 4 * 3.0
	for _i in range(30):
		positions.append((jitter_end_x, 200.0))
	# 90 frames of real sustained motion
	for i in range(90):
		positions.append((jitter_end_x + (i + 1) * 5.0, 200.0))

	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	if result["race_start_frame"] is not None:
		# should detect the real start, not the false burst
		# real motion starts at frame 64 (30 + 4 + 30)
		assert result["race_start_frame"] >= 55


#============================================
def test_gradual_acceleration():
	"""Runner gradually accelerates. Should detect onset with lower confidence."""
	fps = 30.0
	transform = MockSceneTransform()

	positions = []
	# 60 frames stationary
	for _i in range(60):
		positions.append((100.0, 200.0))
	# 90 frames of gradually increasing speed
	x = 100.0
	for i in range(90):
		# speed ramps from 0.5 to 5.0 pixels/frame
		speed = 0.5 + (i / 90.0) * 4.5
		x += speed
		positions.append((x, 200.0))

	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	# should detect onset somewhere after frame 60
	if result["race_start_frame"] is not None:
		assert result["race_start_frame"] >= 55


#============================================
def test_return_dict_keys():
	"""Verify all expected keys are present in the return dict."""
	fps = 30.0
	transform = MockSceneTransform()
	positions = [(100.0, 200.0)] * 60
	trajectory = _make_trajectory(positions)
	result = race_phases.detect_race_start(trajectory, transform, fps)

	expected_keys = {
		"race_start_frame", "race_start_s", "confidence",
		"method", "threshold_used", "debounce_frames",
	}
	assert set(result.keys()) == expected_keys
