"""Implementation for the track_runner analyze CLI mode."""

import argparse
import os
import pathlib
import statistics
import sys

import analyze_report
import camera_motion
import encode_analysis
import encode_analysis_report
import fastread_video
import interval_solver
import modes.shared
import regime_classifier
import scene_coords
import state_io
import torso_box_coords_io
import tr_paths
import tr_crop


#============================================
def _compute_crop_trajectory(
	trajectory: list,
	video_info: dict,
	cfg: dict,
	nif_frames: set = None,
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
def _attach_interval_scores(interval_results: list, score_data: dict) -> list:
	"""Return solved geometry paired with its current interval-score records.

	The torso-coordinate NPZ intentionally contains geometry only. Analyze uses
	the interval-score JSON for its solver-context summary, so each geometry
	interval must have one matching current score by frame range.
	"""
	scores_by_range = {
		(int(score_record["start_frame"]), int(score_record["end_frame"])):
		score_record["interval_score"]
		for score_record in score_data["intervals"]
	}
	scored_results = []
	for interval_result in interval_results:
		key = (
			int(interval_result["start_frame"]),
			int(interval_result["end_frame"]),
		)
		if key not in scores_by_range:
			raise RuntimeError(
				f"interval {key[0]}-{key[1]} lacks current interval scores; "
				"run 'solve' first"
			)
		scored_result = dict(interval_result)
		scored_result["interval_score"] = scores_by_range[key]
		scored_results.append(scored_result)
	return scored_results


#============================================
def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	diag_path: str,
	intervals_path: str | None = None,
	video_context: fastread_video.VideoContext | None = None,
) -> None:
	"""Analyze mode: compute crop-path stability metrics before encoding.

	Reconstructs the trajectory from solved intervals (same pipeline as
	encode), computes crop rects, then runs crop-path stability analysis
	and solver context analysis. Prints a formatted console report and
	writes a diagnostic YAML file.

	Reports name BOTH the canonical source (the original video) and the
	decode source (the fast-read working video basename, or the literal
	"original" when no fast-read is in use). Analyze reads solved-interval
	artifacts and does not decode frames itself, but it carries the routing
	labels so report consumers see the same provenance as decode modes.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
			If None, derived from input_file.
		video_context: Resolved per-run routing. Supplies canonical_source
			(original) and decode_source labels for the reports. When None
			(diagnostic/test callers), labels default to the input file as
			canonical and "original" as decode.
	"""
	# resolve the canonical source (original) and decode-source label. The
	# decode label is the fast-read basename when a valid fast-read is in
	# use, otherwise the literal "original".
	if video_context is None:
		canonical_source = os.path.basename(args.input_file)
		decode_source = "original"
	else:
		canonical_source = os.path.basename(video_context.original_video_path)
		if video_context.working_decode.using_fastread:
			decode_source = os.path.basename(video_context.working_decode.path)
		else:
			decode_source = "original"
		# show the routing banner; analyze itself does not decode frames,
		# so the decode line reflects what working modes would use.
		fastread_video.print_video_routing_banner(
			video_context.original_video_path,
			video_context.working_decode.path,
		)
	# apply aspect override
	if getattr(args, "aspect", None) is not None:
		cfg.setdefault("processing", {})
		cfg["processing"]["crop_aspect"] = args.aspect

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
	# NIF edge anchors steer the crop only. Keep the erased trajectory as
	# truth for analysis panels and confidence metrics: a NIF seed means the
	# runner is absent, not located at the source-frame edge.
	crop_trajectory, nif_frames = modes.shared.build_nif_crop_inputs(
		trajectory, all_seeds, diag_data, video_info,
	)

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

	# compute output dimensions (same logic as encode)
	proc_cfg = cfg.get("processing", {})
	user_resolution = proc_cfg.get("output_resolution")
	if user_resolution is not None:
		crop_w = int(user_resolution[0])
		crop_h = int(user_resolution[1])
	elif crop_rects:
		all_widths = [r[2] for r in crop_rects]
		all_heights = [r[3] for r in crop_rects]
		crop_w = int(statistics.median(all_widths))
		crop_h = int(statistics.median(all_heights))
	else:
		crop_h = video_info["height"] // 2
		crop_w = crop_h
	# ensure even dimensions
	crop_w = crop_w - (crop_w % 2)
	crop_h = crop_h - (crop_h % 2)

	fps = float(diag_data.get("fps", video_info["fps"]))

	# run crop-path stability analysis
	print("analyzing crop path stability...")
	analysis = encode_analysis.analyze_crop_stability(
		crop_rects, trajectory, crop_w, crop_h, fps,
	)

	# run solver context analysis
	solver_context = encode_analysis.analyze_solver_context(
		_attach_interval_scores(interval_results, diag_data), all_seeds, fps,
	)

	# run regime classification for smart mode diagnostics
	regime_spans = regime_classifier.classify_regimes(
		trajectory, video_info,
	)
	regime_summary_line = regime_classifier.format_regime_summary(
		regime_spans, video_info["frame_count"],
	)

	# write YAML report
	analysis_path = tr_paths.default_encode_analysis_path(args.input_file)
	encode_analysis_report.write_analysis_yaml(
		analysis, solver_context, analysis_path,
		regime_spans=regime_spans,
		canonical_source=canonical_source,
		decode_source=decode_source,
		dolly_crop_report=dolly_report.as_dict() if dolly_report is not None else None,
	)

	# print console report
	report = encode_analysis_report.format_analysis_report(
		analysis, solver_context, analysis_path,
		regime_summary_line=regime_summary_line,
		canonical_source=canonical_source,
		decode_source=decode_source,
	)
	print(report)

	# write HTML diagnostic report when --plot is set. Camera motion is
	# loaded with the same graceful-degradation pattern as encode mode
	# (see modes.encode.run draw_velocity branch): a missing
	# artifact is a warning, not an error.
	if args.write_plots:
		# Single message string used in both the HTML warnings section and
		# the stderr line below; defining it once prevents tense drift
		# between the two surfaces.
		camera_motion_missing_msg = (
			"Camera-motion data unavailable; "
			"camera and speed panels were skipped."
		)
		report_warnings = []
		motion_track = None
		scene_transform = None
		try:
			motion_track = camera_motion.load_active_camera_motion_or_fail(
				args.input_file, cfg, video_info=video_info,
			)
		except RuntimeError:
			report_warnings.append(camera_motion_missing_msg)
		if motion_track is None:
			print(f"  warning: {camera_motion_missing_msg}", file=sys.stderr)
		else:
			scene_transform = scene_coords.SceneTransform(motion_track)

		# derive HTML output path: same stem as the YAML report, .html suffix
		html_path = pathlib.Path(analysis_path).with_suffix('.html')
		# strip the trailing .encode_analysis from the stem so the HTML's
		# References section links back to the YAML at <stem>.encode_analysis.yaml
		stem = html_path.stem
		if stem.endswith('.encode_analysis'):
			stem = stem[:-len('.encode_analysis')]

		# tr_crop.trajectory_to_crop_rects pads crop_rects to the full source
		# frame count via gap-filling, but `trajectory` here is the shorter
		# post-erasure list. Pad trajectory with None for the missing frames
		# so panel builders can index trajectory[i] alongside crop_rects[i];
		# None entries are treated as "no torso geometry" (gaps in the panel
		# series) per the documented degradation contract.
		total_frames = video_info["frame_count"]
		if len(trajectory) < total_frames:
			trajectory_aligned = list(trajectory) + [None] * (total_frames - len(trajectory))
		else:
			trajectory_aligned = trajectory[:total_frames]

		# The shared crop seam owns the full NIF span. Its indices are exactly
		# the frames erased from runner truth, not the old per-seed radius.
		# The report can therefore distinguish human-confirmed absence from a
		# tracker gap without deriving a second NIF span.
		erased_frames = nif_frames

		out = analyze_report.write_analyze_report(
			out_path=html_path,
			video_stem=stem,
			trajectory=trajectory_aligned,
			crop_rects=crop_rects,
			motion_track=motion_track,
			scene_transform=scene_transform,
			fps=fps,
			config=cfg,
			warnings=report_warnings,
			erased_frames=erased_frames,
			canonical_source=canonical_source,
			decode_source=decode_source,
		)
		print(f"wrote diagnostic report: {out}")
