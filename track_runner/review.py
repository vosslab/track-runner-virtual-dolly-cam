"""Weak span identification and seed suggestion for track_runner.

After interval solving, analyzes results to tell the user where to add
more seeds. Provides human-readable summaries and refinement target lists.
"""

# local repo modules
import interval_solver
import scoring


#============================================
# Human-readable explanation for each failure reason
_REASON_EXPLANATIONS = {
	"low_agreement": "forward/backward trajectories diverge",
	"weak_motion_model": "velocity model fit is weak or inconsistent",
	"long_occlusion": "long occlusion span reduces reliability",
	"low_motion_quality": "camera motion estimates are poor quality",
	"sparse_support": "too few directional support seeds for robust fitting",
}


#============================================
def build_interval_risk_view(
	seeds: list,
	motion_track: object,
	solve_artifact: dict,
) -> dict:
	"""Rebuild target-ranking evidence from durable solve inputs.

	The scoring and Stage-4 owners supply every computed value.  This function
	only joins their outputs into the cross-process view consumed by target.
	``scene_transform`` and runtime ``fps`` are deliberately ephemeral entries
	attached by the target-mode loader; neither is persisted here.
	"""
	if "scene_transform" not in solve_artifact:
		raise RuntimeError("target risk reconstruction requires a scene transform")
	if "fps" not in solve_artifact:
		raise RuntimeError("target risk reconstruction requires runtime video fps")
	video_identity = solve_artifact.get("video_identity")
	if not isinstance(video_identity, dict) or "frame_count" not in video_identity:
		raise RuntimeError("target risk reconstruction requires solve video identity")
	scene_transform = solve_artifact["scene_transform"]
	fps = float(solve_artifact["fps"])
	race_start = solve_artifact.get("race_start")
	race_start_interval = None
	race_start_frame = int(solve_artifact.get("race_start_frame", 0))
	if isinstance(race_start, dict):
		if "race_start_frame" in race_start:
			race_start_frame = int(race_start["race_start_frame"])
		if "race_start_interval" in race_start:
			interval = race_start["race_start_interval"]
			if len(interval) != 2:
				raise RuntimeError("solve artifact race_start_interval must have two frames")
			race_start_interval = (int(interval[0]), int(interval[1]))
	usable_seeds = interval_solver.filter_usable_seeds_sorted(seeds)
	by_bounds = {}
	for artifact_interval in solve_artifact.get("solved_intervals", {}).values():
		key = (int(artifact_interval["start_frame"]), int(artifact_interval["end_frame"]))
		by_bounds[key] = artifact_interval
	interval_results = []
	for pair_idx in range(len(usable_seeds) - 1):
		seed_start = usable_seeds[pair_idx]
		seed_end = usable_seeds[pair_idx + 1]
		start_frame = int(seed_start["frame_index"])
		end_frame = int(seed_end["frame_index"])
		artifact_interval = by_bounds.get((start_frame, end_frame))
		if artifact_interval is None:
			raise RuntimeError(
				f"solve artifact lacks interval ({start_frame}, {end_frame}); run solve first"
			)
		# solve_queue classifies a pair as pre-race only when it ends at or
		# before race_start_interval's low endpoint.  The spanning pair is a
		# normal analytical interval and must remain scoreable.
		is_pre_race = (race_start_interval is not None and
			end_frame <= race_start_interval[0]) or (
			artifact_interval.get("forward_path") is None
			and artifact_interval.get("backward_path") is None
			and artifact_interval.get("conf") is None
		)
		if is_pre_race:
			interval_score = scoring.score_pre_race_artifact_interval()
			result = {
				"start_frame": start_frame,
				"end_frame": end_frame,
				"source": "pre_race_reference",
				"interval_score": interval_score,
			}
		else:
			interval_score = scoring.score_interval_from_artifact(
				seed_start, seed_end, seeds, scene_transform, motion_track,
				artifact_interval, fps,
			)
			result = {
				"start_frame": start_frame,
				"end_frame": end_frame,
				"interval_score": interval_score,
			}
		interval_results.append(result)
	risk_by_pair = interval_solver.promotion_risk_by_pair(
		interval_results, usable_seeds, scene_transform, fps,
		int(video_identity["frame_count"]),
	)
	promoted_pairs = set(interval_solver.select_promoted_intervals(
		interval_results, usable_seeds, scene_transform, fps,
		int(video_identity["frame_count"]), race_start_frame,
	))
	view = {}
	for pair_idx, result in enumerate(interval_results):
		key = (int(result["start_frame"]), int(result["end_frame"]))
		interval_score = result["interval_score"]
		view[key] = {
			"risk": float(risk_by_pair.get(pair_idx, 0.0)),
			"severity": classify_interval_severity(result, fps) or "pre_race",
			"promoted": pair_idx in promoted_pairs,
			"failure_reasons": list(interval_score["failure_reasons"]),
			"interval_score": interval_score,
		}
	return view


