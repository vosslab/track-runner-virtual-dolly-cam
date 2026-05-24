#!/usr/bin/env python3
"""Build a winner-distance histogram report for proximity-gate-rejected frames.

Reads every `verdicts.csv` matched by `--verdicts-glob`, filters to rows with
`lost_at_stage == "gate_prox"`, bins `winner_dist_h` into [0.0, 3.0) at width
0.1 plus a `[3.0, inf)` overflow bin, and emits per-interval, per-video, and
corpus-wide histograms to a Markdown report.

Each input verdicts.csv is expected to live under a directory shaped like
`.../<video_basename>/interval_<start>_<end>/verdicts.csv`; the histogram tool
keys "interval" on the leaf directory name and "video" on its parent.

Decides per the plan WP-2A test 4:

- H_DIST_SHAPE_THRESHOLD when the corpus mode bin starts in [0.6, 1.2);
  supports policies B / D (loosen / reshape the proximity threshold).
- H_DIST_SHAPE_BLOB when the corpus mode bin starts in [1.5, inf);
  supports policies C / E / F (blob selection / centroid / weighted snap).
- INCONCLUSIVE otherwise.

Usage:
	source source_me.sh && python3 tools/blob_distance_histogram.py \\
		-g 'output_smoke/blob_refine_fix/run01/m2_sweep/**/verdicts.csv' \\
		-o output_smoke/blob_refine_fix/run01/analyses/distance_histogram.md
"""

# Standard Library
import os
import csv
import glob
import math
import argparse

# bin layout: 30 fixed-width bins covering [0.0, 3.0) plus one overflow bin
BIN_WIDTH = 0.1
BIN_COUNT_FINITE = 30
OVERFLOW_BIN_INDEX = BIN_COUNT_FINITE
TOTAL_BINS = BIN_COUNT_FINITE + 1

# bar rendering width in characters (Markdown fenced ASCII bar)
BAR_WIDTH_CHARS = 40

# decision thresholds for H_DIST_SHAPE_* (mode bin LOW edge, inclusive)
THRESHOLD_LOW = 0.6
THRESHOLD_HIGH = 1.2
BLOB_LOW = 1.5


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Emit per-interval, per-video, and corpus-wide histograms of "
			"winner_dist_h on proximity-gate-rejected verdicts.csv rows."
		),
	)
	parser.add_argument(
		"-g", "--verdicts-glob", dest="verdicts_glob", required=True,
		help="Glob pattern matching verdicts.csv files (supports **).",
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", required=True,
		help="Path to output Markdown report.",
	)
	return parser.parse_args()


#============================================
def bin_index_for_distance(distance: float) -> int:
	"""Return the bin index for a winner_dist_h value.

	Bins 0..29 cover [0.0, 0.1) ... [2.9, 3.0). Bin 30 is the [3.0, inf)
	overflow bin. NaN distances are mapped to the overflow bin so they
	never silently corrupt the mode bin selection.

	Args:
		distance: winner_dist_h value (units of torso height).

	Returns:
		int: bin index in [0, TOTAL_BINS).
	"""
	# NaN guard: numpy NaN comparisons are False so this catches both
	# math.nan and string-cast 'nan' once converted to float.
	if math.isnan(distance):
		return OVERFLOW_BIN_INDEX
	if distance >= 3.0:
		return OVERFLOW_BIN_INDEX
	if distance < 0.0:
		# Negative distances are not meaningful for proximity-rejected
		# frames but must not crash the binner. Clamp to bin 0.
		return 0
	# Avoid binary-float bin-edge surprises: `0.7 / 0.1 == 6.9999...`
	# would land 0.7 in bin 6 instead of bin 7 via a naive int(). Scale
	# by 10, then round to the nearest tenth before floor-dividing so an
	# exact bin-edge value resolves to its bin's low-edge index.
	scaled = round(distance * 10.0, 6)
	index = int(scaled)
	if index >= BIN_COUNT_FINITE:
		# Floating-point rounding guard for values like 2.9999999.
		return BIN_COUNT_FINITE - 1
	return index


