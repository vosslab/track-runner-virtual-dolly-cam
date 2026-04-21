#!/usr/bin/env python3
"""Print metric distributions, tier breakdown, and failure-reason counts
from a track_runner interval-scores JSON. Intended for investigating
why intervals are scored the way they are.

Usage:
	source source_me.sh && python tools/inspect_score_distribution.py \\
		<path-to-interval_scores.json>

If a sibling `.agreement_debug.json` sidecar is present, extra per-frame
correlation stats are printed too.
"""

# Standard Library
import os
import json
import argparse
from collections import Counter


#============================================
def parse_args():
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Inspect track runner diagnostics score distribution.",
	)
	parser.add_argument(
		"-i", "--input", dest="diag_path", required=True,
		help="Path to track_runner diagnostics JSON file.",
	)
	return parser.parse_args()


#============================================
def percentile(values: list, p: float) -> float:
	"""Return the p-percentile of a list using linear interpolation."""
	if not values:
		return 0.0
	sorted_vals = sorted(values)
	k = (len(sorted_vals) - 1) * p
	f = int(k)
	c = min(f + 1, len(sorted_vals) - 1)
	return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


#============================================
def histogram(values: list, bins: int = 10) -> list:
	"""Return bin-edge + count pairs for a simple uniform histogram."""
	if not values:
		return []
	lo = min(values)
	hi = max(values)
	if hi == lo:
		# degenerate distribution
		return [(lo, hi, len(values))]
	width = (hi - lo) / bins
	counts = [0] * bins
	for v in values:
		idx = min(bins - 1, int((v - lo) / width))
		counts[idx] += 1
	return [
		(lo + i * width, lo + (i + 1) * width, counts[i])
		for i in range(bins)
	]


#============================================
def print_histogram(label: str, values: list) -> None:
	"""Print a text histogram for a metric distribution."""
	print(f"\n  {label}:")
	if not values:
		print("    (no data)")
		return
	hist = histogram(values, bins=10)
	total = len(values)
	max_count = max(c for _, _, c in hist) if hist else 0
	for lo, hi, count in hist:
		pct = (count / total) * 100 if total else 0
		bar_width = int(40 * count / max_count) if max_count else 0
		bar = "#" * bar_width + "." * (40 - bar_width)
		print(f"    [{lo:.3f} - {hi:.3f}]  {bar}  {count:4d}  ({pct:5.1f}%)")


#============================================
def print_summary_stats(label: str, values: list) -> None:
	"""Print min/p10/p25/median/p75/p90/max summary for a metric."""
	if not values:
		return
	print(
		f"    {label:24s}  min={min(values):.3f}  "
		f"p10={percentile(values, 0.10):.3f}  "
		f"p25={percentile(values, 0.25):.3f}  "
		f"median={percentile(values, 0.50):.3f}  "
		f"p75={percentile(values, 0.75):.3f}  "
		f"p90={percentile(values, 0.90):.3f}  "
		f"max={max(values):.3f}"
	)


#============================================
def pearson(xs: list, ys: list) -> float:
	"""Pearson correlation between two equal-length lists."""
	n = len(xs)
	if n == 0 or n != len(ys):
		return 0.0
	mx = sum(xs) / n
	my = sum(ys) / n
	num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
	dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
	dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
	if dx * dy == 0:
		return 0.0
	return num / (dx * dy)


