"""Interval confidence metrics for track_runner.

Takes interval evidence from the forward interval path and backward interval path and
returns agreement score, identity score, competitor margin, and a
final confidence label.
"""

# Standard Library
import math

# PIP3 modules
import numpy


#============================================
def _compute_dice_coefficient(
	box_a: dict,
	box_b: dict,
) -> float:
	"""Compute Dice coefficient between two bounding boxes.

	Dice = 2 * intersection_area / (area_a + area_b).
	Result is in [0.0, 1.0] where 1.0 means identical boxes.

	Args:
		box_a: Dict with keys "cx", "cy", "w", "h" (center-format box).
		box_b: Dict with keys "cx", "cy", "w", "h" (center-format box).

	Returns:
		Float Dice coefficient in [0.0, 1.0].
	"""
	# convert center-format to corner-format rectangles
	a_x1 = box_a["cx"] - box_a["w"] / 2.0
	a_y1 = box_a["cy"] - box_a["h"] / 2.0
	a_x2 = box_a["cx"] + box_a["w"] / 2.0
	a_y2 = box_a["cy"] + box_a["h"] / 2.0

	b_x1 = box_b["cx"] - box_b["w"] / 2.0
	b_y1 = box_b["cy"] - box_b["h"] / 2.0
	b_x2 = box_b["cx"] + box_b["w"] / 2.0
	b_y2 = box_b["cy"] + box_b["h"] / 2.0

	# compute intersection rectangle
	inter_x1 = max(a_x1, b_x1)
	inter_y1 = max(a_y1, b_y1)
	inter_x2 = min(a_x2, b_x2)
	inter_y2 = min(a_y2, b_y2)

	# intersection area (zero if no overlap)
	inter_w = max(0.0, inter_x2 - inter_x1)
	inter_h = max(0.0, inter_y2 - inter_y1)
	intersection = inter_w * inter_h

	# individual areas
	area_a = box_a["w"] * box_a["h"]
	area_b = box_b["w"] * box_b["h"]

	# Dice coefficient: 2 * intersection / (area_a + area_b)
	total_area = area_a + area_b
	if total_area <= 0:
		return 0.0
	dice = 2.0 * intersection / total_area
	return dice


