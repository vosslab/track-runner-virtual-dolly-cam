#!/usr/bin/env python3
"""Test 6 (WP-2C): centroid-bias analyzer for blob refinement audit.

Reads the leave-one-out (LOO) oracle CSV produced by WP-1A
(tools/seed_oracle_blob_audit.py) and decides:

- H_CENTROID_BIAS (plan Test 6, supports policy E):
	On centroid-far frames (loo_winner_dist_h > 0.6) inside the TRUST 0%
	subset, count those whose winner mask overlaps the seed component
	region by >= 50%. SUPPORTED corpus-wide when that rate is >= 40% AND
	at least 6 of 12 videos hit >= 40%.

- AC-2d (policy E oracle-correctness on TRUST 0%):
	For every centroid-far oracle row on the TRUST 0% subset, compute the
	policy-E shifted centroid `(loo_winner + raw_pred_loo) / 2` and the
	per-row delta:
		dist(shifted, seed_torso) - dist(loo_winner, seed_torso).
	PASS when the corpus-mean delta is strictly < 0 (the shift moves the
	centroid CLOSER to the seed torso on average).

- AC-2e (policy E oracle-correctness on positive controls):
	Same shifted-centroid delta restricted to positive-control oracle
	rows. PASS when the corpus-mean delta is strictly < 0. Guards against
	a shift that helps on TRUST 0% but hurts on positive controls.

Outputs:

- Markdown report (--output) with per-video plus corpus aggregate tables
	and AC-2d / AC-2e verdicts.
- Sidecar JSON (--gates-json) containing the policy-E inclusion decision
	consumed by WP-3A (counterfactual_gate_sim.py). Schema:
		{
		  "h1_supported_corpus": bool,
		  "h1_supported_videos_count": int,
		  "oracle_correctness_trust_0_pass": bool,
		  "oracle_correctness_pos_ctrl_pass": bool,
		  "policy_e_eligible": bool
		}
	`policy_e_eligible` is the AND of:
		h1_supported_corpus AND h1_supported_videos_count >= 6 AND
		oracle_correctness_trust_0_pass AND
		oracle_correctness_pos_ctrl_pass.

This tool does not run on real oracle data in this WP; fixture-driven
pytest in tests/test_centroid_bias.py exercises bucket assignment and
sidecar JSON schema.
"""

# Standard Library
import os
import json
import math
import argparse
from pathlib import Path

# PIP3 modules
import pandas


#============================================
# Plan thresholds
#============================================

# Centroid-far frame is loo_winner more than 0.6 * h_seed from seed torso
CENTROID_FAR_DIST_H = 0.6

# Mask-overlap signature for H1 / H_CENTROID_BIAS (plan Test 6)
OVERLAP_HIGH_THRESHOLD = 0.5

# H_CENTROID_BIAS support thresholds (plan Test 6 and task spec)
H1_RATE_THRESHOLD = 0.40
H1_VIDEO_COUNT_THRESHOLD = 6


#============================================
# Geometry helpers
#============================================

def _euclidean_distance(cx1: float, cy1: float, cx2: float, cy2: float) -> float:
	"""Return Euclidean distance between two points in pixels."""
	# Pandas NA / None / NaN propagate to nan
	if pandas.isna(cx1) or pandas.isna(cy1) or pandas.isna(cx2) or pandas.isna(cy2):
		return float("nan")
	dx = float(cx1) - float(cx2)
	dy = float(cy1) - float(cy2)
	return math.sqrt(dx * dx + dy * dy)


def _shifted_centroid(loo_cx: float, loo_cy: float, raw_cx: float, raw_cy: float) -> tuple:
	"""Compute policy-E shifted centroid as midpoint of LOO winner and raw_pred."""
	if pandas.isna(loo_cx) or pandas.isna(loo_cy) or pandas.isna(raw_cx) or pandas.isna(raw_cy):
		return (float("nan"), float("nan"))
	shifted_cx = (float(loo_cx) + float(raw_cx)) / 2.0
	shifted_cy = (float(loo_cy) + float(raw_cy)) / 2.0
	return (shifted_cx, shifted_cy)