#============================================
def analyze_diagnostics(diag_path: str) -> dict:
	"""Parse the diagnostics JSON and print metric/tier/failure breakdown."""
	with open(diag_path) as f:
		diagnostics = json.load(f)
	intervals = diagnostics.get("intervals", [])
	if not intervals:
		print("  (no intervals in diagnostics)")
		return diagnostics
	n = len(intervals)
	print(f"  total intervals: {n}")
	# detect schema: analytical interval_score dict or legacy flat keys
	sample = intervals[0]
	nested_schema = (
		"interval_score" in sample
		and isinstance(sample["interval_score"], dict)
	)
	def get(iv: dict, k: str):
		if nested_schema:
			return iv["interval_score"].get(k)
		return iv.get(k)
	# collect metrics, tiers, severities, failures
	metrics = {
		"agreement": [],
		"velocity_consistency": [],
		"size_consistency": [],
		"motion_quality": [],
		"occlusion_fraction": [],
	}
	tiers = Counter()
	severities = Counter()
	failures = Counter()
	lens = []
	for iv in intervals:
		for key in metrics:
			val = get(iv, key)
			if val is not None:
				metrics[key].append(float(val))
		tier = get(iv, "confidence_tier") or get(iv, "confidence")
		if tier:
			tiers[tier] += 1
		sev = get(iv, "severity")
		if sev:
			severities[sev] += 1
		reasons = get(iv, "failure_reasons") or []
		for r in reasons:
			failures[r] += 1
		lens.append(int(iv["end_frame"]) - int(iv["start_frame"]))

	print("\n  tier distribution:")
	for tier in ("high", "good", "fair", "low"):
		c = tiers.get(tier, 0)
		pct = 100 * c / n if n else 0
		print(f"    {tier:6s}  {c:4d}  ({pct:5.1f}%)")
	print("\n  severity distribution:")
	for sev in ("low", "medium", "high"):
		c = severities.get(sev, 0)
		pct = 100 * c / n if n else 0
		print(f"    {sev:6s}  {c:4d}  ({pct:5.1f}%)")

	print("\n  metric percentile summary:")
	for key in metrics:
		print_summary_stats(key, metrics[key])
	print_summary_stats("interval_length_frames", lens)

	for key in metrics:
		if metrics[key]:
			print_histogram(key, metrics[key])

	print("\n  failure reasons (interval-level):")
	if failures:
		for reason, count in failures.most_common():
			pct = 100 * count / n
			print(f"    {reason:30s}  {count:4d}  ({pct:5.1f}%)")
	else:
		print("    (no failure reasons recorded)")

	# useful counterfactuals
	ag = metrics["agreement"]
	vc = metrics["velocity_consistency"]
	if ag and vc and len(ag) == len(vc):
		print("\n  metric correlations:")
		print(f"    agreement vs velocity_consistency:  {pearson(ag, vc):+.3f}")
		# rescueable: good agreement dragged down by low vc
		rescue_05 = sum(1 for i in range(len(ag)) if ag[i] > 0.5 and vc[i] < 0.3)
		rescue_03 = sum(1 for i in range(len(ag)) if ag[i] > 0.3 and vc[i] < 0.3)
		print("\n  counterfactual (would-be-good intervals blocked by vc):")
		print(f"    agreement > 0.5 AND vc < 0.3:  {rescue_05}")
		print(f"    agreement > 0.3 AND vc < 0.3:  {rescue_03}")

	return diagnostics


#============================================
def analyze_sidecar(sidecar_path: str) -> None:
	"""Print per-frame agreement correlation stats from the debug sidecar."""
	with open(sidecar_path) as f:
		payload = json.load(f)
	intervals = payload.get("intervals", [])
	if not intervals:
		print("  sidecar has no intervals")
		return
	# aggregate per-frame records across all intervals
	ious = []
	cdists = []
	srat_w = []
	srat_h = []
	for iv in intervals:
		for rec in iv.get("per_frame", []):
			ious.append(float(rec["iou"]))
			cdists.append(float(rec["center_dist_px"]))
			srat_w.append(float(rec["size_ratio_w"]))
			srat_h.append(float(rec["size_ratio_h"]))

	n = len(ious)
	if n == 0:
		print("  sidecar has no per-frame records")
		return
	print(f"\n  agreement debug sidecar: {len(intervals)} intervals, {n} frames")
	print(f"    iou:             median={percentile(ious, 0.5):.3f}  "
		f"p10={percentile(ious, 0.1):.3f}  p90={percentile(ious, 0.9):.3f}")
	print(f"    center_dist_px:  median={percentile(cdists, 0.5):.2f}  "
		f"p90={percentile(cdists, 0.9):.2f}")
	print(f"    size_ratio_w:    median={percentile(srat_w, 0.5):.3f}  "
		f"p90={percentile(srat_w, 0.9):.3f}")
	print(f"    size_ratio_h:    median={percentile(srat_h, 0.5):.3f}  "
		f"p90={percentile(srat_h, 0.9):.3f}")
	print("\n  per-frame correlations (what drives low IoU?):")
	print(f"    iou vs center_dist_px:  {pearson(ious, cdists):+.3f}  "
		f"(negative = center offset explains low IoU)")
	print(f"    iou vs size_ratio_w:    {pearson(ious, srat_w):+.3f}  "
		f"(negative = width mismatch explains low IoU)")
	print(f"    iou vs size_ratio_h:    {pearson(ious, srat_h):+.3f}  "
		f"(negative = height mismatch explains low IoU)")

	# frames where IoU is bad but center is close -> size/alignment artifact
	bad_iou_close_center = sum(
		1 for i in range(n)
		if ious[i] < 0.3
		and cdists[i] < 10.0  # within 10 px of each other
	)
	print(
		f"\n  frames with iou<0.3 but center<10px: {bad_iou_close_center} "
		f"({100*bad_iou_close_center/n:.1f}% of frames)  "
		"-- if large, size mismatch is the cause, not center offset"
	)


#============================================
def main():
	"""Entry point."""
	args = parse_args()
	diag_path = args.diag_path
	if not os.path.isfile(diag_path):
		raise RuntimeError(f"diagnostics file not found: {diag_path}")
	print(f"inspecting: {diag_path}")
	analyze_diagnostics(diag_path)
	# look for matching agreement_debug sidecar
	sidecar_path = diag_path.replace(
		".interval_scores.json", ".agreement_debug.json",
	)
	if os.path.isfile(sidecar_path):
		print(f"\nsidecar found: {sidecar_path}")
		analyze_sidecar(sidecar_path)
	else:
		print(
			"\n  no agreement_debug sidecar found "
			"(run solve with --debug to produce one)"
		)


#============================================
if __name__ == "__main__":
	main()
