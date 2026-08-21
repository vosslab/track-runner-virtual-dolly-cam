"""Console and YAML reporting bridge for encode analysis."""

# Standard Library
import os

# PIP3 modules
import yaml


# Default cap shared by printed reports and analyze-to-seed targets.
ANALYZE_TOP_DEFAULT = 10

# Minimum seconds between suggested target frames.
ANALYZE_MIN_SPACING_S = 0.5

# Gap multiplier that triggers an automatic gap-coverage target.
ANALYZE_GAP_RATIO_THRESHOLD = 4.0


#============================================
def format_analysis_report(
	analysis: dict,
	solver_context: dict,
	output_yaml_path: str,
	regime_summary_line: str = "",
	canonical_source: str | None = None,
	decode_source: str | None = None,
) -> str:
	"""Format analysis results as the established console report."""
	summary = analysis["summary"]
	motion = analysis["motion_stability"]
	conf = analysis["confidence"]
	regions = analysis["instability_regions"]
	dominant = analysis["dominant_symptom"]
	seeds_suggested = analysis["seed_suggestions"]
	lines = []
	lines.append("=== crop path analysis ===")
	if canonical_source is not None:
		lines.append(f"  canonical source:   {canonical_source}")
	if decode_source is not None:
		lines.append(f"  decode source:      {decode_source}")
	lines.append(f"  frames:             {summary['frames']}"
		+ f" ({summary['duration_s']:.1f}s at {summary['fps']:.0f}fps)")
	lines.append(f"  output size:        {summary['output_size'][0]}x{summary['output_size'][1]}")
	lines.append("")
	lines.append("  motion stability:")
	lines.append(f"    center jerk:      median {motion['center_jerk_p50']} px/f"
		+ f", p95 {motion['center_jerk_p95']} px/f")
	lines.append(f"    height jerk:      median {motion['height_jerk_p50']} px/f"
		+ f", p95 {motion['height_jerk_p95']} px/f")
	lines.append(f"    crop size CV:     {motion['crop_size_cv']}")
	lines.append(f"    quant chatter:    {motion['quantization_chatter_fraction'] * 100:.1f}% of frames")
	lines.append("")
	lines.append("  confidence:")
	lines.append(f"    mean:             {conf['mean']}")
	lines.append(f"    low-conf frames:  {conf['low_conf_fraction'] * 100:.1f}%")
	lines.append("")
	lines.append("  solver context:")
	lines.append(f"    seed density:     {solver_context['seed_density']} seeds/min")
	lines.append(f"    desert count:     {solver_context['desert_count']}")
	lines.append(f"    seed gaps:        mean {solver_context['seed_gap_mean_s']}s, "
		+ f"max {solver_context['seed_gap_max_s']}s")
	lines.append(f"    velocity consistency: {solver_context['velocity_consistency_median']}")
	lines.append(f"    size consistency:     {solver_context['size_consistency_median']}")
	lines.append(f"    motion quality:      {solver_context['motion_quality_median']}")
	lines.append("")
	top_gaps = solver_context.get("top_seed_gaps") or []
	if top_gaps:
		shown_gaps = top_gaps[:ANALYZE_TOP_DEFAULT]
		lines.append(f"  largest seed gaps (top {len(shown_gaps)}):")
		for gap in shown_gaps:
			lines.append(f"    frames {gap['start_frame']}-{gap['end_frame']}: "
				+ f"{gap['gap_s']}s (midpoint {gap['midpoint_frame']})")
		lines.append("")
	if regions:
		top_regions = regions[:ANALYZE_TOP_DEFAULT]
		lines.append(f"  instability regions (top {len(top_regions)}):")
		for region in top_regions:
			lines.append(f"    frames {region['start_frame']}-{region['end_frame']}:"
				+ f" {region['cause']}"
				+ f" (conf {region['mean_confidence']}"
				+ f", jerk p95 {region['jerk_p95']})")
	else:
		lines.append("  instability regions: none detected")
	lines.append("")
	lines.append("  diagnosis:")
	lines.append(f"    dominant symptom: {dominant}")
	if regions:
		primary = regions[0]
		lines.append(f"    primary issue: {primary['cause']} (heuristic)")
		affected = primary["end_frame"] - primary["start_frame"]
		lines.append(f"    affected frames: {affected}")
	if seeds_suggested:
		frame_list = ", ".join(str(frame) for frame in seeds_suggested[:ANALYZE_TOP_DEFAULT])
		lines.append(f"    suggested seed frames: {frame_list}")
	chatter = motion["quantization_chatter_fraction"]
	if chatter > 0.03:
		lines.append("    secondary: quantization chatter in stationary sections")
		lines.append("    suggestion: crop controller subpixel smoothing")
	if regime_summary_line:
		lines.append("")
		lines.append(f"  {regime_summary_line}")
	lines.append("")
	lines.append(f"  wrote: {output_yaml_path}")
	report = "\n".join(lines)
	return report