#============================================
# Centroid-bias filtering and counting
#============================================

def _annotate_centroid_far(df: pandas.DataFrame) -> pandas.DataFrame:
	"""Add a centroid_far boolean column and an overlap_high boolean column."""
	# Copy so caller's df is untouched
	annotated = df.copy()
	# Centroid-far flag: LOO winner sits more than 0.6 * h_seed from seed torso
	annotated["centroid_far"] = annotated["loo_winner_dist_h"] > CENTROID_FAR_DIST_H
	# Overlap-high flag: winner mask overlaps seed component region by >= 50%
	annotated["overlap_high"] = annotated["loo_winner_mask_overlap_frac"] >= OVERLAP_HIGH_THRESHOLD
	return annotated


def _build_cross_tab(df_subset: pandas.DataFrame) -> dict:
	"""Return cross-tab counts of overlap_high vs centroid_far on the subset.

	The returned dict has the four bucket counts and the totals needed by
	the markdown report.
	"""
	overlap_high_far = int(((df_subset["overlap_high"]) & (df_subset["centroid_far"])).sum())
	overlap_high_near = int(((df_subset["overlap_high"]) & (~df_subset["centroid_far"])).sum())
	overlap_low_far = int(((~df_subset["overlap_high"]) & (df_subset["centroid_far"])).sum())
	overlap_low_near = int(((~df_subset["overlap_high"]) & (~df_subset["centroid_far"])).sum())
	total = overlap_high_far + overlap_high_near + overlap_low_far + overlap_low_near
	return {
		"overlap_high_far": overlap_high_far,
		"overlap_high_near": overlap_high_near,
		"overlap_low_far": overlap_low_far,
		"overlap_low_near": overlap_low_near,
		"total": total,
	}


def _h1_rate_for_subset(df_subset: pandas.DataFrame) -> tuple:
	"""Return (rate, hits, total) for the H_CENTROID_BIAS signature on a subset.

	The numerator counts rows that are both centroid-far AND overlap-high
	(the limb-centroid-bias signature). The denominator is the row count
	of the subset (typically the TRUST 0% subset corpus-wide or per-video).
	"""
	total = int(len(df_subset))
	if total == 0:
		return (0.0, 0, 0)
	hits = int(((df_subset["overlap_high"]) & (df_subset["centroid_far"])).sum())
	rate = hits / total
	return (rate, hits, total)


#============================================
# AC-2d / AC-2e shifted-centroid delta
#============================================

def _compute_shifted_delta_series(df_subset: pandas.DataFrame) -> pandas.Series:
	"""Compute the per-row policy-E shifted-centroid delta on a subset.

	Delta is:
		dist(shifted, seed_torso) - dist(loo_winner, seed_torso),
	where `shifted = (loo_winner + raw_pred_loo) / 2`. Distances are in
	raw pixels (the AC-2d / AC-2e gates only need the sign of the mean).
	"""
	deltas = []
	for _, row in df_subset.iterrows():
		shifted_cx, shifted_cy = _shifted_centroid(
			row["loo_winner_cx"], row["loo_winner_cy"],
			row["raw_pred_loo_cx"], row["raw_pred_loo_cy"],
		)
		dist_shifted = _euclidean_distance(
			shifted_cx, shifted_cy,
			row["seed_torso_cx"], row["seed_torso_cy"],
		)
		dist_winner = _euclidean_distance(
			row["loo_winner_cx"], row["loo_winner_cy"],
			row["seed_torso_cx"], row["seed_torso_cy"],
		)
		# Either distance may be NaN if the row is missing centroid data
		if math.isnan(dist_shifted) or math.isnan(dist_winner):
			deltas.append(float("nan"))
			continue
		deltas.append(dist_shifted - dist_winner)
	return pandas.Series(deltas, dtype=float)


