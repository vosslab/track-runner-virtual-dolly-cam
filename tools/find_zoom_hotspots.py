#!/usr/bin/env python3
"""Find the worst zoom-bounce windows in a single encoded movie.

Runs the same Fourier-Mellin per-frame scale estimation that
tools/assess_pixel_zoom.py uses, computes a per-frame bounce intensity
(abs(log_scale)) smoothed across a configurable window, then reports the
top-N hotspot windows in descending intensity order. Optionally extracts
short MP4 clips around each hotspot so visual review can focus on the
worst regions instead of full-length playback.

Output:
	<input_basename>.hotspots.md  -- markdown report with rank, start
	                                 time, end time, max intensity.
	<input_basename>.hotspot_<rank>.mkv  -- only when --extract-clips set.

The hotspot reflects measured zoom bounce in the encoded pixels and may
include source-content motion that looks like zoom (camera pans on a
bumpy hand-held shot). Cross-check against the source-video timeline
when interpreting results.
"""

# Standard Library
import os
import sys
import argparse
import subprocess

# add tools directory to path so we can reuse assess_pixel_zoom helpers
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
	sys.path.insert(0, _TOOLS_DIR)

# PIP3 modules
import cv2
import numpy

# local repo modules
import assess_pixel_zoom


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description="Find the worst zoom-bounce windows in an encoded movie."
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Encoded movie file to analyze",
	)
	parser.add_argument(
		"-n", "--top", dest="top_n", type=int, default=5,
		help="Number of hotspot windows to return (default: 5)",
	)
	parser.add_argument(
		"-w", "--window", dest="window_seconds", type=float, default=5.0,
		help="Window length in seconds (default: 5.0)",
	)
	parser.add_argument(
		"-c", "--extract-clips", dest="extract_clips", action="store_true",
		help="Extract MP4 clips around each hotspot via ffmpeg -c copy",
	)
	parser.add_argument(
		"-W", "--weighting", dest="weighting", default="edge_weighted",
		choices=["full", "edge_weighted", "side_strips"],
		help="Edge masking mode (default: edge_weighted)",
	)
	args = parser.parse_args()
	return args


#============================================
def compute_log_scale_series(
	video_path: str,
	weighting: str,
) -> tuple:
	"""Compute the per-frame log-scale series for a video via Fourier-Mellin.

	Args:
		video_path: Path to encoded movie file.
		weighting: Edge-masking mode passed through to assess_pixel_zoom.

	Returns:
		Tuple (log_scale, fps, total_frames) where log_scale is a numpy
		array of length total_frames; element 0 is 0.0 by convention.
	"""
	cap = cv2.VideoCapture(video_path)
	if not cap.isOpened():
		raise RuntimeError(f"Cannot open video: {video_path}")

	# read frame metadata
	width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
	height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
	fps = float(cap.get(cv2.CAP_PROP_FPS))
	total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
	if total_frames <= 1:
		cap.release()
		raise RuntimeError(f"Video has too few frames: {total_frames}")

	# build edge mask and Hann window (one-time setup)
	edge_mask = assess_pixel_zoom.build_edge_mask(height, width, weighting)
	hann_window = numpy.outer(
		numpy.hanning(height), numpy.hanning(width),
	).astype(numpy.float32)
	max_radius = min(height, width) // 2

	# per-frame log_scale; index 0 is the reference (no previous frame)
	log_scale = numpy.zeros(total_frames, dtype=numpy.float64)

	lp_prev = None
	frame_index = 0
	while True:
		ret, frame = cap.read()
		if not ret:
			break
		gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
		lp_curr = assess_pixel_zoom.compute_fft_log_polar(
			gray, edge_mask, hann_window,
		)
		if lp_prev is not None:
			# (scale, confidence); convert scale back to log for arithmetic
			scale_val, _ = assess_pixel_zoom.estimate_scale_fourier_mellin(
				lp_prev, lp_curr, max_radius, width,
			)
			# numpy.log handles the (0.80, 1.25) clamped range safely
			log_scale[frame_index] = numpy.log(max(scale_val, 1e-6))
		lp_prev = lp_curr
		frame_index += 1
	cap.release()

	# trim trailing zeros if the file declared more frames than it had
	if frame_index < total_frames:
		log_scale = log_scale[:frame_index]
	return (log_scale, fps, len(log_scale))


