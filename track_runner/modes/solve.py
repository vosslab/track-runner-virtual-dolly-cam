"""Implementation for the track_runner solve CLI mode."""

# Standard Library
import argparse
import os

# local repo modules
import fastread_video
import modes.shared as mode_shared
import modes.video_artifacts
import torso_box_coords_io


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
	"""Solve mode: run interval solver, write diagnostics, exit.

	Non-interactive: solves, writes diagnostics, prints quality summary.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
		video_context: Resolved per-run routing; solve decodes from
			video_context.working_decode.path.
		video_identity: Identity of the current source video.
	"""
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	seeds = mode_shared._load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")

	modes.video_artifacts.clear_incompatible_derived_artifact(
		"interval scores", diag_path, video_identity,
	)
	modes.video_artifacts.clear_incompatible_derived_artifact(
		"solved intervals", intervals_path, video_identity,
	)

	# only clear intervals if a prior solve completed successfully;
	# if the prior solve was interrupted, resume from saved intervals
	if os.path.isfile(intervals_path):
		intervals_file = torso_box_coords_io.load_torso_box_coords(intervals_path)
		prior_complete = intervals_file.get("solve_complete", False)
		prior_count = len(intervals_file.get("solved_intervals", {}))
		# --upgrade: keep store, run Stage 4 promotion only. Skip the
		# clear-and-re-solve prompt entirely; we do not want to wipe
		# anything. Falls through to _run_solve below with upgrade=True.
		if args.upgrade:
			if not prior_complete or prior_count == 0:
				print("  --upgrade requires a completed prior solve; "
					"run 'solve --hermite-only --keep' first")
				return
			print(f"  --upgrade: running Stage 4 on existing store "
				f"({prior_count} intervals)")
		elif prior_complete and prior_count > 0:
			print(f"  prior solve completed ({prior_count} intervals)")
			if args.assume_yes:
				answer = "y"
				print("  clear and re-solve from scratch? [y/N] y (-y)")
			elif args.keep_prior:
				answer = "n"
				print("  clear and re-solve from scratch? [y/N] n (--keep)")
			else:
				answer = input(
					"  clear and re-solve from scratch? [y/N] "
				).strip().lower()
			if answer in ("y", "yes"):
				os.remove(intervals_path)
				print("  cleared solved intervals (full re-solve)")
			else:
				print("  keeping prior results (use 'refine' for incremental updates)")
				return
		elif prior_count > 0:
			print(f"  resuming interrupted solve ({prior_count} intervals saved)")
		else:
			os.remove(intervals_path)

	num_workers = mode_shared._resolve_workers(args, video_info)
	mode_shared._run_solve(
		args, cfg, seeds, video_info,
		intervals_path, diag_path, num_workers, video_identity,
		decode_video_path=video_context.working_decode.path,
	)
