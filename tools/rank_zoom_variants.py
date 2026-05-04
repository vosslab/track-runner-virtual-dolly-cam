#!/usr/bin/env python3
"""Rank encode-variant runs by zoom-bounce metric versus a baseline.

Ingests a directory containing one sub-directory per variant. Each
sub-directory must contain a pixel_zoom_comparison.csv produced by
tools/assess_pixel_zoom.py in batch mode. The baseline/ sub-directory
is required and acts as the reference.

For each variant, this tool computes:
  - per-video raw value of the chosen metric
  - per-video delta versus baseline (variant - baseline)
  - per-video percent delta (delta / baseline * 100)
  - per-video rank (1 = best variant on that video)

Then summarizes:
  - per-variant median percent delta
  - per-variant count of videos that beat baseline by --win-threshold
  - per-variant verdict: win / tie / loss

For bounce_rate_per_s (default), "lower is better". For metrics where
higher is better (e.g. valid_frame_fraction), pass --higher-is-better.

Output is a single markdown file at <directory>/RANKING.md (or the
path given by -o/--output).
"""

# Standard Library
import os
import csv
import argparse


#============================================
def parse_args() -> argparse.Namespace:
	"""
	Parse command-line arguments.
	"""
	parser = argparse.ArgumentParser(
		description="Rank encode-variant runs by a zoom-bounce metric.",
	)
	parser.add_argument(
		"-d", "--directory", dest="directory", required=True,
		help=(
			"Root directory containing one sub-directory per variant; "
			"each sub-directory has a pixel_zoom_comparison.csv."
		),
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default="",
		help="Output markdown path (default: <directory>/RANKING.md)",
	)
	parser.add_argument(
		"-m", "--metric", dest="metric", default="bounce_rate_per_s",
		help=(
			"Column name to rank on. Common choices: bounce_rate_per_s, "
			"zoom_velocity_log_p95, zoom_jerk_p95, zoom_cv. Default: "
			"bounce_rate_per_s."
		),
	)
	parser.add_argument(
		"-w", "--win-threshold", dest="win_threshold", type=float, default=0.10,
		help=(
			"Fractional improvement over baseline that counts as a win. "
			"For 'lower is better' metrics, win = variant <= baseline * "
			"(1 - threshold). Default 0.10 (10%%)."
		),
	)
	parser.add_argument(
		"-H", "--higher-is-better", dest="higher_is_better",
		action="store_true",
		help=(
			"Treat higher metric values as better (for e.g. "
			"valid_frame_fraction). Default: lower is better."
		),
	)
	parser.add_argument(
		"-b", "--baseline-name", dest="baseline_name", default="baseline",
		help="Sub-directory name of the baseline variant (default: baseline)",
	)
	args = parser.parse_args()
	return args


#============================================
def load_variant_csv(csv_path: str, metric: str) -> dict:
	"""Load a pixel_zoom_comparison.csv and return a {filename: value} dict.

	Args:
		csv_path: Path to the CSV file.
		metric: Column name whose value should be extracted.

	Returns:
		Dict mapping filename (CSV row) to float value of the metric.
	"""
	if not os.path.isfile(csv_path):
		raise RuntimeError(f"CSV not found: {csv_path}")
	rows = {}
	with open(csv_path, newline="") as f:
		reader = csv.DictReader(f)
		if reader.fieldnames is None or metric not in reader.fieldnames:
			raise RuntimeError(
				f"Metric '{metric}' not in CSV columns: {reader.fieldnames}"
			)
		for row in reader:
			filename = row["filename"]
			rows[filename] = float(row[metric])
	return rows


#============================================
def discover_variants(directory: str, baseline_name: str) -> list:
	"""List variant sub-directory names under directory, baseline first.

	Args:
		directory: Root directory.
		baseline_name: Name of the baseline sub-directory.

	Returns:
		List of variant names; baseline_name is the first element.
	"""
	if not os.path.isdir(directory):
		raise RuntimeError(f"Not a directory: {directory}")
	all_subdirs = sorted(
		entry for entry in os.listdir(directory)
		if os.path.isdir(os.path.join(directory, entry))
	)
	if baseline_name not in all_subdirs:
		raise RuntimeError(
			f"Required baseline sub-directory '{baseline_name}' not found "
			f"under {directory}. Found: {all_subdirs}"
		)
	# baseline first, then the rest in alphabetical order
	variants = [baseline_name] + [v for v in all_subdirs if v != baseline_name]
	return variants


