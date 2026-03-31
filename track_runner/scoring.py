"""Interval confidence metrics for track_runner.

Takes interval evidence from forward and backward tracking passes and
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
	forward_track: list,
	backward_track: list,
) -> list:
	"""Compute per-frame center and scale errors between forward and backward tracks.

	Args:
		forward_track: List of tracking state dicts from forward propagation.
			Each dict has keys "cx", "cy", "w", "h", "conf", "source".
		backward_track: List of tracking state dicts from backward propagation,
			already reversed to align frame-by-frame with forward_track.

	Returns:
		List of dicts with keys:
			- "frame": int, frame index
			- "center_err_px": float, Euclidean center distance in pixels
			- "scale_err_pct": float, fractional height difference (0.0 to 1.0+)
	"""
	errors = []
	# Iterate over the shorter of the two tracks to avoid index errors
	num_frames = min(len(forward_track), len(backward_track))
	for i in range(num_frames):
		fwd = forward_track[i]
		bwd = backward_track[i]
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
			"frame": i,
			"center_err_px": center_err,
			"scale_err_pct": scale_err,
		}
		errors.append(frame_error)
	return errors


#============================================
def compute_agreement(forward_track: list, backward_track: list) -> float:
	"""Compute overall agreement score between forward and backward tracks.

	Uses Dice coefficient (2*intersection / (area_a + area_b)) per frame,
	which naturally handles scale: two large boxes with high overlap score
	well regardless of absolute pixel size.

	Args:
		forward_track: List of tracking state dicts from forward propagation.
		backward_track: List of tracking state dicts from backward propagation,
			aligned frame-by-frame with forward_track.

	Returns:
		Float in [0.0, 1.0] where 1.0 means perfect agreement.
	"""
	num_frames = min(len(forward_track), len(backward_track))
	if num_frames == 0:
		return 0.0

	frame_scores = []
	for i in range(num_frames):
		fwd = forward_track[i]
		bwd = backward_track[i]
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
	forward_track: list,
	backward_track: list,
	identity_scores: list,
	competitor_margins: list,
) -> dict:
	"""Score an interval using forward/backward track evidence.

	Args:
		forward_track: List of tracking state dicts from forward propagation.
			Each dict has keys "cx", "cy", "w", "h", "conf", "source".
		backward_track: List of tracking state dicts from backward propagation,
			already reversed to align frame-by-frame with forward_track.
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
	agreement_score = compute_agreement(forward_track, backward_track)

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
		interval_length=len(forward_track),
	)

	# Compute per-frame meeting point errors for diagnostic output
	meeting_point_error = compute_meeting_point_errors(forward_track, backward_track)

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
def score_interval_analytical(
	forward_track: list,
	backward_track: list,
	all_seeds_scene: list,
	interval_curves: dict,
	scene_transform: object,
	motion_track: object = None,
	all_seeds: list = None,
) -> dict:
	"""Score an interval using analytical velocity model metrics.

	Computes agreement (Dice FWD/BWD), velocity consistency (LOO prediction
	error), size consistency (interpolation residual), and assigns confidence
	tier and failure reasons.

	Args:
		forward_track: List of tracking state dicts from propagate_forward_analytical.
		backward_track: List of tracking state dicts from propagate_backward_analytical.
		all_seeds_scene: List of all seeds as (frame, sx, sy, sw, sh) tuples
			in scene coordinates.
		motion_track: Optional MotionTrack for computing motion_quality.
		all_seeds: Optional list of original seed dicts for occlusion_fraction.
		interval_curves: Dict from fit_interval_curves with curve parameters.
		scene_transform: SceneTransform instance.

	Returns:
		Dict with keys (interval_score_v2 schema):
			- agreement: float [0, 1], Dice FWD/BWD overlap
			- velocity_consistency: float [0, 1], LOO prediction error (higher=better)
			- size_consistency: float [0, 1], box-size interpolation residual
			- motion_quality: float, set to 1.0 (computed during camera motion)
			- occlusion_fraction: float [0, 1], fraction in approx-seed spans
			- confidence_tier: str, "high"|"good"|"fair"|"low"
			- severity: str, "high"|"medium"|"low"
			- failure_reasons: list of str
			- warning_flags: list of str
	"""
	# compute agreement between forward and backward tracks
	agreement = compute_agreement(forward_track, backward_track)

	# compute velocity consistency: LOO prediction error for support seeds
	start_frame = interval_curves["start_frame"]
	end_frame = interval_curves["end_frame"]

	# find directional support seeds for LOO analysis
	# collect seeds near but not at endpoints
	support_seeds_left = []
	support_seeds_right = []
	for frame, sx, sy, sw, sh in all_seeds_scene:
		if frame < start_frame:
			support_seeds_left.append((frame, sx, sy, sw, sh))
		elif frame > end_frame:
			support_seeds_right.append((frame, sx, sy, sw, sh))

	# compute LOO velocity consistency using slope prediction error
	# for each support seed, compare its actual position to the position
	# predicted by the slope estimated from the OTHER support seeds
	velocity_errors = []
	left_pos = interval_curves["left_pos"]
	right_pos = interval_curves["right_pos"]
	left_frame = start_frame
	right_frame = end_frame
	# check left-side support seeds: predict their position from interval slope
	for seed_data in support_seeds_left[-4:]:
		frame, sx, sy, _, _ = seed_data
		# predict from left endpoint using FWD slope
		fwd_slopes = interval_curves["fwd_slopes"]
		dt = float(frame - left_frame)
		pred_sx = left_pos[0] + fwd_slopes[0] * dt
		pred_sy = left_pos[1] + fwd_slopes[1] * dt
		# error normalized by interval span
		dist = math.sqrt((pred_sx - sx) ** 2 + (pred_sy - sy) ** 2)
		# normalize by box size for scale-invariance
		left_sh = interval_curves["left_size"][1]
		norm_error = dist / max(left_sh, 1.0)
		velocity_errors.append(min(norm_error, 1.0))
	# check right-side support seeds: predict from right endpoint using BWD slope
	for seed_data in support_seeds_right[:4]:
		frame, sx, sy, _, _ = seed_data
		bwd_slopes = interval_curves["bwd_slopes"]
		dt = float(frame - right_frame)
		pred_sx = right_pos[0] + bwd_slopes[0] * dt
		pred_sy = right_pos[1] + bwd_slopes[1] * dt
		dist = math.sqrt((pred_sx - sx) ** 2 + (pred_sy - sy) ** 2)
		right_sh = interval_curves["right_size"][1]
		norm_error = dist / max(right_sh, 1.0)
		velocity_errors.append(min(norm_error, 1.0))

	if velocity_errors:
		# invert so higher = better consistency
		avg_error = float(numpy.mean(velocity_errors))
		velocity_consistency = max(0.0, 1.0 - avg_error)
	else:
		# no support seeds: neutral score
		velocity_consistency = 0.5

	# compute size consistency: box height interpolation residual
	left_sw, left_sh = interval_curves["left_size"]
	right_sw, right_sh = interval_curves["right_size"]
	interval_length = float(end_frame - start_frame)
	if interval_length > 0:
		# average interpolation error over interval
		size_errors = []
		for i, state in enumerate(forward_track):
			frame_idx = start_frame + i
			t = (frame_idx - start_frame) / interval_length
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
	interval_len = len(forward_track)
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

	# long intervals (> 10s): demote one tier
	fps_val = 30.0
	if interval_len > fps_val * 10.0:
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

	# severity
	if confidence_tier == "low" or failure_reasons:
		severity = "high"
	elif confidence_tier == "fair":
		severity = "medium"
	else:
		severity = "low"

	# short-interval demotion: intervals < 10 frames demote high -> medium
	if interval_len < 10 and severity == "high":
		severity = "medium"

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
		"severity": severity,
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
