#!/usr/bin/env python3
"""Measure black-bar exposure (letterboxing) in an encoded movie.

When the centered crop in track_runner/tr_crop.py is allowed to extend
past the source frame edges (via crop_centered_fit_to_source: False),
the encoder fills the over-extended region with black. The artifact-cost
of that policy is what this tool quantifies: per-frame top, bottom,
left, and right border-bar fractions, plus aggregates.

A row counts as a "black row" when at least --row-fraction of its
pixels are below the --threshold luminance. The top-bar height is the
contiguous count of black rows from the top edge inward; bottom is
the same from the bottom edge. Columns are handled symmetrically.

Used by the Path A trade-off review: a low score on baseline + a high
score on path_a is the artifact-cost the user must accept knowingly.
"""

# Standard Library
import os
import sys
import argparse

# allow imports from repo + tools when invoked outside the repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

# PIP3 modules
import cv2
import numpy

# local repo modules
import common_tools.probe_video
import common_tools.frame_reader


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description="Measure black-bar exposure (letterboxing) in an encoded movie.",
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Encoded movie file to analyze",
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default="",
		help="Output markdown path (default: <input>.black_bars.md)",
	)
	parser.add_argument(
		"-t", "--threshold", dest="threshold", type=int, default=16,
		help=(
			"Luminance threshold (0-255). Pixels at or below this value "
			"count as 'black'. Default 16."
		),
	)
	parser.add_argument(
		"-r", "--row-fraction", dest="row_fraction", type=float,
		default=0.95,
		help=(
			"Minimum fraction of pixels in a row (or column) below the "
			"threshold required to call that row/column 'black'. "
			"Default 0.95."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def measure_bars_for_frame(
	gray_frame: numpy.ndarray,
	threshold: int,
	row_fraction: float,
) -> tuple:
	"""Return (top, bottom, left, right) bar heights for one grayscale frame.

	A row is "black" when row_fraction of its pixels are at or below the
	luminance threshold. The bar heights are the contiguous counts of
	black rows from each edge inward.

	Args:
		gray_frame: 2D numpy array of luminance values (uint8 expected).
		threshold: Luminance ceiling for "black" pixels.
		row_fraction: Required fraction of black pixels per row/column.

	Returns:
		(top_rows, bottom_rows, left_cols, right_cols) integer counts.
	"""
	height, width = gray_frame.shape[:2]
	black_pixel = gray_frame <= threshold
	# row-wise: a row is "black" if at least row_fraction of its pixels are black
	row_black_fraction = black_pixel.mean(axis=1)
	row_is_black = row_black_fraction >= row_fraction
	# col-wise: same logic transposed
	col_black_fraction = black_pixel.mean(axis=0)
	col_is_black = col_black_fraction >= row_fraction
	# count contiguous black rows/cols from each edge inward
	top_rows = count_run_from_start(row_is_black)
	bottom_rows = count_run_from_start(row_is_black[::-1])
	left_cols = count_run_from_start(col_is_black)
	right_cols = count_run_from_start(col_is_black[::-1])
	# guard against bars meeting in the middle (entirely black frame)
	if top_rows + bottom_rows > height:
		top_rows = height
		bottom_rows = 0
	if left_cols + right_cols > width:
		left_cols = width
		right_cols = 0
	return (int(top_rows), int(bottom_rows), int(left_cols), int(right_cols))


#============================================
def count_run_from_start(boolean_series: numpy.ndarray) -> int:
	"""Count contiguous True values starting at index 0 of a 1D bool array.
	"""
	if len(boolean_series) == 0:
		return 0
	# numpy.argmin on a bool array returns the first False index, which is
	# also the contiguous-True count from the start
	if boolean_series[0] is numpy.False_ or not bool(boolean_series[0]):
		return 0
	if boolean_series.all():
		return int(len(boolean_series))
	return int(numpy.argmin(boolean_series))


#============================================
def per_frame_bar_area_fraction(
	top: int, bottom: int, left: int, right: int,
	height: int, width: int,
) -> float:
	"""Return the fraction of the frame covered by border bars.

	Uses inclusion-exclusion: top + bottom + left + right strips minus
	the four corners (which would otherwise be double-counted). Handles
	the edge case where bars overlap (entirely black frame) by clamping
	to 1.0.
	"""
	total_area = float(height * width)
	if total_area <= 0:
		return 0.0
	# strip areas
	top_area = top * width
	bottom_area = bottom * width
	left_area = left * height
	right_area = right * height
	# corner overlaps (each corner is min(strip, side_strip))
	tl = top * left
	tr = top * right
	bl = bottom * left
	br = bottom * right
	covered = (
		top_area + bottom_area + left_area + right_area
		- tl - tr - bl - br
	)
	fraction = covered / total_area
	# clamp to [0, 1] to handle pathological all-black frames
	if fraction < 0.0:
		return 0.0
	if fraction > 1.0:
		return 1.0
	return float(fraction)


#============================================
def scan_video(
	video_path: str,
	threshold: int,
	row_fraction: float,
) -> dict:
	"""Open the video, scan every frame, return aggregated bar statistics.

	Args:
		video_path: Path to encoded movie file.
		threshold: Luminance ceiling for "black".
		row_fraction: Required fraction of black pixels per row/column.

	Returns:
		Dict with keys:
			n_frames, height, width,
			frames_with_any_bar,
			frame_with_any_bar_fraction,
			mean_bar_area_fraction (mean over frames-with-any-bar),
			max_bar_area_fraction (max over all frames),
			top_max, bottom_max, left_max, right_max (per-edge max bar),
	"""
	info = common_tools.probe_video.probe_video(video_path)
	fps = float(info["fps"])
	total_frames = int(info["frame_count"])
	width = int(info["width"])
	height = int(info["height"])
	if total_frames <= 0:
		raise RuntimeError(f"Video has no frames: {video_path}")

	reader = common_tools.frame_reader.FrameReader(
		video_path=video_path, fps=fps, total_frames=total_frames,
	)

	# per-frame accumulators
	top_max = 0
	bottom_max = 0
	left_max = 0
	right_max = 0
	frames_any_bar = 0
	bar_area_sum = 0.0
	max_area = 0.0
	n_processed = 0

	for frame_index in range(total_frames):
		frame = reader.read_frame(frame_index)
		if frame is None:
			break
		gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		t, b, l, r = measure_bars_for_frame(gray, threshold, row_fraction)
		any_bar = (t > 0) or (b > 0) or (l > 0) or (r > 0)
		if any_bar:
			frames_any_bar += 1
		area_fraction = per_frame_bar_area_fraction(t, b, l, r, height, width)
		if any_bar:
			bar_area_sum += area_fraction
		if area_fraction > max_area:
			max_area = area_fraction
		# per-edge max
		if t > top_max:
			top_max = t
		if b > bottom_max:
			bottom_max = b
		if l > left_max:
			left_max = l
		if r > right_max:
			right_max = r
		n_processed += 1
	reader.close()

	mean_area = (
		bar_area_sum / frames_any_bar if frames_any_bar > 0 else 0.0
	)
	any_bar_fraction = (
		frames_any_bar / n_processed if n_processed > 0 else 0.0
	)
	return {
		"n_frames": n_processed,
		"height": int(height),
		"width": int(width),
		"frames_with_any_bar": frames_any_bar,
		"frame_with_any_bar_fraction": float(any_bar_fraction),
		"mean_bar_area_fraction": float(mean_area),
		"max_bar_area_fraction": float(max_area),
		"top_max": int(top_max),
		"bottom_max": int(bottom_max),
		"left_max": int(left_max),
		"right_max": int(right_max),
	}


#============================================
def render_report(
	video_path: str,
	stats: dict,
	threshold: int,
	row_fraction: float,
) -> str:
	"""Render the markdown report content from the stats dict.
	"""
	# build the markdown content in a variable, then return the variable
	lines = []
	lines.append(f"# Black-bar exposure: {os.path.basename(video_path)}")
	lines.append("")
	lines.append(f"Video: {video_path}")
	lines.append(
		f"Dimensions: {stats['width']} x {stats['height']} "
		f"({stats['n_frames']} frames scanned)"
	)
	lines.append(f"Threshold: pixels with luminance <= {threshold} count as black")
	lines.append(f"Row/column fraction: {row_fraction:.2f}")
	lines.append("")
	lines.append("## Aggregate")
	lines.append("")
	lines.append("| Field | Value |")
	lines.append("| --- | --- |")
	lines.append(f"| frames_with_any_bar | {stats['frames_with_any_bar']} |")
	lines.append(
		"| frame_with_any_bar_fraction | "
		f"{stats['frame_with_any_bar_fraction']:.4f} |"
	)
	lines.append(
		"| mean_bar_area_fraction (frames-with-any-bar) | "
		f"{stats['mean_bar_area_fraction']:.4f} |"
	)
	lines.append(
		f"| max_bar_area_fraction | {stats['max_bar_area_fraction']:.4f} |"
	)
	lines.append("")
	lines.append("## Per-edge max bar height")
	lines.append("")
	lines.append("| Edge | Max bar (pixels) |")
	lines.append("| --- | --- |")
	lines.append(f"| top | {stats['top_max']} |")
	lines.append(f"| bottom | {stats['bottom_max']} |")
	lines.append(f"| left | {stats['left_max']} |")
	lines.append(f"| right | {stats['right_max']} |")
	lines.append("")
	report_text = "\n".join(lines)
	return report_text


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"Input file not found: {args.input_file}")

	print(f"Scanning: {args.input_file}")
	stats = scan_video(args.input_file, args.threshold, args.row_fraction)
	print(
		f"  frames={stats['n_frames']} ({stats['width']}x{stats['height']})"
	)
	print(
		f"  frames_with_any_bar={stats['frames_with_any_bar']} "
		f"({stats['frame_with_any_bar_fraction']*100.0:.2f}%)"
	)
	print(
		f"  mean_bar_area_fraction={stats['mean_bar_area_fraction']:.4f} "
		f"max={stats['max_bar_area_fraction']:.4f}"
	)
	print(
		f"  per-edge max (px): top={stats['top_max']} "
		f"bottom={stats['bottom_max']} left={stats['left_max']} "
		f"right={stats['right_max']}"
	)

	# write output markdown
	base = os.path.splitext(args.input_file)[0]
	output_path = args.output_path or (base + ".black_bars.md")
	report_text = render_report(
		args.input_file, stats, args.threshold, args.row_fraction,
	)
	with open(output_path, "w") as f:
		f.write(report_text)
	print(f"  wrote: {output_path}")


#============================================

if __name__ == "__main__":
	main()