#============================================
def target_intervals_from_risk_view(
	risk_view: dict,
	severity: str | None = None,
	top_n: int | None = None,
) -> list:
	"""Order target intervals from the assembled promotion-risk view.

	Default targeting includes exactly current-policy candidates with positive
	risk.  ``--top`` retains its explicit-request behavior: it selects the top
	non-pre-race intervals even when their current risk is zero.  No score
	metric is recomputed here.
	"""
	entries = []
	for (start_frame, end_frame), value in risk_view.items():
		if value["severity"] == "pre_race":
			continue
		if top_n is None:
			if float(value["risk"]) <= 0.0:
				continue
			if severity is not None:
				if _SEVERITY_RANK.get(value["severity"], 0) < _SEVERITY_RANK[severity]:
					continue
		entries.append((float(value["risk"]), int(start_frame), int(end_frame)))
	entries.sort(key=lambda entry: (-entry[0], entry[1]))
	if top_n is not None:
		entries = entries[:top_n]
	return entries


#============================================
def format_blend_commitment_review_item(state: dict | None) -> str | None:
	"""Return the review text for one in-memory blend decision.

	The blend owner supplies direction and transition alpha on in-memory
	trajectory states.  Review derives the explanation here instead of adding a
	persisted commitment-reason field to either trajectory artifact.

	Args:
		state: Optional blended trajectory state for one frame.

	Returns:
		Human-readable commitment text, or None when this frame is not part of
		a disagreement run.  An unavailable decision stays neutral: it names no
		winning direction and says that the baseline was retained.
	"""
	if state is None or not state.get("blend_flag", False):
		return None
	direction = state.get("commitment_direction")
	if direction == "unavailable":
		result = "Blend commitment unavailable; evidence unavailable; baseline retained"
		return result
	if direction not in ("fwd", "bwd"):
		return None
	alpha = float(state.get("commitment_alpha", 0.0))
	result = (
		f"Blend committed to {direction.upper()} at {alpha:.0%} transition; "
		"residual-motion evidence"
	)
	return result


#============================================
def get_confidence_label(score: dict) -> str:
	"""Extract confidence label from a current interval score.

	Args:
		score: Current nested interval score dict.

	Returns:
		Confidence label string (high, good, fair, or low).
	"""
	return score["confidence_tier"]


#============================================
def _midpoint_frame(start_frame: int, end_frame: int) -> int:
	"""Return the midpoint frame index between two frames.

	Args:
		start_frame: Start frame index.
		end_frame: End frame index.

	Returns:
		Integer frame index at the midpoint.
	"""
	return (start_frame + end_frame) // 2


