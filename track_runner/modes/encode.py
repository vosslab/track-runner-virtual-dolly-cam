"""Implementation for the track_runner encode CLI mode."""

import modes.shared
import argparse
import math
import os
import statistics
import time

import camera_motion
import common_tools.frame_reader
import common_tools.probe_video
import fastread_video
import interval_solver
import key_input
import scene_coords
import state_io
import torso_box_coords_io
import tr_paths
import tr_crop


def _parse_resolution(value: str) -> list:
	"""Parse a 'WxH' string into a [width, height] integer list.

	Args:
		value: Resolution string like '1920x1080'.

	Returns:
		[width, height] with both values as ints.

	Raises:
		RuntimeError: If the string is not in WxH form or either dim
			is not a positive integer.
	"""
	# split on the literal 'x' separator; anything else is an error
	parts = value.lower().split("x")
	if len(parts) != 2 or not parts[0] or not parts[1]:
		raise RuntimeError(
			f"invalid --output-resolution '{value}', expected WxH "
			"(e.g. '1920x1080')"
		)
	width = int(parts[0])
	height = int(parts[1])
	if width <= 0 or height <= 0:
		raise RuntimeError(
			f"invalid --output-resolution '{value}', width and height "
			"must be positive"
		)
	return [width, height]


#============================================
def _apply_encode_overrides(args: argparse.Namespace, cfg: dict) -> None:
	"""Merge CLI encode-override flags into cfg['processing'] in place.

	Handles --aspect plus the phase-4 flags (--torso-multiple,
	-r/--output-resolution, --crf, --video-codec). Each flag is applied
	only when the user set it; unset flags leave the config untouched.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict, mutated in place.
	"""
	# Keep the OpenCV-backed filter registry out of CLI startup so
	# non-encode commands do not load cv2 or its FFmpeg bundle.

	cfg.setdefault("processing", {})
	processing = cfg["processing"]
	# aspect is the explicit output-shape override flag.
	if getattr(args, "aspect", None) is not None:
		processing["crop_aspect"] = args.aspect
	# torso_height_multiple: crop zoom knob
	multiple = getattr(args, "torso_multiple", None)
	if multiple is not None:
		processing["torso_height_multiple"] = float(multiple)
	# explicit output resolution, parsed as WxH
	resolution_str = getattr(args, "output_resolution", None)
	if resolution_str is not None:
		processing["output_resolution"] = _parse_resolution(resolution_str)
	# CRF quality
	crf_override = getattr(args, "crf", None)
	if crf_override is not None:
		processing["crf"] = int(crf_override)
	# ffmpeg video codec
	codec_override = getattr(args, "video_codec", None)
	if codec_override is not None:
		processing["video_codec"] = codec_override


#============================================
def _resolve_encode_filters(args: argparse.Namespace, proc_cfg: dict) -> list:
	"""Resolve the encode filter list from CLI and tr_config.

	Precedence (highest first):
	1. --no-filters wins over everything (returns []).
	2. -F none (case-insensitive, whitespace-tolerant) is an alias for
	   --no-filters and returns []. Mixed forms like 'none,blur' are
	   rejected with a targeted error.
	3. -F <list> overrides config processing.encode_filters.
	4. config processing.encode_filters is the fallback.

	Validates each filter name against the known filter list.

	Args:
		args: Parsed argparse namespace.
		proc_cfg: The processing section of the config dict.

	Returns:
		List of validated filter name strings, or empty list.
	"""
	# Lazy import for the same reason as _apply_encode_overrides():
	# only encode mode needs the OpenCV-backed filter registry.
	import common_tools.frame_filters as frame_filters

	# --no-filters short-circuit (parse-time validation already rejects
	# the --no-filters + -F combination, so reaching here means -F is
	# None whenever no_filters is True; defensive check kept for direct
	# callers).
	if getattr(args, "no_filters", False):
		if getattr(args, "encode_filters", None) is not None:
			raise RuntimeError(
				"--no-filters cannot be combined with -F/--encode-filters; "
				"pick one"
			)
		return []
	# -F none alias: case-insensitive, whitespace-tolerant
	cli_value = getattr(args, "encode_filters", None)
	if cli_value is not None:
		raw_tokens = [tok.strip() for tok in cli_value.split(",") if tok.strip()]
		lowered = [tok.lower() for tok in raw_tokens]
		if "none" in lowered:
			# 'none' must appear alone; reject mixed forms like 'none,blur'
			if len(lowered) > 1:
				raise RuntimeError(
					"'none' cannot be combined with other filters; use "
					"--no-filters or pass an explicit list"
				)
			return []
		filter_list = raw_tokens
	else:
		# fall back to config value
		filter_list = list(proc_cfg.get("encode_filters", []))
	# validate each filter name
	for name in filter_list:
		if name not in frame_filters.ALL_ENCODE_FILTERS:
			raise RuntimeError(
				f"unknown encode filter: '{name}'. "
				f"Valid filters: {frame_filters.ALL_ENCODE_FILTERS}"
			)
	return filter_list