#============================================
def compute_meeting_point_errors(
	forward_path: list,
	backward_path: list,
) -> list:
	"""Compute per-frame center and scale errors between forward and backward interval paths.

	Args:
		forward_path: List of tracking state dicts from forward propagation.
			Each dict has keys "cx", "cy", "w", "h", "conf", "source".
		backward_path: List of tracking state dicts from backward propagation,
			already reversed to align frame-by-frame with forward_path.

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
def compute_agreement_debug(
	forward_path: list,
	backward_path: list,
	start_frame: int = 0,
) -> dict:
	"""Compute FWD/BWD agreement with per-frame diagnostic records.

	Returns the same aggregate Dice that compute_agreement() returns, plus
	per-frame records (iou, center distance, width/height ratios) and iou
	percentiles. This is meant for --debug runs so the agreement metric
	can be investigated empirically without changing the metric itself.

	Args:
		forward_path: List of tracking state dicts from forward propagation.
		backward_path: List of tracking state dicts from backward propagation,
			aligned frame-by-frame with forward_path.
		start_frame: Absolute frame index of forward_path[0], used to tag
			per-frame records with their absolute frame_index.

	Returns:
		Dict with keys:
			- agreement: float [0, 1], mean Dice (same as compute_agreement)
			- iou_p10, iou_p50, iou_p90: float percentile summary of per-frame Dice
			- per_frame: list of dicts with frame_index, fwd_cx, fwd_cy,
				fwd_w, fwd_h, bwd_cx, bwd_cy, bwd_w, bwd_h, iou,
				center_dist_px, size_ratio_w, size_ratio_h
	"""
	num_frames = min(len(forward_path), len(backward_path))
	if num_frames == 0:
		return {
			"agreement": 0.0,
			"iou_p10": 0.0, "iou_p50": 0.0, "iou_p90": 0.0,
			"per_frame": [],
		}

	per_frame = []
	ious = []
	for i in range(num_frames):
		fwd = forward_path[i]
		bwd = backward_path[i]
		iou = _compute_dice_coefficient(fwd, bwd)
		ious.append(iou)
		dx = float(fwd["cx"]) - float(bwd["cx"])
		dy = float(fwd["cy"]) - float(bwd["cy"])
		center_dist = math.sqrt(dx * dx + dy * dy)
		# ratios always >= 1.0: larger / smaller; 1.0 means identical size
		fwd_w = max(1.0, float(fwd.get("w", 0.0)))
		fwd_h = max(1.0, float(fwd.get("h", 0.0)))
		bwd_w = max(1.0, float(bwd.get("w", 0.0)))
		bwd_h = max(1.0, float(bwd.get("h", 0.0)))
		size_ratio_w = max(fwd_w, bwd_w) / min(fwd_w, bwd_w)
		size_ratio_h = max(fwd_h, bwd_h) / min(fwd_h, bwd_h)
		per_frame.append({
			"frame_index": start_frame + i,
			"fwd_cx": round(float(fwd["cx"]), 2),
			"fwd_cy": round(float(fwd["cy"]), 2),
			"fwd_w": round(fwd_w, 2),
			"fwd_h": round(fwd_h, 2),
			"bwd_cx": round(float(bwd["cx"]), 2),
			"bwd_cy": round(float(bwd["cy"]), 2),
			"bwd_w": round(bwd_w, 2),
			"bwd_h": round(bwd_h, 2),
			"iou": round(iou, 4),
			"center_dist_px": round(center_dist, 2),
			"size_ratio_w": round(size_ratio_w, 3),
			"size_ratio_h": round(size_ratio_h, 3),
		})

	return {
		"agreement": float(numpy.mean(ious)),
		"iou_p10": float(numpy.percentile(ious, 10)),
		"iou_p50": float(numpy.percentile(ious, 50)),
		"iou_p90": float(numpy.percentile(ious, 90)),
		"per_frame": per_frame,
	}


#============================================
def compute_agreement(forward_path: list, backward_path: list) -> float:
	"""Compute overall agreement score between forward and backward interval paths.

	Uses Dice coefficient (2*intersection / (area_a + area_b)) per frame,
	which naturally handles scale: two large boxes with high overlap score
	well regardless of absolute pixel size.

	Args:
		forward_path: List of tracking state dicts from forward propagation.
		backward_path: List of tracking state dicts from backward propagation,
			aligned frame-by-frame with forward_path.

	Returns:
		Float in [0.0, 1.0] where 1.0 means perfect agreement.
	"""
	num_frames = min(len(forward_path), len(backward_path))
	if num_frames == 0:
		return 0.0

	frame_scores = []
	for i in range(num_frames):
		fwd = forward_path[i]
		bwd = backward_path[i]
		# Dice coefficient captures both position and scale agreement
		# as a single area-overlap metric
		dice = _compute_dice_coefficient(fwd, bwd)
		frame_scores.append(dice)

	agreement = float(numpy.mean(frame_scores))
	return agreement


#============================================
def classify_confidence(
	agreement: float,
	identity: float,
	margin: float,
	interval_length: int = 0,
) -> tuple:
	"""Classify overall confidence from agreement, identity, and competitor margin.

	Four-tier decision grid:
		- agreement > 0.5 + margin > 0.5 -> "high"   (trusted)
		- agreement > 0.5 + margin > 0.2 -> "good"   (acceptable)
		- agreement > 0.2 + margin > 0.1 -> "fair"   (borderline)
		- everything else                 -> "low"    (needs seed)

	Short-interval promotion: intervals of 5 frames or fewer get bumped
	up one tier (low->fair, fair->good) since FWD/BWD barely propagated
	and agreement noise dominates. Never promotes to "high".

	Additional failure reasons are appended regardless of tier:
		- margin < 0.2                    -> "likely_identity_swap"
		- identity < 0.4                  -> "weak_appearance"

	Args:
		agreement: Float [0, 1], forward/backward agreement score.
		identity: Float [0, 1], average identity match score.
		margin: Float [0, 1], average separation from competitors.
		interval_length: Number of frames in the interval. When > 0 and
			<= 5, promotes confidence one tier (never to "high").

	Returns:
		Tuple of (confidence_label: str, failure_reasons: list of str).
	"""
	failure_reasons = []

	# four-tier classification
	if agreement > 0.5 and margin > 0.5:
		confidence = "high"
	elif agreement > 0.5 and margin > 0.2:
		confidence = "good"
		failure_reasons.append("low_separation")
	elif agreement > 0.2 and margin > 0.1:
		confidence = "fair"
		if agreement <= 0.5:
			failure_reasons.append("low_agreement")
		if margin <= 0.2:
			failure_reasons.append("low_separation")
	else:
		confidence = "low"
		failure_reasons.append("low_agreement")

	# short intervals: agreement is noisy, promote one tier
	short_interval = interval_length > 0 and interval_length <= 5
	if short_interval and confidence not in ("high",):
		tier_order = ["low", "fair", "good", "high"]
		idx = tier_order.index(confidence)
		confidence = tier_order[min(idx + 1, len(tier_order) - 1)]

	# additional reasons
	if margin < 0.2:
		failure_reasons.append("likely_identity_swap")
	if identity < 0.4:
		failure_reasons.append("weak_appearance")

	return (confidence, failure_reasons)


#============================================
def score_interval(
	forward_path: list,
	backward_path: list,
	identity_scores: list,
	competitor_margins: list,
) -> dict:
	"""Score an interval using forward/backward interval path evidence.

	Args:
		forward_path: List of tracking state dicts from forward propagation.
			Each dict has keys "cx", "cy", "w", "h", "conf", "source".
		backward_path: List of tracking state dicts from backward propagation,
			already reversed to align frame-by-frame with forward_path.
		identity_scores: List of per-frame identity match scores (float 0-1).
		competitor_margins: List of per-frame competitor margin scores (float 0-1).

	Returns:
		Dict with keys:
			- "agreement_score": float, forward/backward agreement [0, 1]
			- "identity_score": float, average identity match [0, 1]
			- "competitor_margin": float, average competitor separation [0, 1]
			- "confidence": str, "high", "good", "fair", or "low"
			- "failure_reasons": list of str
			- "meeting_point_error": list of per-frame error dicts
	"""
	# Compute agreement between forward and backward passes
	agreement_score = compute_agreement(forward_path, backward_path)

	# Average identity score across frames; default 0.0 if no data
	if identity_scores:
		identity_score = float(numpy.mean(identity_scores))
	else:
		identity_score = 0.0

	# Average competitor margin across frames; default 0.0 if no data
	if competitor_margins:
		competitor_margin = float(numpy.mean(competitor_margins))
	else:
		competitor_margin = 0.0

	# Classify confidence from the three aggregate signals
	confidence, failure_reasons = classify_confidence(
		agreement_score, identity_score, competitor_margin,
		interval_length=len(forward_path),
	)

	# Compute per-frame meeting point errors for diagnostic output
	meeting_point_error = compute_meeting_point_errors(forward_path, backward_path)

	result = {
		"agreement_score": agreement_score,
		"identity_score": identity_score,
		"competitor_margin": competitor_margin,
		"confidence": confidence,
		"failure_reasons": failure_reasons,
		"meeting_point_error": meeting_point_error,
	}
	return result


#============================================
# minimum-real-motion floor for velocity_consistency, in scene units per
# frame. Prevents the ratio median(|a|) / median(|v|) from blowing up when
# the runner is nearly stationary. Calibrated to roughly match the
# stationary baseline used in race_phases.py. Not a perfect constant
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

	Computes agreement (Dice FWD/BWD), velocity consistency (internal
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
		Dict with keys (interval_score_v2 schema):
			- agreement: float [0, 1], Dice FWD/BWD overlap
			- velocity_consistency: float [0, 1], internal smoothness (higher=better)
			- size_consistency: float [0, 1], box-size interpolation residual
			- motion_quality: float, set to 1.0 (computed during camera motion)
			- occlusion_fraction: float [0, 1], fraction in approx-seed spans
			- confidence_tier: str, "high"|"good"|"fair"|"low"
			- severity: str, "high"|"medium"|"low"
			- failure_reasons: list of str
			- warning_flags: list of str
	"""
	# compute agreement between forward and backward interval paths
	agreement = compute_agreement(forward_path, backward_path)

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
			if status in ("approximate", "obstructed"):
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
		confidence_tier = "high"
	elif agreement > 0.5 and velocity_consistency > 0.3:
		confidence_tier = "good"
	elif agreement > 0.2 and velocity_consistency > 0.2:
		confidence_tier = "fair"
	else:
		confidence_tier = "low"

	# tier modifiers
	tier_order = ["low", "fair", "good", "high"]

	# short intervals (<= 5 frames): promote one tier (never to high)
	if interval_len <= 5 and confidence_tier != "high":
		idx = tier_order.index(confidence_tier)
		confidence_tier = tier_order[min(idx + 1, len(tier_order) - 1)]

	# long intervals (> 10s of real time): demote one tier
	if interval_len > fps * 10.0:
		idx = tier_order.index(confidence_tier)
		confidence_tier = tier_order[max(idx - 1, 0)]

	# low motion quality: demote one tier
	if motion_quality < 0.5:
		idx = tier_order.index(confidence_tier)
		confidence_tier = tier_order[max(idx - 1, 0)]

	# high occlusion fraction: cap at fair
	if occlusion_fraction > 0.3:
		if tier_order.index(confidence_tier) > tier_order.index("fair"):
			confidence_tier = "fair"

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
		"confidence_tier": confidence_tier,
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
		intervals: List of interval dicts from diagnostics, each with
			'start_frame', 'end_frame', 'agreement_score', 'identity_score',
			'competitor_margin' keys.

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
		# combine metrics from adjacent intervals (v2 or v3 format)
		# detect format from first adjacent interval
		iscore = adjacent[0].get("interval_score", adjacent[0])
		is_v3 = "confidence_tier" in iscore
		agreements = []
		secondary_scores = []
		for iv in adjacent:
			iv_score = iv.get("interval_score", iv)
			if is_v3:
				# analytical v3: use agreement + velocity_consistency
				agreements.append(float(iv_score.get("agreement", 0.0)))
				secondary_scores.append(
					float(iv_score.get("velocity_consistency", 0.5)),
				)
			else:
				# legacy v2: use agreement_score + competitor_margin
				agreements.append(float(iv_score.get("agreement_score", 0.0)))
				secondary_scores.append(
					float(iv_score.get("competitor_margin", 0.0)),
				)
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


