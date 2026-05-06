#!/usr/bin/env python3
"""Analyze torso-box noise as the cause of zoom bounce.

Working hypothesis (per plan
`/Users/vosslab/.claude/plans/noisy-jittering-tendril.md`):

	Zoom bounce is caused by frame-to-frame noise in the solved
	torso box (especially h/w). The crop algorithm faithfully
	tracks the noisy biological measurement; the encoded video
	shows that noise as visible zoom bounce.

This tool emits a per-frame trace CSV documenting the path from
solved torso h/w to the final integer crop rectangle, plus per-pass
torso-h time series (FWD / BWD / blended / stitched / final) so the
introduction point of any noise can be localized.

Output:
	{output-dir}/crop_zoom_trace.csv             per-frame trace
	{output-dir}/{basename}_torso_noise_summary.md   summary doc
	{output-dir}/hotspot_window_<rank>.png       4-series plot per
		hotspot window when --hotspots-md is supplied

The decision tree at the end of the summary says whether the
torso-h-noise hypothesis is supported (Pearson rho > 0.5 between
abs(diff(torso_h)) and abs(diff(smoothed_crop_height))).
"""

# Standard Library
import os
import re
import sys
import csv
import argparse

# allow imports from repo + tools + track_runner when invoked outside the repo
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# PIP3 modules
import numpy
import scipy.stats
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# local repo modules (tools)
import find_zoom_hotspots

# local repo modules (track_runner)
import interval_solver
import state_io
import tr_config
import encoder
import tr_crop
import tr_paths
import torso_size_stabilizer
import video_io