#============================================
def compute_deltas(
	baseline_values: dict,
	variant_values: dict,
) -> dict:
	"""Compute per-video deltas of variant relative to baseline.

	Args:
		baseline_values: {filename: value} from baseline.
		variant_values: {filename: value} from variant.

	Returns:
		Dict mapping filename to (baseline_v, variant_v, delta, pct_delta).
		Filenames missing from either side are skipped (and reported by
		the caller).
	"""
	deltas = {}
	for filename, variant_v in variant_values.items():
		if filename not in baseline_values:
			continue
		baseline_v = baseline_values[filename]
		delta = variant_v - baseline_v
		# avoid division by zero on zero baseline; report 0 in that case
		if baseline_v != 0:
			pct_delta = (delta / baseline_v) * 100.0
		else:
			pct_delta = 0.0
		deltas[filename] = (baseline_v, variant_v, delta, pct_delta)
	return deltas


#============================================
def per_video_ranking(
	all_deltas: dict,
	higher_is_better: bool,
) -> dict:
	"""Compute per-video rank of each variant.

	Args:
		all_deltas: {variant_name: {filename: (b, v, d, pct)}}.
		higher_is_better: If True, higher value ranks 1.

	Returns:
		Dict mapping (variant_name, filename) to integer rank (1 = best).
	"""
	# group by filename
	rows_by_filename = {}
	for variant_name, deltas in all_deltas.items():
		for filename, (_b, variant_v, _d, _pct) in deltas.items():
			rows_by_filename.setdefault(filename, []).append(
				(variant_name, variant_v),
			)
	rank_map = {}
	for filename, entries in rows_by_filename.items():
		# sort: best first
		entries_sorted = sorted(
			entries, key=lambda kv: kv[1], reverse=higher_is_better,
		)
		for rank, (variant_name, _v) in enumerate(entries_sorted, start=1):
			rank_map[(variant_name, filename)] = rank
	return rank_map


#============================================
def variant_verdict(
	deltas: dict,
	win_threshold: float,
	higher_is_better: bool,
) -> tuple:
	"""Compute median pct delta, win count, and verdict for one variant.

	Args:
		deltas: {filename: (b, v, d, pct)} for one variant.
		win_threshold: Fractional improvement to count as a win.
		higher_is_better: If True, win = variant >= baseline * (1 + thresh).

	Returns:
		Tuple (median_pct, win_count, total_count, verdict_string).
	"""
	if not deltas:
		return (0.0, 0, 0, "no-data")
	pct_values = [pct for (_b, _v, _d, pct) in deltas.values()]
	# numpy not strictly needed here; use sorted middle for the median
	pct_sorted = sorted(pct_values)
	mid = len(pct_sorted) // 2
	if len(pct_sorted) % 2 == 0:
		median_pct = 0.5 * (pct_sorted[mid - 1] + pct_sorted[mid])
	else:
		median_pct = pct_sorted[mid]

	# count wins
	win_count = 0
	loss_count = 0
	for (_b, variant_v, _d, _pct) in deltas.values():
		baseline_v = _b
		if higher_is_better:
			win = variant_v >= baseline_v * (1.0 + win_threshold)
			loss = variant_v <= baseline_v * (1.0 - win_threshold)
		else:
			win = variant_v <= baseline_v * (1.0 - win_threshold)
			loss = variant_v >= baseline_v * (1.0 + win_threshold)
		if win:
			win_count += 1
		elif loss:
			loss_count += 1

	total_count = len(deltas)
	# verdict: win if more than half are wins, loss if more than half are
	# losses, tie otherwise
	if win_count > total_count / 2:
		verdict = "win"
	elif loss_count > total_count / 2:
		verdict = "loss"
	else:
		verdict = "tie"
	return (median_pct, win_count, total_count, verdict)


