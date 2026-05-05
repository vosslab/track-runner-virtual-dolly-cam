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
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# local repo modules (tools)
import find_zoom_hotspots

# local repo modules (track_runner)
import interval_solver
import state_io
import tr_config
import tr_crop
import tr_paths
import torso_size_stabilizer

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
	the intermediate float signals: desired_crop_h (W+H average),
	smoothed_crop_h (post-EMA, pre-fit), and the final post-fit
	smoothed_h. This helper reimplements just enough of the math to
	expose those values without modifying production code.

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
		  edge_clamped (bool: True where Step 3.6 shrank the EMA
		    output).
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

	# desired_crop_h: W+H average per tr_crop.direct_center_crop_trajectory
	desired_h_from_h = raw_h * torso_multiple
	desired_h_from_w = (raw_w * torso_multiple) / aspect_ratio
	desired_crop_h = 0.5 * (desired_h_from_h + desired_h_from_w)

	# smoothed_crop_h: forward-backward EMA on desired_crop_h
	if alpha_size > 0:
		smoothed_h_pre_fit = tr_crop._forward_backward_ema(desired_crop_h, alpha_size)
	else:
		smoothed_h_pre_fit = desired_crop_h.copy()
	# the production code applies torso_anchor offset to cy here; we
	# don't need it for the size diagnostic.
	smoothed_h_pre_fit = numpy.maximum(smoothed_h_pre_fit, 1.0)
	smoothed_w_pre_fit = numpy.maximum(smoothed_h_pre_fit * aspect_ratio, 1.0)

	# Step 3.6: centered-fit-to-source. Re-implement the same per-frame
	# fit and second-pass EMA + re-fit. Use the production helper.
	final_h = smoothed_h_pre_fit.copy()
	final_w = smoothed_w_pre_fit.copy()
	if fit_to_source:
		# first fit pass
		for i in range(n):
			fit_w, fit_h = tr_crop._max_centered_fit_size(
				raw_cx[i], raw_cy[i],
				final_w[i],
				int(video_info["width"]), int(video_info["height"]),
				aspect_ratio,
			)
			final_w[i] = max(fit_w, 1.0)
			final_h[i] = max(fit_h, 1.0)
		# second-pass EMA + re-fit when alpha_size > 0
		if alpha_size > 0:
			final_h = tr_crop._forward_backward_ema(final_h, alpha_size)
			final_w = final_h * aspect_ratio
			for i in range(n):
				fit_w, fit_h = tr_crop._max_centered_fit_size(
					raw_cx[i], raw_cy[i],
					final_w[i],
					int(video_info["width"]), int(video_info["height"]),
					aspect_ratio,
				)
				final_w[i] = max(fit_w, 1.0)
				final_h[i] = max(fit_h, 1.0)

	# edge_clamped: True where the fit pass shrank the EMA output
	edge_clamped = final_h < (smoothed_h_pre_fit - 0.5)

	return {
		"raw_cx": raw_cx,
		"raw_cy": raw_cy,
		"raw_w": raw_w,
		"raw_h": raw_h,
		"desired_crop_h": desired_crop_h,
		"smoothed_crop_h": smoothed_h_pre_fit,
		"final_smoothed_h": final_h,
		"final_smoothed_w": final_w,
		"edge_clamped": edge_clamped,
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


#============================================

if __name__ == "__main__":
	main()