# local repo modules (common_tools)
import common_tools.probe_video


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description=(
			"Analyze torso-box noise as the cause of zoom bounce. "
			"Emits a per-frame trace CSV and per-hotspot 4-series plot."
		),
	)
	parser.add_argument(
		"-i", "--input", dest="source_file", required=True,
		help=(
			"Source video file. Solved-interval data is discovered next "
			"to this file via tr_paths."
		),
	)
	parser.add_argument(
		"-e", "--encoded", dest="encoded_file", default="",
		help=(
			"Optional encoded *_tracked.mkv. When supplied, the tool "
			"adds a post-encode log_scale series for cross-reference."
		),
	)
	parser.add_argument(
		"-o", "--output-dir", dest="output_dir", default="",
		help=(
			"Output directory. Default: "
			"output_smoke/zoom_bounce/EVIDENCE/torso_noise/{basename}/"
		),
	)
	parser.add_argument(
		"--reference-window", dest="reference_window", type=int,
		default=7,
		help=(
			"Window length for the diagnostic median-filtered torso_h "
			"reference series shown on hotspot plots. Default 7."
		),
	)
	parser.add_argument(
		"-S", "--stabilizer-method", dest="stabilizer_method",
		choices=torso_size_stabilizer.METHOD_CHOICES, default="none",
		help=(
			"Apply a robust torso w/h stabilizer to the loaded trajectory "
			"before computing crop signals. Default `none` (baseline). "
			"Choices: none, median, hampel, mad_gated."
		),
	)
	parser.add_argument(
		"-W", "--stabilizer-window", dest="stabilizer_window", type=int,
		default=7,
		help=(
			"Centered window length for the stabilizer (used when "
			"--stabilizer-method is non-`none`). Default 7."
		),
	)
	parser.add_argument(
		"-H", "--hotspots-md", dest="hotspots_md", default="",
		help=(
			"Path to per-video hotspots.md (from find_zoom_hotspots). "
			"When supplied, the tool emits a per-hotspot 4-series PNG."
		),
	)
	parser.add_argument(
		"-T", "--frame-tolerance", dest="frame_tolerance", type=int,
		default=5000,
		help="Frame-count tolerance for video vs trajectory alignment. Default 5000.",
	)
	parser.add_argument(
		"--bisect-stages", dest="bisect_stages", action="store_true",
		help=(
			"Emit a per-stage zoom-bounce table (Markdown) and a "
			"per-frame stage trace CSV covering S0..S5 of "
			"tr_crop.direct_center_crop_trajectory. See plan "
			"~/.claude/plans/lucky-napping-lake.md."
		),
	)
	parser.add_argument(
		"--variant", dest="variant", default="none",
		choices=("none", "v1"),
		help=(
			"Run a tool-side fit-to-source variant alongside production. "
			"`v1` simulates the rolling-min source-fit ceiling (window 7 "
			"by default; V1b informational at window 3). Adds S3_v1 / "
			"S5_v1 (and S3_v1b / S5_v1b) rows to the per-stage table "
			"and additional columns to the trace CSV. Requires "
			"--bisect-stages. Default `none` (production only)."
		),
	)
	parser.add_argument(
		"--encode-review", dest="encode_review", action="store_true",
		help=(
			"Encode a 30 s side-by-side review clip centered on the worst "
			"S5 hotspot frame: production crop vs V1 crop. Writes "
			"production_clip.mkv, v1_clip.mkv, and side_by_side.mkv "
			"under <output_dir>/review_clips/. Requires --variant v1. "
			"Diagnostic surface only; does not change tr_crop, the "
			"encoder CLI, or any production behavior."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def load_per_pass_torso_h(intervals_data: dict, total_frames: int) -> dict:
	"""Concatenate per-interval forward/backward/blended paths into per-frame torso_h.

	Each solved interval has forward_path, backward_path, blended_path
	(see state_io.load_torso_box_coords). Each path is a list of state
	dicts with frame indices and torso h. We assemble three per-frame
	arrays of length total_frames; missing frames stay NaN.

	Args:
		intervals_data: Output of state_io.load_torso_box_coords.
		total_frames: Length of the output arrays (matches the final
			stitched trajectory length).

	Returns:
		Dict with keys "forward_h", "backward_h", "blended_h"; each
		value is a numpy array of length total_frames with NaN where
		the interval did not provide a sample.
	"""
	fwd = numpy.full(total_frames, numpy.nan, dtype=numpy.float64)
	bwd = numpy.full(total_frames, numpy.nan, dtype=numpy.float64)
	blended = numpy.full(total_frames, numpy.nan, dtype=numpy.float64)
	solved = intervals_data.get("solved_intervals", {})
	for entry in solved.values():
		# each path is a list of state dicts that include "frame" or are
		# implicitly indexed from start_frame; check the first entry
		for path_key, target in (
			("forward_path", fwd),
			("backward_path", bwd),
			("blended_path", blended),
		):
			path = entry.get(path_key)
			if not path:
				continue
			start_frame = int(entry["start_frame"])
			for offset, state in enumerate(path):
				if state is None:
					continue
				frame_index = state.get("frame", start_frame + offset)
				if 0 <= frame_index < total_frames:
					target[int(frame_index)] = float(state["h"])
	return {
		"forward_h": fwd,
		"backward_h": bwd,
		"blended_h": blended,
	}


#============================================
def compute_pre_encode_crop_signals(
	trajectory: list,
	video_info: dict,
	cfg: dict,
) -> dict:
	"""Re-derive the pre-encode crop signals using tr_crop's logic.

	The production `tr_crop.direct_center_crop_trajectory` returns only
	integer crop rectangles. To diagnose torso-vs-crop noise we need
	the intermediate float signals at every pipeline stage. This helper
	reimplements just enough of the math (by reusing the production
	primitives directly) to expose those values without modifying
	production code.

	Stages exposed (see plan `~/.claude/plans/lucky-napping-lake.md`):
	  S0_raw      raw torso, scale-normalized to crop units
	  S1_w_h_avg  post W+H average (desired_crop_h)
	  S2_ema_1    post EMA pass 1
	  S3_fit_1    post fit-to-source pass 1
	  S4_ema_2    post EMA pass 2 (== S3 when alpha_size == 0)
	  S5_fit_2    post fit-to-source pass 2 (the existing gate point)

	Args:
		trajectory: Per-frame state list (from interval_solver after
			anchor + erasure).
		video_info: Dict with width, height, fps.
		cfg: Project config.

	Returns:
		Dict with per-frame numpy arrays:
		  raw_cx, raw_cy, raw_w, raw_h,
		  desired_crop_h (W+H average; the pre-EMA crop-size estimate),
		  smoothed_crop_h (post-EMA, pre-fit),
		  final_smoothed_h, final_smoothed_w (post-Step 3.6),
		  edge_clamped (bool: True where Step 3.6 pass-2 shrank the
		    EMA output),
		  edge_clamped_S3 (bool: True where Step 3.6 pass-1 shrank
		    the EMA pass-1 output),
		  S0_raw, S1_w_h_avg, S2_ema_1, S3_fit_1, S4_ema_2, S5_fit_2
		    (each a sub-dict with `h` and `w` numpy arrays of length
		    n_frames).
	"""
	processing = cfg.get("processing", {})
	aspect_str = processing.get("crop_aspect", "1:1")
	aspect_ratio = tr_crop.parse_aspect_ratio(aspect_str)
	torso_multiple = float(processing.get("torso_height_multiple", 3.33))
	alpha_size = float(processing.get("crop_post_smooth_size_strength", 0.15))
	fit_to_source = bool(processing.get("crop_centered_fit_to_source", True))

	n = len(trajectory)
	raw_cx = numpy.zeros(n, dtype=numpy.float64)
	raw_cy = numpy.zeros(n, dtype=numpy.float64)
	raw_w = numpy.zeros(n, dtype=numpy.float64)
	raw_h = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		state = trajectory[i]
		if state is None:
			# leave NaN-equivalent zero; downstream filters drop these
			continue
		raw_cx[i] = float(state["cx"])
		raw_cy[i] = float(state["cy"])
		raw_w[i] = float(state["w"])
		raw_h[i] = float(state["h"])

	frame_w = int(video_info["width"])
	frame_h = int(video_info["height"])

	# Per-frame geometric ceiling (the bouncy signal V1 attacks).
	# Same arithmetic as tr_crop._max_centered_fit_size:
	#   max_w_horiz = 2 * min(cx, frame_w - cx)
	#   max_w_vert  = aspect * 2 * min(cy, frame_h - cy)
	#   ceiling_w   = min(max_w_horiz, max_w_vert)
	max_w_horiz = 2.0 * numpy.minimum(raw_cx, frame_w - raw_cx)
	max_w_vert = aspect_ratio * 2.0 * numpy.minimum(raw_cy, frame_h - raw_cy)
	geometric_ceiling_w = numpy.minimum(max_w_horiz, max_w_vert)
	geometric_ceiling_h = geometric_ceiling_w / aspect_ratio

	# S0: scale-normalized raw torso. Not equal to S1 by construction;
	# kept as a "before any crop construction" reference signal.
	s0_h = raw_h * torso_multiple
	s0_w = raw_w * torso_multiple

	# S1: W+H average per tr_crop.direct_center_crop_trajectory
	desired_h_from_h = raw_h * torso_multiple
	desired_h_from_w = (raw_w * torso_multiple) / aspect_ratio
	desired_crop_h = 0.5 * (desired_h_from_h + desired_h_from_w)
	s1_h = desired_crop_h
	s1_w = s1_h * aspect_ratio

	# S2: forward-backward EMA pass 1 on desired_crop_h
	if alpha_size > 0:
		smoothed_h_pre_fit = tr_crop._forward_backward_ema(desired_crop_h, alpha_size)
	else:
		smoothed_h_pre_fit = desired_crop_h.copy()
	# the production code applies torso_anchor offset to cy here; we
	# don't need it for the size diagnostic.
	smoothed_h_pre_fit = numpy.maximum(smoothed_h_pre_fit, 1.0)
	smoothed_w_pre_fit = numpy.maximum(smoothed_h_pre_fit * aspect_ratio, 1.0)
	s2_h = smoothed_h_pre_fit.copy()
	s2_w = smoothed_w_pre_fit.copy()

	# S3: fit-to-source pass 1. When fit_to_source is False, S3 == S2.
	s3_h = s2_h.copy()
	s3_w = s2_w.copy()
	if fit_to_source:
		for i in range(n):
			fit_w, fit_h = tr_crop._max_centered_fit_size(
				raw_cx[i], raw_cy[i],
				s3_w[i],
				frame_w, frame_h,
				aspect_ratio,
			)
			s3_w[i] = max(fit_w, 1.0)
			s3_h[i] = max(fit_h, 1.0)
	edge_clamped_s3 = s3_h < (s2_h - 0.5)

	# S4: forward-backward EMA pass 2 on the fit output. Only runs in
	# production when both fit_to_source and alpha_size > 0; otherwise
	# S4 == S3.
	s4_h = s3_h.copy()
	s4_w = s3_w.copy()
	if fit_to_source and alpha_size > 0:
		s4_h = tr_crop._forward_backward_ema(s3_h, alpha_size)
		s4_w = s4_h * aspect_ratio

	# S5: fit-to-source pass 2. Only runs in production when both
	# fit_to_source and alpha_size > 0; otherwise S5 == S4 == S3.
	s5_h = s4_h.copy()
	s5_w = s4_w.copy()
	if fit_to_source and alpha_size > 0:
		for i in range(n):
			fit_w, fit_h = tr_crop._max_centered_fit_size(
				raw_cx[i], raw_cy[i],
				s5_w[i],
				frame_w, frame_h,
				aspect_ratio,
			)
			s5_w[i] = max(fit_w, 1.0)
			s5_h[i] = max(fit_h, 1.0)

	# edge_clamped: True where the final fit pass shrank its input
	edge_clamped = s5_h < (s4_h - 0.5) if (fit_to_source and alpha_size > 0) else edge_clamped_s3

	# fit_diagnostics: per-frame fit-to-source diagnostics (WP-A1).
	# requested_h, fitted_h, shrink_h, s4_overshoot, limiting_edge,
	# distance to each frame edge, and edge_clamp run id/length.
	requested_h = s2_h.copy()
	fitted_h = s3_h.copy()
	shrink_h = numpy.maximum(requested_h - fitted_h, 0.0)
	s4_overshoot = numpy.maximum(s4_h - s3_h, 0.0)
	# limiting_edge classification per frame. 0=none (S2 fit; desired
	# was the binding constraint), 1=top, 2=bottom, 3=left, 4=right.
	# When max_w_horiz binds, the limiting side is left or right
	# (whichever is closer); when max_w_vert binds, top or bottom.
	desired_w_at_s3 = s2_w
	limiting_edge_code = numpy.zeros(n, dtype=numpy.int32)
	# horizontal-bind: max_w_horiz < min(desired_w, max_w_vert)
	horiz_binds = (max_w_horiz <= max_w_vert + 1e-9) & (max_w_horiz < desired_w_at_s3 - 1e-9)
	vert_binds = (max_w_vert < max_w_horiz - 1e-9) & (max_w_vert < desired_w_at_s3 - 1e-9)
	# pick the closer side for each axis
	left_closer = raw_cx <= (frame_w - raw_cx)
	top_closer = raw_cy <= (frame_h - raw_cy)
	limiting_edge_code[horiz_binds & left_closer] = 3   # left
	limiting_edge_code[horiz_binds & ~left_closer] = 4  # right
	limiting_edge_code[vert_binds & top_closer] = 1     # top
	limiting_edge_code[vert_binds & ~top_closer] = 2    # bottom
	dist_top = raw_cy.copy()
	dist_bottom = float(frame_h) - raw_cy
	dist_left = raw_cx.copy()
	dist_right = float(frame_w) - raw_cx
	# edge-clamp run id and length: walk edge_clamped_s3 to assign a
	# unique run id to each maximal contiguous True block, and tag
	# every frame in a run with that run's length.
	edge_clamp_run_id = numpy.full(n, -1, dtype=numpy.int32)
	edge_clamp_run_length = numpy.zeros(n, dtype=numpy.int32)
	rid = 0
	i = 0
	while i < n:
		if edge_clamped_s3[i]:
			j = i
			while j < n and edge_clamped_s3[j]:
				j += 1
			run_len = j - i
			edge_clamp_run_id[i:j] = rid
			edge_clamp_run_length[i:j] = run_len
			rid += 1
			i = j
		else:
			i += 1

	fit_diagnostics = {
		"requested_h": requested_h,
		"fitted_h": fitted_h,
		"shrink_h": shrink_h,
		"geometric_ceiling_h": geometric_ceiling_h,
		"s4_overshoot": s4_overshoot,
		"limiting_edge_code": limiting_edge_code,
		"dist_top": dist_top,
		"dist_bottom": dist_bottom,
		"dist_left": dist_left,
		"dist_right": dist_right,
		"edge_clamp_run_id": edge_clamp_run_id,
		"edge_clamp_run_length": edge_clamp_run_length,
	}

	return {
		"raw_cx": raw_cx,
		"raw_cy": raw_cy,
		"raw_w": raw_w,
		"raw_h": raw_h,
		"desired_crop_h": desired_crop_h,
		"smoothed_crop_h": smoothed_h_pre_fit,
		"final_smoothed_h": s5_h,
		"final_smoothed_w": s5_w,
		"edge_clamped": edge_clamped,
		"edge_clamped_S3": edge_clamped_s3,
		"S0_raw":     {"h": s0_h, "w": s0_w},
		"S1_w_h_avg": {"h": s1_h, "w": s1_w},
		"S2_ema_1":   {"h": s2_h, "w": s2_w},
		"S3_fit_1":   {"h": s3_h, "w": s3_w},
		"S4_ema_2":   {"h": s4_h, "w": s4_w},
		"S5_fit_2":   {"h": s5_h, "w": s5_w},
		"fit_diagnostics": fit_diagnostics,
	}


#============================================
def median_filter_1d(values: numpy.ndarray, window: int) -> numpy.ndarray:
	"""Window-local median over a 1D array; centered window with edge clipping.

	Used as the torso-h noise reference: a per-frame robust estimate of
	the underlying torso height that ignores brief outliers. If the raw
	signal has high frame-to-frame variance but the median-filtered
	signal is smooth, the variance is noise rather than real subject-
	size change.
	"""
	window = max(int(window), 1)
	if window == 1:
		return values.copy()
	half = window // 2
	n = len(values)
	out = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		slice_vals = values[start:end]
		# ignore NaN in the slice
		valid = slice_vals[numpy.isfinite(slice_vals)]
		if len(valid) == 0:
			out[i] = numpy.nan
		else:
			out[i] = float(numpy.median(valid))
	return out


#============================================
def safe_pearson(a: numpy.ndarray, b: numpy.ndarray) -> tuple:
	"""Pearson correlation that drops NaN-paired entries.

	Returns (rho, p_value). When fewer than 3 valid pairs remain,
	returns (NaN, NaN).
	"""
	a = numpy.asarray(a, dtype=numpy.float64)
	b = numpy.asarray(b, dtype=numpy.float64)
	valid = numpy.isfinite(a) & numpy.isfinite(b)
	if int(numpy.sum(valid)) < 3:
		return (float("nan"), float("nan"))
	result = scipy.stats.pearsonr(a[valid], b[valid])
	return (float(result[0]), float(result[1]))


#============================================
def velocity_abs(values: numpy.ndarray) -> numpy.ndarray:
	"""abs(diff(values)); index 0 padded to NaN to keep length aligned.
	"""
	out = numpy.full_like(values, numpy.nan, dtype=numpy.float64)
	if len(values) > 1:
		out[1:] = numpy.abs(numpy.diff(values))
	return out


#============================================
def log_velocity(values: numpy.ndarray) -> numpy.ndarray:
	"""abs(diff(log(values))); index 0 padded to NaN.

	Mirrors the definition used by `assess_pixel_zoom.py`'s
	`zoom_velocity_log_p95` but applied to the simulated pre-encode
	crop signal rather than the post-encode Fourier-Mellin output.
	Non-positive or non-finite inputs produce NaN at that index.
	"""
	out = numpy.full_like(values, numpy.nan, dtype=numpy.float64)
	if len(values) > 1:
		valid = numpy.isfinite(values) & (values > 0)
		log_v = numpy.full_like(values, numpy.nan, dtype=numpy.float64)
		log_v[valid] = numpy.log(values[valid])
		out[1:] = numpy.abs(numpy.diff(log_v))
	return out


#============================================
def compute_simulated_hotspot_severity(
	log_velocity_series: numpy.ndarray,
	fps: float,
	top_n: int = 5,
) -> dict:
	"""Window-local p95 of log-velocity; report top-N hotspot severity.

	Mirrors `find_zoom_hotspots.score_velocity_p95` and
	`find_top_n_hotspots` but operates on the simulated pre-encode
	log-velocity series. Window is 5 seconds at the given fps (matches
	the find_zoom_hotspots default behavior). Returns the sum of the
	top-N peak intensities as the severity scalar plus the per-hotspot
	list for diagnostics.
	"""
	n = len(log_velocity_series)
	if n < 2 or fps <= 0.0:
		return {"severity": float("nan"), "hotspots": []}
	window_frames = max(int(round(5.0 * float(fps))), 5)
	half = window_frames // 2
	intensity = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		window_slice = log_velocity_series[start:end]
		valid_count = int(numpy.sum(numpy.isfinite(window_slice)))
		if valid_count == 0:
			intensity[i] = 0.0
		else:
			intensity[i] = float(numpy.nanpercentile(window_slice, 95))
	# greedy non-max suppression with min_gap = window_frames
	working = intensity.copy()
	hotspots = []
	for _ in range(int(top_n)):
		idx = int(numpy.argmax(working))
		val = float(working[idx])
		if not numpy.isfinite(val) or val <= 0.0:
			break
		hotspots.append((idx, val))
		mask_start = max(0, idx - window_frames)
		mask_end = min(len(working), idx + window_frames + 1)
		working[mask_start:mask_end] = -numpy.inf
	severity = float(sum(v for _, v in hotspots))
	return {"severity": severity, "hotspots": hotspots}


#============================================
STAGE_IDS = ("S0_raw", "S1_w_h_avg", "S2_ema_1", "S3_fit_1", "S4_ema_2", "S5_fit_2")


#============================================
def compute_per_stage_zoom_metrics(
	signals: dict, fps: float,
) -> list:
	"""Per-stage log_velocity_p95 and hotspot severity for S0..S5.

	For each stage the geometric-mean log_size is
	`log(sqrt(stage_h * stage_w))`. log_velocity at frame i is
	`abs(log_size[i] - log_size[i-1])` with index 0 set to NaN. The
	p95 is computed over the finite tail. hotspot severity reuses
	`compute_simulated_hotspot_severity` per stage.

	Args:
		signals: Output of `compute_pre_encode_crop_signals`.
		fps: Source video frame rate.

	Returns:
		List of six dicts, in canonical S0..S5 order, each with keys
		`stage_id, log_velocity_p95, hotspot_severity, n_frames_finite`.
	"""
	rows = []
	for stage_id in STAGE_IDS:
		stage = signals[stage_id]
		stage_h = numpy.asarray(stage["h"], dtype=numpy.float64)
		stage_w = numpy.asarray(stage["w"], dtype=numpy.float64)
		# geometric-mean log size; non-positive or non-finite -> NaN
		area = numpy.sqrt(numpy.maximum(stage_w * stage_h, 0.0))
		stage_log_v = log_velocity(area)
		finite = stage_log_v[numpy.isfinite(stage_log_v)]
		n_finite = int(finite.size)
		if n_finite >= 2:
			log_v_p95 = float(numpy.percentile(finite, 95))
		else:
			log_v_p95 = float("nan")
		hotspot = compute_simulated_hotspot_severity(stage_log_v, fps)
		rows.append({
			"stage_id": stage_id,
			"log_velocity_p95": log_v_p95,
			"hotspot_severity": float(hotspot["severity"]),
			"n_frames_finite": n_finite,
		})
	return rows


#============================================
def _stage_row_for(stage_id: str, stage: dict, fps: float) -> dict:
	"""Compute one row of per-stage zoom metrics for an arbitrary stage dict.

	Mirrors the per-stage iteration in `compute_per_stage_zoom_metrics`
	but operates on a single (stage_id, h, w) triple. Used to append
	V1/V1b rows to the per-stage table.
	"""
	stage_h = numpy.asarray(stage["h"], dtype=numpy.float64)
	stage_w = numpy.asarray(stage["w"], dtype=numpy.float64)
	area = numpy.sqrt(numpy.maximum(stage_w * stage_h, 0.0))
	stage_log_v = log_velocity(area)
	finite = stage_log_v[numpy.isfinite(stage_log_v)]
	n_finite = int(finite.size)
	if n_finite >= 2:
		log_v_p95 = float(numpy.percentile(finite, 95))
	else:
		log_v_p95 = float("nan")
	hotspot = compute_simulated_hotspot_severity(stage_log_v, fps)
	return {
		"stage_id": stage_id,
		"log_velocity_p95": log_v_p95,
		"hotspot_severity": float(hotspot["severity"]),
		"n_frames_finite": n_finite,
	}


#============================================
def rolling_min_1d(values: numpy.ndarray, window: int) -> numpy.ndarray:
	"""Centered rolling minimum over a 1D array.

	Window length is clipped to a minimum of 1. Edge handling truncates
	the window at array boundaries (no NaN introduced).

	Args:
		values: 1D float array.
		window: Centered window length.

	Returns:
		A new array of the same length where each element is the
		minimum of `values[max(0, i - half) : min(n, i + half + 1)]`.
	"""
	window = max(int(window), 1)
	if window == 1:
		return values.astype(numpy.float64, copy=True)
	half = window // 2
	n = len(values)
	out = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		start = max(0, i - half)
		end = min(n, i + half + 1)
		slice_vals = values[start:end]
		valid = slice_vals[numpy.isfinite(slice_vals)]
		if valid.size == 0:
			out[i] = float("nan")
		else:
			out[i] = float(numpy.min(valid))
	return out


#============================================
def compute_v1_rolling_min_ceiling(
	signals: dict, video_info: dict, cfg: dict, window: int = 7,
) -> dict:
	"""Variant V1: rolling-min source-fit ceiling.

	Mechanism: smooth the per-frame geometric ceiling sequence with a
	centered rolling minimum, then use the smoothed ceiling at the S3
	and S5 per-frame clip points. The S4 EMA between them is unchanged.

	Properties (all asserted at end of helper):
	  - smooth_ceiling_w[i] <= geometric_ceiling_w[i] for every i
	    (rolling min only shrinks)
	  - S5_v1_w[i] <= smooth_ceiling_w[i] for every i (no boundary
	    violation)
	  - S5_v1_h[i] <= S5_fit_2.h[i] for every i (V1 never enlarges
	    the crop)

	Args:
		signals: Output of `compute_pre_encode_crop_signals`.
		video_info: Dict with width, height, fps.
		cfg: Project config.
		window: Centered window length for the rolling min. Default 7.

	Returns:
		Dict with stage outputs:
		  S3_v1, S4_v1, S5_v1 (each a sub-dict with `h` and `w`),
		  smooth_ceiling_w (numpy array of length n_frames).
	"""
	processing = cfg.get("processing", {})
	aspect_str = processing.get("crop_aspect", "1:1")
	aspect_ratio = tr_crop.parse_aspect_ratio(aspect_str)
	alpha_size = float(processing.get("crop_post_smooth_size_strength", 0.15))

	# inputs from production simulation
	s2_w = numpy.asarray(signals["S2_ema_1"]["w"], dtype=numpy.float64)
	diag = signals["fit_diagnostics"]
	geometric_ceiling_h = numpy.asarray(
		diag["geometric_ceiling_h"], dtype=numpy.float64
	)
	geometric_ceiling_w = geometric_ceiling_h * aspect_ratio

	# Smoothed ceiling delegates to the production helper so the
	# tool-side simulation cannot drift from production semantics.
	# Identical arithmetic by construction.
	smooth_ceiling_w = tr_crop._rolling_min_ceiling_per_frame(
		signals["raw_cx"], signals["raw_cy"],
		int(video_info["width"]), int(video_info["height"]),
		aspect_ratio, window=window,
	)
	smooth_ceiling_h = smooth_ceiling_w / aspect_ratio

	# S3_v1: clip S2 by smooth ceiling
	s3v1_w = numpy.minimum(s2_w, smooth_ceiling_w)
	s3v1_w = numpy.maximum(s3v1_w, 1.0)
	s3v1_h = s3v1_w / aspect_ratio
	s3v1_h = numpy.maximum(s3v1_h, 1.0)

	# S4_v1: forward-backward EMA on S3_v1 (same alpha as production)
	if alpha_size > 0:
		s4v1_h = tr_crop._forward_backward_ema(s3v1_h, alpha_size)
	else:
		s4v1_h = s3v1_h.copy()
	s4v1_h = numpy.maximum(s4v1_h, 1.0)
	s4v1_w = s4v1_h * aspect_ratio

	# S5_v1: clip S4_v1 by smooth ceiling
	s5v1_w = numpy.minimum(s4v1_w, smooth_ceiling_w)
	s5v1_w = numpy.maximum(s5v1_w, 1.0)
	s5v1_h = s5v1_w / aspect_ratio
	s5v1_h = numpy.maximum(s5v1_h, 1.0)

	# invariants. The 1-pixel sanity floor is part of production
	# behavior (max(fit_w, 1.0) in tr_crop's S3/S5 loops); invariants
	# allow that floor to lift values to 1.0 even when the underlying
	# bound is < 1.
	# (1) smooth_ceiling <= geometric_ceiling per frame
	assert numpy.all(smooth_ceiling_w <= geometric_ceiling_w + 1e-9), (
		"V1 invariant violated: smooth_ceiling exceeds geometric_ceiling"
	)
	# (2) S5_v1 <= max(smooth_ceiling, 1.0) per frame
	smooth_or_floor = numpy.maximum(smooth_ceiling_w, 1.0)
	assert numpy.all(s5v1_w <= smooth_or_floor + 1e-9), (
		"V1 invariant violated: S5_v1_w exceeds max(smooth_ceiling_w, 1.0)"
	)
	# (3) S5_v1 <= S5_fit_2 per frame (modulo the floor): V1 never
	# enlarges the crop relative to production except possibly to the
	# 1-pixel floor on degenerate frames. Production S5 is bounded by
	# the per-frame geometric ceiling, while V1 S5 is bounded by
	# smooth_ceiling <= geometric ceiling. With identical S2/S4
	# inputs and tighter bound, V1 cannot exceed production except via
	# the EMA path operating on the (lower) S3_v1 input. Since EMA is
	# monotone in its input, S4_v1 <= S4 (production), so S5_v1 <= S5.
	s5_prod_w = numpy.asarray(signals["S5_fit_2"]["w"], dtype=numpy.float64)
	# allow 1-pixel slack for the sanity floor
	assert numpy.all(s5v1_w <= numpy.maximum(s5_prod_w, 1.0) + 1e-6), (
		"V1 invariant violated: S5_v1_w exceeds production S5_fit_2.w"
	)

	return {
		"S3_v1": {"h": s3v1_h, "w": s3v1_w},
		"S4_v1": {"h": s4v1_h, "w": s4v1_w},
		"S5_v1": {"h": s5v1_h, "w": s5v1_w},
		"smooth_ceiling_w": smooth_ceiling_w,
		"smooth_ceiling_h": smooth_ceiling_h,
	}


#============================================
def _resize_letterboxed(
	frame: numpy.ndarray, target_w: int, target_h: int,
) -> tuple:
	"""Resize frame to (target_w, target_h) preserving aspect ratio.

	Letterboxes (or pillarboxes) with black to fill the target box.
	Used by the review harness to render the source-context panel
	without distorting aspect.

	Returns:
		(out_image, scale, x_off, y_off) so callers can map
		full-frame coordinates onto the letterboxed panel for overlays.
	"""
	src_h, src_w = frame.shape[:2]
	scale = min(target_w / src_w, target_h / src_h)
	new_w = max(1, int(round(src_w * scale)))
	new_h = max(1, int(round(src_h * scale)))
	resized = cv2.resize(
		frame, (new_w, new_h),
		interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LANCZOS4,
	)
	out = numpy.zeros((target_h, target_w, 3), dtype=frame.dtype)
	x_off = (target_w - new_w) // 2
	y_off = (target_h - new_h) // 2
	out[y_off:y_off + new_h, x_off:x_off + new_w] = resized
	return out, float(scale), int(x_off), int(y_off)


#============================================
def _draw_torso_box_on_panel(
	image: numpy.ndarray,
	state: dict,
	transform: tuple,
	color: tuple = (0, 255, 0),
	thickness: int = 2,
) -> None:
	"""Draw a torso box on a review panel in place.

	Args:
		image: Panel image (modified in place).
		state: Trajectory state dict with cx, cy, w, h in source coords.
		transform: ("letterbox", scale, x_off, y_off) for the source panel,
			or ("crop", crop_rect, out_w, out_h) for the cropped panels.
			The crop variant delegates to encoder._box_to_crop_coords so
			the diagnostic harness uses the same arithmetic the encoder
			uses for the production debug overlay.
		color: BGR triple. Default green (matches the seed/predicted
			torso style used in the encoder debug overlay).
		thickness: Line thickness in pixels.
	"""
	if state is None:
		return
	cx = state.get("cx")
	cy = state.get("cy")
	w = state.get("w")
	h = state.get("h")
	if cx is None or cy is None or w is None or h is None:
		return
	kind = transform[0]
	if kind == "letterbox":
		_, scale, x_off, y_off = transform
		x1 = int(round((cx - w / 2.0) * scale + x_off))
		y1 = int(round((cy - h / 2.0) * scale + y_off))
		x2 = int(round((cx + w / 2.0) * scale + x_off))
		y2 = int(round((cy + h / 2.0) * scale + y_off))
	elif kind == "crop":
		_, crop_rect, out_w, out_h = transform
		x1, y1, x2, y2 = encoder._box_to_crop_coords(
			[float(cx), float(cy), float(w), float(h)],
			crop_rect, out_w, out_h,
		)
	else:
		return
	cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)


#============================================
def encode_review_clips(
	source_path: str,
	trajectory: list,
	signals: dict,
	video_info: dict,
	cfg: dict,
	output_dir: str,
	half_seconds: float = 15.0,
	target_size: int = 1080,
	review_torso_multiple: float = 5.0,
) -> dict:
	"""Encode side-by-side review clips: production-pre-V1 vs production-V1
	around the worst S5 hotspot, with a source-context panel on the left.

	Calls `tr_crop.direct_center_crop_trajectory` directly with the
	private `_use_rolling_min_ceiling` kwarg toggled to produce the
	pre-V1 (per-frame `_max_centered_fit_size` clip) and V1 (rolling-min
	ceiling) crop trajectories. Both rect sequences come from the SAME
	upstream pipeline (position EMA, composition offset, containment
	clamp), so the only difference is the fit-to-source step. This
	avoids the haywire-between-seeds artifact the previous harness
	produced by reading raw trajectory positions directly.

	Crop tightness is overridden to `review_torso_multiple` (default 5)
	so the clip shows enough context around the runner to assess the
	V1 effect. The override is a tweak of `cfg["processing"][
	"torso_height_multiple"]` for the harness call only -- it does not
	affect any persisted config or production behavior.

	Three panels in the side-by-side: source (full frame letterboxed)
	on the left, pre-V1 production crop in the middle, V1 production
	crop on the right.

	Args:
		source_path: Source video path (.mkv / .mov / etc.).
		trajectory: Per-frame state list (post-stitch, post-anchor,
			post-erasure -- the same trajectory the encoder uses).
		signals: Output of `compute_pre_encode_crop_signals` (used
			only to find the worst S5 hotspot frame).
		video_info: Probe dict with width, height, fps.
		cfg: Project config; this function copies and mutates a local
			copy to override `torso_height_multiple` for the review.
		output_dir: Directory to write the .mkv files.
		half_seconds: Half-window in seconds. Default 15 (-> 30 s).
		target_size: Output square size per panel in pixels. Default
			1080. Side-by-side total width = 3 * target_size.
		review_torso_multiple: Override for `torso_height_multiple` in
			the review crop. Default 5.0 (looser than the 3.33
			project default).

	Returns:
		Dict describing the encoded clips, or None if no hotspot
		frame is found.
	"""
	fps = float(video_info["fps"])
	frame_w = int(video_info["width"])
	frame_h = int(video_info["height"])
	half_window = int(round(half_seconds * fps))
	n_frames = len(signals["raw_cx"])

	# find worst S5 hotspot frame from the tool's production simulation
	s5_h = numpy.asarray(signals["S5_fit_2"]["h"], dtype=numpy.float64)
	s5_w_arr = numpy.asarray(signals["S5_fit_2"]["w"], dtype=numpy.float64)
	area = numpy.sqrt(numpy.maximum(s5_w_arr * s5_h, 0.0))
	s5_log_v = log_velocity(area)
	hotspot = compute_simulated_hotspot_severity(s5_log_v, fps, top_n=1)
	if not hotspot["hotspots"]:
		return None
	center_frame = int(hotspot["hotspots"][0][0])
	start = max(0, center_frame - half_window)
	end = min(n_frames, center_frame + half_window + 1)

	# build a review config: looser crop for visual assessment
	review_cfg = {
		"processing": dict(cfg.get("processing", {})),
	}
	review_cfg["processing"]["torso_height_multiple"] = float(
		review_torso_multiple
	)

	# gap-fill None entries by holding last-known state, mirroring the
	# logic in tr_crop.trajectory_to_crop_rects. direct_center_crop_trajectory
	# itself does not handle None states; production normally gap-fills
	# upstream of it.
	last_known = None
	full_trajectory = []
	for i in range(n_frames):
		state = trajectory[i] if i < len(trajectory) else None
		if state is not None:
			full_trajectory.append(state)
			last_known = state
		elif last_known is not None:
			hold_state = {
				"cx": last_known["cx"],
				"cy": last_known["cy"],
				"w": last_known["w"],
				"h": last_known["h"],
				"conf": 0.15,
				"source": "hold_last",
			}
			full_trajectory.append(hold_state)
		else:
			fallback = {
				"cx": frame_w / 2.0,
				"cy": frame_h / 2.0,
				"w": float(frame_w) * 0.3,
				"h": float(frame_h) * 0.5,
				"conf": 0.1,
				"source": "fallback",
			}
			full_trajectory.append(fallback)

	# call production tr_crop with both ceiling modes; full pipeline
	# (position smoothing, composition offset, containment clamp) is
	# applied identically to both, so the only difference is the
	# fit-to-source step.
	pre_v1_rects = tr_crop.direct_center_crop_trajectory(
		full_trajectory, frame_w, frame_h, review_cfg, fps=fps,
		_use_rolling_min_ceiling=False,
	)
	v1_rects = tr_crop.direct_center_crop_trajectory(
		full_trajectory, frame_w, frame_h, review_cfg, fps=fps,
		_use_rolling_min_ceiling=True,
	)

	# open source with cv2 and seek
	os.makedirs(output_dir, exist_ok=True)
	cap = cv2.VideoCapture(source_path)
	if not cap.isOpened():
		raise RuntimeError(f"cv2 failed to open {source_path}")
	cap.set(cv2.CAP_PROP_POS_FRAMES, start)
	pre_path = os.path.join(output_dir, "pre_v1_clip.mkv")
	v1_path = os.path.join(output_dir, "v1_clip.mkv")
	sxs_path = os.path.join(output_dir, "side_by_side.mkv")
	src_path = os.path.join(output_dir, "source_clip.mkv")
	pre_writer = video_io.VideoWriter(
		pre_path, target_size, target_size, fps, codec="libx264", crf=20,
	)
	v1_writer = video_io.VideoWriter(
		v1_path, target_size, target_size, fps, codec="libx264", crf=20,
	)
	src_writer = video_io.VideoWriter(
		src_path, target_size, target_size, fps, codec="libx264", crf=20,
	)
	sxs_writer = video_io.VideoWriter(
		sxs_path, target_size * 3, target_size, fps,
		codec="libx264", crf=20,
	)
	# main loop
	try:
		for i, idx in enumerate(range(start, end)):
			ret, frame = cap.read()
			if not ret:
				break
			# source panel: letterboxed full frame
			src_panel, lb_scale, lb_xoff, lb_yoff = _resize_letterboxed(
				frame, target_size, target_size,
			)
			# pre-V1 panel
			pre_crop = tr_crop.apply_crop(frame, pre_v1_rects[idx])
			pre_resized = cv2.resize(
				pre_crop, (target_size, target_size),
				interpolation=cv2.INTER_AREA,
			)
			# V1 panel
			v1_crop = tr_crop.apply_crop(frame, v1_rects[idx])
			v1_resized = cv2.resize(
				v1_crop, (target_size, target_size),
				interpolation=cv2.INTER_AREA,
			)
			# torso box overlays
			state = full_trajectory[idx]
			_draw_torso_box_on_panel(
				src_panel, state,
				("letterbox", lb_scale, lb_xoff, lb_yoff),
			)
			_draw_torso_box_on_panel(
				pre_resized, state,
				("crop", pre_v1_rects[idx], target_size, target_size),
			)
			_draw_torso_box_on_panel(
				v1_resized, state,
				("crop", v1_rects[idx], target_size, target_size),
			)
			src_writer.write_frame(src_panel)
			pre_writer.write_frame(pre_resized)
			v1_writer.write_frame(v1_resized)
			sxs = numpy.hstack([src_panel, pre_resized, v1_resized])
			sxs_writer.write_frame(sxs)
	finally:
		cap.release()
		src_writer.close()
		pre_writer.close()
		v1_writer.close()
		sxs_writer.close()
	return {
		"center_frame": center_frame,
		"start_frame": start,
		"end_frame": end,
		"source_path": src_path,
		"pre_v1_path": pre_path,
		"v1_path": v1_path,
		"side_by_side_path": sxs_path,
		"hotspot_intensity": float(hotspot["hotspots"][0][1]),
		"review_torso_multiple": float(review_torso_multiple),
	}


#============================================
def velocity_fractional(values: numpy.ndarray) -> numpy.ndarray:
	"""abs(diff(values)) / values[1:]; cross-video comparable per C2.

	Returns an array of the same length as `values`; index 0 is NaN to
	keep length alignment. Divisors that are non-finite or below
	1e-9 in absolute value produce NaN at that index, so zero or
	near-zero values do not blow up the metric.
	"""
	out = numpy.full_like(values, numpy.nan, dtype=numpy.float64)
	if len(values) > 1:
		diffs = numpy.abs(numpy.diff(values))
		divisors = values[1:]
		valid = numpy.isfinite(divisors) & (numpy.abs(divisors) > 1e-9)
		frac = numpy.full_like(diffs, numpy.nan, dtype=numpy.float64)
		frac[valid] = diffs[valid] / numpy.abs(divisors[valid])
		out[1:] = frac
	return out


#============================================
def jerk_abs(values: numpy.ndarray) -> numpy.ndarray:
	"""abs(diff(diff(values))); first two indices padded to NaN.
	"""
	out = numpy.full_like(values, numpy.nan, dtype=numpy.float64)
	if len(values) > 2:
		out[2:] = numpy.abs(numpy.diff(values, n=2))
	return out


#============================================
def write_trace_csv(
	output_path: str,
	signals: dict,
	per_pass: dict,
	post_encode_log_scale: numpy.ndarray,
) -> None:
	"""Emit the per-frame trace CSV with the columns the plan spec'd.
	"""
	n = len(signals["raw_cx"])
	final_h = signals["final_smoothed_h"]
	final_w = signals["final_smoothed_w"]
	delta_h = velocity_abs(final_h)
	jerk_h = jerk_abs(final_h)
	# fractional velocities (per C2: cross-video comparable)
	torso_h_frac_v = velocity_fractional(signals["raw_h"])
	torso_w_frac_v = velocity_fractional(signals["raw_w"])
	crop_h_frac_v = velocity_fractional(final_h)
	# log-velocity of geometric-mean area (the primary zoom-bounce gate)
	final_area = numpy.sqrt(numpy.maximum(final_w * final_h, 0.0))
	final_crop_log_velocity = log_velocity(final_area)

	header = [
		"frame",
		"torso_box_height",
		"torso_box_width",
		"torso_center_x",
		"torso_center_y",
		"raw_crop_height_estimate",
		"smoothed_crop_height",
		"final_crop_height_after_fit",
		"final_crop_width",
		"edge_clamped_bool",
		"crop_height_delta",
		"crop_height_jerk",
		"torso_h_fractional_velocity",
		"torso_w_fractional_velocity",
		"crop_h_fractional_velocity",
		"final_crop_log_velocity",
		"forward_torso_h",
		"backward_torso_h",
		"blended_torso_h",
		"post_encode_log_scale",
	]

	def fmt(value: float) -> str:
		"""Format a float consistently; NaN becomes empty cell.
		"""
		if not numpy.isfinite(value):
			return ""
		return f"{value:.6f}"

	with open(output_path, "w", newline="") as f:
		writer = csv.writer(f)
		writer.writerow(header)
		for i in range(n):
			row = [
				i,
				fmt(signals["raw_h"][i]),
				fmt(signals["raw_w"][i]),
				fmt(signals["raw_cx"][i]),
				fmt(signals["raw_cy"][i]),
				fmt(signals["desired_crop_h"][i]),
				fmt(signals["smoothed_crop_h"][i]),
				fmt(final_h[i]),
				fmt(final_w[i]),
				str(int(bool(signals["edge_clamped"][i]))),
				fmt(delta_h[i]),
				fmt(jerk_h[i]),
				fmt(torso_h_frac_v[i]),
				fmt(torso_w_frac_v[i]),
				fmt(crop_h_frac_v[i]),
				fmt(final_crop_log_velocity[i]),
				fmt(per_pass["forward_h"][i]),
				fmt(per_pass["backward_h"][i]),
				fmt(per_pass["blended_h"][i]),
				fmt(post_encode_log_scale[i] if i < len(post_encode_log_scale) else float("nan")),
			]
			writer.writerow(row)


#============================================
def parse_hotspots_md(path: str) -> list:
	"""Parse a *_tracked.hotspots.md file and return a list of dicts.

	Returns: [{"rank": int, "frame": int, "start_s": float, "end_s": float}, ...]
	"""
	with open(path) as f:
		text = f.read()
	# row: | rank | frame | start_s | end_s | velocity_p95 | abs_smoothed |
	pattern = re.compile(
		r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"
	)
	hotspots = []
	for match in pattern.finditer(text):
		# the header line "| --- | --- | ..." also matches; skip rows
		# whose first capture is not a digit (it always is here, but
		# the pattern is digit-only so header rows are filtered by the
		# explicit \d+ on the rank column)
		hotspots.append({
			"rank": int(match.group(1)),
			"frame": int(match.group(2)),
			"start_s": float(match.group(3)),
			"end_s": float(match.group(4)),
		})
	return hotspots


#============================================
def render_hotspot_window_plot(
	output_path: str,
	signals: dict,
	median_h: numpy.ndarray,
	post_encode_log_scale: numpy.ndarray,
	center_frame: int,
	half_window: int,
	rank: int,
	basename: str,
) -> None:
	"""Render the 4-series plot for one hotspot window.

	Series:
		1. raw torso_h
		2. median-filtered torso_h
		3. smoothed crop_h (final, post-fit)
		4. post-encode log_scale (when supplied)
	"""
	start = max(0, center_frame - half_window)
	end = min(len(signals["raw_h"]), center_frame + half_window + 1)
	x = numpy.arange(start, end)
	raw_h_slice = signals["raw_h"][start:end]
	med_slice = median_h[start:end]
	final_h_slice = signals["final_smoothed_h"][start:end]
	if len(post_encode_log_scale) >= end:
		log_slice = post_encode_log_scale[start:end]
	else:
		log_slice = numpy.full(end - start, numpy.nan)

	fig, axes = plt.subplots(4, 1, figsize=(10, 9), sharex=True)
	axes[0].plot(x, raw_h_slice, color="#1f77b4", linewidth=1.0)
	axes[0].set_ylabel("raw torso_h\n(px)")
	axes[0].grid(True, alpha=0.3)
	axes[1].plot(x, med_slice, color="#2ca02c", linewidth=1.0)
	axes[1].set_ylabel("median_filt\ntorso_h (px)")
	axes[1].grid(True, alpha=0.3)
	axes[2].plot(x, final_h_slice, color="#d62728", linewidth=1.0)
	axes[2].set_ylabel("final crop_h\n(px)")
	axes[2].grid(True, alpha=0.3)
	axes[3].plot(x, log_slice, color="#9467bd", linewidth=1.0)
	axes[3].set_ylabel("post-encode\nlog_scale")
	axes[3].set_xlabel("frame index")
	axes[3].grid(True, alpha=0.3)
	axes[3].axhline(0.0, color="black", linewidth=0.5)
	# vertical line at the center frame
	for ax in axes:
		ax.axvline(center_frame, color="black", linewidth=0.5, linestyle=":")
	fig.suptitle(
		f"Hotspot rank {rank}: {basename} center frame {center_frame}"
	)
	fig.tight_layout()
	fig.savefig(output_path, dpi=120)
	plt.close(fig)


#============================================
def write_summary_md(
	output_path: str,
	basename: str,
	signals: dict,
	median_h: numpy.ndarray,
	post_encode_log_scale: numpy.ndarray,
	per_pass: dict,
	hotspots: list,
	fps: float,
) -> None:
	"""Compose the per-video summary markdown.
	"""
	# build content in a variable, then write
	lines = []
	lines.append(f"# Torso-noise summary: {basename}")
	lines.append("")
	lines.append("## Frame coverage")
	lines.append("")
	lines.append("| Field | Value |")
	lines.append("| --- | --- |")
	lines.append(f"| n_frames | {len(signals['raw_h'])} |")
	lines.append(
		f"| n_finite_torso_h | "
		f"{int(numpy.sum(numpy.isfinite(signals['raw_h']) & (signals['raw_h'] > 0)))} |"
	)
	lines.append(
		f"| n_finite_post_encode_log_scale | "
		f"{int(numpy.sum(numpy.isfinite(post_encode_log_scale)))} |"
	)
	for key in ("forward_h", "backward_h", "blended_h"):
		lines.append(
			f"| n_{key} | "
			f"{int(numpy.sum(numpy.isfinite(per_pass[key])))} |"
		)
	lines.append(
		f"| n_edge_clamped_frames | "
		f"{int(numpy.sum(signals['edge_clamped']))} |"
	)
	lines.append("")

	# Pearson correlations: torso_h velocity vs crop_h velocity, vs encoded
	torso_v = velocity_abs(signals["raw_h"])
	crop_v = velocity_abs(signals["final_smoothed_h"])
	enc_v = velocity_abs(post_encode_log_scale)
	rho_torso_crop, p_torso_crop = safe_pearson(torso_v, crop_v)
	rho_torso_enc, p_torso_enc = safe_pearson(
		torso_v[: len(enc_v)], enc_v[: len(torso_v)],
	)
	# fractional-units variant (cross-video comparable per C2)
	torso_v_frac = velocity_fractional(signals["raw_h"])
	crop_v_frac = velocity_fractional(signals["final_smoothed_h"])
	rho_torso_crop_frac, p_torso_crop_frac = safe_pearson(
		torso_v_frac, crop_v_frac,
	)

	lines.append("## Pearson correlations on velocity series")
	lines.append("")
	lines.append("| Pair | Pearson rho | p-value |")
	lines.append("| --- | --- | --- |")
	lines.append(
		f"| abs(diff(torso_h)) vs abs(diff(final_crop_h)) | "
		f"{rho_torso_crop:+.4f} | {p_torso_crop:.4g} |"
	)
	lines.append(
		f"| fractional(torso_h) vs fractional(final_crop_h) | "
		f"{rho_torso_crop_frac:+.4f} | {p_torso_crop_frac:.4g} |"
	)
	lines.append(
		f"| abs(diff(torso_h)) vs abs(diff(post_encode_log_scale)) | "
		f"{rho_torso_enc:+.4f} | {p_torso_enc:.4g} |"
	)
	lines.append("")

	# Primary zoom-bounce gate: log-velocity of geometric-mean
	# crop area on the simulated final crop rectangle. Mirrors
	# `assess_pixel_zoom.zoom_velocity_log_p95` but pre-encode.
	final_h_arr = signals["final_smoothed_h"]
	final_w_arr = signals["final_smoothed_w"]
	final_area = numpy.sqrt(numpy.maximum(final_w_arr * final_h_arr, 0.0))
	final_log_v = log_velocity(final_area)
	valid_log_v = final_log_v[numpy.isfinite(final_log_v)]
	if len(valid_log_v) > 0:
		final_crop_log_velocity_p95 = float(
			numpy.nanpercentile(valid_log_v, 95)
		)
	else:
		final_crop_log_velocity_p95 = float("nan")
	severity_info = compute_simulated_hotspot_severity(
		final_log_v, fps=fps, top_n=5,
	)
	lines.append("## Zoom-bounce gate (simulated final crop rectangle)")
	lines.append("")
	lines.append("Primary metric mirrors `assess_pixel_zoom.zoom_velocity_log_p95`")
	lines.append("but is computed pre-encode on the simulated final crop area.")
	lines.append("")
	lines.append("| Metric | Value |")
	lines.append("| --- | --- |")
	lines.append(
		f"| final_crop_log_velocity_p95 | "
		f"{final_crop_log_velocity_p95:.6f} |"
	)
	lines.append(
		f"| simulated_hotspot_severity (sum top-5 p95-windows) | "
		f"{severity_info['severity']:.6f} |"
	)
	lines.append(f"| n_simulated_hotspots | {len(severity_info['hotspots'])} |")
	lines.append("")

	# Fractional jitter p95 (cross-video comparable per C2)
	lines.append("## Fractional jitter (p95)")
	lines.append("")
	lines.append("| Series | n_finite | fractional_velocity_p95 |")
	lines.append("| --- | --- | --- |")
	for label, series in (
		("torso_h_fractional_velocity", torso_v_frac),
		("torso_w_fractional_velocity",
			velocity_fractional(signals["raw_w"])),
		("crop_h_fractional_velocity", crop_v_frac),
	):
		valid = numpy.isfinite(series)
		n_valid = int(numpy.sum(valid))
		if n_valid > 0:
			p95 = float(numpy.nanpercentile(series[valid], 95))
			lines.append(f"| {label} | {n_valid} | {p95:.6f} |")
		else:
			lines.append(f"| {label} | 0 | -- |")
	lines.append("")

	# per-pass jitter statistics
	lines.append("## Per-pass torso_h jitter (p95 of abs diff)")
	lines.append("")
	lines.append("| Layer | n_finite | velocity_p95 |")
	lines.append("| --- | --- | --- |")
	for layer_name, layer_values in (
		("FWD path", per_pass["forward_h"]),
		("BWD path", per_pass["backward_h"]),
		("blended path", per_pass["blended_h"]),
		("stitched + anchored + erased (final)", signals["raw_h"]),
	):
		layer_v = velocity_abs(layer_values)
		valid = numpy.isfinite(layer_v)
		n_valid = int(numpy.sum(valid))
		if n_valid > 0:
			p95 = float(numpy.nanpercentile(layer_v[valid], 95))
			lines.append(f"| {layer_name} | {n_valid} | {p95:.6f} |")
		else:
			lines.append(f"| {layer_name} | 0 | -- |")
	lines.append("")

	# hotspot/torso-spike alignment
	if hotspots:
		torso_v_p95 = float(numpy.nanpercentile(
			torso_v[numpy.isfinite(torso_v)], 95,
		))
		aligned_count = 0
		alignment_rows = []
		for hs in hotspots[:5]:
			frame_index = hs["frame"]
			# search +/- 60 frames (~1s at 60fps, ~2s at 30fps) for a
			# torso_v spike above the per-video p95
			start = max(0, frame_index - 60)
			end = min(len(torso_v), frame_index + 61)
			window = torso_v[start:end]
			spike = (window > torso_v_p95)
			any_spike = bool(numpy.any(spike))
			if any_spike:
				aligned_count += 1
			alignment_rows.append((hs["rank"], frame_index, any_spike))
		lines.append("## Hotspot / torso-spike alignment (+/- 60 frames)")
		lines.append("")
		lines.append("| Hotspot rank | center frame | torso-v spike present |")
		lines.append("| --- | --- | --- |")
		for rank, frame, present in alignment_rows:
			yes_no = "YES" if present else "NO"
			lines.append(f"| {rank} | {frame} | {yes_no} |")
		lines.append(
			f"\nAlignment count: {aligned_count} of {len(alignment_rows)} "
			"hotspots within +/- 60 frames of a torso-velocity p95+ spike."
		)
		lines.append("")
	else:
		lines.append(
			"## Hotspot alignment\n\n"
			"No --hotspots-md supplied; alignment check skipped.\n"
		)

	# verdict
	lines.append("## Verdict")
	lines.append("")
	if numpy.isfinite(rho_torso_crop) and rho_torso_crop > 0.5:
		lines.append(
			"**H1 supported on this video.** Pearson rho between "
			"`abs(diff(torso_h))` and `abs(diff(final_crop_h))` exceeds "
			"0.5: the crop size velocity tracks the torso-h velocity. "
			"Fix space moves to torso-h stabilization upstream of the "
			"crop pipeline."
		)
	elif numpy.isfinite(rho_torso_crop) and rho_torso_crop > 0.2:
		lines.append(
			"**H1 partially supported.** Correlation positive but "
			"below 0.5; torso-h noise contributes but is not the "
			"dominant driver. Cross-check the per-pass jitter table "
			"to identify whether jitter enters before or after the "
			"blend, and look at the per-hotspot plots to see whether "
			"the alignment is local."
		)
	else:
		lines.append(
			"**H1 not supported on this video.** Crop velocity does "
			"not track torso velocity. Investigate other mechanisms; "
			"consider a different hotspot subset before concluding."
		)
	lines.append("")
	report_text = "\n".join(lines) + "\n"
	with open(output_path, "w") as f:
		f.write(report_text)


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	if not os.path.isfile(args.source_file):
		raise RuntimeError(f"Source video not found: {args.source_file}")
	basename = os.path.splitext(os.path.basename(args.source_file))[0]

	# resolve output dir
	if args.output_dir:
		output_dir = args.output_dir
	else:
		output_dir = os.path.join(
			"output_smoke", "zoom_bounce", "EVIDENCE",
			"torso_noise", basename,
		)
	os.makedirs(output_dir, exist_ok=True)
	print(f"Source:    {args.source_file}")
	print(f"Output:    {output_dir}")

	# load config + probe video
	config_path = tr_paths.default_config_path(args.source_file)
	if os.path.isfile(config_path):
		cfg = tr_config.load_config(config_path, auto_save_migration=False)
	else:
		cfg = tr_config.read_default_config()
	video_info = common_tools.probe_video.probe_video(args.source_file)
	print(
		f"  source: {video_info['width']}x{video_info['height']} "
		f"@ {video_info['fps']:.4f}fps  "
		f"{video_info['frame_count']} frames"
	)

	# load solved trajectory
	intervals_path = tr_paths.default_intervals_path(args.source_file)
	seeds_path = tr_paths.default_seeds_path(args.source_file)
	if not os.path.isfile(intervals_path):
		raise RuntimeError(f"Intervals NPZ not found: {intervals_path}")
	if not os.path.isfile(seeds_path):
		raise RuntimeError(f"Seeds JSON not found: {seeds_path}")
	intervals_data = state_io.load_torso_box_coords(intervals_path)
	solved = intervals_data.get("solved_intervals", {})
	if not solved:
		raise RuntimeError(f"No solved intervals in {intervals_path}")
	interval_results = sorted(
		solved.values(), key=lambda r: int(r["start_frame"]),
	)
	print(f"  intervals: {len(interval_results)} solved")

	trajectory = interval_solver.stitch_trajectories(interval_results)
	seeds_data = state_io.load_seeds(seeds_path)
	all_seeds = seeds_data.get("seeds", [])
	trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
	trajectory = interval_solver._apply_trajectory_erasure(
		trajectory, all_seeds, float(video_info["fps"]),
	)
	print(f"  trajectory: {len(trajectory)} frames, {len(all_seeds)} seeds")

	# apply torso w/h stabilizer (between trajectory load and crop ingest);
	# `none` is a passthrough that preserves the M1 baseline byte-equally.
	if args.stabilizer_method != "none":
		print(
			f"  stabilizer: method={args.stabilizer_method} "
			f"window={args.stabilizer_window}"
		)
		trajectory = torso_size_stabilizer.stabilize_trajectory(
			trajectory,
			args.stabilizer_method,
			args.stabilizer_window,
		)

	# pre-encode crop signals (re-derived from tr_crop's logic)
	signals = compute_pre_encode_crop_signals(
		trajectory, video_info, cfg,
	)
	# per-pass torso_h time series (FWD/BWD/blended)
	per_pass = load_per_pass_torso_h(intervals_data, len(trajectory))

	# optional post-encode log_scale
	post_encode_log_scale = numpy.array([])
	if args.encoded_file and os.path.isfile(args.encoded_file):
		print(f"  loading post-encode log_scale from {args.encoded_file}")
		log_scale, _enc_fps, _n = (
			find_zoom_hotspots.compute_log_scale_series(
				args.encoded_file, weighting="edge_weighted",
			)
		)
		# truncate to trajectory length per --frame-tolerance
		gap = abs(len(log_scale) - len(trajectory))
		if gap > args.frame_tolerance:
			print(
				f"  WARNING: video frames ({len(log_scale)}) and "
				f"trajectory ({len(trajectory)}) differ by {gap} > "
				f"tolerance {args.frame_tolerance}; head-truncating "
				"to the shorter side"
			)
		n_pair = min(len(log_scale), len(trajectory))
		post_encode_log_scale = log_scale[:n_pair]

	# median-filtered torso_h reference (diagnostic plot only)
	median_h = median_filter_1d(signals["raw_h"], args.reference_window)

	# write trace CSV
	trace_path = os.path.join(output_dir, "crop_zoom_trace.csv")
	write_trace_csv(trace_path, signals, per_pass, post_encode_log_scale)
	print(f"  wrote: {trace_path}")

	# per-hotspot plots
	hotspots = []
	if args.hotspots_md:
		if not os.path.isfile(args.hotspots_md):
			raise RuntimeError(f"hotspots.md not found: {args.hotspots_md}")
		hotspots = parse_hotspots_md(args.hotspots_md)
		half_window = max(int(round(2.5 * float(video_info["fps"]))), 60)
		for hs in hotspots[:5]:
			plot_path = os.path.join(
				output_dir, f"hotspot_window_{hs['rank']}.png",
			)
			render_hotspot_window_plot(
				plot_path, signals, median_h, post_encode_log_scale,
				hs["frame"], half_window, hs["rank"], basename,
			)
			print(f"  wrote: {plot_path}")

	# summary markdown; per-method/window file when stabilizer is on
	if args.stabilizer_method == "none":
		summary_name = f"{basename}_torso_noise_summary.md"
	else:
		summary_name = (
			f"{args.stabilizer_method}_w{args.stabilizer_window}"
			"_torso_noise_summary.md"
		)
	summary_path = os.path.join(output_dir, summary_name)
	write_summary_md(
		summary_path, basename, signals, median_h,
		post_encode_log_scale, per_pass, hotspots,
		float(video_info["fps"]),
	)
	print(f"  wrote: {summary_path}")

	# --bisect-stages: emit per-frame stage trace CSV + per-stage table
	if args.bisect_stages:
		# optionally compute V1 and V1b stages (rolling-min ceiling)
		variant_stages = None
		variant_b_stages = None
		if args.variant == "v1":
			variant_stages = compute_v1_rolling_min_ceiling(
				signals, video_info, cfg, window=7,
			)
			variant_b_stages = compute_v1_rolling_min_ceiling(
				signals, video_info, cfg, window=3,
			)
		stage_csv_path = os.path.join(output_dir, "crop_zoom_stage_trace.csv")
		write_stage_trace_csv(
			stage_csv_path, signals,
			variant_stages=variant_stages,
			variant_b_stages=variant_b_stages,
		)
		print(f"  wrote: {stage_csv_path}")
		stage_rows = compute_per_stage_zoom_metrics(
			signals, float(video_info["fps"]),
		)
		# append V1 / V1b rows when variant is enabled
		fps_val = float(video_info["fps"])
		if variant_stages is not None:
			for stage_id, label in (("S3_v1", "S3_v1"), ("S5_v1", "S5_v1")):
				stage_rows.append(
					_stage_row_for(label, variant_stages[stage_id], fps_val)
				)
		if variant_b_stages is not None:
			for stage_id, label in (("S3_v1", "S3_v1b"), ("S5_v1", "S5_v1b")):
				stage_rows.append(
					_stage_row_for(label, variant_b_stages[stage_id], fps_val)
				)
		stage_md_name = (
			"stage_bisect.md" if args.stabilizer_method == "none"
			else f"stage_bisect_{args.stabilizer_method}_w{args.stabilizer_window}.md"
		)
		stage_md_path = os.path.join(output_dir, stage_md_name)
		write_stage_bisect_md(
			stage_md_path, basename, stage_rows, fps_val,
			signals=signals,
		)
		print(f"  wrote: {stage_md_path}")

		# WP-C3: optional encode-review side-by-side clips
		if args.encode_review:
			review_dir = os.path.join(output_dir, "review_clips")
			print(f"  encoding review clips to {review_dir} ...")
			review_info = encode_review_clips(
				args.source_file, trajectory, signals,
				video_info, cfg, review_dir,
			)
			if review_info is not None:
				print(
					f"  review clips: center_frame={review_info['center_frame']} "
					f"frames=[{review_info['start_frame']}, {review_info['end_frame']})"
				)
				print(f"  review torso_multiple: {review_info['review_torso_multiple']}")
				print(f"  wrote: {review_info['source_path']}")
				print(f"  wrote: {review_info['pre_v1_path']}")
				print(f"  wrote: {review_info['v1_path']}")
				print(f"  wrote: {review_info['side_by_side_path']}")
			else:
				print("  no S5 hotspot found; skipping review clips")


#============================================
def write_stage_trace_csv(
	output_path: str, signals: dict,
	variant_stages: dict = None,
	variant_b_stages: dict = None,
) -> None:
	"""Per-frame trace CSV covering all six pipeline stages S0..S5.

	One row per frame. Columns:
	  frame, torso_w, torso_h,
	  S0_h, S0_w, S0_log_size, S1_h, S1_w, S1_log_size, ...
	  S5_h, S5_w, S5_log_size,
	  edge_clamped_S3, edge_clamped_S5,
	  log_velocity_S0, log_velocity_S1, ..., log_velocity_S5

	When `variant_stages` is supplied, additional columns are appended
	for S3_v1 / S5_v1 (and `smooth_ceiling_w`). When `variant_b_stages`
	is also supplied, S3_v1b / S5_v1b columns follow.

	`log_velocity_*[0]` is NaN by definition (no prior frame).
	"""
	n = len(signals["raw_cx"])
	# precompute log_size and log_velocity per stage (geometric mean)
	stage_log_size = {}
	stage_log_v = {}
	for stage_id in STAGE_IDS:
		stage = signals[stage_id]
		stage_h = numpy.asarray(stage["h"], dtype=numpy.float64)
		stage_w = numpy.asarray(stage["w"], dtype=numpy.float64)
		area = numpy.sqrt(numpy.maximum(stage_w * stage_h, 0.0))
		log_size = numpy.full(n, numpy.nan, dtype=numpy.float64)
		positive = numpy.isfinite(area) & (area > 0)
		log_size[positive] = numpy.log(area[positive])
		stage_log_size[stage_id] = log_size
		stage_log_v[stage_id] = log_velocity(area)

	header = ["frame", "torso_w", "torso_h"]
	for stage_id in STAGE_IDS:
		header += [f"{stage_id}_h", f"{stage_id}_w", f"{stage_id}_log_size"]
	header += ["edge_clamped_S3", "edge_clamped_S5"]
	for stage_id in STAGE_IDS:
		header += [f"log_velocity_{stage_id}"]
	# fit-to-source diagnostics (WP-A1)
	header += [
		"requested_h", "fitted_h", "shrink_h",
		"geometric_ceiling_h", "geometric_ceiling_log_velocity",
		"s4_overshoot",
		"limiting_edge",
		"dist_top", "dist_bottom", "dist_left", "dist_right",
		"edge_clamp_run_id", "edge_clamp_run_length",
	]
	# V1 variant columns (WP-B2): rolling-min ceiling stages
	if variant_stages is not None:
		header += [
			"smooth_ceiling_w",
			"S3v1_h", "S3v1_w", "S3v1_log_size", "log_velocity_S3v1",
			"S5v1_h", "S5v1_w", "S5v1_log_size", "log_velocity_S5v1",
		]
	if variant_b_stages is not None:
		header += [
			"smooth_ceiling_b_w",
			"S3v1b_h", "S3v1b_w", "S3v1b_log_size", "log_velocity_S3v1b",
			"S5v1b_h", "S5v1b_w", "S5v1b_log_size", "log_velocity_S5v1b",
		]

	edge_s3 = signals["edge_clamped_S3"]
	edge_s5 = signals["edge_clamped"]
	diag = signals["fit_diagnostics"]
	# log-velocity of the geometric ceiling (informational; matches
	# the per-frame oscillation V1 attacks)
	gc_log_v = log_velocity(diag["geometric_ceiling_h"])
	edge_name_map = {0: "none", 1: "top", 2: "bottom", 3: "left", 4: "right"}

	# precompute variant per-frame log_size and log_velocity
	def _stage_log_arrays(stage):
		stage_h = numpy.asarray(stage["h"], dtype=numpy.float64)
		stage_w = numpy.asarray(stage["w"], dtype=numpy.float64)
		area = numpy.sqrt(numpy.maximum(stage_w * stage_h, 0.0))
		log_size = numpy.full(len(area), numpy.nan, dtype=numpy.float64)
		positive = numpy.isfinite(area) & (area > 0)
		log_size[positive] = numpy.log(area[positive])
		return log_size, log_velocity(area)

	if variant_stages is not None:
		v1_s3_log_size, v1_s3_log_v = _stage_log_arrays(variant_stages["S3_v1"])
		v1_s5_log_size, v1_s5_log_v = _stage_log_arrays(variant_stages["S5_v1"])
	if variant_b_stages is not None:
		v1b_s3_log_size, v1b_s3_log_v = _stage_log_arrays(variant_b_stages["S3_v1"])
		v1b_s5_log_size, v1b_s5_log_v = _stage_log_arrays(variant_b_stages["S5_v1"])
	with open(output_path, "w", newline="") as fh:
		writer = csv.writer(fh)
		writer.writerow(header)
		for i in range(n):
			row = [
				i,
				f"{signals['raw_w'][i]:.4f}",
				f"{signals['raw_h'][i]:.4f}",
			]
			for stage_id in STAGE_IDS:
				stage = signals[stage_id]
				row.append(f"{stage['h'][i]:.4f}")
				row.append(f"{stage['w'][i]:.4f}")
				ls = stage_log_size[stage_id][i]
				row.append("" if not numpy.isfinite(ls) else f"{ls:.6f}")
			row.append(int(bool(edge_s3[i])))
			row.append(int(bool(edge_s5[i])))
			for stage_id in STAGE_IDS:
				lv = stage_log_v[stage_id][i]
				row.append("" if not numpy.isfinite(lv) else f"{lv:.6f}")
			# fit-to-source diagnostics
			row.append(f"{diag['requested_h'][i]:.4f}")
			row.append(f"{diag['fitted_h'][i]:.4f}")
			row.append(f"{diag['shrink_h'][i]:.4f}")
			row.append(f"{diag['geometric_ceiling_h'][i]:.4f}")
			gclv = gc_log_v[i]
			row.append("" if not numpy.isfinite(gclv) else f"{gclv:.6f}")
			row.append(f"{diag['s4_overshoot'][i]:.4f}")
			row.append(edge_name_map.get(int(diag["limiting_edge_code"][i]), "none"))
			row.append(f"{diag['dist_top'][i]:.2f}")
			row.append(f"{diag['dist_bottom'][i]:.2f}")
			row.append(f"{diag['dist_left'][i]:.2f}")
			row.append(f"{diag['dist_right'][i]:.2f}")
			row.append(int(diag["edge_clamp_run_id"][i]))
			row.append(int(diag["edge_clamp_run_length"][i]))
			# V1 variant columns
			if variant_stages is not None:
				row.append(f"{variant_stages['smooth_ceiling_w'][i]:.4f}")
				row.append(f"{variant_stages['S3_v1']['h'][i]:.4f}")
				row.append(f"{variant_stages['S3_v1']['w'][i]:.4f}")
				ls = v1_s3_log_size[i]
				row.append("" if not numpy.isfinite(ls) else f"{ls:.6f}")
				lv = v1_s3_log_v[i]
				row.append("" if not numpy.isfinite(lv) else f"{lv:.6f}")
				row.append(f"{variant_stages['S5_v1']['h'][i]:.4f}")
				row.append(f"{variant_stages['S5_v1']['w'][i]:.4f}")
				ls = v1_s5_log_size[i]
				row.append("" if not numpy.isfinite(ls) else f"{ls:.6f}")
				lv = v1_s5_log_v[i]
				row.append("" if not numpy.isfinite(lv) else f"{lv:.6f}")
			if variant_b_stages is not None:
				row.append(f"{variant_b_stages['smooth_ceiling_w'][i]:.4f}")
				row.append(f"{variant_b_stages['S3_v1']['h'][i]:.4f}")
				row.append(f"{variant_b_stages['S3_v1']['w'][i]:.4f}")
				ls = v1b_s3_log_size[i]
				row.append("" if not numpy.isfinite(ls) else f"{ls:.6f}")
				lv = v1b_s3_log_v[i]
				row.append("" if not numpy.isfinite(lv) else f"{lv:.6f}")
				row.append(f"{variant_b_stages['S5_v1']['h'][i]:.4f}")
				row.append(f"{variant_b_stages['S5_v1']['w'][i]:.4f}")
				ls = v1b_s5_log_size[i]
				row.append("" if not numpy.isfinite(ls) else f"{ls:.6f}")
				lv = v1b_s5_log_v[i]
				row.append("" if not numpy.isfinite(lv) else f"{lv:.6f}")
			writer.writerow(row)


#============================================
def format_per_stage_table(stage_rows: list) -> str:
	"""Render a Markdown table of per-stage zoom metrics.

	Columns: stage | log_velocity_p95 | hotspot_severity | delta vs S0 | delta vs prev.
	delta values are absolute differences in log_velocity_p95 (positive
	means more bouncy than the reference).
	"""
	out = ""
	out += (
		"| stage | log_velocity_p95 | hotspot_severity | "
		"delta vs S0 | delta vs prev |\n"
	)
	out += (
		"| --- | --- | --- | --- | --- |\n"
	)
	if not stage_rows:
		return out
	s0_p95 = stage_rows[0]["log_velocity_p95"]
	prev_p95 = s0_p95
	for row in stage_rows:
		p95 = row["log_velocity_p95"]
		sev = row["hotspot_severity"]
		delta_s0 = p95 - s0_p95 if numpy.isfinite(p95) and numpy.isfinite(s0_p95) else float("nan")
		delta_prev = p95 - prev_p95 if numpy.isfinite(p95) and numpy.isfinite(prev_p95) else float("nan")
		out += (
			f"| {row['stage_id']} | {p95:.6f} | {sev:.6f} | "
			f"{delta_s0:+.6f} | {delta_prev:+.6f} |\n"
		)
		prev_p95 = p95
	return out


#============================================
def _format_fit_diagnostics_section(signals: dict, fps: float) -> str:
	"""Render the WP-A2 fit-to-source diagnostics section.

	Reports geometric-ceiling oscillation (the V1 mechanism evidence),
	edge-clamp run-length histogram, per-edge fractions, Pearson rho
	between geometric_ceiling_log_velocity and the S5-only contribution,
	and the s4_overshoot distribution (informational only).
	"""
	diag = signals["fit_diagnostics"]
	out = "## Fit-to-source diagnostics\n\n"
	# geometric ceiling oscillation: log_velocity_p95 + hotspot severity
	gc_h = diag["geometric_ceiling_h"]
	gc_log_v = log_velocity(gc_h)
	finite = gc_log_v[numpy.isfinite(gc_log_v)]
	gc_p95 = float(numpy.percentile(finite, 95)) if finite.size >= 2 else float("nan")
	gc_hot = compute_simulated_hotspot_severity(gc_log_v, fps)
	gc_severity = float(gc_hot["severity"])
	out += "### Geometric-ceiling oscillation (primary mechanism evidence)\n\n"
	out += "| metric | value |\n| --- | --- |\n"
	out += f"| `log_velocity_p95(geometric_ceiling_h)` | {gc_p95:.6f} |\n"
	out += f"| `hotspot_severity(geometric_ceiling_h)` | {gc_severity:.6f} |\n\n"
	# edge-clamp run-length histogram: only count one entry per run
	# (use frames where edge_clamp_run_length > 0 and i is the first
	# frame of its run, i.e., run_id transitions)
	run_id = diag["edge_clamp_run_id"]
	run_len = diag["edge_clamp_run_length"]
	# count one per run by sampling at the first frame of each run
	seen_ids = set()
	per_run_lengths = []
	for i in range(len(run_id)):
		rid = int(run_id[i])
		if rid >= 0 and rid not in seen_ids:
			seen_ids.add(rid)
			per_run_lengths.append(int(run_len[i]))
	def _bucket(L: int) -> str:
		if L == 1:
			return "1"
		if L <= 5:
			return "2-5"
		if L <= 30:
			return "6-30"
		return "31+"
	buckets = {"1": 0, "2-5": 0, "6-30": 0, "31+": 0}
	for L in per_run_lengths:
		buckets[_bucket(L)] += 1
	total_runs = sum(buckets.values())
	out += "### Edge-clamp run-length histogram\n\n"
	out += "| bucket | count | percent |\n| --- | --- | --- |\n"
	for b in ("1", "2-5", "6-30", "31+"):
		c = buckets[b]
		pct = (100.0 * c / total_runs) if total_runs > 0 else 0.0
		out += f"| {b} | {c} | {pct:.1f}% |\n"
	out += f"\nTotal edge-clamp runs: {total_runs}\n\n"
	# per-edge fractions: only count limiting_edge != 0 (clamped)
	limiting = diag["limiting_edge_code"]
	clamped_mask = limiting != 0
	clamped_count = int(numpy.sum(clamped_mask))
	out += "### Per-edge fractions (of edge-clamped frames)\n\n"
	out += "| edge | count | percent |\n| --- | --- | --- |\n"
	edge_names = [(1, "top"), (2, "bottom"), (3, "left"), (4, "right")]
	for code, name in edge_names:
		c = int(numpy.sum(limiting == code))
		pct = (100.0 * c / clamped_count) if clamped_count > 0 else 0.0
		out += f"| {name} | {c} | {pct:.1f}% |\n"
	out += f"\nTotal edge-clamped frames: {clamped_count}\n\n"
	# Pearson rho: ceiling log-velocity vs S5-only contribution
	# S5-only contribution: log_velocity_S5 - log_velocity_S4
	s4_h = signals["S4_ema_2"]["h"]
	s5_h = signals["S5_fit_2"]["h"]
	s4_w = signals["S4_ema_2"]["w"]
	s5_w = signals["S5_fit_2"]["w"]
	s4_area = numpy.sqrt(numpy.maximum(s4_w * s4_h, 0.0))
	s5_area = numpy.sqrt(numpy.maximum(s5_w * s5_h, 0.0))
	s4_lv = log_velocity(s4_area)
	s5_lv = log_velocity(s5_area)
	s5_only = s5_lv - s4_lv
	# pair valid rows
	valid = numpy.isfinite(gc_log_v) & numpy.isfinite(s5_only)
	if int(numpy.sum(valid)) >= 3:
		rho, p_value = scipy.stats.pearsonr(gc_log_v[valid], s5_only[valid])
		rho_val = float(rho)
		p_val = float(p_value)
	else:
		rho_val = float("nan")
		p_val = float("nan")
	out += "### Pearson correlation: ceiling oscillation vs S5-only log-velocity\n\n"
	out += "| pair | rho | p-value |\n| --- | --- | --- |\n"
	out += (
		f"| `geometric_ceiling_log_velocity` vs `log_velocity_S5 - log_velocity_S4` | "
		f"{rho_val:+.4f} | {p_val:.3g} |\n\n"
	)
	# s4_overshoot distribution (informational)
	s4o = diag["s4_overshoot"]
	finite_s4o = s4o[numpy.isfinite(s4o)]
	nonzero = finite_s4o[finite_s4o > 0]
	out += "### s4_overshoot distribution (informational)\n\n"
	out += "| metric | value |\n| --- | --- |\n"
	out += f"| count_nonzero | {nonzero.size} |\n"
	if nonzero.size > 0:
		out += f"| p50 | {float(numpy.percentile(nonzero, 50)):.4f} px |\n"
		out += f"| p95 | {float(numpy.percentile(nonzero, 95)):.4f} px |\n"
		out += f"| max | {float(numpy.max(nonzero)):.4f} px |\n"
	else:
		out += "| p50 | n/a |\n| p95 | n/a |\n| max | n/a |\n"
	out += "\n"
	# diagnostic done-check verdict
	gc_pass = numpy.isfinite(gc_severity) and gc_severity >= 0.30
	rho_pass = numpy.isfinite(rho_val) and rho_val >= 0.5
	out += "### Diagnostic done-check\n\n"
	out += (
		f"- `hotspot_severity(geometric_ceiling_h) >= 0.30`: "
		f"{'PASS' if gc_pass else 'fail'} ({gc_severity:.4f})\n"
	)
	out += (
		f"- Pearson rho >= 0.5: {'PASS' if rho_pass else 'fail'} "
		f"({rho_val:+.4f})\n"
	)
	if gc_pass or rho_pass:
		out += (
			"\nAt least one threshold reached on this video; the V1 "
			"mechanism (rolling-min ceiling) is plausible here.\n\n"
		)
	else:
		out += (
			"\nNeither threshold reached on this video. If this is the "
			"pattern across all three representative videos, V1's "
			"mechanism is wrong and the plan should stop at the "
			"diagnostic.\n\n"
		)
	return out


#============================================
def write_stage_bisect_md(
	output_path: str, basename: str,
	stage_rows: list, fps: float,
	signals: dict = None,
) -> None:
	"""Per-video summary Markdown for the --bisect-stages mode.

	Names the first responsible stage using the >=50%-of-S5 rule, with
	hotspot-severity / >20%-transition fallback when the primary rule
	is inconclusive. When `signals` is supplied, also emits the WP-A2
	fit-to-source diagnostics section.
	"""
	out = ""
	out += f"# Per-stage zoom metrics: {basename}\n\n"
	out += f"fps: {fps:.4f}\n\n"
	out += format_per_stage_table(stage_rows)
	out += "\n"
	# bisect call
	if not stage_rows:
		out += "_No stage data._\n"
	else:
		s5 = stage_rows[-1]["log_velocity_p95"]
		responsible = None
		fallback_used = ""
		if numpy.isfinite(s5) and s5 >= 5e-3:
			# primary rule: smallest stage with p95 >= 0.5 * S5
			threshold = 0.5 * s5
			for row in stage_rows:
				p95 = row["log_velocity_p95"]
				if numpy.isfinite(p95) and p95 >= threshold:
					responsible = row["stage_id"]
					break
		if responsible is None:
			# fallback: hotspot severity + >20% transition rule
			fallback_used = (
				"S5 is below the noise floor or the 50% rule was "
				"inconclusive; using hotspot severity and the >20% "
				"transition rule instead."
			)
			for i in range(1, len(stage_rows)):
				prev = stage_rows[i - 1]["hotspot_severity"]
				cur = stage_rows[i]["hotspot_severity"]
				if numpy.isfinite(prev) and prev > 0 and numpy.isfinite(cur):
					if cur > 1.2 * prev:
						responsible = stage_rows[i]["stage_id"]
						break
		# amplification flags (always reported)
		amplifying = []
		for i in range(1, len(stage_rows)):
			prev = stage_rows[i - 1]["log_velocity_p95"]
			cur = stage_rows[i]["log_velocity_p95"]
			if numpy.isfinite(prev) and prev > 0 and numpy.isfinite(cur):
				if cur > 1.2 * prev:
					amplifying.append(stage_rows[i]["stage_id"])
		out += "## Bisect call\n\n"
		if responsible is not None:
			out += f"First responsible stage: **{responsible}**.\n\n"
		else:
			out += "First responsible stage: **inconclusive**.\n\n"
		if fallback_used:
			out += fallback_used + "\n\n"
		if amplifying:
			out += (
				"Amplifying stages (>20% jump in log_velocity_p95 vs prior stage): "
				+ ", ".join(amplifying) + ".\n\n"
			)
		else:
			out += "No stage amplifies the metric by >20%.\n\n"
	# WP-A2: fit-to-source diagnostics, when full signals are provided
	if signals is not None and "fit_diagnostics" in signals:
		out += _format_fit_diagnostics_section(signals, fps)
	with open(output_path, "w") as fh:
		fh.write(out)


#============================================

if __name__ == "__main__":
	main()