#============================================
def smooth_intensity(
	log_scale: numpy.ndarray,
	window_frames: int,
) -> numpy.ndarray:
	"""Smooth the absolute log-scale series with a rolling-mean window.

	Args:
		log_scale: Per-frame log-scale array.
		window_frames: Window length in frames (must be >= 1).

	Returns:
		Per-frame smoothed intensity (same length as log_scale). Centers
		the rolling window so smoothed[i] reflects bounce around frame i.
	"""
	window_frames = max(int(window_frames), 1)
	intensity = numpy.abs(log_scale)
	if window_frames == 1:
		return intensity
	# rolling mean via cumulative sum; faster than convolve for long series
	cumulative = numpy.concatenate(([0.0], numpy.cumsum(intensity)))
	half = window_frames // 2
	n = len(intensity)
	smoothed = numpy.zeros(n, dtype=numpy.float64)
	for i in range(n):
		# centered window with edge clipping
		start = max(0, i - half)
		end = min(n, i + half + 1)
		smoothed[i] = (cumulative[end] - cumulative[start]) / (end - start)
	return smoothed


#============================================
def find_top_n_hotspots(
	intensity: numpy.ndarray,
	top_n: int,
	min_gap_frames: int,
) -> list:
	"""Find top-N intensity peaks with a minimum-gap separation.

	Greedy non-maximum suppression: pick the global argmax, mask out a
	window of half-width min_gap_frames on each side, repeat until top_n
	peaks are found or the array is exhausted.

	Args:
		intensity: 1D per-frame intensity array.
		top_n: Maximum number of hotspots to return.
		min_gap_frames: Minimum separation between any two hotspot
			centers, in frames.

	Returns:
		List of (frame_index, intensity_value) tuples sorted by
		intensity descending. Length is at most top_n.
	"""
	if top_n <= 0 or len(intensity) == 0:
		return []
	top_n = min(int(top_n), len(intensity))
	min_gap_frames = max(int(min_gap_frames), 1)
	# work on a copy so masking does not destroy caller data
	working = intensity.astype(numpy.float64).copy()
	hotspots = []
	for _ in range(top_n):
		idx = int(numpy.argmax(working))
		val = float(working[idx])
		# stop early if everything left is the masking sentinel
		if not numpy.isfinite(val):
			break
		hotspots.append((idx, val))
		# mask the window around this peak so the next argmax is gap_frames away
		mask_start = max(0, idx - min_gap_frames)
		mask_end = min(len(working), idx + min_gap_frames + 1)
		working[mask_start:mask_end] = -numpy.inf
	return hotspots


#============================================
def hotspot_window_to_time_range(
	frame_index: int,
	window_frames: int,
	fps: float,
	total_frames: int,
) -> tuple:
	"""Convert a hotspot frame index to (start_s, end_s, start_frame, end_frame).
	"""
	half = window_frames // 2
	start_frame = max(0, frame_index - half)
	end_frame = min(total_frames - 1, frame_index + half)
	start_s = start_frame / fps if fps > 0 else 0.0
	end_s = end_frame / fps if fps > 0 else 0.0
	return (start_s, end_s, start_frame, end_frame)