#============================================
def _reason_to_suggestion(
	reason: str,
	start_frame: int,
	end_frame: int,
	fps: float,
) -> dict:
	"""Build a single seed suggestion from a failure reason.

	Places the suggestion at the midpoint of the interval by default.

	Args:
		reason: Failure reason string.
		start_frame: Interval start frame.
		end_frame: Interval end frame.
		fps: Frames per second.

	Returns:
		Seed suggestion dict with frame, time_s, reason, competitor_summary.
	"""
	if reason == "low_agreement":
		# disagreement often peaks in the middle
		frame = _midpoint_frame(start_frame, end_frame)
	else:
		# default: midpoint is a reasonable choice
		frame = _midpoint_frame(start_frame, end_frame)

	time_s = frame / max(1.0, fps)
	explanation = _REASON_EXPLANATIONS.get(reason, reason)
	suggestion = {
		"frame_index": frame,
		"time_s": time_s,
		"reason": reason,
		"competitor_summary": explanation,
	}
	return suggestion


# Severity tier ordering for comparisons
_SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}

# Duration threshold (seconds) for promoting severity one level
_DURATION_PROMOTE_THRESHOLD_S = 10.0

# Short intervals (< this many frames) produce noisy FWD/BWD metrics,
# so high severity is unconditionally demoted to medium
_SHORT_INTERVAL_FRAMES = 10


#============================================
def classify_interval_severity(interval: dict, fps: float) -> str:
	"""Classify an interval's weakness severity as high, medium, or low.

	Uses the current interval_score fields: agreement and confidence_tier.

	Pre-race intervals are synthesized geometry with perfect consistency
	metrics and are not quality-ranked; returns None for pre_race tiers
	so callers can skip severity classification.

	Args:
		interval: Interval dict with interval_score sub-dict, start_frame, end_frame.
		fps: Video frame rate for duration calculation.

	Returns:
		"high", "medium", or "low" severity string, or None for pre-race intervals.
	"""
	score = interval["interval_score"]
	# Pre-race intervals are not quality-ranked; skip severity classification
	if score.get("confidence_tier") == "pre_race":
		return None
	start_frame = int(interval["start_frame"])
	end_frame = int(interval["end_frame"])
	interval_frames = end_frame - start_frame

	agreement = float(score.get("agreement", 0.0))
	confidence_tier = score["confidence_tier"]
	if agreement >= 0.40 and confidence_tier in ("good", "high"):
		severity = "low"
	elif agreement < 0.20 or confidence_tier == "low":
		severity = "high"
	else:
		severity = "medium"

	# short-interval demotion: intervals under 10 frames are noisy,
	# demote high -> medium unconditionally
	if interval_frames < _SHORT_INTERVAL_FRAMES and severity == "high":
		severity = "medium"

	# duration-based promotion: intervals longer than threshold promote one level
	duration_s = interval_frames / max(1.0, fps)
	if duration_s > _DURATION_PROMOTE_THRESHOLD_S:
		if severity == "low":
			severity = "medium"
		elif severity == "medium":
			severity = "high"

	return severity


# Ascending sort: lower agreement sorts first, confidence tier
# breaks ties. pre_race is not a quality tier and sorts last.
_CONFIDENCE_RANK = {"low": 0, "fair": 1, "good": 2, "high": 3}


#============================================
def rank_key(interval: dict) -> tuple:
	"""Lexicographic sort key for ordering intervals worst-first.

	Sort ascending: the interval with the lowest agreement sorts
	first; confidence tier breaks ties (low < fair < good < high).
	Pre-race intervals are a separate class and sort to the end.

	Args:
		interval: Interval dict with 'interval_score' sub-dict
			containing agreement and confidence_tier.

	Returns:
		Tuple (agreement, confidence_tier_rank) suitable for
		passing to sorted(..., key=rank_key).
	"""
	score = interval["interval_score"]
	agreement = float(score.get("agreement", 0.0))
	conf = score.get("confidence_tier", "low")
	# pre_race is not on the quality axis; sort it to the end
	if conf == "pre_race":
		return (float('inf'), 999)
	return (agreement, _CONFIDENCE_RANK[conf])