#============================================
def render_ranking_markdown(
	directory: str,
	metric: str,
	higher_is_better: bool,
	win_threshold: float,
	variants: list,
	baseline_values: dict,
	all_deltas: dict,
	rank_map: dict,
) -> str:
	"""Build the ranking markdown content.

	Args:
		directory: Corpus root.
		metric: Metric column name.
		higher_is_better: True if higher metric values are better.
		win_threshold: Fractional improvement threshold for win verdict.
		variants: List of variant names; first is baseline.
		baseline_values: {filename: value} from baseline.
		all_deltas: {variant_name: {filename: (b, v, d, pct)}}.
		rank_map: {(variant_name, filename): rank}.

	Returns:
		Markdown string.
	"""
	# build the markdown content into a variable, then return the variable
	lines = []
	lines.append(f"# Variant ranking: {os.path.basename(directory.rstrip('/'))}")
	lines.append("")
	direction = "higher is better" if higher_is_better else "lower is better"
	lines.append(f"Metric: `{metric}` ({direction})")
	lines.append(f"Win threshold: {win_threshold * 100.0:.1f}% improvement")
	lines.append(f"Baseline: `{variants[0]}/`")
	lines.append("")

	# per-variant sections
	non_baseline = variants[1:]
	for variant in non_baseline:
		lines.append(f"## {variant}")
		lines.append("")
		deltas = all_deltas[variant]
		if not deltas:
			lines.append("No videos shared with baseline; skipped.")
			lines.append("")
			continue
		lines.append(
			"| Video | Baseline | Variant | Delta | Pct delta | Rank |"
		)
		lines.append("| --- | --- | --- | --- | --- | --- |")
		# stable per-variant sort by filename for readable output
		for filename in sorted(deltas.keys()):
			baseline_v, variant_v, delta, pct = deltas[filename]
			rank = rank_map.get((variant, filename), "-")
			lines.append(
				f"| {filename} | {baseline_v:.6g} | {variant_v:.6g} | "
				f"{delta:+.6g} | {pct:+.2f}% | {rank} |"
			)
		lines.append("")

	# summary section
	lines.append("## Summary")
	lines.append("")
	lines.append(
		"| Variant | Median pct delta | Wins | Total | Verdict |"
	)
	lines.append("| --- | --- | --- | --- | --- |")
	for variant in non_baseline:
		median_pct, wins, total, verdict = variant_verdict(
			all_deltas[variant], win_threshold, higher_is_better,
		)
		lines.append(
			f"| {variant} | {median_pct:+.2f}% | {wins} | {total} | {verdict} |"
		)
	lines.append("")

	# baseline reference table for context
	lines.append("## Baseline reference")
	lines.append("")
	lines.append("| Video | Baseline value |")
	lines.append("| --- | --- |")
	for filename in sorted(baseline_values.keys()):
		lines.append(f"| {filename} | {baseline_values[filename]:.6g} |")
	lines.append("")
	report_text = "\n".join(lines)
	return report_text


#============================================
def main() -> None:
	"""
	Main entry point.
	"""
	args = parse_args()

	directory = os.path.abspath(args.directory)
	variants = discover_variants(directory, args.baseline_name)
	print(f"Discovered variants ({len(variants)}): {variants}")

	# load each variant's CSV
	per_variant_values = {}
	for variant in variants:
		csv_path = os.path.join(
			directory, variant, "pixel_zoom_comparison.csv",
		)
		per_variant_values[variant] = load_variant_csv(csv_path, args.metric)

	baseline_values = per_variant_values[variants[0]]
	if not baseline_values:
		raise RuntimeError(
			f"Baseline '{variants[0]}' has no rows in {args.metric}"
		)

	# compute deltas for each non-baseline variant
	all_deltas = {}
	for variant in variants[1:]:
		all_deltas[variant] = compute_deltas(
			baseline_values, per_variant_values[variant],
		)
		# warn about missing rows
		missing = (
			set(baseline_values.keys()) - set(per_variant_values[variant].keys())
		)
		if missing:
			print(
				f"  warning: variant {variant} missing baseline videos: "
				f"{sorted(missing)}"
			)

	# per-video rank across variants (baseline + non-baseline together)
	rank_input = {variants[0]: {
		fname: (val, val, 0.0, 0.0) for fname, val in baseline_values.items()
	}}
	for variant in variants[1:]:
		rank_input[variant] = all_deltas[variant]
	rank_map = per_video_ranking(rank_input, args.higher_is_better)

	# render markdown
	output_path = args.output_path or os.path.join(directory, "RANKING.md")
	report_text = render_ranking_markdown(
		directory, args.metric, args.higher_is_better, args.win_threshold,
		variants, baseline_values, all_deltas, rank_map,
	)
	with open(output_path, "w") as f:
		f.write(report_text)
	print(f"  wrote: {output_path}")

	# stdout summary
	print("\nSummary:")
	for variant in variants[1:]:
		median_pct, wins, total, verdict = variant_verdict(
			all_deltas[variant], args.win_threshold, args.higher_is_better,
		)
		print(
			f"  {variant}: median {median_pct:+.2f}%  "
			f"wins={wins}/{total}  verdict={verdict}"
		)


#============================================

if __name__ == "__main__":
	main()