#============================================
def bin_label(index: int) -> str:
	"""Return a human-readable label for a bin index."""
	if index == OVERFLOW_BIN_INDEX:
		return "[3.0, inf)"
	low = index * BIN_WIDTH
	high = low + BIN_WIDTH
	return f"[{low:.1f}, {high:.1f})"


#============================================
def bin_low_edge(index: int) -> float:
	"""Return the inclusive low edge of a bin in units of torso height."""
	if index == OVERFLOW_BIN_INDEX:
		return 3.0
	return index * BIN_WIDTH


#============================================
def classify_corpus_mode(mode_index: int) -> str:
	"""Classify the corpus-wide mode bin per plan WP-2A test 4.

	Returns one of: H_DIST_SHAPE_THRESHOLD, H_DIST_SHAPE_BLOB, INCONCLUSIVE.
	"""
	low_edge = bin_low_edge(mode_index)
	if THRESHOLD_LOW <= low_edge < THRESHOLD_HIGH:
		return "H_DIST_SHAPE_THRESHOLD"
	if low_edge >= BLOB_LOW:
		return "H_DIST_SHAPE_BLOB"
	return "INCONCLUSIVE"


#============================================
def interval_key_from_path(verdicts_path: str) -> str:
	"""Return the interval key (leaf directory name) for a verdicts.csv path."""
	interval_dir = os.path.dirname(verdicts_path)
	return os.path.basename(interval_dir)


#============================================
def video_key_from_path(verdicts_path: str) -> str:
	"""Return the video key (parent of interval directory) for a verdicts.csv path."""
	interval_dir = os.path.dirname(verdicts_path)
	video_dir = os.path.dirname(interval_dir)
	video_key = os.path.basename(video_dir)
	if not video_key:
		# Fallback for shallow inputs (rare; only when a verdicts.csv lives
		# at the filesystem root). Use a sentinel so the report stays sane.
		video_key = "_unknown"
	return video_key


#============================================
def read_prox_distances(verdicts_path: str) -> list:
	"""Read proximity-rejected winner_dist_h values from one verdicts.csv.

	Args:
		verdicts_path: Path to a verdicts.csv emitted by visualize_blob_gates.

	Returns:
		list[float]: winner_dist_h values for rows where lost_at_stage ==
		"gate_prox" and winner_dist_h is non-empty.
	"""
	distances = []
	with open(verdicts_path, "r", newline="") as f:
		reader = csv.DictReader(f)
		for row in reader:
			# Required columns per WP-1B + WP-0B. Use indexed access so a
			# missing column fails loudly per docs/PYTHON_STYLE.md.
			lost_at_stage = row["lost_at_stage"]
			if lost_at_stage != "gate_prox":
				continue
			raw_dist = row["winner_dist_h"]
			if raw_dist == "" or raw_dist is None:
				# Missing measurement on a prox-rejected frame is a bug
				# in the upstream tool, but skipping here keeps the
				# analyzer usable on partial sweeps. Recorded in the
				# report via a per-row count when surfaced upstream.
				continue
			distances.append(float(raw_dist))
	return distances


#============================================
def build_counts(distances: list) -> list:
	"""Return a TOTAL_BINS-length list of frame counts per bin."""
	counts = [0] * TOTAL_BINS
	for distance in distances:
		index = bin_index_for_distance(distance)
		counts[index] += 1
	return counts


#============================================
def mode_bin_index(counts: list) -> int:
	"""Return the bin index with the highest count.

	Ties resolve to the lowest bin index (stable left-most mode); this
	matches the convention used in plan WP-2A reporting so the corpus
	verdict is reproducible across reruns.
	"""
	max_count = -1
	mode_index = 0
	for index, count in enumerate(counts):
		if count > max_count:
			max_count = count
			mode_index = index
	return mode_index


