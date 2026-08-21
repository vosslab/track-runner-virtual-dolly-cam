"""Implementation for the track_runner seed CLI mode."""

# Standard Library
import argparse
import os

# local repo modules
import fastread_video
import modes.predictions as mode_predictions
import modes.shared as mode_shared
import seeding
import state_io
import tr_paths


def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	video_context: fastread_video.VideoContext,
	video_identity: dict,
) -> None:
	"""Seed collection mode: collect seeds and save.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		video_context: Resolved per-run routing; the seeding UI decodes
			from video_context.working_decode.path while seed state keys
			off video_context.original_video_path.
		video_identity: Identity of the source video that owns saved seeds.
	"""
	# show which physical video frames decode from for this run
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	# parse optional time range
	time_range = mode_shared._parse_time_range(args.time_range)
	# load existing seeds
	seeds_data = state_io.load_seeds(seeds_path)
	existing_seeds = seeds_data.get("seeds", [])
	# determine pass number
	if existing_seeds:
		print(f"loaded {len(existing_seeds)} existing seeds from {seeds_path}")
		existing_passes = [s["pass"] for s in existing_seeds]
		pass_number = max(existing_passes) + 1
	else:
		pass_number = 1
	# load predictions from solved intervals with scores merged in
	predictions = None
	diag_path = tr_paths.default_interval_scores_path(args.input_file)
	intervals_path = tr_paths.default_torso_box_coords_path(args.input_file)
	if os.path.isfile(intervals_path):
		predictions = mode_predictions.predictions_from_torso_box_coords(
			intervals_path, diag_path, video_info["fps"], seeds=existing_seeds,
		)
		if predictions:
			print(f"  loaded predictions for {len(predictions)} frames")

	# convert --start time to frame index
	start_frame = None
	if getattr(args, "start_time", None) is not None:
		start_frame = int(args.start_time * video_info["fps"])

	# seed collection
	print(f"launching seed collection (pass {pass_number})...")
	seeds = seeding.collect_seeds(
		video_context.working_decode.path,
		args.seed_interval,
		cfg,
		pass_number=pass_number,
		existing_seeds=existing_seeds if existing_seeds else None,
		frame_count_override=video_info["frame_count"],
		debug=args.debug,
		save_callback=mode_shared._make_save_callback(seeds_path, video_identity),
		time_range=time_range,
		predictions=predictions,
		start_frame=start_frame,
	)
	if not seeds:
		raise RuntimeError("no seeds collected")
	mode_shared._save_seeds_to_disk(seeds, seeds_path, video_identity)
	print(f"saved {len(seeds)} seeds to {seeds_path}")