#============================================
def identify_weak_spans(diagnostics: dict) -> list:
	"""Walk interval results and return seed suggestions for weak intervals.

	For each interval whose confidence is "low" or "fair", generates one or more
	seed suggestions with a specific frame, time, reason, and competitor summary.
	Args:
		diagnostics: Dict returned by interval_solver.solve_all_intervals().
			Must have "intervals" key with list of interval result dicts.
			Each interval result must have start_frame, end_frame, interval_score.

	Returns:
		List of seed suggestion dicts sorted by frame, each with:
			frame (int), time_s (float), reason (str), competitor_summary (str or None).
	"""
	intervals = diagnostics.get("intervals", [])
	fps = float(diagnostics.get("fps", 30.0))
	suggestions = []

	for interval in intervals:
		start_frame = int(interval["start_frame"])
		end_frame = int(interval["end_frame"])
		score = interval["interval_score"]
		confidence = get_confidence_label(score)

		# pre_race intervals are synthesized, not tracked; skip flagging
		if confidence == "pre_race":
			continue

		failure_reasons = list(score.get("failure_reasons", []))

		# only suggest seeds for low/fair confidence intervals
		if confidence in ("high", "good"):
			continue

		if failure_reasons:
			# one suggestion per failure reason
			for reason in failure_reasons:
				suggestion = _reason_to_suggestion(
					reason, start_frame, end_frame, fps,
				)
				suggestions.append(suggestion)
		else:
			# no specific reason: suggest midpoint
			frame = _midpoint_frame(start_frame, end_frame)
			time_s = frame / max(1.0, fps)
			suggestion = {
				"frame_index": frame,
				"time_s": time_s,
				"reason": "low_confidence",
				"competitor_summary": "interval scored below threshold",
			}
			suggestions.append(suggestion)

	# deduplicate by frame, keeping first occurrence
	seen_frames = set()
	unique_suggestions = []
	for s in suggestions:
		if s["frame_index"] not in seen_frames:
			seen_frames.add(s["frame_index"])
			unique_suggestions.append(s)

	# sort by frame index
	unique_suggestions.sort(key=lambda s: s["frame_index"])
	return unique_suggestions