#============================================
def _stitch_debug_projection_paths(
	interval_results: list,
	n_frames: int,
) -> tuple[list, list]:
	"""Stitch optional FWD/BWD diagnostics into trajectory-sized paths.

	Pre-race intervals deliberately have no independent FWD/BWD projections:
	the durable interval artifact represents that authoritative absence with
	both paths set to None. Their blended path remains available for the
	production trajectory, while their debug-projection slots stay None.

	Raises:
		RuntimeError: If an artifact has only one directional path. That is not
			a valid pre-race absence and must not be silently converted to one.
	"""
	fwd_trajectory = [None] * n_frames
	bwd_trajectory = [None] * n_frames
	for result in interval_results:
		start = int(result["start_frame"])
		fwd_track = result["forward_path"]
		bwd_track = result["backward_path"]
		if fwd_track is None and bwd_track is None:
			# Pre-race has only blended geometry by durable-artifact contract.
			continue
		if fwd_track is None or bwd_track is None:
			raise RuntimeError(
				"invalid solved interval has only one FWD/BWD path; "
				"pre-race intervals must omit both"
			)
		for i, fwd_state in enumerate(fwd_track):
			fi = start + i
			if 0 <= fi < n_frames and fwd_state is not None:
				fwd_trajectory[fi] = fwd_state
		for i, bwd_state in enumerate(bwd_track):
			fi = start + i
			if 0 <= fi < n_frames and bwd_state is not None:
				bwd_trajectory[fi] = bwd_state
	return fwd_trajectory, bwd_trajectory


#============================================
def _compute_crop_trajectory(
	trajectory: list,
	video_info: dict,
	cfg: dict,
	nif_frames: set,
) -> tuple:
	"""Return crop rectangles and explicit-dolly provenance when applicable."""
	crop_mode = str(cfg.get("processing", {}).get("crop_mode", "dolly"))
	if crop_mode == "dolly":
		return tr_crop.trajectory_to_crop_rects(
			trajectory, video_info, cfg, nif_frames=nif_frames,
			return_dolly_report=True,
		)
	rects = tr_crop.trajectory_to_crop_rects(
		trajectory, video_info, cfg, nif_frames=nif_frames,
	)
	return (rects, None)


