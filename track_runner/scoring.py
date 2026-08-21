"""Interval confidence metrics for track_runner.

Takes interval evidence from the forward interval path and backward interval path and
returns agreement score, identity score, competitor margin, and a
final confidence label.
"""

# Standard Library
import enum
import math

# PIP3 modules
import numpy

# local repo modules
import trajectory_confidence


# Track-runner schema versions are kept in lockstep per contract C9.
# Alias directly to tr_schema.SCHEMA_VERSION (the single authority);
# do not chain through state_io.
import tr_schema
INTERVAL_SCORE_SCHEMA_VERSION = tr_schema.SCHEMA_VERSION


#============================================
class ConfidenceTier(enum.IntEnum):
	"""Ordered interval confidence tiers for bounded promotions and demotions."""

	LOW = 0
	FAIR = 1
	GOOD = 2
	HIGH = 3

	@property
	def label(self) -> str:
		"""Return the persisted lower-case tier label."""
		return self.name.lower()


#============================================
def compute_meeting_point_errors(
	forward_path: list,
	backward_path: list,
) -> list:
	"""Compute per-frame center and scale errors between forward and backward interval paths.

	Args:
		forward_path: List of tracking state dicts from forward propagation.
			Each dict has keys "cx", "cy", "w", "h", "conf", "source".
		backward_path: List of tracking state dicts from backward propagation.
			Chronological from propagate_backward_analytical; aligned
			frame-by-frame with forward_path by shared slot convention
			(slot i for both is absolute frame start_frame + i).

	Returns:
		List of dicts with keys:
			- "frame_index": int, frame index
			- "center_err_px": float, Euclidean center distance in pixels
			- "scale_err_pct": float, fractional height difference (0.0 to 1.0+)
	"""
	errors = []
	# Iterate over the shorter of the two tracks to avoid index errors
	num_frames = min(len(forward_path), len(backward_path))
	for i in range(num_frames):
		fwd = forward_path[i]
		bwd = backward_path[i]
		# Compute Euclidean center distance
		dx = fwd["cx"] - bwd["cx"]
		dy = fwd["cy"] - bwd["cy"]
		center_err = float(numpy.sqrt(dx * dx + dy * dy))
		# Compute scale error as fractional height difference
		fwd_h = fwd["h"]
		bwd_h = bwd["h"]
		if fwd_h > 0 and bwd_h > 0:
			# Use mean height as reference so the ratio is symmetric
			mean_h = (fwd_h + bwd_h) / 2.0
			scale_err = abs(fwd_h - bwd_h) / mean_h
		else:
			scale_err = 1.0
		frame_error = {
			"frame_index": i,
			"center_err_px": center_err,
			"scale_err_pct": scale_err,
		}
		errors.append(frame_error)
	return errors


#============================================
# minimum-real-motion floor for velocity_consistency, in scene units per
# frame. Prevents the ratio median(|a|) / median(|v|) from blowing up when
# the runner is nearly stationary. Calibrated to roughly match the
# stationary baseline used by current interval scoring. Not a perfect constant
# (actual stationary speed depends on image scale) but a conservative
# lower bound so we never divide by tiny numbers.
VELOCITY_FLOOR_SCENE_UNITS_PER_FRAME = 1.5


