#!/usr/bin/env python3
"""Benchmark scattered-seek vs sequential read speed for an original video
and its fast-read counterpart.

Measures per-call FrameReader.read_frame cost in two patterns:
  - scattered seeks: N_SCATTER_SEEKS randomly-chosen frame indices read one
    at a time (triggers strategy-1 seek each call on a fresh FrameReader);
  - sequential reads: SEQ_RUN_LENGTH consecutive frames (triggers
    strategy-0 fast-path after the first frame).

Reports median and p95 for scattered seeks, ms/frame for sequential, plus
file sizes for both videos.  Encode time is NOT re-measured here (transcode
is prepare's job); the script prints a note instead.

3x gate: if the fast-read scattered-seek median is less than 3x faster than
the original, the script exits non-zero (unless --report-only is set).

Usage:
    source source_me.sh && python3 tools/benchmark_fastread_video.py -i <clip>.mkv
    source source_me.sh && python3 tools/benchmark_fastread_video.py -i <clip>.mkv -r
"""

# Standard Library
import os
import sys
import time
import random
import argparse

# adjust sys.path so common_tools and track_runner are importable when run
# from any working directory
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

# local repo modules
import common_tools.frame_reader
import common_tools.probe_video
import track_runner.tr_paths


#============================================
# How many random frame indices to read in the scattered-seek measurement.
# More samples give better statistics; 40 is enough for a stable median/p95
# without taking too long on slow HEVC sources.
N_SCATTER_SEEKS = 40

# How many consecutive frames to read for the sequential measurement.
# 60 frames is ~0.5 s at 120 fps; long enough to amortize decoder warm-up.
SEQ_RUN_LENGTH = 60

