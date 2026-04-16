"""Race phase detection from solved trajectory.

Post-hoc interpretation layer: reads a finished trajectory and produces
a derived signal indicating when the race starts. Does not modify the
trajectory or affect solve behavior.

This module is the start of a race semantics layer. Future additions
(race end detection, split detection, pacing analysis) belong here.
"""

# Standard Library
import math
import statistics

# Timing constants (seconds, converted to frames via fps)
PRE_WINDOW_S = 0.75		# trailing window for baseline velocity
POST_WINDOW_S = 0.20		# leading window for onset velocity
DEBOUNCE_S = 0.25			# sustained motion required
MEDIAN_FILTER_S = 0.17		# jitter smoothing window
BASELINE_S = 1.5			# early period for stationary baseline estimate

# Detection thresholds
R_ENTER = 4.0				# ratio to trigger onset
R_EXIT = 2.0				# ratio below which debounce resets
BASELINE_PERCENTILE = 0.25	# use lowest 25% of early velocities for baseline
MIN_CONFIDENCE_THRESHOLD = 0.3	# minimum trajectory confidence to use a frame

# Hard floor for T_min (scene units per second) -- prevents zero baseline
T_MIN_FLOOR = 0.5

# Confidence scaling
R_HIGH = 5.0				# ratio for full confidence

# Minimum detection confidence required before the detected race start
# is allowed to gate downstream consumers (e.g., motion-cue fusion).
# Below this, consumers treat the result as "not detected" rather than
# suppressing frames based on a weak signal.
GATE_MIN_CONFIDENCE = 0.3


#============================================
def _compute_scene_velocities(
	trajectory: list,
	scene_transform,
	fps: float,
) -> list:
	"""Convert trajectory to per-frame scene-space velocities.

	Args:
		trajectory: List of frame dicts (or None) with cx, cy, conf keys.
		scene_transform: SceneTransform instance for pixel-to-scene conversion.
		fps: Video frame rate.

	Returns:
		List of (velocity_scene_units_per_s, is_valid) tuples, one per frame.
		velocity is None for frames that cannot be computed.
	"""
	n_frames = len(trajectory)
	# convert all positions to scene coordinates
	scene_positions = []
	for i in range(n_frames):
		state = trajectory[i]
		if state is None:
			scene_positions.append(None)
			continue
		conf = state.get("conf", 0.0)
		if conf < MIN_CONFIDENCE_THRESHOLD:
			scene_positions.append(None)
			continue
		sx, sy = scene_transform.pixel_to_scene(i, state["cx"], state["cy"])
		scene_positions.append((sx, sy))

	# compute frame-to-frame displacement in scene space
	velocities = []
	for i in range(n_frames):
		if i == 0 or scene_positions[i] is None or scene_positions[i - 1] is None:
			velocities.append(None)
			continue
		dx = scene_positions[i][0] - scene_positions[i - 1][0]
		dy = scene_positions[i][1] - scene_positions[i - 1][1]
		# displacement per frame, convert to per second
		disp_per_frame = math.sqrt(dx * dx + dy * dy)
		disp_per_s = disp_per_frame * fps
		velocities.append(disp_per_s)

	# apply median filter for jitter smoothing
	filter_half = max(1, int(MEDIAN_FILTER_S * fps / 2))
	smoothed = []
	for i in range(n_frames):
		if velocities[i] is None:
			smoothed.append(None)
			continue
		# gather neighborhood values
		window_vals = []
		for j in range(max(0, i - filter_half), min(n_frames, i + filter_half + 1)):
			if velocities[j] is not None:
				window_vals.append(velocities[j])
		if len(window_vals) == 0:
			smoothed.append(None)
		else:
			smoothed.append(statistics.median(window_vals))

	return smoothed


#============================================
def _estimate_stationary_baseline(
	velocities: list,
	fps: float,
) -> float:
	"""Estimate the stationary velocity baseline from early frames.

	Uses the lowest percentile of velocities in the first BASELINE_S seconds
	to produce a robust estimate that is not inflated by slight pre-start motion.

	Args:
		velocities: List of velocity values (or None), one per frame.
		fps: Video frame rate.

	Returns:
		Baseline velocity in scene units per second. At least T_MIN_FLOOR.
	"""
	baseline_frames = int(BASELINE_S * fps)
	early_vals = []
	for i in range(min(baseline_frames, len(velocities))):
		if velocities[i] is not None:
			early_vals.append(velocities[i])

	if len(early_vals) == 0:
		return T_MIN_FLOOR

	# sort and take lowest percentile
	early_vals.sort()
	cutoff = max(1, int(len(early_vals) * BASELINE_PERCENTILE))
	low_vals = early_vals[:cutoff]
	baseline = statistics.median(low_vals)

	# apply floor
	return max(baseline, T_MIN_FLOOR)