def _evaluate_oracle_correctness(df_subset: pandas.DataFrame) -> dict:
	"""Run the AC-2d / AC-2e mean-delta check on a TRUST 0% or pos-ctrl subset.

	Returns a dict with row count, valid-delta count, mean delta, and the
	PASS / FAIL verdict (PASS when mean delta is strictly < 0).
	"""
	if len(df_subset) == 0:
		return {
			"n_rows": 0,
			"n_valid_deltas": 0,
			"mean_delta_px": float("nan"),
			"pass": False,
			"reason": "no centroid-far rows in subset",
		}
	deltas = _compute_shifted_delta_series(df_subset)
	valid = deltas.dropna()
	if len(valid) == 0:
		return {
			"n_rows": int(len(df_subset)),
			"n_valid_deltas": 0,
			"mean_delta_px": float("nan"),
			"pass": False,
			"reason": "no rows had complete centroid + raw_pred data",
		}
	mean_delta = float(valid.mean())
	verdict_pass = mean_delta < 0.0
	return {
		"n_rows": int(len(df_subset)),
		"n_valid_deltas": int(len(valid)),
		"mean_delta_px": mean_delta,
		"pass": verdict_pass,
		"reason": "" if verdict_pass else "mean delta >= 0 (shift did not move centroid closer)",
	}


#============================================
# Per-video aggregation
#============================================

def _summarize_per_video(df_annotated: pandas.DataFrame, tier_columns_present: bool) -> dict:
	"""Build per-video summary for H_CENTROID_BIAS and shifted-delta stats.

	For each video the summary records:
	- TRUST 0% row count, hit count, hit rate, h1_supported flag.
	- Cross-tab on the centroid-far subset.
	- Mean shifted-delta on the centroid-far TRUST 0% subset and the
	  centroid-far positive-control subset.

	When `tier_columns_present` is False the per-video TRUST 0% subset
	degrades to "all rows for this video", and the positive-control
	subset is empty. AC-2d / AC-2e mean-delta columns then read n/a.
	"""
	per_video = {}
	for video_name in sorted(df_annotated["video"].unique()):
		video_df = df_annotated[df_annotated["video"] == video_name]
		if tier_columns_present:
			trust_0_df = video_df[video_df["is_trust_0_interval"]]
			pos_ctrl_df = video_df[video_df["is_pos_control_interval"]]
		else:
			# Degraded mode: H1 numerator covers every video row
			trust_0_df = video_df
			pos_ctrl_df = video_df.iloc[0:0]
		rate, hits, total = _h1_rate_for_subset(trust_0_df)
		# Centroid-far subsets feed AC-2d / AC-2e per-video deltas
		trust_0_far = trust_0_df[trust_0_df["centroid_far"]]
		pos_ctrl_far = pos_ctrl_df[pos_ctrl_df["centroid_far"]]
		trust_0_far_deltas = _compute_shifted_delta_series(trust_0_far).dropna()
		pos_ctrl_far_deltas = _compute_shifted_delta_series(pos_ctrl_far).dropna()
		per_video[video_name] = {
			"trust_0_total": total,
			"trust_0_hits": hits,
			"trust_0_rate": rate,
			"trust_0_supported": rate >= H1_RATE_THRESHOLD,
			"trust_0_cross_tab": _build_cross_tab(trust_0_df),
			"trust_0_far_n": int(len(trust_0_far_deltas)),
			"trust_0_far_mean_delta_px": (
				float(trust_0_far_deltas.mean()) if len(trust_0_far_deltas) > 0 else float("nan")
			),
			"pos_ctrl_far_n": int(len(pos_ctrl_far_deltas)),
			"pos_ctrl_far_mean_delta_px": (
				float(pos_ctrl_far_deltas.mean()) if len(pos_ctrl_far_deltas) > 0 else float("nan")
			),
		}
	return per_video


#============================================
# Top-level analyzer
#============================================