#============================================
def load_analyze_target_frames(
	analysis_path: str,
	frame_count: int,
	top_n: int = ANALYZE_TOP_DEFAULT,
	existing_seed_frames: list = None,
	gap_top_n: int = 0,
) -> list:
	"""Read worst-ranked analysis targets for the seeding UI."""
	if not os.path.isfile(analysis_path):
		raise RuntimeError(
			f"no encode_analysis.yaml found at {analysis_path}; "
			f"run 'analyze' first"
		)
	with open(analysis_path) as f:
		doc = yaml.safe_load(f)
	if not isinstance(doc, dict):
		raise RuntimeError(f"malformed encode_analysis.yaml at {analysis_path}")
	summary = doc.get("summary") or {}
	fps = float(summary["fps"])
	min_gap_frames = int(round(ANALYZE_MIN_SPACING_S * fps))
	existing = sorted(existing_seed_frames) if existing_seed_frames else []
	kept = []
	if len(existing) >= 2:
		gaps = [(existing[i] - existing[i - 1], i) for i in range(1, len(existing))]
		max_gap, max_idx = max(gaps)
		mean_gap = sum(gap for gap, _ in gaps) / len(gaps)
		if max_gap > ANALYZE_GAP_RATIO_THRESHOLD * mean_gap and max_gap > min_gap_frames * 2:
			gap_mid = (existing[max_idx - 1] + existing[max_idx]) // 2
			if 0 <= gap_mid < frame_count:
				gap_s = max_gap / fps
				mean_s = mean_gap / fps
				print(f"  injected gap-coverage target at frame {gap_mid} "
					+ f"(gap {gap_s:.1f}s vs mean {mean_s:.1f}s, "
					+ f">{ANALYZE_GAP_RATIO_THRESHOLD:.0f}x)")
				kept.append(gap_mid)
	if gap_top_n > 0:
		top_gaps = (doc.get("solver_context") or {}).get("top_seed_gaps") or []
		for gap in top_gaps[:gap_top_n]:
			mid = int(gap["midpoint_frame"])
			if not (0 <= mid < frame_count):
				continue
			if any(abs(mid - kept_frame) < min_gap_frames for kept_frame in kept):
				continue
			kept.append(mid)
	peaks_added = 0
	for region in doc.get("instability_regions") or []:
		if peaks_added >= top_n:
			break
		start = int(region["start_frame"])
		end = int(region["end_frame"])
		if "peak_frame" in region:
			peak = int(region["peak_frame"])
		else:
			peak = (start + end) // 2
		if not (0 <= peak < frame_count):
			continue
		near_kept = any(abs(peak - kept_frame) < min_gap_frames for kept_frame in kept)
		near_seed = any(abs(peak - seed_frame) < min_gap_frames for seed_frame in existing)
		if near_kept or near_seed:
			continue
		kept.append(peak)
		peaks_added += 1
	return sorted(kept)


#============================================
def write_analysis_yaml(
	analysis: dict,
	solver_context: dict,
	output_path: str,
	regime_spans: list = None,
	canonical_source: str | None = None,
	decode_source: str | None = None,
	dolly_crop_report: dict | None = None,
) -> None:
	"""Write the established diagnostic YAML artifact."""
	doc = {"track_runner_encode_analysis": 1}
	if canonical_source is not None:
		doc["canonical_source"] = canonical_source
	if decode_source is not None:
		doc["decode_source"] = decode_source
	if dolly_crop_report is not None:
		doc["dolly_crop_report"] = dolly_crop_report
	doc.update({
		"summary": analysis["summary"],
		"motion_stability": analysis["motion_stability"],
		"confidence": analysis["confidence"],
		"instability_regions": analysis["instability_regions"],
		"dominant_symptom": analysis["dominant_symptom"],
		"solver_context": solver_context,
		"seed_suggestions": analysis["seed_suggestions"],
	})
	regions = analysis["instability_regions"]
	diagnosis = {}
	if regions:
		primary = regions[0]
		diagnosis["primary_issue"] = f"{primary['cause']} (heuristic)"
		diagnosis["affected_frames"] = primary["end_frame"] - primary["start_frame"]
		diagnosis["suggestion_method"] = "instability_region_max_frame"
	chatter = analysis["motion_stability"]["quantization_chatter_fraction"]
	if chatter > 0.03:
		diagnosis["secondary_issue"] = "quantization_chatter (heuristic)"
		diagnosis["suggestion_secondary"] = "crop controller subpixel smoothing"
	if diagnosis:
		doc["diagnosis"] = diagnosis
	if regime_spans:
		total_frames = analysis["summary"]["frames"]
		regime_counts = {}
		for span in regime_spans:
			regime = span["regime"]
			span_len = span["end_frame"] - span["start_frame"]
			regime_counts[regime] = regime_counts.get(regime, 0) + span_len
		regime_pcts = {}
		for regime, count in regime_counts.items():
			regime_pcts[regime] = round(100.0 * count / total_frames, 1)
		doc["regime_summary"] = {
			"frame_percentages": regime_pcts,
			"num_transitions": max(0, len(regime_spans) - 1),
			"spans": regime_spans,
		}
	parent_dir = os.path.dirname(os.path.abspath(output_path))
	os.makedirs(parent_dir, exist_ok=True)
	with open(output_path, "w") as f:
		f.write("# auto-generated by track_runner analyze\n")
		f.write("# this is a diagnostic report, not an encode settings file\n")
		yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)