# Starting frame index for the sequential run (offset to avoid the very
# first GOP which has a keyframe-restart overhead at offset 0).
SEQ_START_OFFSET = 200


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments.

	Returns:
		argparse.Namespace with fields: input_file, report_only.
	"""
	parser = argparse.ArgumentParser(
		description="Benchmark scattered-seek and sequential read speed for an"
		" original video vs its fast-read counterpart."
	)
	parser.add_argument(
		'-i', '--input',
		dest='input_file',
		required=True,
		help="Path to the ORIGINAL video file (.mkv). The fast-read path is"
		" derived automatically via tr_paths.fastread_video_path.",
	)
	parser.add_argument(
		'-r', '--report-only',
		dest='report_only',
		action='store_true',
		help="Collect and print numbers without applying the 3x gate (no"
		" non-zero exit on slow fast-read).",
	)
	args = parser.parse_args()
	return args


#============================================
def _format_size_mb(size_bytes: int) -> str:
	"""Format a byte count as a human-readable MB string.

	Args:
		size_bytes: File size in bytes.

	Returns:
		String like '2847.3 MB'.
	"""
	size_mb = size_bytes / (1024 * 1024)
	return f"{size_mb:.1f} MB"


#============================================
def _percentile(sorted_values: list, pct: float) -> float:
	"""Compute a percentile from a pre-sorted list using linear interpolation.

	Args:
		sorted_values: Pre-sorted list of numeric values.
		pct: Percentile in [0, 100].

	Returns:
		Interpolated percentile value.
	"""
	n = len(sorted_values)
	if n == 0:
		raise ValueError("empty list")
	# fractional position in [0, n-1]
	idx = pct / 100.0 * (n - 1)
	lo = int(idx)
	hi = lo + 1
	if hi >= n:
		return float(sorted_values[-1])
	frac = idx - lo
	return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


#============================================
def measure_scattered_seeks(
	video_path: str,
	frame_indices: list,
) -> list:
	"""Measure per-call FrameReader.read_frame cost for a list of frame indices.

	Opens a fresh FrameReader and reads each index individually, measuring
	wall time around each call.  Each call after the first is a strategy-1
	seek because successive indices are randomly ordered (not consecutive).

	Args:
		video_path: Path to the video file to open.
		frame_indices: List of frame indices to read in order.

	Returns:
		List of per-call durations in milliseconds (same length as
		frame_indices).
	"""
	reader = common_tools.frame_reader.FrameReader(video_path)
	durations_ms = []
	for idx in frame_indices:
		t0 = time.monotonic()
		reader.read_frame(idx)
		t1 = time.monotonic()
		durations_ms.append((t1 - t0) * 1000.0)
	return durations_ms


#============================================
def measure_sequential_run(
	video_path: str,
	start_frame: int,
	run_length: int,
) -> float:
	"""Measure average per-frame cost reading run_length consecutive frames.

	Opens a fresh FrameReader, then reads frames start_frame through
	start_frame + run_length - 1 in order, triggering strategy-0 on all
	frames after the first.

	Args:
		video_path: Path to the video file to open.
		start_frame: First frame index in the sequential run.
		run_length: Number of consecutive frames to read.

	Returns:
		Average per-frame duration in milliseconds.
	"""
	reader = common_tools.frame_reader.FrameReader(video_path)
	# prime the capture to the start frame (one strategy-1 seek)
	reader.read_frame(start_frame)
	# measure strategy-0 sequential reads only
	t0 = time.monotonic()
	for frame_index in range(start_frame + 1, start_frame + run_length):
		reader.read_frame(frame_index)
	t1 = time.monotonic()
	elapsed_ms = (t1 - t0) * 1000.0
	# run_length - 1 frames measured (the first was the priming read)
	return elapsed_ms / (run_length - 1)


#============================================
def benchmark_video(
	video_path: str,
	frame_count: int,
	label: str,
) -> dict:
	"""Run both scatter and sequential benchmarks on one video.

	Picks N_SCATTER_SEEKS random frame indices from [0, frame_count),
	seeds the RNG deterministically so both videos get the same set.
	Sequential run starts at SEQ_START_OFFSET (capped below frame_count).

	Args:
		video_path: Path to the video file.
		frame_count: Total number of frames in the video.
		label: Short label for progress printing ('original' or 'fast-read').

	Returns:
		Dict with keys:
			scatter_median_ms  -- median scattered-seek cost in ms
			scatter_p95_ms     -- p95 scattered-seek cost in ms
			seq_ms_per_frame   -- average sequential ms/frame
	"""
	# use a fixed seed so original and fast-read sample the same frame set
	rng = random.Random(42)
	scatter_indices = sorted(rng.sample(range(frame_count), min(N_SCATTER_SEEKS, frame_count)))

	print(f"  [{label}] scattered seeks ({len(scatter_indices)} frames)...")
	scatter_durations = measure_scattered_seeks(video_path, scatter_indices)
	scatter_sorted = sorted(scatter_durations)
	scatter_median = _percentile(scatter_sorted, 50)
	scatter_p95 = _percentile(scatter_sorted, 95)

	# cap start offset so sequential run fits within the file
	seq_start = min(SEQ_START_OFFSET, max(0, frame_count - SEQ_RUN_LENGTH - 1))
	print(f"  [{label}] sequential reads ({SEQ_RUN_LENGTH} frames from frame {seq_start})...")
	seq_ms_per_frame = measure_sequential_run(video_path, seq_start, SEQ_RUN_LENGTH)

	return {
		'scatter_median_ms': scatter_median,
		'scatter_p95_ms': scatter_p95,
		'seq_ms_per_frame': seq_ms_per_frame,
	}


#============================================
def print_results(
	original_path: str,
	fastread_path: str,
	orig_results: dict,
	fast_results: dict,
	orig_size: int,
	fast_size: int,
	speedup: float,
) -> None:
	"""Print the benchmark results table and summary.

	Args:
		original_path: Path to the original video.
		fastread_path: Path to the fast-read video.
		orig_results: Benchmark dict from benchmark_video for the original.
		fast_results: Benchmark dict from benchmark_video for the fast-read.
		orig_size: File size of the original in bytes.
		fast_size: File size of the fast-read in bytes.
		speedup: Scatter median speedup ratio (orig / fast-read).
	"""
	orig_name = os.path.basename(original_path)
	fast_name = os.path.basename(fastread_path)
	size_ratio = orig_size / fast_size if fast_size > 0 else 0.0

	print()
	print("=" * 72)
	print("benchmark_fastread_video results")
	print("=" * 72)
	print(f"  original:  {orig_name}")
	print(f"  fast-read: {fast_name}")
	print()

	# scattered-seek table
	print(f"  {'':30s}  {'original':>12s}  {'fast-read':>12s}")
	print(f"  {'-'*30}  {'-'*12}  {'-'*12}")
	print(
		f"  {'scattered seek median (ms)':30s}"
		f"  {orig_results['scatter_median_ms']:>12.1f}"
		f"  {fast_results['scatter_median_ms']:>12.1f}"
	)
	print(
		f"  {'scattered seek p95 (ms)':30s}"
		f"  {orig_results['scatter_p95_ms']:>12.1f}"
		f"  {fast_results['scatter_p95_ms']:>12.1f}"
	)
	print(
		f"  {'sequential ms/frame':30s}"
		f"  {orig_results['seq_ms_per_frame']:>12.2f}"
		f"  {fast_results['seq_ms_per_frame']:>12.2f}"
	)
	print(
		f"  {'file size':30s}"
		f"  {_format_size_mb(orig_size):>12s}"
		f"  {_format_size_mb(fast_size):>12s}"
	)
	print()
	print(f"  scatter seek speedup (orig / fast-read):  {speedup:.2f}x")
	print(f"  file size ratio (orig / fast-read):       {size_ratio:.2f}x")
	print()
	# encode time is the prepare mode's job; report a note rather than re-run ffmpeg
	print("  encode time: not measured (run prepare with timing to record it)")
	print("=" * 72)


#============================================
def main() -> None:
	"""Entry point: benchmark original vs fast-read video and optionally gate on 3x speedup."""
	args = parse_args()

	original_path = os.path.abspath(args.input_file)
	if not os.path.isfile(original_path):
		raise RuntimeError(f"original video not found: {original_path!r}")

	fastread_path = track_runner.tr_paths.fastread_video_path(original_path)
	if not os.path.isfile(fastread_path):
		raise RuntimeError(
			f"fast-read video not found: {fastread_path!r}\n"
			f"  Run `track_runner -i {original_path} prepare` first."
		)

	print(f"probing {os.path.basename(original_path)} ...")
	orig_meta = common_tools.probe_video.probe_video(original_path)
	orig_frame_count = orig_meta['frame_count']
	print(f"  frame_count={orig_frame_count}  fps={orig_meta['fps']:.3f}"
		f"  {orig_meta['width']}x{orig_meta['height']}")

	print(f"probing {os.path.basename(fastread_path)} ...")
	fast_meta = common_tools.probe_video.probe_video(fastread_path)
	fast_frame_count = fast_meta['frame_count']
	print(f"  frame_count={fast_frame_count}  fps={fast_meta['fps']:.3f}"
		f"  {fast_meta['width']}x{fast_meta['height']}")

	# use the smaller frame count if they differ (defensive; prepare validates equality)
	bench_frame_count = min(orig_frame_count, fast_frame_count)

	print()
	print(f"benchmarking original ({N_SCATTER_SEEKS} scatter seeks, {SEQ_RUN_LENGTH} seq frames)...")
	orig_results = benchmark_video(original_path, bench_frame_count, "original")

	print()
	print(f"benchmarking fast-read ({N_SCATTER_SEEKS} scatter seeks, {SEQ_RUN_LENGTH} seq frames)...")
	fast_results = benchmark_video(fastread_path, bench_frame_count, "fast-read")

	orig_size = os.path.getsize(original_path)
	fast_size = os.path.getsize(fastread_path)

	# compute scattered-seek median speedup (how much faster is the fast-read)
	speedup = orig_results['scatter_median_ms'] / fast_results['scatter_median_ms']

	print_results(
		original_path, fastread_path,
		orig_results, fast_results,
		orig_size, fast_size,
		speedup,
	)

	# 3x gate: fast-read must be at least 3x faster on scattered seeks
	gate_target = 3.0
	if speedup < gate_target:
		msg = (
			f"FAIL: fast-read scattered-seek speedup {speedup:.2f}x is below the {gate_target:.0f}x"
			f" target.\n"
			f"  original median: {orig_results['scatter_median_ms']:.1f} ms\n"
			f"  fast-read median: {fast_results['scatter_median_ms']:.1f} ms\n"
			f"  To skip this gate: re-run with --report-only."
		)
		if args.report_only:
			print()
			print(f"[report-only] {msg}")
		else:
			print()
			raise RuntimeError(msg)
	else:
		print(f"PASS: {speedup:.2f}x speedup meets the {gate_target:.0f}x target.")


#============================================
if __name__ == '__main__':
	main()