#============================================
def render_ascii_histogram(counts: list) -> str:
	"""Render counts as an ASCII bar histogram in a fenced code block.

	Uses `#` for fill and `.` for empty space per docs/MARKDOWN_STYLE.md
	(no Unicode block characters). Bars are scaled to the max bin count.
	"""
	total = sum(counts)
	if total == 0:
		return "```\n(no proximity-rejected frames)\n```\n"
	max_count = max(counts)
	lines = ["```"]
	for index, count in enumerate(counts):
		# Scale bars to the largest bin; use 0 chars when the bin is empty
		# so empty bins read as flat dots rather than spurious fill.
		if max_count > 0 and count > 0:
			bar_width = int(round(BAR_WIDTH_CHARS * count / max_count))
		else:
			bar_width = 0
		bar = "#" * bar_width + "." * (BAR_WIDTH_CHARS - bar_width)
		percent = 100.0 * count / total
		label = bin_label(index)
		lines.append(f"{label:>12s}  {bar}  {count:6d}  ({percent:5.1f}%)")
	lines.append("```")
	return "\n".join(lines) + "\n"


#============================================
def format_group_section(
	heading: str,
	counts: list,
	verdict_line: str = "",
) -> str:
	"""Format a histogram section for one group (interval / video / corpus)."""
	total = sum(counts)
	mode_index = mode_bin_index(counts) if total > 0 else 0
	parts = [heading, ""]
	parts.append(f"- Total proximity-rejected frames: {total}")
	if total > 0:
		mode_count = counts[mode_index]
		mode_pct = 100.0 * mode_count / total
		parts.append(
			f"- Mode bin: {bin_label(mode_index)}  "
			f"({mode_count} frames, {mode_pct:.1f}%)"
		)
	else:
		parts.append("- Mode bin: (no data)")
	if verdict_line:
		parts.append(verdict_line)
	parts.append("")
	parts.append(render_ascii_histogram(counts))
	return "\n".join(parts)


#============================================
def aggregate(per_interval: dict) -> tuple:
	"""Aggregate per-interval counts into per-video and corpus-wide counts.

	Args:
		per_interval: dict[(video_key, interval_key) -> counts list].

	Returns:
		tuple of (per_video_counts dict, corpus_counts list).
	"""
	per_video: dict = {}
	corpus_counts = [0] * TOTAL_BINS
	for (video_key, _interval_key), counts in per_interval.items():
		if video_key not in per_video:
			per_video[video_key] = [0] * TOTAL_BINS
		for bin_idx, value in enumerate(counts):
			per_video[video_key][bin_idx] += value
			corpus_counts[bin_idx] += value
	return per_video, corpus_counts


#============================================
def build_report(per_interval: dict, glob_pattern: str) -> str:
	"""Build the full Markdown report string."""
	per_video, corpus_counts = aggregate(per_interval)

	# corpus verdict
	corpus_total = sum(corpus_counts)
	if corpus_total > 0:
		corpus_mode_index = mode_bin_index(corpus_counts)
		corpus_verdict = classify_corpus_mode(corpus_mode_index)
	else:
		corpus_mode_index = 0
		corpus_verdict = "INCONCLUSIVE"

	# header
	sections = []
	sections.append("# Blob proximity-gate distance histogram\n")
	sections.append(
		"This report bins `winner_dist_h` on proximity-rejected verdicts.csv "
		"rows (`lost_at_stage == \"gate_prox\"`) into 30 width-0.1 bins over "
		"[0.0, 3.0) plus a `[3.0, inf)` overflow bin.\n"
	)
	sections.append(f"- Verdicts glob: `{glob_pattern}`")
	sections.append(f"- Files scanned: {len(per_interval)}")
	sections.append(f"- Videos covered: {len(per_video)}")
	sections.append(f"- Corpus-wide proximity-rejected frames: {corpus_total}")
	sections.append("")

	# verdict summary up-front so reviewers see the decision before the
	# histograms
	sections.append("## Corpus verdict")
	sections.append("")
	sections.append(f"- Corpus mode bin: {bin_label(corpus_mode_index)}")
	sections.append(f"- Verdict: **{corpus_verdict}**")
	sections.append("")
	sections.append(
		"Decision rule (plan WP-2A test 4): H_DIST_SHAPE_THRESHOLD when "
		"corpus mode is in [0.6, 1.2); H_DIST_SHAPE_BLOB when corpus mode "
		"is in [1.5, inf); INCONCLUSIVE otherwise."
	)
	sections.append("")

	# corpus-wide histogram
	corpus_verdict_line = f"- Verdict: **{corpus_verdict}**"
	sections.append(
		format_group_section(
			"## Corpus-wide histogram",
			corpus_counts,
			corpus_verdict_line,
		)
	)

	# per-video histograms (sorted for stable output)
	sections.append("## Per-video histograms\n")
	for video_key in sorted(per_video.keys()):
		video_counts = per_video[video_key]
		video_total = sum(video_counts)
		if video_total > 0:
			video_mode_index = mode_bin_index(video_counts)
			video_verdict = classify_corpus_mode(video_mode_index)
		else:
			video_verdict = "INCONCLUSIVE"
		video_verdict_line = f"- Per-video verdict: **{video_verdict}**"
		sections.append(
			format_group_section(
				f"### {video_key}",
				video_counts,
				video_verdict_line,
			)
		)

	# per-interval histograms (sorted for stable output)
	sections.append("## Per-interval histograms\n")
	keys_sorted = sorted(per_interval.keys())
	for video_key, interval_key in keys_sorted:
		interval_counts = per_interval[(video_key, interval_key)]
		interval_total = sum(interval_counts)
		if interval_total > 0:
			interval_mode_index = mode_bin_index(interval_counts)
			interval_verdict = classify_corpus_mode(interval_mode_index)
		else:
			interval_verdict = "INCONCLUSIVE"
		interval_verdict_line = (
			f"- Per-interval verdict: **{interval_verdict}**"
		)
		sections.append(
			format_group_section(
				f"### {video_key} / {interval_key}",
				interval_counts,
				interval_verdict_line,
			)
		)

	report = "\n".join(sections)
	return report