#============================================
def generate_refinement_targets(
	diagnostics: dict,
	mode: str = "suggested",
	seed_interval: int = 300,
	gap_threshold: int = 600,
	time_range: tuple | None = None,
	severity: str | None = None,
) -> list:
	"""Generate frame numbers where new seeds should be placed.

	Supports three modes that can be combined with comma-separation:
	- "suggested": frames from weak span analysis
	- "interval": evenly spaced frames at seed_interval spacing
	- "gap": frames where existing seed spacing exceeds gap_threshold

	When severity is set, only intervals at or above the given severity
	tier are included. Hierarchy: "high" shows only high-severity;
	"medium" shows high + medium; "low" (or None) shows all.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().
		mode: Mode string: "suggested", "interval", "gap", or comma-separated
			combination such as "suggested,gap".
		seed_interval: Frame spacing for "interval" mode.
		gap_threshold: Minimum seed gap (frames) to trigger a suggestion
			in "gap" mode.
		time_range: Optional (start_s, end_s) tuple to restrict scope.
			None means no restriction.
		severity: Optional minimum severity tier ("high", "medium", or "low").
			None means include all weak intervals.

	Returns:
		Sorted, deduplicated list of frame numbers (ints).
	"""
	fps = float(diagnostics.get("fps", 30.0))
	intervals = diagnostics.get("intervals", [])

	# build a set of interval frame ranges that pass severity filter
	# so we can exclude suggestions from intervals below threshold
	# pre_race intervals are excluded from severity filtering
	min_rank = _SEVERITY_RANK.get(severity, 0) if severity is not None else 0
	excluded_intervals = set()
	if severity is not None:
		for idx, iv in enumerate(intervals):
			score = iv["interval_score"]
			confidence = get_confidence_label(score)
			# pre_race intervals never appear in severity-filtered suggestions
			if confidence == "pre_race":
				excluded_intervals.add(idx)
				continue
			if confidence in ("high", "good"):
				continue
			iv_severity = classify_interval_severity(iv, fps)
			if _SEVERITY_RANK.get(iv_severity, 0) < min_rank:
				excluded_intervals.add(idx)

	# determine frame range limits from time_range
	range_start = None
	range_end = None
	if time_range is not None:
		range_start = int(time_range[0] * fps)
		range_end = int(time_range[1] * fps)

	def _in_range(frame: int) -> bool:
		"""Return True if frame is within the optional time_range."""
		if range_start is not None and frame < range_start:
			return False
		if range_end is not None and frame > range_end:
			return False
		return True

	def _frame_in_excluded_interval(frame: int) -> bool:
		"""Return True if frame falls within an excluded interval."""
		for idx in excluded_intervals:
			iv = intervals[idx]
			if int(iv["start_frame"]) <= frame <= int(iv["end_frame"]):
				return True
		return False

	active_modes = [m.strip() for m in mode.split(",")]
	target_set = set()

	if "suggested" in active_modes:
		# use weak span suggestions, filtered by severity
		suggestions = identify_weak_spans(diagnostics)
		for s in suggestions:
			if _in_range(s["frame_index"]) and not _frame_in_excluded_interval(s["frame_index"]):
				target_set.add(s["frame_index"])
		# When the user passed --severity, ensure the target-frame count
		# matches the "severity breakdown" line printed at solve time
		# (cli.py:_print_quality_summary). That breakdown only counts
		# intervals with confidence NOT in {high, good} -- it is a
		# secondary severity tag layered on already-weak intervals.
		# Mirror that predicate here so `-s low` produces exactly the
		# same set of intervals the breakdown summed.
		if severity is not None:
			for idx, iv in enumerate(intervals):
				if idx in excluded_intervals:
					continue
				score = iv["interval_score"]
				confidence = get_confidence_label(score)
				if confidence in ("high", "good", "pre_race"):
					continue
				iv_severity = classify_interval_severity(iv, fps)
				if _SEVERITY_RANK.get(iv_severity, 0) < min_rank:
					continue
				start_frame = int(iv["start_frame"])
				end_frame = int(iv["end_frame"])
				if any(start_frame <= f <= end_frame for f in target_set):
					continue
				mid = _midpoint_frame(start_frame, end_frame)
				if _in_range(mid):
					target_set.add(mid)

	if "interval" in active_modes:
		# evenly spaced frames; find total frame span from intervals
		if intervals:
			overall_start = int(intervals[0]["start_frame"])
			overall_end = int(intervals[-1]["end_frame"])
			frame = overall_start + seed_interval
			while frame < overall_end:
				if _in_range(frame):
					target_set.add(frame)
				frame += seed_interval

	if "gap" in active_modes:
		# suggest frame at midpoint of any seed pair separated by more than threshold
		for idx, interval in enumerate(intervals):
			if idx in excluded_intervals:
				continue
			start_frame = int(interval["start_frame"])
			end_frame = int(interval["end_frame"])
			gap = end_frame - start_frame
			if gap > gap_threshold:
				mid = _midpoint_frame(start_frame, end_frame)
				if _in_range(mid):
					target_set.add(mid)

	targets = sorted(target_set)

	# enforce minimum gap between high-severity target frames
	if severity == "high" and targets:
		targets = _enforce_severity_gap(targets, diagnostics, fps)

	return targets


