#!/usr/bin/env python3
"""
WP-2B alternate-blob test: would the closest corridor blob have passed?

For every verdicts.csv row where lost_at_stage == "gate_prox", parse the
corridor_blobs_json column, find the blob with the smallest dist_h, and check
whether dist_h <= PROXIMITY_THRESHOLD_H (0.6). Aggregate the rescue rate per
interval, per video, and corpus-wide. Decide hypothesis H_ALT_PASSES per plan
section "Test 5: alternate-blob test":

	SUPPORTED  when corpus-wide rescue rate >= 0.50 on TRUST 0% intervals.
	REFUTED    when corpus-wide rescue rate <  0.50 on TRUST 0% intervals.

Inputs are the per-interval verdicts.csv files produced by WP-1B (corpus
sweep) via tools/visualize_blob_gates.py --write-corridor-blob-list. Output is
a markdown report.

Path layout assumption (matches plan WP-1B):

	{run_root}/m2_sweep/{video_basename}/interval_{start}_{end}/verdicts.csv

Each row carries a "lost_at_stage" tag (see tools/classify_blob_failure_mode.py
classify_funnel) and a "corridor_blobs_json" column populated when the
visualizer ran with --write-corridor-blob-list. Each entry in
corridor_blobs_json is a dict with the per-blob score set:

	{"cx", "cy", "area", "dist_h", "integrated_mag",
	 "size_score", "proximity_score", "total_score"}

References:
 - Plan: /Users/vosslab/.claude/plans/kind-exploring-cray.md (WP-2B)
 - Visualizer: tools/visualize_blob_gates.py encode_corridor_blobs_json
 - Funnel tagger: tools/classify_blob_failure_mode.py classify_funnel
"""

# Standard Library
import csv
import glob
import json
import argparse
from pathlib import Path


#============================================
# Constants
#============================================

# Current production proximity gate threshold: dist <= 0.6 * h. Per-blob
# dist_h in corridor_blobs_json is already normalized (dist / h), so the
# rescue check is dist_h <= 0.6.
PROXIMITY_THRESHOLD_H = 0.6

# Corpus-wide rescue-rate threshold for H_ALT_PASSES SUPPORTED verdict.
H_ALT_PASSES_THRESHOLD = 0.50

# Funnel-stage tag indicating the proximity gate rejected the chosen blob.
PROX_REJECTED_TAG = "gate_prox"


#============================================
# Path parsing
#============================================

def parse_verdicts_path(verdicts_path: Path) -> tuple:
	"""
	Extract (video_basename, interval_id) from a verdicts.csv path.

	Expected layout per plan WP-1B:
		{run_root}/m2_sweep/{video_basename}/interval_{start}_{end}/verdicts.csv

	Args:
		verdicts_path: Path to a verdicts.csv file.

	Returns:
		Tuple of (video_basename, interval_id). Falls back to parent
		directory names when the path does not match the expected layout
		(test fixtures, ad-hoc layouts).
	"""
	# parent is the interval_* dir, parent.parent is the video_basename dir
	interval_dir = verdicts_path.parent
	video_dir = interval_dir.parent
	interval_id = interval_dir.name
	video_basename = video_dir.name
	return video_basename, interval_id


#============================================
# Per-row alternate-blob check
#============================================

def find_min_dist_h_blob(corridor_blobs: list) -> dict:
	"""
	Return the blob in corridor_blobs with the smallest dist_h.

	Args:
		corridor_blobs: List of blob dicts. Each dict has a "dist_h" key.

	Returns:
		The blob dict with the minimum dist_h, or None if list is empty.
	"""
	if not corridor_blobs:
		return None
	best_blob = corridor_blobs[0]
	for blob in corridor_blobs[1:]:
		if blob["dist_h"] < best_blob["dist_h"]:
			best_blob = blob
	return best_blob


