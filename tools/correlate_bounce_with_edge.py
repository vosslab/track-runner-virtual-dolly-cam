#!/usr/bin/env python3
"""Correlate per-frame zoom bounce with runner-to-source-edge distance.

Direct test of the Step 3.6 fit-to-source ratchet hypothesis: the
asymmetric clamp in tr_crop.py:855-885 only fires when the centered
crop would extend past a source edge. If bounce concentrates on frames
where the runner is near a source edge, the clamp is the dominant
source. If bounce shows no edge correlation, the residual is something
else (raw EMA leakage, source content motion, etc.).

Inputs:
	-i / --input    Encoded movie file (Fourier-Mellin scale signal).
	-s / --source   Source video file (only used for source dimensions).
	-d / --data-dir Directory containing the source's solved-interval
	                artifacts. Defaults to the standard tr_config/
	                location next to the source video.

Outputs:
	<input_basename>.bounce_edge.png   Scatter plot with marginal
	                                   histograms; correlation in title.
	<input_basename>.bounce_edge.md    Per-quartile bounce intensity
	                                   table (markdown).

Reading the result:
	Spearman > 0.4 supports the Step 3.6 ratchet hypothesis.
	Spearman near zero suggests bounce is NOT edge-driven.
	Spearman < 0 (rare) suggests bounce concentrates AWAY from edges,
	pointing at a different mechanism (e.g. source content motion).
"""

# Standard Library
import os
import sys
import argparse

# add tools and track_runner directories to path
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
import tr_paths

