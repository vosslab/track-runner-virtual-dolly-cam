"""Implementation for the track_runner edit CLI mode."""

# Standard Library
import argparse
import os

# local repo modules
import fastread_video
import modes.predictions as mode_predictions
import modes.shared as mode_shared
import scoring
import seed_editor
import review
import state_io


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
	"""Seed editor mode: review/fix/delete existing seeds interactively.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
		video_context: Resolved per-run routing; the editor UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
		video_identity: Identity of the source video that owns edited seeds.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	seeds = mode_shared._load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds to edit in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	# compute seed confidence scores from diagnostics if available
	seed_confidences = None
	if os.path.isfile(diag_path):
		diag_data = state_io.load_interval_scores(diag_path)
		if diag_data.get("intervals"):
			# compute seed confidence scores from interval diagnostics
			seed_confidences = scoring.compute_seed_confidences(
				seeds, diag_data.get("intervals", []),
			)
			if seed_confidences:
				print(f"  computed confidence for {len(seed_confidences)} seeds")

	# load predictions from solved intervals (has per-frame tracks and merged scores)
	predictions = None
	if os.path.isfile(intervals_path):
		predictions = mode_predictions.predictions_from_torso_box_coords(
			intervals_path, diag_path, video_info["fps"], seeds=seeds,
		)
		if predictions:
			print(f"  loaded predictions for {len(predictions)} frames")

	# optionally filter by severity (show only seeds near weak intervals)
	# pre_race intervals are excluded from severity filtering
	frame_filter = None
	severity = getattr(args, "severity", None)
	if severity is not None and os.path.isfile(diag_path):
		fps = video_info["fps"]
		diag_data = state_io.load_interval_scores(diag_path)
		intervals = diag_data.get("intervals", [])
		# collect frame ranges from weak intervals at the severity threshold
		weak_frames = set()
		pre_race_excluded = 0
		for iv in intervals:
			score = iv["interval_score"]
			confidence = review.get_confidence_label(score)
			# pre_race intervals are synthesized and excluded from severity filtering
			if confidence == "pre_race":
				pre_race_excluded += 1
				continue
			if confidence in ("high", "good"):
				continue
			sev = review.classify_interval_severity(iv, fps)
			# include if severity meets threshold
			include = False
			if severity == "low":
				include = True
			elif severity == "medium" and sev in ("medium", "high"):
				include = True
			elif severity == "high" and sev == "high":
				include = True
			if include:
				start_f = int(iv["start_frame"])
				end_f = int(iv["end_frame"])
				# include seeds within the weak interval range
				for seed in seeds:
					fi = int(seed.get("frame_index", -1))
					if start_f <= fi <= end_f:
						weak_frames.add(fi)
		if weak_frames:
			frame_filter = weak_frames
			msg = f"  severity filter: {len(weak_frames)} seeds near {severity}+ severity intervals"
			if pre_race_excluded > 0:
				msg += f" ({pre_race_excluded} pre-race intervals excluded)"
			print(msg)
		else:
			msg = f"  no seeds match severity={severity} filter, showing all"
			if pre_race_excluded > 0:
				msg += f" ({pre_race_excluded} pre-race intervals excluded)"
			print(msg)

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# run the editor
	edited_seeds, summary = seed_editor.edit_seeds(
		video_context.working_decode.path, seeds, cfg,
		predictions=predictions,
		frame_filter=frame_filter,
		seed_confidences=seed_confidences,
		debug=args.debug,
		start_frame=start_frame,
	)

	# save if changes were made
	changes = summary["redrawn"] + summary["deleted"] + summary["status_changed"]
	if changes > 0:
		mode_shared._save_seeds_to_disk(
			edited_seeds, seeds_path, video_identity,
		)
		print(f"saved {len(edited_seeds)} seeds to {seeds_path}")
		# invalidate only solved intervals that touch changed seeds
		changed_frames = summary.get("changed_frames", set())
		if changed_frames and os.path.isfile(intervals_path):
			mode_shared._invalidate_intervals_for_frames(
				intervals_path, changed_frames, video_identity,
			)
	else:
		print("no changes made")