def alternate_blob_passes(corridor_blobs_json: str) -> tuple:
	"""
	Check whether the closest corridor blob would pass the proximity gate.

	Args:
		corridor_blobs_json: Compact JSON string of blob dicts, or empty.

	Returns:
		Tuple of (passes, min_dist_h, n_corridor_blobs).
		- passes: True when min_dist_h <= PROXIMITY_THRESHOLD_H. False when
		  no blobs exist or the closest is still outside the gate.
		- min_dist_h: The closest blob's dist_h, or None when no blobs.
		- n_corridor_blobs: Number of blobs parsed from the JSON.
	"""
	# Empty / missing JSON column: no alternate exists for this row.
	if not corridor_blobs_json:
		return False, None, 0
	blobs = json.loads(corridor_blobs_json)
	if not blobs:
		return False, None, 0
	best_blob = find_min_dist_h_blob(blobs)
	min_dist_h = best_blob["dist_h"]
	passes = min_dist_h <= PROXIMITY_THRESHOLD_H
	return passes, min_dist_h, len(blobs)


#============================================
# Per-interval aggregation
#============================================

def aggregate_interval(verdicts_path: Path) -> dict:
	"""
	Aggregate alternate-blob rescue stats for a single verdicts.csv.

	Walks the CSV, filters to lost_at_stage == "gate_prox" rows, and counts
	how many of those rows would be rescued by the closest corridor blob.

	Args:
		verdicts_path: Path to a verdicts.csv file.

	Returns:
		Dict with keys: video, interval, n_prox_rejected, n_rescued,
		rescue_rate, per_row (list of per-row dicts for the report).
	"""
	video, interval = parse_verdicts_path(verdicts_path)
	n_prox_rejected = 0
	n_rescued = 0
	per_row_records = []

	with open(verdicts_path, newline="") as csv_file:
		reader = csv.DictReader(csv_file)
		for row in reader:
			lost_at_stage = row["lost_at_stage"].strip()
			# Only proximity-gate rejections matter for this test.
			if lost_at_stage != PROX_REJECTED_TAG:
				continue
			n_prox_rejected += 1
			corridor_blobs_json = row["corridor_blobs_json"]
			passes, min_dist_h, n_blobs = alternate_blob_passes(corridor_blobs_json)
			if passes:
				n_rescued += 1
			per_row_records.append({
				"frame_index": row["frame_index"],
				"pass": row["pass"],
				"min_dist_h": min_dist_h,
				"n_corridor_blobs": n_blobs,
				"rescued": passes,
			})

	# Avoid divide-by-zero for intervals that have no prox rejections.
	if n_prox_rejected > 0:
		rescue_rate = n_rescued / n_prox_rejected
	else:
		rescue_rate = 0.0

	interval_record = {
		"video": video,
		"interval": interval,
		"n_prox_rejected": n_prox_rejected,
		"n_rescued": n_rescued,
		"rescue_rate": rescue_rate,
		"per_row": per_row_records,
	}
	return interval_record


#============================================
# Per-video and corpus-wide rollups
#============================================

def aggregate_by_video(interval_records: list) -> dict:
	"""
	Roll per-interval records up to per-video totals.

	Args:
		interval_records: List of dicts emitted by aggregate_interval.

	Returns:
		Dict mapping video_basename -> dict with n_prox_rejected, n_rescued,
		rescue_rate, n_intervals.
	"""
	video_totals = {}
	for record in interval_records:
		video = record["video"]
		if video not in video_totals:
			video_totals[video] = {
				"n_prox_rejected": 0,
				"n_rescued": 0,
				"n_intervals": 0,
			}
		video_totals[video]["n_prox_rejected"] += record["n_prox_rejected"]
		video_totals[video]["n_rescued"] += record["n_rescued"]
		video_totals[video]["n_intervals"] += 1

	# Compute per-video rescue rate (guard divide-by-zero per video).
	for video, totals in video_totals.items():
		if totals["n_prox_rejected"] > 0:
			totals["rescue_rate"] = totals["n_rescued"] / totals["n_prox_rejected"]
		else:
			totals["rescue_rate"] = 0.0
	return video_totals