# local repo modules (common_tools)
import common_tools.probe_video


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description=(
			"Correlate per-frame zoom bounce with runner-to-source-edge "
			"distance to test the Step 3.6 ratchet hypothesis."
		)
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Encoded movie file (provides the bounce signal)",
	)
	parser.add_argument(
		"-s", "--source", dest="source_file", required=True,
		help="Source video file (only used for source dimensions)",
	)
	parser.add_argument(
		"-d", "--data-dir", dest="data_dir", default="",
		help=(
			"Directory containing the source's solved intervals + seeds. "
			"Defaults to the standard tr_config/ location next to source."
		),
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default="",
		help="Output PNG plot path (default: <input>.bounce_edge.png)",
	)
	parser.add_argument(
		"-W", "--weighting", dest="weighting", default="edge_weighted",
		choices=["full", "edge_weighted", "side_strips"],
		help="Edge masking mode (default: edge_weighted)",
	)
	parser.add_argument(
		"-L", "--lag-window", dest="lag_window", type=int, default=5,
		help=(
			"Compute Spearman correlation at lags -N..+N frames "
			"(default 5). Reports both lag-0 (primary) and best-lag."
		),
	)
	parser.add_argument(
		"-T", "--frame-tolerance", dest="frame_tolerance", type=int,
		default=2,
		help=(
			"Allowed mismatch (in frames) between encoded movie and "
			"solved trajectory. Default 2."
		),
	)
	parser.add_argument(
		"-F", "--frame-offset", dest="frame_offset", type=int, default=0,
		help=(
			"Manual integer frame offset applied to the encoded movie's "
			"log_scale before pairing with the trajectory. Default 0."
		),
	)
	parser.add_argument(
		"-M", "--truncate-mode", dest="truncate_mode", default="head",
		choices=["head", "tail", "intersection"],
		help=(
			"How to truncate longer signals: head (drop trailing), "
			"tail (drop leading), or intersection (the shorter length). "
			"Default head."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def get_source_dimensions(source_file: str) -> tuple:
	"""Return (source_width, source_height, fps) from the source video metadata.
	"""
	info = common_tools.probe_video.probe_video(source_file)
	width = int(info["width"])
	height = int(info["height"])
	fps = float(info["fps"])
	if width <= 0 or height <= 0:
		raise RuntimeError(f"Invalid source dimensions: {width}x{height}")
	return (width, height, fps)


#============================================
def resolve_data_paths(source_file: str, data_dir: str) -> tuple:
	"""Resolve the intervals and seeds JSON/NPZ paths for a source video.

	Args:
		source_file: Path to the source video.
		data_dir: User override for the data directory; empty string to
			use the standard tr_config/ location.

	Returns:
		Tuple (intervals_path, seeds_path).
	"""
	if data_dir:
		basename = os.path.splitext(os.path.basename(source_file))[0]
		intervals_path = os.path.join(
			data_dir, basename + ".track_runner.torso_box_coords.npz",
		)
		seeds_path = os.path.join(
			data_dir, basename + ".track_runner.seeds.json",
		)
	else:
		intervals_path = tr_paths.default_intervals_path(source_file)
		seeds_path = tr_paths.default_seeds_path(source_file)
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"Intervals NPZ not found: {intervals_path}\n"
			f"Pass --data-dir to override the default tr_config/ location."
		)
	if not os.path.isfile(seeds_path):
		raise RuntimeError(
			f"Seeds JSON not found: {seeds_path}\n"
			f"Pass --data-dir to override the default tr_config/ location."
		)
	return (intervals_path, seeds_path)


#============================================
def load_solved_trajectory(
	intervals_path: str,
	seeds_path: str,
	fps: float,
) -> list:
	"""Load and post-process the solved trajectory for a source video.

	Mirrors the same load + anchor + erasure sequence the encoder applies,
	so the resulting per-frame state list is the same data downstream
	tr_crop sees.

	Args:
		intervals_path: torso_box_coords.npz path.
		seeds_path: seeds.json path.
		fps: Source video fps (needed for erasure radius).

	Returns:
		List of tracking state dicts (or None) indexed by frame.
	"""
	intervals_data = state_io.load_torso_box_coords(intervals_path)
	solved = intervals_data.get("solved_intervals", {})
	if not solved:
		raise RuntimeError(f"No solved intervals in {intervals_path}")
	# sort intervals by start_frame for stitch_trajectories
	interval_results = sorted(
		solved.values(), key=lambda r: int(r["start_frame"]),
	)
	trajectory = interval_solver.stitch_trajectories(interval_results)

	# load seeds, then anchor + erase exactly as the encoder does
	seeds_data = state_io.load_seeds(seeds_path)
	all_seeds = seeds_data.get("seeds", [])
	trajectory = interval_solver.anchor_to_seeds(trajectory, all_seeds)
	trajectory = interval_solver._apply_trajectory_erasure(
		trajectory, all_seeds, fps,
	)
	return trajectory


#============================================
def compute_edge_distances(
	trajectory: list,
	source_w: int,
	source_h: int,
) -> numpy.ndarray:
	"""Compute per-frame minimum gap from torso bbox to source edge.

	For each frame with a valid trajectory state, the edge gap is the
	minimum signed distance from any of the four torso-box edges to the
	corresponding source-frame edge:

		left_gap   = cx - w/2
		right_gap  = source_w - (cx + w/2)
		top_gap    = cy - h/2
		bottom_gap = source_h - (cy + h/2)
		edge_gap   = min(left_gap, right_gap, top_gap, bottom_gap)

	A small or negative gap means the runner is at (or past) a source
	edge; the Step 3.6 fit clamp activates in this regime.

	Args:
		trajectory: Per-frame state list (dicts or None).
		source_w: Source video width in pixels.
		source_h: Source video height in pixels.

	Returns:
		1D numpy array of length len(trajectory). NaN entries indicate
		frames where no trajectory state was available.
	"""
	n = len(trajectory)
	gaps = numpy.full(n, numpy.nan, dtype=numpy.float64)
	for i, state in enumerate(trajectory):
		if state is None:
			continue
		cx = state["cx"]
		cy = state["cy"]
		w = state["w"]
		h = state["h"]
		left_gap = cx - w / 2.0
		right_gap = source_w - (cx + w / 2.0)
		top_gap = cy - h / 2.0
		bottom_gap = source_h - (cy + h / 2.0)
		gaps[i] = min(left_gap, right_gap, top_gap, bottom_gap)
	return gaps


#============================================
def check_frame_alignment(
	n_video: int,
	n_traj: int,
	tolerance: int,
	frame_offset: int,
) -> None:
	"""Verify the encoded movie and solved trajectory line up.

	Raises RuntimeError when the count mismatch exceeds tolerance and
	the user has not declared a manual offset. The point is to fail
	loudly when video / trajectory frame counts disagree more than a
	few frames, since silent misalignment defeats correlation.

	Args:
		n_video: Frame count of the encoded movie's log_scale series.
		n_traj: Frame count of the solved trajectory.
		tolerance: Allowed absolute frame-count gap before erroring.
		frame_offset: Non-zero indicates user-declared offset; bypasses
			the tolerance check.

	Returns:
		None. Raises on mismatch.
	"""
	if frame_offset != 0:
		return
	gap = abs(int(n_video) - int(n_traj))
	if gap <= int(tolerance):
		return
	raise RuntimeError(
		f"Frame count mismatch: video has {n_video}, trajectory has "
		f"{n_traj} (gap {gap} > tolerance {tolerance}). Pass "
		f"--frame-offset INT to declare a manual offset, or "
		f"--frame-tolerance to widen the allowed gap, or check that "
		f"the encoded movie was produced from this source video."
	)


#============================================
def align_signals(
	log_scale: numpy.ndarray,
	edge_gaps: numpy.ndarray,
	frame_offset: int = 0,
	truncate_mode: str = "head",
) -> tuple:
	"""Align the bounce signal and edge-gap signal frame-by-frame.

	Drops frames where edge_gap is NaN (no solved state) or log_scale
	is exactly 0 (the index-0 reference frame). Applies an optional
	integer frame_offset (positive = video lags trajectory by N frames)
	and a truncate_mode for the side that is shorter or longer.

	Args:
		log_scale: Per-frame log-scale array.
		edge_gaps: Per-frame edge-distance array.
		frame_offset: Integer frame offset applied to log_scale.
		truncate_mode: One of "head" (drop trailing), "tail" (drop
			leading), "intersection" (use the shorter length).

	Returns:
		Tuple (intensity, gap_distance) of equal length.
	"""
	# apply user-supplied offset first
	if frame_offset > 0:
		# video lags trajectory: drop the first frame_offset of log_scale
		log_scale_aligned = log_scale[frame_offset:]
		gaps_aligned = edge_gaps[: len(log_scale_aligned)]
	elif frame_offset < 0:
		# video leads trajectory: drop the first |offset| of trajectory
		shift = -frame_offset
		gaps_aligned = edge_gaps[shift:]
		log_scale_aligned = log_scale[: len(gaps_aligned)]
	else:
		log_scale_aligned = log_scale
		gaps_aligned = edge_gaps
	# now reconcile the remaining length difference per truncate_mode
	if truncate_mode == "tail":
		# drop leading from the longer array
		n = min(len(log_scale_aligned), len(gaps_aligned))
		log_scale_aligned = log_scale_aligned[-n:]
		gaps_aligned = gaps_aligned[-n:]
	else:
		# head and intersection both drop trailing (intersection is identical
		# in our usage because we already truncated above)
		n = min(len(log_scale_aligned), len(gaps_aligned))
		log_scale_aligned = log_scale_aligned[:n]
		gaps_aligned = gaps_aligned[:n]
	intensity = numpy.abs(log_scale_aligned)
	# valid frames: gap not NaN, intensity not exactly 0 (frame 0 reference)
	valid = numpy.isfinite(gaps_aligned) & (intensity > 0)
	return (intensity[valid], gaps_aligned[valid])


#============================================
def compute_correlation(
	intensity: numpy.ndarray,
	gaps: numpy.ndarray,
) -> tuple:
	"""Compute Spearman correlation between bounce intensity and inverse edge gap.

	Higher inverse-gap = closer to the edge. Positive Spearman supports
	the ratchet hypothesis.

	Returns:
		Tuple (rho, p_value) per scipy.stats.spearmanr.
	"""
	if len(intensity) < 3:
		return (float("nan"), float("nan"))
	# clamp gaps to a small epsilon so 1/gap is finite; gap can be
	# negative (runner past edge) but the test cares about magnitude
	epsilon = 1.0
	inverse_gap = 1.0 / numpy.maximum(numpy.abs(gaps), epsilon)
	# numpy.where(gaps < 0, ...) would let near-edge negatives map to high inv;
	# the abs() above achieves the same proximity-magnitude semantics.
	result = scipy.stats.spearmanr(intensity, inverse_gap)
	# scipy may return a SignificanceResult or a tuple depending on version;
	# index access is the version-agnostic accessor for both shapes.
	rho = float(result[0])
	p_value = float(result[1])
	return (rho, p_value)


#============================================
def compute_lagged_correlation(
	intensity: numpy.ndarray,
	gaps: numpy.ndarray,
	lag_window: int,
) -> tuple:
	"""Compute Spearman correlation across a range of integer frame lags.

	A lag of +1 means: shift intensity FORWARD by 1 frame relative to
	gaps (i.e. compare gaps[t] against intensity[t+1]). The clamp may
	cause bounce one or two frames AFTER edge contact, so a non-zero
	best-lag is informative even if lag-0 is weak.

	Args:
		intensity: Per-frame bounce intensity.
		gaps: Per-frame edge gap (already aligned with intensity).
		lag_window: Compute lags from -lag_window to +lag_window inclusive.

	Returns:
		Tuple (lags, rhos) where lags is a list of integer lag values
		and rhos is a list of Spearman coefficients (one per lag).
	"""
	lags = list(range(-int(lag_window), int(lag_window) + 1))
	rhos = []
	for lag in lags:
		if lag == 0:
			i_slice = intensity
			g_slice = gaps
		elif lag > 0:
			# intensity shifted forward: intensity[lag:] vs gaps[:-lag]
			i_slice = intensity[lag:]
			g_slice = gaps[:-lag]
		else:
			# intensity shifted backward
			shift = -lag
			i_slice = intensity[:-shift]
			g_slice = gaps[shift:]
		rho, _p = compute_correlation(i_slice, g_slice)
		rhos.append(rho)
	return (lags, rhos)


#============================================
def find_best_lag(lags: list, rhos: list) -> tuple:
	"""Return the lag with the largest finite Spearman coefficient.

	Args:
		lags: List of integer lags.
		rhos: List of Spearman coefficients aligned with lags.

	Returns:
		Tuple (best_lag, best_rho). Returns (0, NaN) if all rhos are
		non-finite.
	"""
	finite_pairs = [
		(lag, rho) for lag, rho in zip(lags, rhos) if numpy.isfinite(rho)
	]
	if not finite_pairs:
		return (0, float("nan"))
	best = max(finite_pairs, key=lambda kv: kv[1])
	return (int(best[0]), float(best[1]))


#============================================
def quartile_table(
	intensity: numpy.ndarray,
	gaps: numpy.ndarray,
) -> str:
	"""Build a markdown table of mean bounce intensity by edge-gap quartile.

	Sorting by gap (smallest = nearest edge, largest = furthest from edge),
	splits frames into four quartiles and reports per-quartile mean and
	median bounce intensity. If the ratchet hypothesis holds, the smallest-
	gap quartile should have the highest intensity.
	"""
	# build the markdown content in a variable, then return the variable
	if len(intensity) < 8:
		return "Too few frames for quartile analysis."
	# sort by gap ascending
	order = numpy.argsort(gaps)
	sorted_intensity = intensity[order]
	n = len(sorted_intensity)
	q_size = n // 4
	# quartile labels and slices; keep range explicit so off-by-one is visible
	q1 = sorted_intensity[:q_size]
	q2 = sorted_intensity[q_size:2 * q_size]
	q3 = sorted_intensity[2 * q_size:3 * q_size]
	q4 = sorted_intensity[3 * q_size:]
	lines = []
	lines.append("| Edge-gap quartile | Frames | Mean intensity | Median intensity |")
	lines.append("| --- | --- | --- | --- |")
	for label, q in (
		("Q1 (nearest edge)", q1),
		("Q2", q2),
		("Q3", q3),
		("Q4 (furthest from edge)", q4),
	):
		mean_val = float(numpy.mean(q))
		median_val = float(numpy.median(q))
		lines.append(
			f"| {label} | {len(q)} | {mean_val:.6f} | {median_val:.6f} |"
		)
	table_text = "\n".join(lines)
	return table_text


#============================================
def render_scatter_plot(
	intensity: numpy.ndarray,
	gaps: numpy.ndarray,
	rho_lag0: float,
	best_lag: int,
	best_rho: float,
	lags: list,
	rhos: list,
	output_path: str,
	input_name: str,
) -> None:
	"""Render a scatter plot with marginal histograms plus a lag bar chart.

	Layout:
		+--------+--+
		|  hist  |  |   top: gap distribution
		+--------+--+
		| scatter|h |   right: intensity distribution
		+--------+--+
		|       lag |   bottom: per-lag Spearman bar chart
		+--------+--+

	Args:
		intensity: Per-frame bounce intensity (x-axis on scatter).
		gaps: Per-frame edge gap (y-axis on scatter).
		rho_lag0: Lag-0 Spearman coefficient.
		best_lag: Frame lag with the largest correlation.
		best_rho: Spearman at best_lag.
		lags: List of lag offsets used for the bar chart.
		rhos: Spearman per lag (aligned with lags).
		output_path: PNG output path.
		input_name: Source video basename, embedded in title.
	"""
	fig = plt.figure(figsize=(9, 9))
	gs = fig.add_gridspec(
		3, 2,
		width_ratios=(4, 1), height_ratios=(1, 4, 1.5),
		left=0.10, right=0.95, bottom=0.08, top=0.92,
		wspace=0.05, hspace=0.30,
	)
	ax_main = fig.add_subplot(gs[1, 0])
	ax_top = fig.add_subplot(gs[0, 0], sharex=ax_main)
	ax_right = fig.add_subplot(gs[1, 1], sharey=ax_main)
	ax_lag = fig.add_subplot(gs[2, :])

	# main scatter
	ax_main.scatter(intensity, gaps, s=4, alpha=0.4, color="#1f77b4")
	ax_main.set_xlabel("Bounce intensity (|log_scale|)")
	ax_main.set_ylabel("Edge gap (px; smaller = closer to edge)")
	ax_main.grid(True, alpha=0.3)

	# top marginal: distribution of intensity
	ax_top.hist(intensity, bins=40, color="#1f77b4", alpha=0.7)
	ax_top.tick_params(axis="x", labelbottom=False)
	ax_top.set_ylabel("Count")

	# right marginal: distribution of gap
	ax_right.hist(
		gaps, bins=40, orientation="horizontal",
		color="#1f77b4", alpha=0.7,
	)
	ax_right.tick_params(axis="y", labelleft=False)
	ax_right.set_xlabel("Count")

	# bottom: lag bar chart with best-lag highlighted
	bar_colors = [
		"#d62728" if lag == best_lag else "#1f77b4" for lag in lags
	]
	ax_lag.bar(lags, rhos, color=bar_colors, width=0.85)
	ax_lag.axhline(0.0, color="black", linewidth=0.5)
	ax_lag.set_xlabel("Frame lag (positive = bounce trails edge approach)")
	ax_lag.set_ylabel("Spearman rho")
	ax_lag.grid(True, axis="y", alpha=0.3)
	ax_lag.set_xticks(lags)

	# title with both correlations
	fig.suptitle(
		f"Bounce vs edge gap: {input_name}\n"
		f"Spearman lag-0 = {rho_lag0:+.3f}   "
		f"best-lag = {best_rho:+.3f} at lag {best_lag:+d}"
	)
	fig.savefig(output_path, dpi=120)
	plt.close(fig)


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"Input encoded movie not found: {args.input_file}")
	if not os.path.isfile(args.source_file):
		raise RuntimeError(f"Source video not found: {args.source_file}")

	source_w, source_h, source_fps = get_source_dimensions(args.source_file)
	print(f"Source: {args.source_file} ({source_w}x{source_h} @ {source_fps:.2f}fps)")

	intervals_path, seeds_path = resolve_data_paths(args.source_file, args.data_dir)
	print(f"  intervals: {intervals_path}")
	print(f"  seeds:     {seeds_path}")

	trajectory = load_solved_trajectory(intervals_path, seeds_path, source_fps)
	print(f"  trajectory: {len(trajectory)} frames")

	edge_gaps = compute_edge_distances(trajectory, source_w, source_h)
	valid_gap_count = int(numpy.sum(numpy.isfinite(edge_gaps)))
	print(f"  valid edge-gap frames: {valid_gap_count}")

	print(f"Encoded: {args.input_file}")
	log_scale, video_fps, n_video_frames = (
		find_zoom_hotspots.compute_log_scale_series(args.input_file, args.weighting)
	)
	n_traj_frames = len(trajectory)
	print(
		"Frame alignment:"
	)
	print(f"  n_video_frames      = {n_video_frames}")
	print(f"  n_trajectory_frames = {n_traj_frames}")
	print(f"  fps_video           = {video_fps:.4f}")
	print(f"  fps_source          = {source_fps:.4f}")

	# fail loudly on count mismatch unless user overrides
	check_frame_alignment(
		n_video_frames, n_traj_frames,
		args.frame_tolerance, args.frame_offset,
	)

	intensity, gaps = align_signals(
		log_scale, edge_gaps, args.frame_offset, args.truncate_mode,
	)
	n_paired = len(intensity)
	print(f"  n_paired_frames     = {n_paired}")
	print(f"  frame_offset        = {args.frame_offset}")
	print(f"  truncate_mode       = {args.truncate_mode}")

	rho_lag0, p_lag0 = compute_correlation(intensity, gaps)
	lags, rhos = compute_lagged_correlation(intensity, gaps, args.lag_window)
	best_lag, best_rho = find_best_lag(lags, rhos)
	print(f"Spearman lag-0:   rho={rho_lag0:+.4f}  p={p_lag0:.4g}")
	print(f"Spearman best:    rho={best_rho:+.4f}  at lag {best_lag:+d} frames")

	# write outputs
	base = os.path.splitext(args.input_file)[0]
	output_png = args.output_path or (base + ".bounce_edge.png")
	output_md = base + ".bounce_edge.md"

	input_name = os.path.basename(args.input_file)
	render_scatter_plot(
		intensity, gaps,
		rho_lag0, best_lag, best_rho,
		lags, rhos,
		output_png, input_name,
	)
	print(f"  wrote: {output_png}")

	# build the markdown report content in a variable, then write it
	table = quartile_table(intensity, gaps)
	report_lines = []
	report_lines.append(f"# Bounce vs edge gap: {input_name}")
	report_lines.append("")
	report_lines.append(f"Source video: {args.source_file}")
	report_lines.append(f"Source dimensions: {source_w} x {source_h}")
	report_lines.append("")
	report_lines.append("## Frame alignment")
	report_lines.append("")
	report_lines.append("| Field | Value |")
	report_lines.append("| --- | --- |")
	report_lines.append(f"| n_video_frames | {n_video_frames} |")
	report_lines.append(f"| n_trajectory_frames | {n_traj_frames} |")
	report_lines.append(f"| fps_video | {video_fps:.4f} |")
	report_lines.append(f"| fps_source | {source_fps:.4f} |")
	report_lines.append(f"| n_paired_frames | {n_paired} |")
	report_lines.append(f"| frame_offset | {args.frame_offset} |")
	report_lines.append(f"| truncate_mode | {args.truncate_mode} |")
	report_lines.append("")
	report_lines.append("## Spearman correlation")
	report_lines.append("")
	report_lines.append(f"Lag-0 rho: {rho_lag0:+.4f}  (p={p_lag0:.4g})")
	report_lines.append(f"Best-lag rho: {best_rho:+.4f}  at lag {best_lag:+d} frames")
	report_lines.append("")
	report_lines.append("| Lag (frames) | Spearman rho |")
	report_lines.append("| --- | --- |")
	for lag, rho in zip(lags, rhos):
		report_lines.append(f"| {lag:+d} | {rho:+.4f} |")
	report_lines.append("")
	report_lines.append("## Bounce intensity by edge-gap quartile")
	report_lines.append("")
	report_lines.append(table)
	report_lines.append("")
	report_text = "\n".join(report_lines)
	with open(output_md, "w") as f:
		f.write(report_text)
	print(f"  wrote: {output_md}")


#============================================

if __name__ == "__main__":
	main()