def analyze_centroid_bias(df: pandas.DataFrame) -> dict:
	"""Run the full WP-2C analysis and return a single result dict.

	The result includes corpus + per-video H_CENTROID_BIAS counts, the
	AC-2d / AC-2e PASS / FAIL verdicts, and the sidecar JSON payload.

	When the optional interval-tier columns (`is_trust_0_interval`,
	`is_pos_control_interval`) are absent from the oracle CSV, the tool
	degrades gracefully: H_CENTROID_BIAS is computed corpus-wide on ALL
	rows, AC-2d / AC-2e are skipped, and the sidecar JSON flags policy E
	as not eligible with explicit blocked-reason strings. Both tier
	columns are produced by WP-1B (interval_scores.json + sweep-derived
	blob_accept); until that work ships, oracle.csv lacks them.
	"""
	df_annotated = _annotate_centroid_far(df)
	# Detect missing optional interval-tier columns (WP-1B output)
	has_trust_0_col = "is_trust_0_interval" in df_annotated.columns
	has_pos_ctrl_col = "is_pos_control_interval" in df_annotated.columns
	tier_columns_present = has_trust_0_col and has_pos_ctrl_col
	if tier_columns_present:
		# Corpus-wide TRUST 0% and positive-control subsets
		trust_0_df = df_annotated[df_annotated["is_trust_0_interval"]]
		pos_ctrl_df = df_annotated[df_annotated["is_pos_control_interval"]]
	else:
		# Degraded mode: compute H1 on ALL rows, skip AC-2d / AC-2e
		trust_0_df = df_annotated
		pos_ctrl_df = df_annotated.iloc[0:0]
	corpus_rate, corpus_hits, corpus_total = _h1_rate_for_subset(trust_0_df)
	h1_supported_corpus = corpus_rate >= H1_RATE_THRESHOLD
	# Per-video summary feeds the "6 of 12 videos at >= 40%" gate
	per_video = _summarize_per_video(df_annotated, tier_columns_present)
	supported_videos_count = sum(
		1 for v in per_video.values() if v["trust_0_supported"]
	)
	# Corpus-wide cross-tab for the markdown narrative
	corpus_cross_tab = _build_cross_tab(trust_0_df)
	# AC-2d / AC-2e shifted-delta evaluations on centroid-far rows only
	if tier_columns_present:
		trust_0_far = trust_0_df[trust_0_df["centroid_far"]]
		pos_ctrl_far = pos_ctrl_df[pos_ctrl_df["centroid_far"]]
		ac_2d = _evaluate_oracle_correctness(trust_0_far)
		ac_2e = _evaluate_oracle_correctness(pos_ctrl_far)
	else:
		ac_2d = None
		ac_2e = None
	# H_CENTROID_BIAS verdict label for the markdown report
	if h1_supported_corpus and supported_videos_count >= H1_VIDEO_COUNT_THRESHOLD:
		h1_verdict = "SUPPORTED"
	elif corpus_rate < H1_RATE_THRESHOLD * 0.5:
		h1_verdict = "REFUTED"
	else:
		h1_verdict = "INCONCLUSIVE"
	if tier_columns_present:
		policy_e_eligible = bool(
			h1_supported_corpus
			and supported_videos_count >= H1_VIDEO_COUNT_THRESHOLD
			and ac_2d["pass"]
			and ac_2e["pass"]
		)
		gates_payload = {
			"h1_supported_corpus": bool(h1_supported_corpus),
			"h1_supported_videos_count": int(supported_videos_count),
			"oracle_correctness_trust_0_pass": bool(ac_2d["pass"]),
			"oracle_correctness_pos_ctrl_pass": bool(ac_2e["pass"]),
			"policy_e_eligible": policy_e_eligible,
		}
	else:
		# Degraded sidecar: AC-2d / AC-2e unknown, policy E not eligible
		gates_payload = {
			"h1_supported_corpus": bool(h1_supported_corpus),
			"h1_supported_videos_count": int(supported_videos_count),
			"oracle_correctness_trust_0_pass": None,
			"oracle_correctness_pos_ctrl_pass": None,
			"policy_e_eligible": False,
			"ac_2d_blocked_reason": (
				"is_trust_0_interval column missing from oracle.csv;"
				" requires WP-1B sweep verdicts to classify intervals"
			),
			"ac_2e_blocked_reason": (
				"is_pos_control_interval column missing; same blocker"
			),
		}
	result = {
		"corpus": {
			"trust_0_total": corpus_total,
			"trust_0_hits": corpus_hits,
			"trust_0_rate": corpus_rate,
			"trust_0_cross_tab": corpus_cross_tab,
		},
		"per_video": per_video,
		"supported_videos_count": int(supported_videos_count),
		"h1_verdict": h1_verdict,
		"ac_2d": ac_2d,
		"ac_2e": ac_2e,
		"gates": gates_payload,
		"tier_columns_present": tier_columns_present,
	}
	return result