def aggregate_corpus(interval_records: list) -> dict:
	"""
	Roll per-interval records up to a single corpus-wide total.

	Args:
		interval_records: List of dicts emitted by aggregate_interval.

	Returns:
		Dict with n_prox_rejected, n_rescued, rescue_rate, n_intervals,
		n_videos.
	"""
	total_prox_rejected = 0
	total_rescued = 0
	videos_seen = set()
	for record in interval_records:
		total_prox_rejected += record["n_prox_rejected"]
		total_rescued += record["n_rescued"]
		videos_seen.add(record["video"])
	if total_prox_rejected > 0:
		corpus_rescue_rate = total_rescued / total_prox_rejected
	else:
		corpus_rescue_rate = 0.0
	corpus = {
		"n_prox_rejected": total_prox_rejected,
		"n_rescued": total_rescued,
		"rescue_rate": corpus_rescue_rate,
		"n_intervals": len(interval_records),
		"n_videos": len(videos_seen),
	}
	return corpus


def decide_h_alt_passes(corpus_rescue_rate: float, n_prox_rejected: int) -> str:
	"""
	Decide the H_ALT_PASSES verdict.

	The hypothesis is SUPPORTED when the corpus-wide rescue rate on prox-
	rejected rows reaches H_ALT_PASSES_THRESHOLD. Per plan WP-2B, the
	relevant corpus is the TRUST 0% interval set; this tool defers TRUST 0%
	filtering to whatever upstream selection produced the verdicts.csv files
	(via per_video_selection.csv in WP-1B). If the input glob carries only
	TRUST 0% intervals, the verdict is the H_ALT_PASSES decision directly.

	Args:
		corpus_rescue_rate: Fraction of prox-rejected rows the closest
			corridor blob would have rescued.
		n_prox_rejected: Total prox-rejected row count. Used to mark
			INCONCLUSIVE when the sample is empty.

	Returns:
		"SUPPORTED", "REFUTED", or "INCONCLUSIVE".
	"""
	if n_prox_rejected == 0:
		return "INCONCLUSIVE"
	if corpus_rescue_rate >= H_ALT_PASSES_THRESHOLD:
		return "SUPPORTED"
	return "REFUTED"


#============================================
# Report formatting
#============================================

def format_percent(numerator: int, denominator: int) -> str:
	"""
	Format a fraction as a percent string, guarding divide-by-zero.
	"""
	if denominator == 0:
		return "n/a"
	pct = 100.0 * numerator / denominator
	pct_text = f"{pct:.1f}%"
	return pct_text


