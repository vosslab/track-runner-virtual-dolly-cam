#!/usr/bin/env python3
"""Sweep tools/visualize_blob_gates.py across rows in per_video_selection.csv.

Iterates the per-video selection CSV produced by build_interval_selection.py
and invokes the blob-gates visualizer once per interval, sequentially. The
sweep is resumable: intervals whose output dir already contains verdicts.csv
are skipped. Single-interval failures are logged but do not abort the sweep.

Usage:
	source source_me.sh && python3 tools/run_interval_sweep.py
"""

# Standard Library
import os
import csv
import sys
import time
import argparse
import subprocess
import pathlib

# default paths resolved relative to the working directory at invoke time
DEFAULT_SELECTION_CSV = (
	"output_smoke/blob_refine_fix/2026-05-23/m2_sweep/per_video_selection.csv"
)
DEFAULT_OUTPUT_ROOT = "output_smoke/blob_refine_fix/2026-05-23/m2_sweep"
DEFAULT_VIDEOS_DIR = "TRACK_VIDEOS"
DEFAULT_TR_CONFIG_DIR = "tr_config"

# video container extensions tried in order when resolving a video name
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".mov")

# allowed sample filter labels mirror the CSV's `sample` column values
SAMPLE_RANKING = "ranking"
SAMPLE_HELD_OUT = "held_out"
SAMPLE_ALL = "all"
ALLOWED_SAMPLES = (SAMPLE_RANKING, SAMPLE_HELD_OUT, SAMPLE_ALL)

# name of the per-interval verdicts artifact used as the resume marker
VERDICTS_FILENAME = "verdicts.csv"

# log file written under the output root capturing per-interval results
SWEEP_LOG_FILENAME = "sweep_run.log"

# prefix used to detect "Jason" videos for selective PNG rendering
JASON_VIDEO_PREFIX = "Jason"


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Sweep tools/visualize_blob_gates.py over per_video_selection.csv "
			"rows. Resumable, sequential, and tolerant of per-interval failure."
		),
	)
	parser.add_argument(
		"-s", "--selection-csv", dest="selection_csv",
		default=DEFAULT_SELECTION_CSV,
		help=f"Path to per-video selection CSV. Default: {DEFAULT_SELECTION_CSV}",
	)
	parser.add_argument(
		"-o", "--output-root", dest="output_root",
		default=DEFAULT_OUTPUT_ROOT,
		help=f"Root directory for per-interval output. Default: {DEFAULT_OUTPUT_ROOT}",
	)
	parser.add_argument(
		"-v", "--videos-dir", dest="videos_dir",
		default=DEFAULT_VIDEOS_DIR,
		help=f"Directory holding source videos. Default: {DEFAULT_VIDEOS_DIR}",
	)
	parser.add_argument(
		"-c", "--tr-config-dir", dest="tr_config_dir",
		default=DEFAULT_TR_CONFIG_DIR,
		help=f"Directory holding tr_config artifacts. Default: {DEFAULT_TR_CONFIG_DIR}",
	)
	parser.add_argument(
		"--sample", dest="sample", choices=ALLOWED_SAMPLES,
		default=SAMPLE_RANKING,
		help="Filter rows by sample label. Default: ranking",
	)
	parser.add_argument(
		"--jason-pngs", dest="jason_pngs", action="store_true",
		help="Render PNGs only for Jason intervals in the ranking sample.",
	)
	parser.add_argument(
		"--no-jason-pngs", dest="jason_pngs", action="store_false",
		help="Disable Jason-only PNG rendering.",
	)
	parser.set_defaults(jason_pngs=True)
	args = parser.parse_args()
	return args


#============================================
def load_rows(selection_csv: str, sample_filter: str) -> list:
	"""Read selection CSV and return rows matching the sample filter."""
	with open(selection_csv, "r", newline="") as handle:
		reader = csv.DictReader(handle)
		all_rows = list(reader)
	# 'all' bypasses the sample filter; otherwise keep matching rows only
	if sample_filter == SAMPLE_ALL:
		filtered_rows = all_rows
	else:
		filtered_rows = [r for r in all_rows if r["sample"] == sample_filter]
	return filtered_rows


#============================================
def resolve_video_path(videos_dir: str, video_basename: str) -> str:
	"""Return the first existing video path under videos_dir, or empty string."""
	videos_path = pathlib.Path(videos_dir)
	# try each supported container extension in priority order
	for extension in VIDEO_EXTENSIONS:
		candidate = videos_path / (video_basename + extension)
		if candidate.is_file():
			return str(candidate)
	return ""