#============================================
# Markdown rendering
#============================================

def _fmt_rate(rate: float) -> str:
	"""Format a rate as percentage with one decimal place."""
	if math.isnan(rate):
		return "n/a"
	return f"{rate * 100.0:.1f}%"


def _fmt_delta(delta_px: float) -> str:
	"""Format a pixel-distance delta with sign and two decimal places."""
	if math.isnan(delta_px):
		return "n/a"
	return f"{delta_px:+.2f}"


def render_markdown_report(result: dict) -> str:
	"""Render the WP-2C markdown report from the analyzer result dict."""
	lines = []
	lines.append("# Centroid-bias test (WP-2C, plan Test 6)")
	lines.append("")
	lines.append("This report consumes the leave-one-out oracle CSV and decides")
	lines.append("policy-E inclusion for the M3 counterfactual sweep. Three gates:")
	lines.append("H_CENTROID_BIAS (Test 6), AC-2d (TRUST 0% oracle-correctness),")
	lines.append("AC-2e (positive-control oracle-correctness). The policy-E")
	lines.append("inclusion decision is the AND of all three plus the per-video")
	lines.append("quorum of 6 of 12 videos at >= 40%.")
	lines.append("")

	# H_CENTROID_BIAS corpus verdict
	corpus = result["corpus"]
	lines.append("## H_CENTROID_BIAS verdict")
	lines.append("")
	lines.append(f"- Corpus verdict: **{result['h1_verdict']}**")
	lines.append(
		f"- Corpus rate on TRUST 0%: {_fmt_rate(corpus['trust_0_rate'])}"
		f" ({corpus['trust_0_hits']} / {corpus['trust_0_total']})"
	)
	lines.append(
		f"- Videos supporting H_CENTROID_BIAS (>= 40% per video):"
		f" {result['supported_videos_count']}"
		f" (gate requires >= {H1_VIDEO_COUNT_THRESHOLD})"
	)
	lines.append("")

	# Corpus-wide cross-tab
	cross = corpus["trust_0_cross_tab"]
	lines.append("## Corpus cross-tab on TRUST 0% (overlap vs centroid-far)")
	lines.append("")
	lines.append("| Bucket | Count |")
	lines.append("| --- | --- |")
	lines.append(f"| overlap >= 0.5 AND centroid-far (> 0.6 h) | {cross['overlap_high_far']} |")
	lines.append(f"| overlap >= 0.5 AND centroid-near | {cross['overlap_high_near']} |")
	lines.append(f"| overlap < 0.5 AND centroid-far | {cross['overlap_low_far']} |")
	lines.append(f"| overlap < 0.5 AND centroid-near | {cross['overlap_low_near']} |")
	lines.append(f"| total TRUST 0% rows | {cross['total']} |")
	lines.append("")

	# Per-video table
	lines.append("## Per-video summary")
	lines.append("")
	lines.append("| Video | TRUST 0% n | hits | rate | supported |"
		" TRUST 0% far n | TRUST 0% mean delta px |"
		" pos-ctrl far n | pos-ctrl mean delta px |")
	lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
	for video_name, stats in result["per_video"].items():
		supported_flag = "YES" if stats["trust_0_supported"] else "NO"
		lines.append(
			f"| {video_name}"
			f" | {stats['trust_0_total']}"
			f" | {stats['trust_0_hits']}"
			f" | {_fmt_rate(stats['trust_0_rate'])}"
			f" | {supported_flag}"
			f" | {stats['trust_0_far_n']}"
			f" | {_fmt_delta(stats['trust_0_far_mean_delta_px'])}"
			f" | {stats['pos_ctrl_far_n']}"
			f" | {_fmt_delta(stats['pos_ctrl_far_mean_delta_px'])} |"
		)
	lines.append("")

	# AC-2d / AC-2e mean-delta tables (skipped when tier columns absent)
	ac_2d = result["ac_2d"]
	ac_2e = result["ac_2e"]
	tier_columns_present = result["tier_columns_present"]
	if tier_columns_present:
		lines.append("## AC-2d: shifted-centroid mean delta on TRUST 0%")
		lines.append("")
		lines.append("Per-row delta = dist(shifted, seed_torso) - dist(loo_winner, seed_torso),")
		lines.append("where shifted = (loo_winner + raw_pred_loo) / 2. PASS when mean < 0.")
		lines.append("")
		lines.append("| Centroid-far rows | rows with valid delta | mean delta (px) | verdict |")
		lines.append("| --- | --- | --- | --- |")
		verdict_2d = "PASS" if ac_2d["pass"] else "FAIL"
		lines.append(
			f"| {ac_2d['n_rows']} | {ac_2d['n_valid_deltas']}"
			f" | {_fmt_delta(ac_2d['mean_delta_px'])} | {verdict_2d} |"
		)
		if ac_2d["reason"]:
			lines.append("")
			lines.append(f"Note: {ac_2d['reason']}")
		lines.append("")

		lines.append("## AC-2e: shifted-centroid mean delta on positive controls")
		lines.append("")
		lines.append("Same per-row delta restricted to positive-control rows. PASS when mean < 0.")
		lines.append("")
		lines.append("| Centroid-far rows | rows with valid delta | mean delta (px) | verdict |")
		lines.append("| --- | --- | --- | --- |")
		verdict_2e = "PASS" if ac_2e["pass"] else "FAIL"
		lines.append(
			f"| {ac_2e['n_rows']} | {ac_2e['n_valid_deltas']}"
			f" | {_fmt_delta(ac_2e['mean_delta_px'])} | {verdict_2e} |"
		)
		if ac_2e["reason"]:
			lines.append("")
			lines.append(f"Note: {ac_2e['reason']}")
		lines.append("")
	else:
		# Degraded-mode BLOCKED notice in place of AC-2d / AC-2e tables
		lines.append("## BLOCKED on interval-tier columns")
		lines.append("")
		lines.append("The oracle CSV is missing the interval-tier classification columns")
		lines.append("`is_trust_0_interval` and `is_pos_control_interval`. These columns")
		lines.append("are produced by WP-1B (interval_scores.json combined with")
		lines.append("sweep-derived blob_accept verdicts). Until WP-1B ships, AC-2d and")
		lines.append("AC-2e cannot be evaluated, and policy E is reported as not eligible.")
		lines.append("")
		lines.append("Degraded behavior in this report:")
		lines.append("")
		lines.append("- H_CENTROID_BIAS is computed corpus-wide on ALL rows (not just TRUST 0%).")
		lines.append("- AC-2d (TRUST 0% oracle-correctness) is skipped entirely.")
		lines.append("- AC-2e (positive-control oracle-correctness) is skipped entirely.")
		lines.append("- Sidecar JSON reports `policy_e_eligible: false` with blocked-reason strings.")
		lines.append("")

	# Policy-E inclusion decision
	gates = result["gates"]
	lines.append("## Policy-E inclusion decision")
	lines.append("")
	lines.append("Policy E is included in the M3 evaluation set only when all of the")
	lines.append("following are true: H_CENTROID_BIAS supported corpus-wide AND in at")
	lines.append("least 6 of 12 videos AND AC-2d PASS AND AC-2e PASS.")
	lines.append("")
	lines.append("| Gate | Value |")
	lines.append("| --- | --- |")
	lines.append(f"| h1_supported_corpus | {gates['h1_supported_corpus']} |")
	lines.append(f"| h1_supported_videos_count | {gates['h1_supported_videos_count']} |")
	lines.append(f"| oracle_correctness_trust_0_pass | {gates['oracle_correctness_trust_0_pass']} |")
	lines.append(f"| oracle_correctness_pos_ctrl_pass | {gates['oracle_correctness_pos_ctrl_pass']} |")
	lines.append(f"| policy_e_eligible | {gates['policy_e_eligible']} |")
	if "ac_2d_blocked_reason" in gates:
		lines.append(f"| ac_2d_blocked_reason | {gates['ac_2d_blocked_reason']} |")
	if "ac_2e_blocked_reason" in gates:
		lines.append(f"| ac_2e_blocked_reason | {gates['ac_2e_blocked_reason']} |")
	lines.append("")

	report = "\n".join(lines) + "\n"
	return report