#============================================
# self-test for compute_seed_confidences
if __name__ == "__main__":
	# test with matching intervals
	test_seeds = [
		{"frame_index": 100},
		{"frame_index": 500},
		{"frame_index": 999},
	]
	test_intervals = [
		{
			"start_frame": 100, "end_frame": 500,
			"agreement_score": 0.9, "identity_score": 0.8,
			"competitor_margin": 0.6,
		},
		{
			"start_frame": 500, "end_frame": 999,
			"agreement_score": 0.3, "identity_score": 0.2,
			"competitor_margin": 0.1,
		},
	]
	result = compute_seed_confidences(test_seeds, test_intervals)
	# frame 100: one adjacent interval (start_frame=100)
	assert result[100]["adjacent_intervals"] == 1
	assert result[100]["label"] == "high"
	# frame 500: two adjacent intervals (end of first, start of second)
	assert result[500]["adjacent_intervals"] == 2
	# frame 999: one adjacent interval (end_frame=999)
	assert result[999]["adjacent_intervals"] == 1
	assert result[999]["label"] == "low"

	# test with no matching intervals
	orphan_seeds = [{"frame_index": 42}]
	orphan_result = compute_seed_confidences(orphan_seeds, test_intervals)
	assert orphan_result[42]["label"] == "unknown"
	assert orphan_result[42]["adjacent_intervals"] == 0

	print("all scoring self-tests passed")