#============================================
def _enforce_severity_gap(
	targets: list,
	diagnostics: dict,
	fps: float,
	gap_seconds: float = 2.0,
) -> list:
	"""Filter high-severity targets so no two are within gap_seconds.

	When two frames are too close, keeps the one with lower agreement
	(worse tracking = higher priority for a new seed).

	Args:
		targets: Sorted list of target frame numbers.
		diagnostics: Dict with "intervals" key for score lookup.
		fps: Video frames per second.
		gap_seconds: Minimum seconds between kept frames.

	Returns:
		Filtered sorted list of frame numbers.
	"""
	intervals = diagnostics.get("intervals", [])
	min_gap_frames = int(gap_seconds * fps)

	# Build a lookup from frame to parent interval agreement.
	def _lookup_agreement(frame: int) -> float:
		"""Return agreement for the interval containing frame."""
		for iv in intervals:
			start = int(iv["start_frame"])
			end = int(iv["end_frame"])
			if start <= frame <= end:
				score = iv.get("interval_score", {})
				agreement = float(score.get("agreement", 0.0))
				return agreement
		# frame not in any interval: treat as worst possible
		return 0.0

	kept = []
	for frame in targets:
		if not kept or (frame - kept[-1]) >= min_gap_frames:
			# no conflict, keep this frame
			kept.append(frame)
		else:
			# too close to previous; keep whichever has lower agreement
			prev_score = _lookup_agreement(kept[-1])
			curr_score = _lookup_agreement(frame)
			if curr_score < prev_score:
				# current frame is worse, replace previous
				kept[-1] = frame
			# else: drop current frame (previous already has worse score)

	dropped_count = len(targets) - len(kept)
	if dropped_count > 0:
		print(f"  severity gap filter: dropped {dropped_count} frames "
			f"within {gap_seconds:.1f}s of another target")
	return kept


#============================================
def rank_target_frames_by_severity(
	diagnostics: dict,
	target_frames: list,
	max_count: int = 0,
) -> list:
	"""Rank target frames by parent interval severity and return top N.

	Each target frame is mapped to the interval that contains it. Frames
	are grouped by score tier (agreement, margin), worst tiers first.
	Within each tier, frames are spread evenly across the video for
	spatial coverage rather than clustering at the start.

	Args:
		diagnostics: Dict with "intervals" key.
		target_frames: List of frame numbers to rank.
		max_count: Maximum frames to return. 0 means no limit.

	Returns:
		List of frame numbers sorted by frame order, capped to max_count.
	"""
	intervals = diagnostics.get("intervals", [])

	# build lookup: for each target frame, find parent interval scores
	frame_scores = {}
	for frame in target_frames:
		# find the interval containing this frame
		best_score = None
		for iv in intervals:
			start = int(iv["start_frame"])
			end = int(iv["end_frame"])
			if start <= frame <= end:
				best_score = iv["interval_score"]
				break
		if best_score is None:
			# frame not in any interval, assign worst possible score
			agreement = 0.0
			margin = 0.0
		else:
			# pre_race intervals are synthesized and not on the quality axis
			conf_tier = best_score.get("confidence_tier")
			if conf_tier == "pre_race":
				# synthesized intervals sort to the end with a sentinel score
				agreement = float('inf')
				margin = 0.0
			else:
				agreement = float(best_score.get("agreement", 0.0))
				margin = float(best_score.get("velocity_consistency", 0.0))
		# round to bin nearby scores into the same tier
		frame_scores[frame] = (round(agreement, 2), round(margin, 2))

	# group frames by score tier
	tiers = {}
	for frame in target_frames:
		key = frame_scores[frame]
		if key not in tiers:
			tiers[key] = []
		tiers[key].append(frame)

	# sort tiers worst-first (lowest agreement, then lowest margin)
	sorted_tier_keys = sorted(tiers.keys())

	# collect frames tier by tier, subsampling within each tier
	# for even spatial coverage when a tier exceeds remaining budget
	selected = []
	remaining = max_count if max_count > 0 else len(target_frames)
	for key in sorted_tier_keys:
		tier_frames = sorted(tiers[key])
		if len(tier_frames) <= remaining:
			# take all frames from this tier
			selected.extend(tier_frames)
			remaining -= len(tier_frames)
		else:
			# subsample evenly across this tier for spatial coverage
			step = len(tier_frames) / remaining
			for i in range(remaining):
				idx = int(i * step)
				selected.append(tier_frames[idx])
			remaining = 0
		if remaining <= 0:
			break

	# return in frame order for sequential playback
	selected.sort()
	return selected