#============================================
# I/O wrappers
#============================================

def write_markdown_report(report_text: str, output_path: str) -> None:
	"""Write the markdown report to disk, creating parent dirs as needed."""
	parent = Path(output_path).parent
	if str(parent) and not parent.exists():
		os.makedirs(parent, exist_ok=True)
	with open(output_path, "w") as f:
		f.write(report_text)


def write_gates_json(gates_payload: dict, gates_json_path: str) -> None:
	"""Write the policy-E gates sidecar JSON to disk."""
	parent = Path(gates_json_path).parent
	if str(parent) and not parent.exists():
		os.makedirs(parent, exist_ok=True)
	with open(gates_json_path, "w") as f:
		json.dump(gates_payload, f, indent=2, sort_keys=True)


def default_gates_json_path(oracle_csv_path: str) -> str:
	"""Default the gates JSON to analyses/policy_e_gates.json next to oracle.csv.

	The plan specifies a sibling `analyses/` directory under the same run
	root as the `oracle/` dir holding oracle.csv. The default resolves to
	`<run_root>/analyses/policy_e_gates.json` when oracle.csv is at the
	expected `<run_root>/oracle/oracle.csv` location, and falls back to
	`<oracle_dir>/policy_e_gates.json` otherwise.
	"""
	oracle_dir = Path(oracle_csv_path).resolve().parent
	# Expected layout: <run_root>/oracle/oracle.csv
	if oracle_dir.name == "oracle":
		run_root = oracle_dir.parent
		return str(run_root / "analyses" / "policy_e_gates.json")
	return str(oracle_dir / "policy_e_gates.json")


