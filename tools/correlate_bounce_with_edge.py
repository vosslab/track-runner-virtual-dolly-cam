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
	args = parser.parse_args()
	return args


#============================================
def get_source_dimensions(source_file: str) -> tuple:
	"""Return (source_width, source_height, fps) from the source video metadata.
	"""
	try:
		fps, total_frames, width, height = common_tools.probe_video.probe_video(source_file)
	except RuntimeError as e:
		raise RuntimeError(f"Cannot probe source video: {source_file}: {e}")
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
def align_signals(
	log_scale: numpy.ndarray,
	edge_gaps: numpy.ndarray,
) -> tuple:
	"""Align the bounce signal and edge-gap signal frame-by-frame.

	Truncates to the shorter of the two arrays (encoded movie can have a
	different frame count than the source's solved trajectory if the
	encoder dropped or padded frames). Drops frames where edge_gap is
	NaN (no solved state) or log_scale is exactly 0 (the index-0
	reference frame).

	Returns:
		Tuple (intensity, gap_distance) of equal length.
	"""
	n = min(len(log_scale), len(edge_gaps))
	intensity = numpy.abs(log_scale[:n])
	gaps = edge_gaps[:n]
	# valid frames: gap not NaN, intensity not exactly 0 (frame 0 reference)
	valid = numpy.isfinite(gaps) & (intensity > 0)
	return (intensity[valid], gaps[valid])


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
	rho: float,
	output_path: str,
	input_name: str,
) -> None:
	"""Render a scatter plot with marginal histograms.

	Layout:
		   +--------+
		   |  hist  |   top: gap distribution
		   +--------+--+
		   | scatter|h |  right: intensity distribution
		   +--------+--+

	Args:
		intensity: Per-frame bounce intensity (x-axis on scatter).
		gaps: Per-frame edge gap (y-axis on scatter).
		rho: Spearman correlation, embedded in title.
		output_path: PNG output path.
		input_name: Source video basename, embedded in title.
	"""
	fig = plt.figure(figsize=(8, 7))
	gs = fig.add_gridspec(
		2, 2,
		width_ratios=(4, 1), height_ratios=(1, 4),
		left=0.10, right=0.95, bottom=0.10, top=0.92,
		wspace=0.05, hspace=0.05,
	)
	ax_main = fig.add_subplot(gs[1, 0])
	ax_top = fig.add_subplot(gs[0, 0], sharex=ax_main)
	ax_right = fig.add_subplot(gs[1, 1], sharey=ax_main)

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

	# title with correlation
	fig.suptitle(
		f"Bounce vs edge gap: {input_name}\n"
		f"Spearman correlation = {rho:+.3f} "
		f"(positive = bounce concentrates near edges)"
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
	log_scale, _enc_fps, _enc_total = (
		find_zoom_hotspots.compute_log_scale_series(args.input_file, args.weighting)
	)
	print(f"  bounce-signal frames: {len(log_scale)}")

	intensity, gaps = align_signals(log_scale, edge_gaps)
	print(f"  aligned valid frames: {len(intensity)}")

	rho, p_value = compute_correlation(intensity, gaps)
	print(f"Spearman correlation: rho={rho:+.4f}  p={p_value:.4g}")

	# write outputs
	base = os.path.splitext(args.input_file)[0]
	output_png = args.output_path or (base + ".bounce_edge.png")
	output_md = base + ".bounce_edge.md"

	input_name = os.path.basename(args.input_file)
	render_scatter_plot(intensity, gaps, rho, output_png, input_name)
	print(f"  wrote: {output_png}")

	# build the markdown report content in a variable, then write it
	table = quartile_table(intensity, gaps)
	report_lines = []
	report_lines.append(f"# Bounce vs edge gap: {input_name}")
	report_lines.append("")
	report_lines.append(f"Source video: {args.source_file}")
	report_lines.append(f"Source dimensions: {source_w} x {source_h}")
	report_lines.append(f"Aligned valid frames: {len(intensity)}")
	report_lines.append(f"Spearman rho: {rho:+.4f}")
	report_lines.append(f"Spearman p-value: {p_value:.4g}")
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
