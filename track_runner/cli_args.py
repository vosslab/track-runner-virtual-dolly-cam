"""Argparse setup functions for track_runner CLI.

Provides argument parser configuration for all track_runner subcommands.
"""

# Standard Library
import os
import argparse

# local repo modules
import ui.base_controller as base_controller_module


#============================================
def _add_bin_arg(parser: argparse.ArgumentParser) -> None:
	"""Register --bin N on solve and refine subparsers.

	bin_factor is an integer >= 1 applied at the I/O boundary by
	common_tools.frame_reader.FrameReader. bin_factor=1 (default) is
	byte-identical to the pre-bin reader. Goodbox crop runs
	automatically when bin_factor > 1; the safety floor caps per-axis
	loss at 10% with a one-line warning.
	"""
	bin_group = parser.add_mutually_exclusive_group()
	bin_group.add_argument(
		"--bin", dest="bin_factor", type=int, default=1,
		help=(
			"Optional spatial downsample applied to camera-motion and "
			"residual stages only. Integer >= 1; default 1 (no bin). "
			"bin_factor > 1 also crops each scaled axis to the largest "
			"FFT-friendly goodbox not exceeding it (origin-preserving "
			"right/bottom crop). Source-frame outputs unchanged."
		),
	)
	bin_group.add_argument(
		"--auto-bin", dest="auto_bin_target", type=int,
		nargs="?", const=480, default=None, metavar="HEIGHT",
		help=(
			"Auto-pick bin_factor from source height: "
			"bin = max(1, round(source_h / target)). bin_factor is a "
			"whole number, so actual binned height only approximates "
			"the target. Source dims that are not multiples of bin "
			"silently drop at most (bin-1) right/bottom pixels, the "
			"same kind of crop goodbox already does. "
			"Bare flag targets 480; pass --auto-bin 720 for 720. "
			"Examples at target=480: 720->bin2 (360), 1080->bin2 (540), "
			"1440->bin3 (480), 2160->bin4 (540), 2816->bin6 (469). "
			"At target=720: 1080->bin1 (1080), 2160->bin3 (720). "
			"Mutually exclusive with --bin."
		),
	)


def _add_seed_interval_arg(parser: argparse.ArgumentParser) -> None:
	"""Register -I/--seed-interval on a subparser.

	Args:
		parser: Subparser to add the argument to.
	"""
	parser.add_argument(
		"-I", "--seed-interval", dest="seed_interval", type=float, default=10.0,
		help="Interval in seconds between seed frames (default 10).",
	)


#============================================
def _add_severity_arg(
	parser: argparse.ArgumentParser,
	help_text: str,
	include_level_aliases: bool = False,
) -> None:
	"""Register -s/--severity on a subparser.

	Args:
		parser: Subparser to add the argument to.
		help_text: Help string describing the severity filter for this mode.
		include_level_aliases: Add direct high/low shortcut aliases.
	"""
	severity_group = parser
	if include_level_aliases:
		severity_group = parser.add_mutually_exclusive_group(required=False)
	severity_group.add_argument(
		"-s", "--severity", dest="severity", type=str, default=None,
		choices=("high", "medium", "low"),
		help=help_text,
	)
	if include_level_aliases:
		severity_group.add_argument(
			"-H", "--high", dest="severity", action="store_const", const="high",
			help="Alias for -s high.",
		)
		severity_group.add_argument(
			"-L", "--low", dest="severity", action="store_const", const="low",
			help="Alias for -s low.",
		)


#============================================
def _add_top_arg(parser: argparse.ArgumentParser) -> None:
	"""Register -t/--top on a subparser.

	Args:
		parser: Subparser to add the argument to.
	"""
	parser.add_argument(
		"-t", "--top", dest="top_n", type=int, default=None,
		help="limit output to the worst N intervals (sorted worst-first by rank_key).",
	)


#============================================
def _add_gaps_arg(parser: argparse.ArgumentParser) -> None:
	"""Register -g/--gaps on a subparser.

	`--gaps N` injects the midpoints of the N largest seed gaps as
	explicit seeding targets. Independent of `-t/--top`, which targets
	instability-region peaks.

	Args:
		parser: Subparser to add the argument to.
	"""
	parser.add_argument(
		"-g", "--gaps", dest="gap_top_n", type=int, default=None,
		help=(
			"add midpoints of the N largest seed gaps to the seeding "
			"target list (independent of -t/--top). Implies -s on "
			"analyze."
		),
	)