#============================================
def write_hotspot_report(
	report_path: str,
	video_path: str,
	hotspots: list,
	window_frames: int,
	fps: float,
	total_frames: int,
) -> None:
	"""Write a markdown hotspot report next to the input video.

	Args:
		report_path: Output markdown path.
		video_path: Source video path (recorded in the report).
		hotspots: List of (frame_index, intensity) from find_top_n_hotspots.
		window_frames: Hotspot window length in frames.
		fps: Source video fps.
		total_frames: Source video frame count.
	"""
	# build the markdown content in a variable, then write at the end
	lines = []
	lines.append(f"# Zoom-bounce hotspots: {os.path.basename(video_path)}")
	lines.append("")
	duration_s = total_frames / fps if fps > 0 else 0.0
	lines.append(f"Source: {video_path}")
	lines.append(f"Duration: {duration_s:.2f} s ({total_frames} frames at {fps:.2f} fps)")
	lines.append(f"Window: {window_frames} frames ({window_frames / fps:.2f} s)")
	lines.append("")
	lines.append("| Rank | Center frame | Start (s) | End (s) | Max intensity |")
	lines.append("| --- | --- | --- | --- | --- |")
	for rank, (frame_index, intensity) in enumerate(hotspots, start=1):
		start_s, end_s, _start_f, _end_f = hotspot_window_to_time_range(
			frame_index, window_frames, fps, total_frames,
		)
		lines.append(
			f"| {rank} | {frame_index} | {start_s:.2f} | {end_s:.2f} | "
			f"{intensity:.6f} |"
		)
	# trailing newline
	report_text = "\n".join(lines) + "\n"
	with open(report_path, "w") as f:
		f.write(report_text)


#============================================
def extract_clip_via_ffmpeg(
	source_path: str,
	output_path: str,
	start_s: float,
	duration_s: float,
) -> None:
	"""Extract a clip from source_path to output_path using ffmpeg -c copy.

	Stream-copies video and audio so no re-encoding happens; the clip
	may be slightly off the requested start due to keyframe alignment.
	"""
	cmd = [
		"ffmpeg", "-y",
		"-ss", f"{start_s:.3f}",
		"-i", source_path,
		"-t", f"{duration_s:.3f}",
		"-c", "copy",
		output_path,
	]
	subprocess.run(cmd, check=True, capture_output=True)


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	if not os.path.isfile(args.input_file):
		raise RuntimeError(f"Input file not found: {args.input_file}")

	print(f"Analyzing zoom hotspots in: {args.input_file}")
	log_scale, fps, total_frames = compute_log_scale_series(
		args.input_file, args.weighting,
	)
	window_frames = max(int(round(args.window_seconds * fps)), 1)
	print(
		f"  fps={fps:.2f}, frames={total_frames}, "
		f"window={window_frames} frames ({args.window_seconds:.2f} s)"
	)

	intensity = smooth_intensity(log_scale, window_frames)
	hotspots = find_top_n_hotspots(intensity, args.top_n, window_frames)

	# write report next to the input video
	base = os.path.splitext(args.input_file)[0]
	report_path = base + ".hotspots.md"
	write_hotspot_report(
		report_path, args.input_file, hotspots,
		window_frames, fps, total_frames,
	)
	print(f"  wrote: {report_path}")

	# print top-3 to stdout for quick read
	print("Top hotspots (rank, frame, start_s, end_s, intensity):")
	for rank, (frame_index, intensity_val) in enumerate(hotspots[:3], start=1):
		start_s, end_s, _, _ = hotspot_window_to_time_range(
			frame_index, window_frames, fps, total_frames,
		)
		print(
			f"  {rank}: frame={frame_index} "
			f"start={start_s:.2f}s end={end_s:.2f}s intensity={intensity_val:.6f}"
		)

	# extract clips if requested
	if args.extract_clips:
		duration_s = window_frames / fps if fps > 0 else 0.0
		for rank, (frame_index, _intensity_val) in enumerate(hotspots, start=1):
			start_s, _end_s, _, _ = hotspot_window_to_time_range(
				frame_index, window_frames, fps, total_frames,
			)
			clip_path = f"{base}.hotspot_{rank}.mkv"
			print(f"  extracting clip {rank}: {clip_path}")
			extract_clip_via_ffmpeg(args.input_file, clip_path, start_s, duration_s)


#============================================

if __name__ == "__main__":
	main()