def format_report(
	interval_records: list,
	video_totals: dict,
	corpus: dict,
	verdict: str,
	glob_pattern: str,
) -> str:
	"""
	Format the full markdown report.

	Args:
		interval_records: Per-interval dicts from aggregate_interval.
		video_totals: Per-video rollup from aggregate_by_video.
		corpus: Corpus-wide rollup from aggregate_corpus.
		verdict: H_ALT_PASSES verdict string.
		glob_pattern: The input glob pattern (echoed in the report).

	Returns:
		The complete report as a single markdown string.
	"""
	# Build the report in pieces so it is easy to scan and edit later.
	header_lines = []
	header_lines.append("# Alternate-blob winner test (WP-2B)")
	header_lines.append("")
	header_lines.append(f"Input glob: `{glob_pattern}`")
	header_lines.append(f"Proximity gate threshold: dist_h <= {PROXIMITY_THRESHOLD_H}")
	header_lines.append(f"H_ALT_PASSES corpus threshold: rescue_rate >= {H_ALT_PASSES_THRESHOLD}")
	header_lines.append("")
	header_section = "\n".join(header_lines)

	verdict_lines = []
	verdict_lines.append("## Corpus verdict")
	verdict_lines.append("")
	verdict_lines.append(f"H_ALT_PASSES: **{verdict}**")
	verdict_lines.append("")
	rescue_pct = format_percent(corpus["n_rescued"], corpus["n_prox_rejected"])
	verdict_lines.append(f"Corpus rescue rate: {rescue_pct} "
		f"({corpus['n_rescued']}/{corpus['n_prox_rejected']})")
	verdict_lines.append(f"Intervals scanned: {corpus['n_intervals']}")
	verdict_lines.append(f"Videos scanned: {corpus['n_videos']}")
	verdict_lines.append("")
	verdict_section = "\n".join(verdict_lines)

	# Per-video table sorted by video name for stable diffs.
	video_lines = []
	video_lines.append("## Per-video rescue rates")
	video_lines.append("")
	video_lines.append("| video | intervals | prox_rejected | rescued | rescue_rate |")
	video_lines.append("| --- | ---: | ---: | ---: | ---: |")
	for video in sorted(video_totals.keys()):
		totals = video_totals[video]
		rate_text = format_percent(totals["n_rescued"], totals["n_prox_rejected"])
		video_lines.append(
			f"| {video} | {totals['n_intervals']} "
			f"| {totals['n_prox_rejected']} | {totals['n_rescued']} "
			f"| {rate_text} |"
		)
	video_lines.append("")
	video_section = "\n".join(video_lines)

	# Per-interval table sorted by (video, interval) for stable diffs.
	interval_lines = []
	interval_lines.append("## Per-interval rescue rates")
	interval_lines.append("")
	interval_lines.append("| video | interval | prox_rejected | rescued | rescue_rate |")
	interval_lines.append("| --- | --- | ---: | ---: | ---: |")
	sorted_records = sorted(
		interval_records,
		key=lambda r: (r["video"], r["interval"]),
	)
	for record in sorted_records:
		rate_text = format_percent(record["n_rescued"], record["n_prox_rejected"])
		interval_lines.append(
			f"| {record['video']} | {record['interval']} "
			f"| {record['n_prox_rejected']} | {record['n_rescued']} "
			f"| {rate_text} |"
		)
	interval_lines.append("")
	interval_section = "\n".join(interval_lines)

	report_text = (
		header_section + "\n"
		+ verdict_section + "\n"
		+ video_section + "\n"
		+ interval_section
	)
	return report_text


#============================================
# CLI
#============================================

def parse_args():
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description=(
			"WP-2B alternate-blob test: would the closest corridor blob "
			"have passed the current proximity gate on prox-rejected rows?"
		)
	)
	parser.add_argument(
		"-g", "--verdicts-glob",
		dest="verdicts_glob",
		required=True,
		help="Glob for verdicts.csv files (quote so the shell does not expand).",
	)
	parser.add_argument(
		"-o", "--output",
		dest="output_path",
		required=True,
		help="Output markdown report path.",
	)
	args = parser.parse_args()
	return args


def main():
	"""
	Walk every verdicts.csv matching the glob, aggregate rescue stats, and
	write the markdown report.
	"""
	args = parse_args()

	# Collect every verdicts.csv path the glob points at. Sort for
	# determinism so the report ordering does not drift across runs.
	verdicts_paths = sorted(Path(p) for p in glob.glob(args.verdicts_glob, recursive=True))
	if not verdicts_paths:
		raise FileNotFoundError(
			f"No verdicts.csv files matched glob: {args.verdicts_glob}"
		)

	# Aggregate per interval; the per-video and corpus rollups derive from
	# this list. Keeping the interval list as the source of truth lets the
	# report table reuse the same data without double counting.
	interval_records = [aggregate_interval(p) for p in verdicts_paths]

	# Roll up to per-video and corpus tables.
	video_totals = aggregate_by_video(interval_records)
	corpus = aggregate_corpus(interval_records)

	# Decide the hypothesis verdict from the corpus rescue rate.
	verdict = decide_h_alt_passes(corpus["rescue_rate"], corpus["n_prox_rejected"])

	# Build the report text and write it to disk. Ensure the output dir
	# exists so callers can hand in a fresh path without pre-creating it.
	report_text = format_report(
		interval_records,
		video_totals,
		corpus,
		verdict,
		args.verdicts_glob,
	)
	output_path = Path(args.output_path)
	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text(report_text, encoding="ascii")


if __name__ == "__main__":
	main()
