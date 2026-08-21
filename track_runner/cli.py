#!/usr/bin/env python3
"""CLI entry point for the track_runner tool.

Multi-pass orchestration: seed collection, interval solving, refinement,
crop trajectory computation, and video encoding.

Subcommands:
  prepare Create the fast-read working video beside the original
  seed    Collect/add seeds, save, exit
  edit    Review/fix/delete existing seeds interactively
  target  Add seeds at weak interval frames with FWD/BWD overlays
  solve   Full re-solve: clears prior results and solves all intervals fresh
  refine  Re-solve only changed/new intervals, reuse prior results
  encode  Encode from existing trajectory, no solving
"""

# Standard Library
import os
import shutil
import resource
import time

# local repo modules
import cli_args
import tr_config
import tr_paths
import tr_video_identity
import fastread_video

# Per-process memory profile toggle. Flip to True locally when hunting a
# leak; main() prints peak RSS and open-FD count at end of run. Off in
# committed code per PYTHON_STYLE: configuration belongs in source, not
# in custom environment variables.
PROFILE_MEM = False


#============================================
# CLI mode implementations are isolated from orchestration so CLI parsing,
# artifact paths, and dispatch remain here while mode behavior stays local.
import modes.video_artifacts
import modes.analyze
import modes.edit
import modes.encode
import modes.prepare
import modes.refine
import modes.seed
import modes.setup
import modes.solve
import modes.target