#============================================
def _compute_velocity_smoothness(
	track: list,
	start_frame: int,
	scene_transform: object,
) -> float:
	"""Measure internal trajectory smoothness in scene coordinates.

	For each frame i (i >= 1) compute v_i = ||p_i - p_{i-1}|| in scene space
	(camera motion removed), then a_i = v_i - v_{i-1} for i >= 2. The metric
	is::

		1.0 - clamp(median(|a_i|) / max(median(|v_i|), v_floor), 0.0, 1.0)

	Smooth steady motion produces small |a_i| relative to typical |v_i|,
	giving scores near 1.0. Curved motion is unpenalized because only speed
	magnitudes appear in the ratio. Identity swaps, propagator failures, and
	frame drops produce acceleration spikes that lower the score.

	Args:
		track: List of state dicts with "cx", "cy" in pixel coords.
		start_frame: Absolute frame index of track[0].
		scene_transform: SceneTransform for pixel_to_scene conversion.

	Returns:
		Float in [0.0, 1.0]. Returns 1.0 for trivially short tracks (< 3
		frames) where acceleration is undefined.
	"""
	n = len(track)
	if n < 3:
		return 1.0

	# convert all centers to scene coordinates so camera motion is removed
	scene_xy = []
	for i, state in enumerate(track):
		frame_index = start_frame + i
		sx, sy = scene_transform.pixel_to_scene(
			frame_index, float(state["cx"]), float(state["cy"]),
		)
		scene_xy.append((sx, sy))

	# per-frame speed magnitudes in scene units per frame
	speeds = []
	for i in range(1, n):
		dx = scene_xy[i][0] - scene_xy[i - 1][0]
		dy = scene_xy[i][1] - scene_xy[i - 1][1]
		speeds.append(math.sqrt(dx * dx + dy * dy))

	# per-frame acceleration magnitudes (change in speed)
	accels = []
	for i in range(1, len(speeds)):
		accels.append(abs(speeds[i] - speeds[i - 1]))

	if not accels:
		return 1.0

	median_speed = float(numpy.median(speeds))
	median_accel = float(numpy.median(accels))
	# clamp denominator to the real-motion floor to avoid exploding ratios
	# when the runner is nearly stationary and the numerator is pure noise
	denom = max(median_speed, VELOCITY_FLOOR_SCENE_UNITS_PER_FRAME)
	ratio = median_accel / denom
	# clamp and invert so higher = smoother
	return max(0.0, min(1.0, 1.0 - ratio))