#============================================
def _add_encode_args(parser: argparse.ArgumentParser) -> None:
	"""Register encoding-related arguments on a subparser.

	Args:
		parser: Subparser to add arguments to.
	"""
	parser.add_argument(
		"-o", "--output", dest="output_file", default=None,
		help="Output video file path (auto-generated if not provided).",
	)
	parser.add_argument(
		"--aspect", dest="aspect", type=str, default=None,
		help="Override crop aspect ratio (e.g. '1:1', '16:9').",
	)
	parser.add_argument(
		"--keep-temp", dest="keep_temp", action="store_true",
		help="Keep temporary files after encoding.",
	)
	parser.add_argument(
		"-F", "--encode-filters", dest="encode_filters", type=str,
		default=None,
		help=(
			"Comma-separated filter pipeline for encode output "
			"(overrides config). Example: bilateral,hqdn3d. "
			"Pass '-F none' as an alias for --no-filters."
		),
	)
	parser.add_argument(
		"--no-filters", dest="no_filters", action="store_true",
		help=(
			"Disable all encode filters (overrides config and -F). "
			"Cannot be combined with -F/--encode-filters."
		),
	)
	parser.add_argument(
		"--mp4", dest="mp4", action="store_true",
		help=(
			"Write final output as .mp4 (stream-copy remux from the "
			"internal MKV; no re-encode). Default container is .mkv."
		),
	)
	parser.add_argument(
		"--allow-offcenter-crop", dest="allow_offcenter_crop",
		action="store_true",
		help=(
			"Skip the central-window torso-center check and let the "
			"encode proceed even when the runner is sustained outside "
			"the safe central window of the output frame. The encoded "
			"video may show black bars where the crop window extends "
			"past the source frame."
		),
	)
	# overlay tier flags: tracking (review) vs debug (developer); mutex.
	# velocity arrow is independent but requires one of the two tiers.
	overlay_group = parser.add_mutually_exclusive_group()
	overlay_group.add_argument(
		"--draw-tracking-overlay", dest="draw_tracking_overlay",
		action="store_true",
		help=(
			"Draw the normal review overlay: final torso box and small "
			"center crosshair only."
		),
	)
	overlay_group.add_argument(
		"--draw-debug-overlay", dest="draw_debug_overlay",
		action="store_true",
		help=(
			"Draw the developer overlay: tracking overlay plus raw box, "
			"FWD/BWD boxes, source/confidence labels, and other "
			"diagnostic geometry."
		),
	)
	parser.add_argument(
		"--draw-velocity-arrow", dest="draw_velocity_arrow",
		action="store_true",
		help=(
			"Draw the per-frame motion arrow. Requires "
			"--draw-tracking-overlay or --draw-debug-overlay."
		),
	)
	parser.set_defaults(
		no_filters=False, mp4=False, allow_offcenter_crop=False,
		draw_tracking_overlay=False, draw_debug_overlay=False,
		draw_velocity_arrow=False,
	)
	# torso_height_multiple override: no short flag on purpose.
	# "z" reads as "zoom," which the config naming intentionally moved
	# away from; no other single letter is unambiguous here.
	parser.add_argument(
		"--torso-multiple", dest="torso_multiple", type=float, default=None,
		help=(
			"Override torso_height_multiple for this encode only. "
			"Crop height = this x tracked torso height; larger = wider view."
		),
	)
	parser.add_argument(
		"-r", "--output-resolution", dest="output_resolution", type=str,
		default=None,
		help=(
			"Override output_resolution as WxH (e.g. '1920x1080'). "
			"Must match --aspect."
		),
	)
	parser.add_argument(
		"--crf", dest="crf", type=int, default=None,
		help="Override CRF quality for this encode only (lower = higher quality).",
	)
	parser.add_argument(
		"--video-codec", dest="video_codec", type=str, default=None,
		help="Override FFmpeg video codec (e.g. 'libx264', 'libx265').",
	)