#============================================
# CLI
#============================================

def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Centroid-bias analyzer (WP-2C, plan Test 6)."
	)
	parser.add_argument(
		"-c", "--oracle-csv", dest="oracle_csv", required=True,
		help="Path to oracle.csv produced by WP-1A (seed_oracle_blob_audit.py).",
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", required=True,
		help="Path to output markdown report.",
	)
	parser.add_argument(
		"-j", "--gates-json", dest="gates_json_path", default=None,
		help=(
			"Path to sidecar JSON capturing policy-E inclusion. Defaults to"
			" <run_root>/analyses/policy_e_gates.json when oracle.csv is at"
			" <run_root>/oracle/oracle.csv."
		),
	)
	args = parser.parse_args()
	return args


def main() -> None:
	"""Run the WP-2C analyzer end to end."""
	args = parse_args()
	if not os.path.exists(args.oracle_csv):
		raise FileNotFoundError(f"oracle CSV not found: {args.oracle_csv}")
	gates_json_path = args.gates_json_path
	if gates_json_path is None:
		gates_json_path = default_gates_json_path(args.oracle_csv)
	# Pandas nullable backend keeps booleans + NaN behavior consistent
	df = pandas.read_csv(args.oracle_csv, dtype_backend="numpy_nullable")
	result = analyze_centroid_bias(df)
	report_text = render_markdown_report(result)
	write_markdown_report(report_text, args.output_path)
	write_gates_json(result["gates"], gates_json_path)
	print(f"Report written to {args.output_path}")
	print(f"Gates JSON written to {gates_json_path}")


if __name__ == "__main__":
	main()