#============================================
def score_interval_analytical(
	forward_path: list,
	backward_path: list,
	all_seeds_scene: list,
	interval_curves: dict,
	scene_transform: object,
	motion_track: object = None,
	all_seeds: list = None,
	blended_path: list = None,
	fps: float = 30.0,
) -> dict:
	"""Score an interval using analytical velocity model metrics.

	Computes agreement from raw FWD/BWD center geometry, velocity consistency (internal
	trajectory smoothness in scene coordinates), size consistency
	(interpolation residual), and assigns confidence tier and failure reasons.

	Args:
		forward_path: List of tracking state dicts from propagate_forward_analytical.
		backward_path: List of tracking state dicts from propagate_backward_analytical.
		all_seeds_scene: List of all seeds as (frame, sx, sy, sw, sh) tuples
			in scene coordinates.
		motion_track: Optional MotionTrack for computing motion_quality.
		all_seeds: Optional list of original seed dicts for occlusion_fraction.
		interval_curves: Dict from fit_interval_curves with curve parameters.
		scene_transform: SceneTransform instance.
		blended_path: Optional list of blended FWD/BWD states (the blended interval path) used for the
			velocity_consistency smoothness computation. If None, falls back
			to forward_path.
		fps: Video frame rate. Used for the long-interval demotion threshold.

	Returns:
		Dict with interval score fields:
			- agreement: float [0, 1], raw FWD/BWD geometry agreement
			- velocity_consistency: float [0, 1], internal smoothness (higher=better)
			- size_consistency: float [0, 1], box-size interpolation residual
			- motion_quality: float, set to 1.0 (computed during camera motion)
			- occlusion_fraction: float [0, 1], fraction in approx-seed spans
			- confidence_tier: str, "high"|"good"|"fair"|"low"
			- severity: str, "high"|"medium"|"low"
			- failure_reasons: list of str
			- warning_flags: list of str
	"""
	# The confidence owner reads only independent raw FWD/BWD paths (C9).
	agreement = trajectory_confidence.interval_agreement(forward_path, backward_path)

	# velocity consistency: internal trajectory smoothness in scene coords
	# rationale: the previous LOO-slope metric measured whether a linear
	# extrapolation outside the interval matched external seeds. On curved
	# motion (real tracks) that metric systematically fails even for correct
	# tracking, because linear extrapolation is the wrong model. This metric
	# instead measures how jerky the blended interval path is relative to its
	# typical speed. Smooth motion on a curve scores high; identity swaps,
	# frame drops, or propagator failures inject spikes that drop the score.
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]

	# support-seed lists are only used for the sparse_support warning now
	support_seeds_left = []
	support_seeds_right = []
	for frame, sx, sy, sw, sh in all_seeds_scene:
		if frame < start_frame:
			support_seeds_left.append((frame, sx, sy, sw, sh))
		elif frame > end_frame:
			support_seeds_right.append((frame, sx, sy, sw, sh))

	velocity_consistency = _compute_velocity_smoothness(
		blended_path if blended_path is not None else forward_path,
		start_frame, scene_transform,
	)

	# compute size consistency: box height interpolation residual
	left_sw, left_sh = interval_curves["left_size"]
	right_sw, right_sh = interval_curves["right_size"]
	interval_length = float(end_frame - start_frame)
	if interval_length > 0:
		# average interpolation error over interval
		size_errors = []
		for i, state in enumerate(forward_path):
			frame_index = start_frame + i
			t = (frame_index - start_frame) / interval_length
			# expected height by linear interpolation
			expected_h = (1.0 - t) * left_sh + t * right_sh
			actual_h = float(state.get("h", expected_h))
			if expected_h > 0:
				rel_error = abs(actual_h - expected_h) / expected_h
				size_errors.append(rel_error)
		if size_errors:
			avg_size_error = float(numpy.mean(size_errors))
			size_consistency = max(0.0, 1.0 - avg_size_error)
		else:
			size_consistency = 1.0
	else:
		size_consistency = 1.0

	# motion_quality: mean phase-correlation response for this interval's frames
	if motion_track is not None and hasattr(motion_track, "quality"):
		q_arr = motion_track.quality
		# extract quality values for frames in this interval
		f_start = max(0, start_frame)
		f_end = min(len(q_arr), end_frame + 1)
		if f_end > f_start:
			interval_quality = q_arr[f_start:f_end]
			motion_quality = float(numpy.mean(interval_quality))
		else:
			motion_quality = 1.0
	else:
		motion_quality = 1.0

	# occlusion_fraction: fraction of interval frames in approximate-seed spans
	# a hidden span runs from an approximate seed to the next visible/partial seed
	occlusion_fraction = 0.0
	if all_seeds is not None:
		# count frames covered by approximate seeds within this interval
		hidden_frames = 0
		approx_frames = set()
		for seed in all_seeds:
			status = seed.get("status", "")
			frame = int(seed.get("frame_index", -1))
			if status == "approximate":
				if start_frame <= frame <= end_frame:
					approx_frames.add(frame)
		# for each approximate seed, count frames from it to the next
		# visible/partial seed (or interval endpoint) as hidden span
		visible_frames = set()
		for seed in all_seeds:
			status = seed.get("status", "")
			frame = int(seed.get("frame_index", -1))
			if status in ("visible", "partial"):
				if start_frame <= frame <= end_frame:
					visible_frames.add(frame)
		for af in sorted(approx_frames):
			# find next visible/partial frame after this approximate seed
			span_end = end_frame
			for vf in sorted(visible_frames):
				if vf > af:
					span_end = vf
					break
			hidden_frames += (span_end - af)
		if interval_length > 0:
			occlusion_fraction = hidden_frames / interval_length
			occlusion_fraction = min(1.0, occlusion_fraction)

	# confidence tier classification
	interval_len = len(forward_path)
	if agreement > 0.5 and velocity_consistency > 0.5 and size_consistency > 0.5:
		confidence_tier = ConfidenceTier.HIGH
	elif agreement > 0.5 and velocity_consistency > 0.3:
		confidence_tier = ConfidenceTier.GOOD
	elif agreement > 0.2 and velocity_consistency > 0.2:
		confidence_tier = ConfidenceTier.FAIR
	else:
		confidence_tier = ConfidenceTier.LOW

	# short intervals (<= 5 frames): promote one tier (never to high)
	if interval_len <= 5 and confidence_tier != ConfidenceTier.HIGH:
		confidence_tier = ConfidenceTier(min(confidence_tier + 1, ConfidenceTier.HIGH))

	# long intervals (> 10s of real time): demote one tier
	if interval_len > fps * 10.0:
		confidence_tier = ConfidenceTier(max(confidence_tier - 1, ConfidenceTier.LOW))

	# low motion quality: demote one tier
	if motion_quality < 0.5:
		confidence_tier = ConfidenceTier(max(confidence_tier - 1, ConfidenceTier.LOW))

	# high occlusion fraction: cap at fair
	if occlusion_fraction > 0.3:
		confidence_tier = min(confidence_tier, ConfidenceTier.FAIR)

	# failure reasons
	failure_reasons = []
	if agreement < 0.2:
		failure_reasons.append("low_agreement")
	if velocity_consistency < 0.5:
		failure_reasons.append("weak_motion_model")
	if occlusion_fraction > 0.3:
		failure_reasons.append("long_occlusion")
	if motion_quality < 0.5:
		failure_reasons.append("low_motion_quality")
	if len(support_seeds_left) + len(support_seeds_right) < 2:
		failure_reasons.append("sparse_support")

	# warning flags
	warning_flags = []
	if occlusion_fraction > 0.0:
		warning_flags.append("approximate_span")
	if len(support_seeds_left) < 2 or len(support_seeds_right) < 2:
		warning_flags.append("no_directional_support")
	if size_consistency < 0.5:
		warning_flags.append("scale_unstable")

	result = {
		"agreement": agreement,
		"velocity_consistency": velocity_consistency,
		"size_consistency": size_consistency,
		"motion_quality": motion_quality,
		"occlusion_fraction": occlusion_fraction,
		"confidence_tier": confidence_tier.label,
		"failure_reasons": failure_reasons,
		"warning_flags": warning_flags,
	}
	return result


