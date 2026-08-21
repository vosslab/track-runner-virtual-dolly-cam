"""Implementation for the track_runner target CLI mode."""

# Standard Library
import argparse

# local repo modules
import encode_analysis_report
import fastread_video
import modes.predictions as mode_predictions
import modes.shared as mode_shared
import review
import seeding
import state_io
import tr_paths


def _generate_race_start_target_frames(
	diagnostics: dict,
	fps: float,
	frame_count: int,
) -> list:
	"""Generate target frame list for --race-start mode.

	Args:
		diagnostics: Loaded diagnostics dict with race_start_interval and
			race_start_frame in pre_race_reference.
		fps: Video frame rate.
		frame_count: Total frame count for clamping.

	Returns:
		list: Sorted, deduped, clamped frame indices.

	Raises:
		RuntimeError: If diagnostics lacks required fields.
	"""
	pre_race_reference = diagnostics.get("pre_race_reference")
	if pre_race_reference is None:
		raise RuntimeError(
			"diagnostics missing pre_race_reference; "
			"run 'solve' first to detect race_start_frame"
		)

	# Race-start targeting consumes only the current interval-score record.
	if state_io.INTERVAL_SCORES_HEADER_KEY not in diagnostics:
		raise RuntimeError(
			"interval-score file missing current header; "
			"run 'solve' first to generate current interval scores"
		)
	header = diagnostics[state_io.INTERVAL_SCORES_HEADER_KEY]
	if header != state_io.INTERVAL_SCORES_HEADER_VALUE:
		raise RuntimeError(
			f"interval-score schema is version {header}; target --race-start "
			f"requires current schema v{state_io.INTERVAL_SCORES_HEADER_VALUE}. "
			"Run solve first to regenerate."
		)

	# Validate race_start_frame is present (fail loud on missing key);
	# the actual frame is not needed for selection -- the interval
	# bounds drive it.
	_ = int(pre_race_reference["race_start_frame"])
	race_start_interval = pre_race_reference["race_start_interval"]
	if len(race_start_interval) != 2:
		raise RuntimeError(
			"race_start_interval must have exactly 2 elements; "
			"run 'solve' first"
		)

	interval_low = int(race_start_interval[0])
	interval_high = int(race_start_interval[1])
	width = interval_high - interval_low
	if width < 1:
		raise RuntimeError(
			f"race_start_interval ({interval_low}, {interval_high}) has "
			f"non-positive width; run 'solve' first to regenerate"
		)

	# Frame selection scales with interval width so each refine pass
	# converges. Previously used fixed-second offsets (+/- 0.1, 0.25,
	# 0.5 s); on a 4-frame interval those span ~60x the interval and
	# the same far-away frames were proposed every pass. With width-
	# fraction offsets, every proposed frame lies inside
	# [interval_low, interval_high]:
	#   interval_low (last stationary seed)
	#   interval_low + 1/8 * width
	#   interval_low + 1/4 * width
	#   interval_low + 1/2 * width  (= race_start_frame midpoint)
	#   interval_low + 3/4 * width
	#   interval_low + 7/8 * width
	#   interval_high (first moving seed)
	# Dedupe + sort handles short intervals where fractions collapse.
	fractions = (0.0, 0.125, 0.25, 0.5, 0.75, 0.875, 1.0)
	frames = []
	for f in fractions:
		frames.append(interval_low + round(width * f))

	# Clamp to valid range
	clamped_frames = []
	for f in frames:
		clamped = max(0, min(f, frame_count - 1))
		clamped_frames.append(clamped)

	# Deduplicate and sort
	unique_frames = sorted(set(clamped_frames))

	return unique_frames