#============================================
def _build_parser() -> argparse.ArgumentParser:
	"""Construct the full track_runner argparse parser tree.

	Split out of parse_args() so help-dump tooling (tools/dump_cli_help.py)
	can walk subparsers without triggering -i/--input required validation.

	Returns:
		Root argparse.ArgumentParser with all subparsers registered.
	"""
	# Global options go on the main parser so they can precede the
	# subcommand: track_runner.py -i VIDEO seed --start 45
	parser = argparse.ArgumentParser(
		description="track_runner v2: multi-pass runner tracking and crop tool.",
		epilog=(
			"Global options (-i, -d, --time-range, etc.) must appear "
			"before the subcommand. "
			"Example: track_runner.py -i VIDEO seed --start 45"
		),
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Input video file path.",
	)
	parser.add_argument(
		"-c", "--config", dest="config_file", default=None,
		help="Config YAML file path.",
	)
	parser.add_argument(
		"-d", "--debug", dest="debug", action="store_true",
		help=(
			"Enable verbose diagnostic output for developers (logging "
			"and side-channel debug artifacts). Does not affect "
			"rendered overlays in the encoded video; use 'encode "
			"--draw-tracking-overlay' / '--draw-debug-overlay' / "
			"'--draw-velocity-arrow' for those."
		),
	)
	parser.add_argument(
		"-w", "--workers", dest="workers", type=int, default=None,
		help="Number of parallel workers (default: half of CPU cores).",
	)
	parser.add_argument(
		"--time-range", dest="time_range", type=str, default=None,
		help=(
			"Limit processing to time range in seconds "
			"(global option, must appear before the subcommand). "
			"Format: 'START:END', 'START:', or ':END'. "
			"Examples: '30:120', '200:'."
		),
	)
	parser.set_defaults(
		debug=False,
	)

	subparsers = parser.add_subparsers(dest="mode")

	# -- seed mode (interactive) --
	seed_parser = subparsers.add_parser(
		"seed", help="Collect seeds, save, and exit.",
	)
	_add_seed_interval_arg(seed_parser)
	base_controller_module.BaseAnnotationController.add_argparse_args(seed_parser)

	# -- edit mode (interactive) --
	edit_parser = subparsers.add_parser(
		"edit", help="Review/fix/delete existing seeds interactively.",
	)
	_add_severity_arg(edit_parser, "Filter seeds near weak intervals at this severity threshold.")
	base_controller_module.BaseAnnotationController.add_argparse_args(edit_parser)

	# -- target mode (interactive) --
	target_parser = subparsers.add_parser(
		"target", help="Add seeds at weak interval frames with FWD/BWD overlays.",
	)
	# Mutually exclusive group for target sub-modes
	target_submode_group = target_parser.add_mutually_exclusive_group(required=False)
	target_submode_group.add_argument(
		"--race-start", dest="target_race_start", action="store_true",
		help="Target frames around the detected race-start transition for confirmation."
	)
	target_submode_group.add_argument(
		"-A", "--from-analyze", dest="target_from_analyze", action="store_true",
		help=(
			"Target frames from the latest 'analyze' report: union of "
			"seed_suggestions and instability-region midpoints. Run "
			"'analyze' first to refresh the report."
		),
	)
	_add_severity_arg(
		target_parser,
		"Minimum severity of weak intervals to target.",
		include_level_aliases=True,
	)
	_add_top_arg(target_parser)
	_add_gaps_arg(target_parser)
	_add_seed_interval_arg(target_parser)
	base_controller_module.BaseAnnotationController.add_argparse_args(target_parser)
	target_parser.set_defaults(target_race_start=False, target_from_analyze=False)

	# -- solve mode --
	solve_parser = subparsers.add_parser(
		"solve", help="Full re-solve: clears prior results and solves all intervals fresh.",
	)
	# Auto-answer for the 'clear and re-solve from scratch?' prompt.
	# Mutually exclusive: -y forces yes (re-solve), --keep forces no
	# (skip if prior complete), --upgrade keeps the existing store and
	# only runs Stage 4 promotion on it. All three are scripted-run
	# friendly (no interactive prompt).
	solve_prompt_group = solve_parser.add_mutually_exclusive_group(required=False)
	solve_prompt_group.add_argument(
		"-y", "--yes", dest="assume_yes", action="store_true",
		help=(
			"Auto-confirm the 'clear and re-solve from scratch?' "
			"prompt. Use this for scripted re-solve runs."
		),
	)
	solve_prompt_group.add_argument(
		"--keep", dest="keep_prior", action="store_true",
		help=(
			"Auto-decline the 'clear and re-solve from scratch?' prompt. "
			"Skip videos that already have a complete solve. Use this "
			"for scripted runs that should only solve missing videos."
		),
	)
	solve_prompt_group.add_argument(
		"--upgrade", dest="upgrade", action="store_true",
		help=(
			"Run Stage 4 blob promotion on the existing torso_box_coords "
			"store without re-doing Stage 3. Use after a 'solve "
			"--hermite-only' batch to upgrade weak intervals to blob "
			"results."
		),
	)
	# Stage control flags: mutually exclusive group
	solve_stage_group = solve_parser.add_mutually_exclusive_group(required=False)
	solve_stage_group.add_argument(
		"-f", "--full", dest="full_solve", action="store_true",
		help="Run Stage 5: blob pass on every post-race interval (slow).",
	)
	solve_stage_group.add_argument(
		"-H", "--hermite-only", dest="hermite_only", action="store_true",
		help="Stop after Stage 3: Hermite-only solve (fast diagnostics).",
	)
	solve_parser.set_defaults(assume_yes=False, keep_prior=False,
		upgrade=False, full_solve=False, hermite_only=False)
	_add_bin_arg(solve_parser)

	# -- refine mode --
	refine_parser = subparsers.add_parser(
		"refine", help="Re-solve only changed/new intervals, reuse prior results.",
	)
	# Stage control flags: mutually exclusive group (same as solve mode)
	refine_stage_group = refine_parser.add_mutually_exclusive_group(required=False)
	refine_stage_group.add_argument(
		"-f", "--full", dest="full_solve", action="store_true",
		help="Run Stage 5: blob pass on every interval refine touches (slow).",
	)
	refine_stage_group.add_argument(
		"-H", "--hermite-only", dest="hermite_only", action="store_true",
		help="Stop after Stage 3: Hermite-only refine (fast diagnostics).",
	)
	refine_parser.set_defaults(full_solve=False, hermite_only=False)
	_add_bin_arg(refine_parser)

	# -- encode mode --
	encode_parser = subparsers.add_parser(
		"encode", help="Encode cropped video from existing trajectory.",
		epilog=(
			"Global -d/--debug controls diagnostic output only and does "
			"not affect rendered overlays. Use --draw-tracking-overlay "
			"(review), --draw-debug-overlay (developer), and "
			"--draw-velocity-arrow (motion cue) to burn overlays into "
			"the encoded video."
		),
	)
	_add_encode_args(encode_parser)

	# -- analyze mode --
	analyze_parser = subparsers.add_parser(
		"analyze", help="Analyze crop path stability before encoding.",
	)
	analyze_parser.add_argument(
		"--aspect", dest="aspect", type=str, default=None,
		help="Override crop aspect ratio (e.g. '1:1', '16:9').",
	)
	analyze_parser.add_argument(
		"-s", "--seed", dest="analyze_seed", action="store_true",
		help=(
			"After printing the analyze report, open the seeding UI on "
			"the worst-N instability-region peak frames (same set used "
			"by 'target --from-analyze'). Implied by '-t N'."
		),
	)
	_add_top_arg(analyze_parser)
	_add_gaps_arg(analyze_parser)
	analyze_parser.add_argument(
		"-p", "--plot",
		dest="write_plots", action="store_true",
		help="write HTML diagnostic report alongside the encode_analysis.yaml",
	)
	base_controller_module.BaseAnnotationController.add_argparse_args(analyze_parser)
	analyze_parser.set_defaults(analyze_seed=False, write_plots=False)

	# -- setup mode --
	subparsers.add_parser(
		"setup", help="Configure camera settings for this video.",
	)

	# -- prepare mode --
	prepare_parser = subparsers.add_parser(
		"prepare",
		help=(
			"Create the fast-read working video beside the original. "
			"All working modes decode from the fast-read video when present."
		),
	)
	prepare_parser.add_argument(
		"-f", "--force", dest="force", action="store_true",
		help=(
			"Delete any existing fast-read video and recreate unconditionally. "
			"Without --force, an existing valid fast-read is kept; an invalid "
			"one raises an error."
		),
	)
	prepare_parser.add_argument(
		"-v", "--verbose", dest="verbose", action="store_true",
		help="Stream full ffmpeg command and stderr to the terminal.",
	)
	prepare_parser.set_defaults(force=False, verbose=False)

	return parser


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments with subcommands for track_runner v2.

	Returns:
		Parsed argparse.Namespace with a 'mode' attribute.
	"""
	parser = _build_parser()
	args = parser.parse_args()
	# no subcommand given: print help and exit
	if args.mode is None:
		parser.print_help()
		raise SystemExit(0)

	# validate --top flag
	top_n = getattr(args, "top_n", None)
	if top_n is not None and top_n < 1:
		parser.error("--top must be a positive integer")

	# encode-mode CLI contract validation: velocity arrow requires an
	# overlay tier; --no-filters and -F are mutually exclusive; --mp4
	# cannot be combined with an explicit -o ending in .mkv.
	if args.mode == "encode":
		if args.draw_velocity_arrow and not (
			args.draw_tracking_overlay or args.draw_debug_overlay
		):
			parser.error(
				"--draw-velocity-arrow requires --draw-tracking-overlay "
				"or --draw-debug-overlay"
			)
		if args.no_filters and args.encode_filters is not None:
			parser.error(
				"--no-filters cannot be combined with -F/--encode-filters; "
				"pick one"
			)
		output_file = getattr(args, "output_file", None)
		if args.mp4 and output_file is not None:
			# explicit-output ext check is normalized by lowercase
			_, ext = os.path.splitext(output_file)
			if ext.lower() == ".mkv":
				parser.error(
					"--mp4 cannot be combined with -o ending in .mkv; "
					"drop --mp4 or pass an .mp4 output path"
				)

	return args