#============================================
def build_command(video_path: str, tr_config_dir: str, start: str, end: str,
		interval_output_dir: str, render_pngs: bool) -> list:
	"""Construct the visualize_blob_gates.py command list (no shell)."""
	# resolve the visualizer tool path relative to this file's location
	tools_dir = pathlib.Path(__file__).resolve().parent
	visualizer = str(tools_dir / "visualize_blob_gates.py")
	# command is invoked through python3 directly; the caller already has
	# source_me.sh applied so PYTHONPATH is set
	command = [
		sys.executable, visualizer,
		"-v", video_path,
		"-i", start, end,
		"--write-corridor-blob-list",
		"-o", interval_output_dir,
	]
	# Jason-only PNG mode: when render_pngs is False, suppress PNG rendering
	if not render_pngs:
		command.append("--no-png")
	return command


#============================================
def should_render_pngs(video_basename: str, sample_filter: str,
		jason_pngs_flag: bool) -> bool:
	"""Return True only for Jason rows in ranking sample with the flag on."""
	# PNGs are gated three ways: flag enabled, ranking sample, Jason prefix
	if not jason_pngs_flag:
		return False
	if sample_filter != SAMPLE_RANKING:
		return False
	if not video_basename.startswith(JASON_VIDEO_PREFIX):
		return False
	return True


#============================================
def append_log(log_path: str, line: str) -> None:
	"""Append a single line to the sweep log file."""
	with open(log_path, "a") as handle:
		handle.write(line + "\n")


#============================================
def process_row(row: dict, args: argparse.Namespace, log_path: str) -> str:
	"""Run the visualizer for one CSV row. Returns a status token.

	Status tokens are: 'skipped', 'missing_video', 'ok', 'fail'.
	"""
	video = row["video"]
	start = row["interval_start"]
	end = row["interval_end"]
	bucket = row["bucket"]
	# per-interval output dir is unique per (video, start, end) tuple
	interval_dir_name = f"interval_{start}_{end}"
	interval_output_dir = os.path.join(args.output_root, video, interval_dir_name)
	verdicts_path = os.path.join(interval_output_dir, VERDICTS_FILENAME)
	# resume support: skip when the verdicts artifact already exists
	if os.path.isfile(verdicts_path):
		print(f"SKIP existing: {interval_output_dir}")
		return "skipped"
	# resolve the source video; missing videos are logged but do not abort
	video_path = resolve_video_path(args.videos_dir, video)
	if not video_path:
		log_line = f"{video} {start}-{end} {bucket} MISSING_VIDEO"
		append_log(log_path, log_line)
		print(log_line)
		return "missing_video"
	# ensure the per-interval output directory exists before invocation
	os.makedirs(interval_output_dir, exist_ok=True)
	render_pngs = should_render_pngs(video, args.sample, args.jason_pngs)
	command = build_command(
		video_path, args.tr_config_dir, start, end,
		interval_output_dir, render_pngs,
	)
	# time the subprocess for the per-interval log line
	start_time = time.time()
	# check=False keeps the sweep alive when one interval fails
	completed = subprocess.run(command, check=False)
	duration_s = time.time() - start_time
	log_line = (
		f"{video} {start}-{end} {bucket} "
		f"exit={completed.returncode} duration={duration_s:.2f}"
	)
	append_log(log_path, log_line)
	if completed.returncode == 0:
		return "ok"
	return "fail"


#============================================
def main() -> None:
	"""Entry point: drive the per-row sweep and emit a summary."""
	args = parse_args()
	rows = load_rows(args.selection_csv, args.sample)
	# log path lives at the output root for easy discovery
	os.makedirs(args.output_root, exist_ok=True)
	log_path = os.path.join(args.output_root, SWEEP_LOG_FILENAME)
	# counters for the final summary line
	total = len(rows)
	succeeded = 0
	failed = 0
	skipped = 0
	missing = 0
	wall_start = time.time()
	for row in rows:
		status = process_row(row, args, log_path)
		if status == "ok":
			succeeded += 1
		elif status == "fail":
			failed += 1
		elif status == "skipped":
			skipped += 1
		elif status == "missing_video":
			missing += 1
	wall_duration_s = time.time() - wall_start
	# summary mirrors the per-row log format for easy aggregation later
	summary = (
		f"SWEEP DONE total={total} succeeded={succeeded} failed={failed} "
		f"skipped={skipped} missing={missing} wall={wall_duration_s:.2f}s"
	)
	print(summary)
	append_log(log_path, summary)


if __name__ == "__main__":
	main()