#============================================
def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
	video_context: fastread_video.VideoContext,
	video_identity: dict,
) -> None:
	"""Target mode: add seeds at weak interval frames with FWD/BWD overlays.

	Loads solved intervals and diagnostics, generates refinement targets
	filtered by severity, builds FWD/BWD predictions, and launches the
	interactive seed collection UI at those frames.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
		video_context: Resolved per-run routing; the target UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
		video_identity: Identity of the source video that owns saved seeds.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	# load seeds
	seeds = mode_shared._load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")
	fps = video_info["fps"]
	diag_data = mode_shared._ensure_target_diagnostics(
		args, cfg, video_info, seeds, diag_path, intervals_path,
	)

	# Check for sub-modes
	use_race_start_mode = getattr(args, "target_race_start", False)
	use_from_analyze_mode = getattr(args, "target_from_analyze", False)

	if use_from_analyze_mode:
		analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
		top_n = getattr(args, "top_n", None) or encode_analysis_report.ANALYZE_TOP_DEFAULT
		gap_top_n = getattr(args, "gap_top_n", None) or 0
		# pass existing seed frames so the loader skips regions already
		# seeded and can inject a midpoint for the largest seed gap.
		existing_seed_frames = [int(s["frame_index"]) for s in seeds]
		target_frames = encode_analysis_report.load_analyze_target_frames(
			analysis_path, video_info["frame_count"],
			top_n=top_n,
			existing_seed_frames=existing_seed_frames,
			gap_top_n=gap_top_n,
		)
		gaps_label = f", gaps {gap_top_n}" if gap_top_n else ""
		print(
			f"  loaded {len(target_frames)} target frames from "
			f"{analysis_path} (top {top_n}{gaps_label})"
		)
	elif use_race_start_mode:
		# Race-start target mode: fixed frame selection around detected race-start
		target_frames = _generate_race_start_target_frames(
			diag_data,
			fps,
			video_info["frame_count"],
		)
		# Print the contact sheet path
		contact_sheet_path = tr_paths.default_race_start_contact_sheet_path(
			args.input_file
		)
		print(f"  race-start confirmation: {contact_sheet_path}")
	else:
		# Standard target mode: generate refinement targets with optional severity filter
		severity = getattr(args, "severity", None)
		seed_interval = getattr(args, "seed_interval", 10.0)
		top_n = getattr(args, "top_n", None)

		# generate target frames
		target_frames = review.generate_refinement_targets(
			diag_data,
			mode="suggested",
			seed_interval=int(seed_interval * fps),
			severity=severity,
		)

		# apply --top slicing if requested. UX: --top N means "give me N
		# frames, period" -- if the severity floor or confidence-based
		# filter produces fewer than N candidates, supplement with the
		# next-worst intervals so the user always gets the requested count
		# (capped at the number of non-pre-race intervals available).
		if top_n is not None:
			intervals = diag_data.get("intervals", [])
			# every non-pre-race interval is a potential candidate, ranked
			# worst-first by rank_key. Pre-race is excluded per C4.
			candidates = [
				iv for iv in intervals
				if review.get_confidence_label(iv["interval_score"]) != "pre_race"
			]
			candidates.sort(key=review.rank_key)
			top_intervals = candidates[:top_n]

			if top_intervals:
				top_diag = dict(diag_data)
				top_diag["intervals"] = top_intervals
				# severity=None for the regenerate step: --top has already
				# selected the worst-N regardless of severity floor, so
				# re-applying the severity filter would discard frames the
				# user explicitly asked for.
				target_frames = review.generate_refinement_targets(
					top_diag,
					mode="suggested",
					seed_interval=int(seed_interval * fps),
					severity=None,
				)
				# Ensure every top-N interval contributes at least one
				# target frame (midpoint), even high/good-confidence ones
				# that identify_weak_spans skips. With --top N the user
				# expects N frames.
				target_set = set(target_frames)
				for iv in top_intervals:
					start_frame = int(iv["start_frame"])
					end_frame = int(iv["end_frame"])
					if any(start_frame <= f <= end_frame for f in target_set):
						continue
					target_set.add((start_frame + end_frame) // 2)
				target_frames = sorted(target_set)
			else:
				target_frames = []

			if len(top_intervals) < top_n:
				print(
					f"  hint: only {len(top_intervals)} intervals available "
					f"(--top={top_n} requested)"
				)

	if not target_frames:
		if use_from_analyze_mode:
			print("  no target frames from analyze report (run 'analyze' first)")
		elif use_race_start_mode:
			print("  no race-start frames selected")
		else:
			sev_label = f" at {severity}+ severity" if severity else ""
			print(f"  no weak intervals found{sev_label}")
		return

	if use_from_analyze_mode:
		print(f"  {len(target_frames)} target frames from analyze report")
	elif use_race_start_mode:
		print(f"  {len(target_frames)} race-start target frames")
	else:
		sev_label = f" ({severity}+ severity)" if severity else ""
		top_label = f" (top {top_n})" if top_n is not None else ""
		print(f"  {len(target_frames)} target frames from weak intervals{sev_label}{top_label}")

	# build FWD/BWD predictions from solved intervals with scores merged in
	predictions = mode_predictions.predictions_from_torso_box_coords(
		intervals_path, diag_path, fps, seeds=seeds,
	)
	if predictions:
		print(f"  loaded predictions for {len(predictions)} frames")

	# determine pass number
	existing_passes = [s["pass"] for s in seeds]
	next_pass = max(existing_passes) + 1 if existing_passes else 2

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# collect seeds at target frames with predictions overlay
	frame_list = ", ".join(str(f) for f in target_frames)
	print(f"  target frame list: {frame_list}")
	print(f"  collecting seeds at {len(target_frames)} weak interval frames...")
	updated_seeds = seeding.collect_seeds_at_frames(
		video_context.working_decode.path,
		target_frames,
		cfg,
		pass_number=next_pass,
		mode="target_refine",
		existing_seeds=seeds,
		predictions=predictions,
		debug=args.debug,
		save_callback=mode_shared._make_save_callback(seeds_path, video_identity),
		start_frame=start_frame,
	)
	# save updated seeds
	new_count = len(updated_seeds) - len(seeds)
	mode_shared._save_seeds_to_disk(updated_seeds, seeds_path, video_identity)
	print(f"saved {len(updated_seeds)} seeds to {seeds_path} "
		f"({new_count} new)")