#============================================
def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	diag_path: str,
	intervals_path: str | None = None,
	video_context: fastread_video.VideoContext | None = None,
) -> None:
	"""Encode mode: encode cropped video from existing diagnostics.

	Reconstructs the per-frame trajectory from the solved intervals
	file, since the diagnostics file stores only interval summaries
	(no per-frame trajectory data).

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
			If None, derived from input_file.
		video_context: Resolved per-run routing. Encode always reads
			video_context.final_encode.path (the original) for final
			quality. None falls back to args.input_file.
	"""
	# Encode always decodes the original for final quality. final_encode.path
	# equals the original by construction; fall back to args.input_file when
	# no context is threaded (non-routed callers).
	if video_context is None:
		encode_video_path = args.input_file
	else:
		encode_video_path = video_context.final_encode.path
		fastread_video.print_video_routing_banner(
			video_context.original_video_path, encode_video_path,
		)
	# Import encoder lazily so non-encode commands do not load OpenCV.
	import encoder

	# apply CLI overrides (aspect + phase-4 encode-only flags)
	_apply_encode_overrides(args, cfg)

	# load diagnostics (for fps and interval metadata)
	if not os.path.isfile(diag_path):
		raise RuntimeError(
			f"no diagnostics found at {diag_path}; run 'solve' first"
		)
	diag_data = state_io.load_interval_scores(diag_path)

	# reconstruct trajectory from solved intervals
	if intervals_path is None:
		intervals_path = tr_paths.default_torso_box_coords_path(args.input_file)
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"no solved intervals found at {intervals_path}; "
			f"run 'solve' first"
		)
	intervals_file = torso_box_coords_io.load_torso_box_coords(intervals_path)
	solved = intervals_file.get("solved_intervals", {})
	if not solved:
		raise RuntimeError(
			"solved intervals file contains no interval data"
		)
	# sort interval results by start_frame for stitching
	interval_results = sorted(
		solved.values(), key=lambda r: int(r["start_frame"]),
	)
	trajectory = interval_solver.reconstruct_trajectory_with_confidence(
		interval_results,
	)

	# derive overlay-tier flags from CLI: developer overlay implies the
	# review overlay; velocity is independent. Global args.debug is left
	# alone (it controls diagnostic output, not rendered overlays).
	draw_tracking = args.draw_tracking_overlay or args.draw_debug_overlay
	draw_debug = args.draw_debug_overlay
	draw_velocity = args.draw_velocity_arrow

	# save raw trajectory and FWD/BWD tracks before anchoring; only the
	# developer overlay needs them (FWD/BWD/raw boxes are debug-tier)
	raw_trajectory_for_debug = None
	fwd_trajectory_for_debug = None
	bwd_trajectory_for_debug = None
	if draw_debug:
		raw_trajectory_for_debug = [
			dict(s) if s is not None else None
			for s in trajectory
		]
		# stitch forward and backward interval paths for FWD/BWD overlay boxes
		n_frames = len(trajectory)
		fwd_trajectory_for_debug, bwd_trajectory_for_debug = (
			_stitch_debug_projection_paths(interval_results, n_frames)
		)

	# apply multi-seed anchored interpolation to reduce drift
	seeds_path = tr_paths.default_seeds_path(args.input_file)
	all_seeds = []
	if os.path.isfile(seeds_path):
		seeds_data = state_io.load_seeds(seeds_path)
		all_seeds = seeds_data.get("seeds", [])
		trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
		trajectory = interval_solver._stamp_seed_truth(
			trajectory, all_seeds,
		)
	if not trajectory:
		raise RuntimeError(
			"could not reconstruct trajectory from solved intervals"
		)

	# NIF edge anchors are output-crop geometry, never runner truth. The
	# original seed-erased trajectory remains available to debug overlays.
	crop_trajectory, nif_frames = modes.shared.build_nif_crop_inputs(
		trajectory, all_seeds, diag_data, video_info,
	)

	num_workers = modes.shared._resolve_workers(args)

	# compute crop trajectory
	print("computing crop trajectory...")
	crop_rects, dolly_report = _compute_crop_trajectory(
		crop_trajectory, video_info, cfg, nif_frames,
	)
	if dolly_report is not None:
		print(
			"  dolly containment: "
			f"converged={dolly_report.converged} "
			f"iterations={dolly_report.iterations} "
			f"fallback_used={dolly_report.fallback_used}"
		)

	# resolve output path (encoded output stays next to input video).
	# Default container is .mkv (mkvmerge concat output); --mp4 or -o
	# foo.mp4 produces a .mp4 final via the existing audio-mux step
	# (ffmpeg -c copy honors the destination extension's container).
	# The parser already rejects: -o foo.mov; --mp4 -o foo.mkv; etc.
	output_file = getattr(args, "output_file", None)
	if output_file is not None:
		_, ext = os.path.splitext(output_file)
		ext_lower = ext.lower()
		if ext_lower not in (".mkv", ".mp4"):
			raise RuntimeError(
				f"output extension {ext!r} not supported; use .mkv "
				f"(default) or .mp4 (with --mp4)"
			)
		output_path = output_file
	elif getattr(args, "mp4", False):
		# swap the default .mkv extension for .mp4
		default_mkv = tr_paths.default_output_path(args.input_file)
		stem, _ = os.path.splitext(default_mkv)
		output_path = f"{stem}.mp4"
	else:
		output_path = tr_paths.default_output_path(args.input_file)

	# compute output dimensions: explicit config > median of crop rects > fallback
	proc_cfg = cfg.get("processing", {})
	user_resolution = proc_cfg.get("output_resolution")
	if user_resolution is not None:
		# user-specified output resolution
		crop_w = int(user_resolution[0])
		crop_h = int(user_resolution[1])
	elif crop_rects:
		# derive from median of all crop rectangles for stability
		all_widths = [r[2] for r in crop_rects]
		all_heights = [r[3] for r in crop_rects]
		crop_w = int(statistics.median(all_widths))
		crop_h = int(statistics.median(all_heights))
	else:
		crop_h = video_info["height"] // 2
		crop_w = crop_h
	# ensure even dimensions for codec compatibility
	crop_w = crop_w - (crop_w % 2)
	crop_h = crop_h - (crop_h % 2)
	video_codec = proc_cfg.get("video_codec", "libx264")
	crf_value = int(proc_cfg.get("crf", 18))

	# pre-encode validation: refuse when the runner is sustained outside
	# the safe central window of the output frame. Black-fill from a
	# slightly off-source crop is fine, but a runner pinned to one side
	# for a sustained run is almost always a configuration bug; better
	# to fail in a second than to burn a multi-minute encode.
	if not args.allow_offcenter_crop:
		aspect_ratio = tr_crop.parse_aspect_ratio(
			proc_cfg.get("crop_aspect", "1:1")
		)
		torso_multiple = float(
			proc_cfg.get("torso_height_multiple", 3.33)
		)
		tr_crop.validate_torso_within_central_window(
			trajectory, crop_rects,
			crop_w, crop_h,
			video_info["width"], video_info["height"],
			torso_multiple=torso_multiple,
			aspect_ratio=aspect_ratio,
		)

	# resolve encode filters: CLI overrides config, config overrides default
	encode_filters = _resolve_encode_filters(args, proc_cfg)

	# encode. The intermediate is fixed at {final_stem}.tmp.mkv -- the
	# encoder writes Matroska bytes via mkvmerge, so a .mkv-suffixed
	# temp name is truthful. Using `.tmp.mkv` (rather than `.mkv` alone)
	# avoids accidentally overwriting an existing real `{stem}.mkv` if
	# the user has both encodes side by side.
	final_stem, _ = os.path.splitext(output_path)
	temp_video = f"{final_stem}.tmp.mkv"
	workers_enc_label = f" ({num_workers} workers)" if num_workers > 1 else ""
	filters_label = f" filters={encode_filters}" if encode_filters else ""
	print(f"encoding cropped video: {crop_w}x{crop_h}{workers_enc_label}{filters_label}")
	print("  (press Q to quit)")
	t_encode_start = time.time()

	# set up keyboard controls for encoding
	enc_rc = key_input.RunControl()
	key_input.install_sigint_handler(enc_rc)
	# enable quit-chain tracing when debug flag is set
	if args.debug:
		key_input.QUIT_TRACE = True

	# build frame_states for any overlay tier; debug-only fields (raw,
	# FWD/BWD boxes) are only attached when draw_debug is set.
	# prev_center for the velocity arrow is precomputed here on the
	# driver side so parallel workers see correct lookback across chunk
	# boundaries (a chunk-local lookback would miss valid priors from
	# the preceding chunk and silently suppress the arrow at every
	# segment seam).
	any_overlay = draw_tracking or draw_debug or draw_velocity
	frame_states_for_debug = None
	if any_overlay:
		frame_states_for_debug = []
		for i, state in enumerate(trajectory):
			if state is not None:
				debug_state = {
					"cx": state["cx"],
					"cy": state["cy"],
					"w": state["w"],
					"h": state["h"],
					"conf": state["conf"],
					"source": state.get("source", "propagated"),
					"seed_status": state.get("seed_status", ""),
					"frame_index": i,
					"bbox": (state["cx"], state["cy"], state["w"], state["h"]),
				}
				# M3 commitment is diagnostic-only overlay metadata.  It stays
				# alongside the raw FWD/BWD boxes rather than changing the
				# persisted trajectory payload or review-tier rendering.
				if state.get("blend_flag", False):
					debug_state["commitment_direction"] = state.get("commitment_direction")
					debug_state["commitment_alpha"] = state.get("commitment_alpha")
				# attach raw (pre-anchor) position for drift comparison overlay
				if raw_trajectory_for_debug is not None and i < len(raw_trajectory_for_debug):
					raw = raw_trajectory_for_debug[i]
					if raw is not None:
						debug_state["raw_box"] = [raw["cx"], raw["cy"], raw["w"], raw["h"]]
				# attach FWD/BWD projection boxes for debug overlay
				if fwd_trajectory_for_debug is not None and i < len(fwd_trajectory_for_debug):
					fwd = fwd_trajectory_for_debug[i]
					if fwd is not None:
						debug_state["forward_box"] = [fwd["cx"], fwd["cy"], fwd["w"], fwd["h"]]
				if bwd_trajectory_for_debug is not None and i < len(bwd_trajectory_for_debug):
					bwd = bwd_trajectory_for_debug[i]
					if bwd is not None:
						debug_state["backward_box"] = [bwd["cx"], bwd["cy"], bwd["w"], bwd["h"]]
			else:
				debug_state = None
			frame_states_for_debug.append(debug_state)
		# precompute prev_center for the velocity arrow on the driver
		# side; the encoder will read state["prev_center"] directly so
		# parallel workers do not need access to other workers' chunks.
		#
		# By default the prev_center is land-relative (i.e., the runner's
		# motion relative to the ground), not camera-relative: when the
		# camera pans faster than the runner, a camera-relative arrow
		# would point backwards. We project the prior frame's pixel
		# position into scene (land-anchored) coords, then back into the
		# CURRENT frame's pixel coords. The arrow drawn from the current
		# crosshair to the difference is then runner-vs-ground motion
		# expressed in the current camera's pixel space.
		if draw_velocity:
			scene_transform = None
			# Load the canonical camera-motion artifact. Encode is
			# read-only and degrades gracefully when nothing is
			# available -- it does not hard-error like refine does.
			motion_track = None
			try:
				motion_track = camera_motion.load_active_camera_motion_or_fail(
					args.input_file, cfg, video_info=video_info,
				)
			except RuntimeError:
				motion_track = None
			if motion_track is not None:
				scene_transform = scene_coords.SceneTransform(motion_track)
			else:
				print(
					"  warning: no camera_motion artifact available; "
					"velocity arrow will use camera-relative motion "
					"(run 'solve' to populate the artifact)"
				)
			interval_solver_lookback = encoder._VELOCITY_LOOKBACK_FRAMES
			for i, st in enumerate(frame_states_for_debug):
				if st is None:
					continue
				start = i - 1
				stop = max(-1, i - 1 - interval_solver_lookback)
				prev_center = None
				prev_idx = -1
				for k in range(start, stop, -1):
					if k < 0:
						break
					prior = frame_states_for_debug[k]
					if prior is None:
						continue
					pcx = prior.get("cx")
					pcy = prior.get("cy")
					if pcx is None or pcy is None:
						continue
					if not (math.isfinite(pcx) and math.isfinite(pcy)):
						continue
					prev_center = (float(pcx), float(pcy))
					prev_idx = k
					break
				# Reproject the prior pixel position through scene
				# coordinates so the arrow direction reflects ground
				# motion. Falls back to camera-relative when the artifact
				# is unavailable or the frame index is out of range.
				if prev_center is not None and scene_transform is not None:
					try:
						prev_scene = scene_transform.pixel_to_scene(
							prev_idx, prev_center[0], prev_center[1],
						)
						prev_in_current = scene_transform.scene_to_pixel(
							i, prev_scene[0], prev_scene[1],
						)
						prev_center = (
							float(prev_in_current[0]),
							float(prev_in_current[1]),
						)
					except (IndexError, ValueError):
						pass
				st["prev_center"] = prev_center

	with key_input.KeyInputReader() as enc_kreader:
		if num_workers > 1:
			encoder.encode_cropped_video_parallel(
				encode_video_path, crop_rects, temp_video,
				crop_w, crop_h,
				codec=video_codec, crf=crf_value,
				frame_states=frame_states_for_debug,
				workers=num_workers,
				encode_filters=encode_filters,
				run_control=enc_rc,
				key_reader_obj=enc_kreader,
				draw_tracking=draw_tracking,
				draw_debug=draw_debug,
				draw_velocity=draw_velocity,
				nif_frames=nif_frames,
			)
		else:
			probe_info = common_tools.probe_video.probe_video(encode_video_path)
			with common_tools.frame_reader.FrameReader(
				encode_video_path, probe_info["fps"], probe_info["frame_count"],
			) as reader:
				encoder.encode_cropped_video(
					reader, crop_rects, temp_video,
					crop_w, crop_h,
					codec=video_codec, crf=crf_value,
					frame_states=frame_states_for_debug,
					encode_filters=encode_filters,
					run_control=enc_rc,
					key_reader_obj=enc_kreader,
					draw_tracking=draw_tracking,
					draw_debug=draw_debug,
					draw_velocity=draw_velocity,
					nif_frames=nif_frames,
				)
	# restore default signal handler
	key_input.restore_default_sigint()
	t_encode_elapsed = time.time() - t_encode_start
	if enc_rc.quit_requested:
		print(f"  encode interrupted ({t_encode_elapsed:.1f}s)")
		print("  skipping mux and finalize (quit requested)")
		return
	print(f"  encode complete ({t_encode_elapsed:.1f}s)")

	# mux audio
	print("muxing audio...")
	t_mux_start = time.time()
	encoder.copy_audio(args.input_file, temp_video, output_path)
	t_mux_elapsed = time.time() - t_mux_start
	print(f"  mux complete ({t_mux_elapsed:.1f}s)")

	# clean up temp file
	keep_temp = getattr(args, "keep_temp", False)
	if not keep_temp and os.path.isfile(temp_video) and os.path.isfile(output_path):
		os.remove(temp_video)
	print(f"\noutput: {output_path}")