#============================================
def format_review_summary(diagnostics: dict) -> str:
	"""Produce a human-readable summary of all intervals with scores and suggestions.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().

	Returns:
		Multi-line string suitable for printing to the terminal.
	"""
	fps = float(diagnostics.get("fps", 30.0))
	intervals = diagnostics.get("intervals", [])
	suggestions = identify_weak_spans(diagnostics)

	# index suggestions by the interval they fall in for easy lookup
	lines = []
	lines.append("=== Track Runner Review Summary ===")
	lines.append(f"Intervals: {len(intervals)}")

	# count intervals by confidence tier (pre_race as separate class)
	from collections import Counter
	tier_counts = Counter(
		get_confidence_label(iv["interval_score"])
		for iv in intervals
	)
	need_seed = tier_counts.get("low", 0) + tier_counts.get("fair", 0)
	pre_race_count = tier_counts.get("pre_race", 0)
	analytic_count = len(intervals) - pre_race_count
	lines.append(
		f"Confidence tiers: "
		f"{tier_counts.get('high', 0)} high, "
		f"{tier_counts.get('good', 0)} good, "
		f"{tier_counts.get('fair', 0)} fair, "
		f"{tier_counts.get('low', 0)} low"
		f"{f' ({pre_race_count} pre-race)' if pre_race_count > 0 else ''}"
	)
	lines.append(f"Need seeds: {need_seed} / {analytic_count}")
	lines.append("")

	_confidence_labels = {
		"high": "TRUST", "good": "GOOD", "fair": "FAIR", "low": "WEAK",
		"pre_race": "SYNTH",
	}
	for iv in intervals:
		start_frame = int(iv["start_frame"])
		end_frame = int(iv["end_frame"])
		duration_s = (end_frame - start_frame) / max(1.0, fps)
		score = iv["interval_score"]
		confidence = get_confidence_label(score)
		reasons = score.get("failure_reasons", [])

		# format verdict label
		tag = _confidence_labels.get(confidence, "WEAK")
		if confidence == "pre_race":
			# synthesized pre-race intervals: no failure reasons, just tag
			verdict = f"[{tag}]"
		elif confidence in ("high", "good"):
			verdict = f"[{tag}]"
		else:
			reason_str = ", ".join(reasons) if reasons else "low_confidence"
			verdict = f"[{tag}: {reason_str}]"

		agree = float(score.get("agreement", 0.0))
		vel_cons = float(score.get("velocity_consistency", 0.0))
		size_cons = float(score.get("size_consistency", 0.0))
		metrics_str = (
			f"agree={agree:.2f}  "
			f"vel_cons={vel_cons:.2f}  "
			f"size_cons={size_cons:.2f}"
		)

		line = (
			f"  interval {start_frame:5d}-{end_frame:5d} "
			f"({duration_s:.1f}s)  "
			f"{metrics_str}  {verdict}"
		)
		lines.append(line)

	# list seed suggestions
	if suggestions:
		lines.append("")
		lines.append("Suggested seed frames:")
		for s in suggestions:
			time_s = float(s["time_s"])
			frame = int(s["frame_index"])
			reason = s["reason"]
			summary = s.get("competitor_summary") or ""
			lines.append(f"  frame {frame:5d}  ({time_s:.1f}s)  {reason}  -- {summary}")
	else:
		lines.append("")
		lines.append("No additional seeds suggested.")

	summary = "\n".join(lines)
	return summary


#============================================
def needs_refinement(diagnostics: dict) -> bool:
	"""Return True if any interval has low or fair confidence.

	Only low and fair tiers need additional seeds. High and good
	are considered acceptable.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().

	Returns:
		True if at least one interval needs refinement.
	"""
	intervals = diagnostics.get("intervals", [])
	for iv in intervals:
		score = iv["interval_score"]
		confidence = get_confidence_label(score)
		if confidence in ("low", "fair"):
			return True
	return False