#============================================
def main() -> None:
	"""Main entry point for the track_runner CLI."""
	t_total_start = time.time()
	args = cli_args.parse_args()

	# validate input file exists
	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"input file not found: {args.input_file}")

	# require .mkv source: random-access seek on .mov/.mp4 is too slow
	# for the Stage 4 pre-pass and FrameReader strategy-1 path. Remux is
	# lossless and one-time; transcoding is never done by this pipeline.
	ext_lower = os.path.splitext(args.input_file)[1].lower()
	if ext_lower != ".mkv":
		stem = os.path.splitext(args.input_file)[0]
		raise RuntimeError(
			f"input video must be .mkv, got {args.input_file!r}. "
			f".mov/.mp4 are no longer supported because random-access "
			f"seek is too slow on those containers. Remux losslessly: "
			f"mkvmerge -o {stem}.mkv {args.input_file}"
		)

	# verify required external tools are available
	for tool in ("mediainfo", "ffprobe", "ffmpeg"):
		if shutil.which(tool) is None:
			raise RuntimeError(f"{tool} not found in PATH")

	# prepare mode: dispatch early before config/data-path setup; it uses
	# only the original video and ffmpeg and does not need config, seeds,
	# diagnostics, or data directory.
	if args.mode == "prepare":
		# probe to provide the status summary geometry; video_info is the
		# only context prepare needs beyond the input path.
		print(f"probing video: {args.input_file}")
		video_info = modes.video_artifacts.probe_video(args.input_file)
		fps = video_info["fps"]
		print(f"  resolution: {video_info['width']}x{video_info['height']}")
		print(f"  fps:        {fps:.4f}")
		print(f"  frames:     {video_info['frame_count']}")
		print(f"  duration:   {video_info['duration_s']:.2f}s")
		modes.prepare.run(args, video_info)
		t_total_elapsed = time.time() - t_total_start
		print(f"total time: {t_total_elapsed:.1f}s")
		return

	# ensure tr_config/ data directory exists
	tr_paths.ensure_data_dir()

	# resolve config path
	config_path = args.config_file
	if config_path is None:
		config_path = tr_paths.default_config_path(args.input_file)

	# paths for seeds, interval scores, and solved intervals
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	diag_path = tr_paths.default_interval_scores_path(args.input_file)
	intervals_path = tr_paths.default_torso_box_coords_path(args.input_file)

	# print all config and data file paths
	print(f"config:      {os.path.abspath(config_path)}")
	print(f"seeds:       {os.path.abspath(seeds_path)}")
	print(f"diagnostics: {os.path.abspath(diag_path)}")
	print(f"intervals:   {os.path.abspath(intervals_path)}")
	# show analysis file path when it exists (diagnostic awareness)
	analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
	if os.path.isfile(analysis_path):
		print(f"analysis:    {os.path.abspath(analysis_path)}")

	# load config: per-video file if it exists, otherwise defaults.
	# track whether a per-video config already existed so we can gate
	# modes that need `setup` to have been run first (solve/refine/target).
	# resolve_config centralizes per-video and default configuration selection;
	# config_path honors any --config-file override resolved above.
	cfg, had_config_file = tr_config.resolve_config(
		args.input_file, config_path=config_path,
	)
	tr_config.validate_config(cfg)

	# probe video metadata
	print(f"probing video: {args.input_file}")
	video_info = modes.video_artifacts.probe_video(args.input_file)
	fps = video_info["fps"]
	print(f"  resolution: {video_info['width']}x{video_info['height']}")
	print(f"  fps:        {fps:.4f}")
	print(f"  frames:     {video_info['frame_count']}")
	print(f"  duration:   {video_info['duration_s']:.2f}s")

	# build video identity fingerprint for data file tagging
	video_identity = tr_video_identity.make_video_identity(
		args.input_file, video_info,
	)
	# Seed geometry is authored truth and must match this video in every mode.
	# Solve owns rebuild of derived artifacts, so it clears incompatible
	# diagnostics/coordinates inside modes.solve after its stale-schema check.
	modes.video_artifacts.check_identity_mismatch("seeds", seeds_path, video_identity)
	if args.mode != "solve":
		modes.video_artifacts.check_identity_mismatch(
			"diagnostics", diag_path, video_identity,
		)
		modes.video_artifacts.check_identity_mismatch(
			"intervals", intervals_path, video_identity,
		)

	# Resolve original-vs-fast-read routing ONCE for this run (prepare
	# already early-returned above; it is its own creator). A valid context
	# is the authorization for working-mode FrameReaders to decode from
	# working_decode.path. A present-but-invalid fast-read raises here with
	# the remedy. Modes receive this context and never re-run discovery.
	video_context = fastread_video.resolve_video_context(args.input_file)

	# dispatch to mode function
	mode = args.mode

	# gate modes that consume setup-only motion configuration: require a
	# per-video config file to exist. `seed`,
	# `edit`, `encode`, `analyze`, and `setup` itself are intentionally
	# exempt so users can collect seeds before configuring the camera.
	if mode in ("solve", "refine", "target") and not had_config_file:
		raise RuntimeError(
			f"no per-video config at {config_path} -- "
			f"run 'setup' mode first: "
			f"./track_runner/track_runner.py -i {args.input_file} setup"
		)

	# gate `refine` on `solve` having been run at least once: refine
	# reads per-interval diagnostics to decide which intervals to redo.
	# `target` is not gated on solve because target-mode auto-runs a
	# fresh solve when diagnostics are missing (see modes.target.run below).
	if mode == "refine" and not os.path.isfile(diag_path):
		raise RuntimeError(
			f"no diagnostics at {diag_path} -- "
			f"run 'solve' mode first: "
			f"./track_runner/track_runner.py -i {args.input_file} solve"
		)

	if mode == "seed":
		modes.seed.run(
			args, cfg, video_info, seeds_path, video_context, video_identity,
		)
	elif mode == "edit":
		modes.edit.run(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context, video_identity,
		)
	elif mode == "target":
		modes.target.run(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context, video_identity,
		)
	elif mode == "solve":
		modes.solve.run(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context, video_identity,
		)
	elif mode == "refine":
		modes.refine.run(
			args, cfg, video_info, seeds_path, diag_path, intervals_path,
			video_context, video_identity,
		)
	elif mode == "setup":
		modes.setup.run(args, cfg, video_info, config_path, video_context)
	elif mode == "encode":
		modes.encode.run(
			args, cfg, video_info, diag_path, intervals_path, video_context,
		)
	elif mode == "analyze":
		modes.analyze.run(
			args, cfg, video_info, diag_path, intervals_path, video_context,
		)
		# --seed shortcut: hand off to target's --from-analyze path so
		# the user goes from analyze report -> seeding UI in one command.
		# `-t N` and `-g N` also trigger this path -- supplying either
		# is a clear signal the user wants to act on the targets.
		seed_requested = (
			getattr(args, "analyze_seed", False)
			or getattr(args, "top_n", None) is not None
			or getattr(args, "gap_top_n", None) is not None
		)
		if seed_requested:
			if not had_config_file:
				raise RuntimeError(
					f"--seed requires a per-video config at {config_path}; "
					f"run 'setup' mode first"
				)
			args.target_from_analyze = True
			args.target_race_start = False
			# target mode reads optional fields the analyze parser does not
			# define; supply safe defaults so getattr() lookups inside
			# modes.target.run succeed.
			for field, default in (
				("severity", None),
				("seed_interval", 10.0),
				("top_n", None),
				("start_time", None),
			):
				if not hasattr(args, field):
					setattr(args, field, default)
			modes.target.run(
				args, cfg, video_info, seeds_path, diag_path, intervals_path,
				video_context, video_identity,
			)
	else:
		raise RuntimeError(f"unknown mode: {mode}")

	# print total elapsed time
	t_total_elapsed = time.time() - t_total_start
	print(f"total time: {t_total_elapsed:.1f}s")

	# per-process memory profile -- gated by the module-level PROFILE_MEM
	# constant (off by default; flip in source when diagnosing leaks)
	if PROFILE_MEM:
		# ru_maxrss is bytes on macOS, KB on Linux
		ru = resource.getrusage(resource.RUSAGE_SELF)
		open_fds = "n/a"
		# /dev/fd is the macOS equivalent of /proc/self/fd; counting
		# entries gives the current open-FD count without needing psutil
		fd_dir = "/dev/fd"
		if os.path.isdir(fd_dir):
			open_fds = str(len(os.listdir(fd_dir)))
		print(
			f"profile: ru_maxrss={ru.ru_maxrss} (bytes on macOS) "
			f"open_fds={open_fds}"
		)


#============================================
if __name__ == "__main__":
	main()