#============================================
def collect_per_interval_counts(glob_pattern: str) -> dict:
	"""Glob verdicts.csv files and return per-interval bin counts.

	Returns:
		dict[(video_key, interval_key) -> counts list].
	"""
	# recursive=True is required for the ** plan glob to traverse subdirs
	verdicts_paths = sorted(glob.glob(glob_pattern, recursive=True))
	per_interval: dict = {}
	for verdicts_path in verdicts_paths:
		video_key = video_key_from_path(verdicts_path)
		interval_key = interval_key_from_path(verdicts_path)
		distances = read_prox_distances(verdicts_path)
		counts = build_counts(distances)
		# duplicate (video, interval) keys would only happen from a
		# malformed glob; merge by addition rather than overwrite so the
		# corpus total stays correct.
		key = (video_key, interval_key)
		if key in per_interval:
			existing = per_interval[key]
			for bin_idx, value in enumerate(counts):
				existing[bin_idx] += value
		else:
			per_interval[key] = counts
	return per_interval


#============================================
def write_report(output_path: str, report: str) -> None:
	"""Write the report text to disk, creating parent directories as needed."""
	parent = os.path.dirname(os.path.abspath(output_path))
	if parent and not os.path.isdir(parent):
		os.makedirs(parent, exist_ok=True)
	with open(output_path, "w") as f:
		f.write(report)


#============================================
def main() -> None:
	"""Entry point."""
	args = parse_args()
	per_interval = collect_per_interval_counts(args.verdicts_glob)
	report = build_report(per_interval, args.verdicts_glob)
	write_report(args.output_path, report)
	# brief stdout summary so the user has a verdict line without opening
	# the report file
	_per_video, corpus_counts = aggregate(per_interval)
	corpus_total = sum(corpus_counts)
	if corpus_total > 0:
		corpus_mode_index = mode_bin_index(corpus_counts)
		corpus_verdict = classify_corpus_mode(corpus_mode_index)
		mode_label = bin_label(corpus_mode_index)
	else:
		corpus_verdict = "INCONCLUSIVE"
		mode_label = "(no data)"
	print(
		f"wrote {args.output_path}: files={len(per_interval)} "
		f"corpus_frames={corpus_total} corpus_mode={mode_label} "
		f"verdict={corpus_verdict}"
	)


#============================================
if __name__ == "__main__":
	main()