#============================================
def _scan_for_onset(
	velocities: list,
	fps: float,
	t_min: float,
) -> tuple:
	"""Scan velocity series for race start onset.

	Uses sliding pre/post windows with ratio-based detection, debounce,
	and hysteresis.

	Args:
		velocities: Smoothed velocity list (or None per frame).
		fps: Video frame rate.
		t_min: Absolute velocity floor (scene units per second).

	Returns:
		Tuple of (start_frame, confidence) or (None, 0.0).
	"""
	n_frames = len(velocities)
	pre_window = max(2, int(PRE_WINDOW_S * fps))
	post_window = max(2, int(POST_WINDOW_S * fps))
	debounce = max(2, int(DEBOUNCE_S * fps))

	# epsilon for safe division, derived from t_min
	epsilon = t_min * 0.1

	# only start scanning where pre-window is fully populated
	scan_start = pre_window

	candidate_frame = None
	consecutive_count = 0

	for i in range(scan_start, n_frames):
		# gather pre-window velocities (trailing)
		pre_vals = []
		for j in range(i - pre_window, i):
			if 0 <= j < n_frames and velocities[j] is not None:
				pre_vals.append(velocities[j])
		# gather post-window velocities (leading)
		post_vals = []
		for j in range(i, min(n_frames, i + post_window)):
			if velocities[j] is not None:
				post_vals.append(velocities[j])

		if len(pre_vals) == 0 or len(post_vals) == 0:
			# reset debounce on gaps
			candidate_frame = None
			consecutive_count = 0
			continue

		v_pre = statistics.median(pre_vals)
		v_post = statistics.median(post_vals)
		v_pre_safe = max(v_pre, epsilon)
		ratio = v_post / v_pre_safe

		if candidate_frame is None:
			# looking for onset
			if ratio >= R_ENTER and v_post >= t_min:
				candidate_frame = i
				consecutive_count = 1
		else:
			# in debounce: verify signal holds
			if ratio < R_EXIT or v_post < t_min:
				# signal collapsed, reset
				candidate_frame = None
				consecutive_count = 0
			else:
				consecutive_count += 1
				if consecutive_count >= debounce:
					# onset confirmed
					confidence = _compute_confidence(v_post, v_pre_safe)
					return (candidate_frame, confidence)

	return (None, 0.0)


#============================================
def _compute_confidence(v_post: float, v_pre_safe: float) -> float:
	"""Compute continuous confidence from velocity ratio.

	Args:
		v_post: Post-window median velocity.
		v_pre_safe: Pre-window median velocity (clamped above epsilon).

	Returns:
		Confidence value in [0.0, 1.0].
	"""
	ratio = v_post / v_pre_safe
	if ratio <= 1.0:
		return 0.0
	raw = math.log(ratio) / math.log(R_HIGH)
	return max(0.0, min(1.0, raw))


#============================================
def detect_race_start(
	trajectory: list,
	scene_transform,
	fps: float,
) -> dict:
	"""Detect the frame where the target runner transitions to sustained motion.

	Post-hoc analysis of a solved trajectory. Does not modify the trajectory.

	Args:
		trajectory: List of per-frame state dicts (or None), indexed by frame.
		scene_transform: SceneTransform for pixel-to-scene coordinate conversion.
		fps: Video frame rate in frames per second.

	Returns:
		Dict with keys:
			race_start_frame: int or None (frame index of race start)
			race_start_s: float or None (start time in seconds)
			confidence: float in [0.0, 1.0]
			method: str identifying the detection method
			threshold_used: float (T_min in scene units/s)
			debounce_frames: int (debounce window in frames)
	"""
	debounce_frames_used = max(2, int(DEBOUNCE_S * fps))

	# compute scene-space velocities
	velocities = _compute_scene_velocities(trajectory, scene_transform, fps)

	# estimate stationary baseline
	t_min = _estimate_stationary_baseline(velocities, fps)

	# scan for onset
	race_start_frame, confidence = _scan_for_onset(velocities, fps, t_min)

	# handle "already moving at first evaluable frame"
	pre_window = max(2, int(PRE_WINDOW_S * fps))
	if race_start_frame is not None and race_start_frame == pre_window:
		# no stationary pre-phase observed, reduce confidence
		confidence = min(confidence, 0.4)

	# build result
	race_start_s = None
	if race_start_frame is not None:
		race_start_s = round(race_start_frame / fps, 3)

	result = {
		"race_start_frame": race_start_frame,
		"race_start_s": race_start_s,
		"confidence": round(confidence, 4),
		"method": "velocity_ratio_onset",
		"threshold_used": round(t_min, 4),
		"debounce_frames": debounce_frames_used,
	}
	return result