#============================================
def compute_seed_confidences(
	seeds: list,
	intervals: list,
) -> dict:
	"""Compute confidence scores for each seed based on adjacent interval metrics.

	For each seed's frame_index, finds adjacent intervals where start_frame or
	end_frame matches, then combines their metrics into a composite score.

	Args:
		seeds: List of seed dicts with 'frame_index' keys.
		intervals: List of interval dicts from diagnostics with nested scores.

	Returns:
		Dict mapping frame_index (int) to {"score": float, "label": str,
		"adjacent_intervals": int}.
	"""
	confidences = {}
	for seed in seeds:
		fi = int(seed["frame_index"])
		# find intervals adjacent to this seed frame
		adjacent = []
		for iv in intervals:
			start_f = int(iv["start_frame"])
			end_f = int(iv["end_frame"])
			if start_f == fi or end_f == fi:
				adjacent.append(iv)
		if not adjacent:
			confidences[fi] = {
				"score": 0.0,
				"label": "unknown",
				"adjacent_intervals": 0,
			}
			continue
		# Combine metrics from adjacent current-format intervals.
		agreements = []
		secondary_scores = []
		for iv in adjacent:
			iv_score = iv["interval_score"]
			agreements.append(float(iv_score.get("agreement", 0.0)))
			secondary_scores.append(float(iv_score.get("velocity_consistency", 0.5)))
		avg_agreement = float(numpy.mean(agreements))
		avg_secondary = float(numpy.mean(secondary_scores))
		# weighted composite score
		score = 0.6 * avg_agreement + 0.4 * avg_secondary
		# classify label from composite score
		if score > 0.7:
			label = "high"
		elif score > 0.4:
			label = "medium"
		else:
			label = "low"
		confidences[fi] = {
			"score": round(score, 4),
			"label": label,
			"adjacent_intervals": len(adjacent),
		}
	return confidences
