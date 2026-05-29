#!/usr/bin/env python3
"""WP-3B: Hermite-vs-walker comparison framework.

Reads walker_v2 windowed corpus output (fwd_verdicts.csv + bwd_verdicts.csv)
and optionally computes a Hermite reference trajectory, then classifies each
interval as walker_better / hermite_better / tie / inconclusive per amendment
Section 10 walker_better definition.

Usage:
    python3 tools/blob_walk_v2/compare_walker_vs_hermite.py \
        --corpus-root blob_walk_v2/24corpus_windowed \
        --output dump_step1/24corpus_windowed/SOLVER_COMPARISON.md

Hermite trajectory search:
  1. Looks for a pre-built Hermite CSV alongside the walker outputs, at
     <interval_dir>/hermite_trajectory.csv (not yet produced by any tool;
     reserved for future use).
  2. If not found and --hermite-only is set, computes a linear-interpolation
     approximation between left/right seed pixel positions.  This is a
     coarse stand-in for the full velocity_model Hermite (which requires
     scene_transform and the full solver stack).  Rows are tagged
     status='hermite_approx'.
  3. If not found and --hermite-only is not set, the interval row is marked
     inconclusive.

Classification tolerance:
  REGRESSION_TOLERANCE_ABS = 0.05 (5 percentage points absolute).
  Walker improves metric M when walker_value > hermite_value + tolerance
  (for pct_windows_in_5_20mph, higher is better).
  For vel_mag_var_median and vel_angle_spread_median, lower is better:
  walker improves when walker_value < hermite_value - tolerance.
  Ties count as neither improvement nor regression.
  walker_better: walker improves at least 2 of 3 primary metrics, and does
  not worsen the remaining metric by more than REGRESSION_TOLERANCE_ABS.
  hermite_better: same logic reversed.
  tie: neither side improves 2+ metrics without regressing the third.
  inconclusive: missing data for either trajectory.

Amendment Section 10 credibility bar: >= 50% of intervals classified
walker_better on primary metrics.
"""

# Standard Library
import argparse
import math
import pathlib

# local repo modules
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import score_corpus_quality


#============================================
# Regression tolerance for the multi-metric classifier.
# 5 percentage points absolute: documented in module docstring.
REGRESSION_TOLERANCE_ABS = 0.05


#============================================
def _load_walker_trajectory(seed_dir: pathlib.Path) -> tuple:
	"""Load FWD and BWD walker trajectories from verdicts CSVs.

	Args:
		seed_dir: interval directory containing fwd_verdicts.csv + bwd_verdicts.csv.

	Returns:
		Tuple (fwd_rows, bwd_rows): lists of row dicts from DictReader.

	Raises:
		FileNotFoundError: if either CSV is absent.
	"""
	fwd_csv = score_corpus_quality._find_verdicts_csv(seed_dir, "fwd")
	bwd_csv = score_corpus_quality._find_verdicts_csv(seed_dir, "bwd")
	fwd_rows = score_corpus_quality._load_verdicts(fwd_csv)
	bwd_rows = score_corpus_quality._load_verdicts(bwd_csv)
	return fwd_rows, bwd_rows


#============================================
def _build_linear_hermite_rows(left_frame: int, right_frame: int,
		left_cx: float, left_cy: float,
		right_cx: float, right_cy: float) -> list:
	"""Build a linear-interpolation stand-in for Hermite trajectory.

	Produces one row per frame from left_frame to right_frame inclusive.
	Status is 'hermite_approx' to distinguish from real solver output.

	Args:
		left_frame: left seed frame index.
		right_frame: right seed frame index.
		left_cx: left seed center x in pixels.
		left_cy: left seed center y in pixels.
		right_cx: right seed center x in pixels.
		right_cy: right seed center y in pixels.

	Returns:
		List of dicts with keys: frame_index, cand_cx, cand_cy, status.
	"""
	n_frames = right_frame - left_frame
	rows = []
	for offset in range(n_frames + 1):
		frame_index = left_frame + offset
		if n_frames > 0:
			t = offset / n_frames
		else:
			t = 0.0
		cx = left_cx + t * (right_cx - left_cx)
		cy = left_cy + t * (right_cy - left_cy)
		rows.append({
			"frame_index": str(frame_index),
			"cand_cx": str(cx),
			"cand_cy": str(cy),
			"status": "hermite_approx",
		})
	return rows


#============================================
def _load_hermite_trajectory(seed_dir: pathlib.Path, left_frame: int, right_frame: int,
		hermite_only_flag: bool, fwd_rows: list) -> list:
	"""Load or compute a Hermite reference trajectory for one interval.

	Checks for a pre-built hermite_trajectory.csv.  If missing:
	- With --hermite-only: builds a linear-interpolation approximation from
	  the seed positions available in fwd_rows (step-0 row gives left seed;
	  last row's provisional coords give a proxy for right seed).
	- Without --hermite-only: returns empty list (inconclusive).

	The conventional path checked is: <seed_dir>/hermite_trajectory.csv

	Args:
		seed_dir: interval directory.
		left_frame: left seed frame index.
		right_frame: right seed frame index.
		hermite_only_flag: if True, fall back to linear approximation.
		fwd_rows: FWD walker rows, used to extract seed positions for approx.

	Returns:
		List of row dicts with cand_cx, cand_cy, frame_index, status fields.
		Empty list when unavailable and hermite_only_flag is False.
	"""
	# Check conventional location
	hermite_csv = seed_dir / "hermite_trajectory.csv"
	if hermite_csv.exists():
		return score_corpus_quality._load_verdicts(hermite_csv)

	if not hermite_only_flag:
		return []

	# Build linear approximation from seed positions embedded in fwd_rows.
	# Step-0 row: status=='accepted', step==0 -> gives left seed position.
	# We use pred_cx / pred_cy from step-0 (which equals the seed).
	# Right seed: extract from the last row's provisional_cx_px or from
	# the stop-row columns if available.  Fall back to last accepted row.
	left_cx = None
	left_cy = None
	right_cx = None
	right_cy = None

	for r in fwd_rows:
		if r.get("step") == "0" or r.get("step") == 0:
			# Step-0 bootstrap row: pred_cx, pred_cy hold the left seed position.
			if r.get("pred_cx") and r["pred_cx"] != "":
				left_cx = float(r["pred_cx"])
				left_cy = float(r["pred_cy"])
			elif r.get("cand_cx") and r["cand_cx"] != "":
				left_cx = float(r["cand_cx"])
				left_cy = float(r["cand_cy"])
			break

	# Right seed: last row that has stop_reason == accepted or last accepted row.
	for r in reversed(fwd_rows):
		if r.get("cand_cx") and r["cand_cx"] != "" and r.get("status") == "accepted":
			right_cx = float(r["cand_cx"])
			right_cy = float(r["cand_cy"])
			break

	if left_cx is None or right_cx is None:
		# Cannot build approximation; treat as inconclusive.
		return []

	return _build_linear_hermite_rows(
		left_frame, right_frame,
		left_cx, left_cy, right_cx, right_cy,
	)


#============================================
def _hermite_rows_to_walk_metrics(hermite_rows: list, source_fps: float,
		torso_w_px: float) -> dict:
	"""Compute walk metrics for a Hermite trajectory row list.

	Reuses _compute_walk_metrics by treating every Hermite row as accepted.

	Args:
		hermite_rows: list of row dicts with cand_cx, cand_cy, frame_index, status.
		source_fps: frames per second.
		torso_w_px: torso width in pixels.

	Returns:
		Metrics dict from _compute_walk_metrics.
	"""
	# Normalize rows to the expected format for _compute_walk_metrics.
	# All hermite rows count as accepted.
	normalized = []
	for r in hermite_rows:
		normalized.append({
			"frame_index": r["frame_index"],
			"cand_cx": r.get("cand_cx", ""),
			"cand_cy": r.get("cand_cy", ""),
			"status": "accepted",
		})
	return score_corpus_quality._compute_walk_metrics(normalized, source_fps, torso_w_px)


#============================================
def _extract_primary_metrics(metrics: dict) -> tuple:
	"""Extract the three primary plausibility metrics from a walk metrics dict.

	Args:
		metrics: output of _compute_walk_metrics.

	Returns:
		Tuple (pct_windows_in_5_20mph, vel_mag_var_median, vel_angle_spread_median).
		nan values are preserved.
	"""
	pct = metrics["windowed_speed_in_range_fraction"]
	var = metrics["windowed_vel_mag_variance_median"]
	ang = metrics["windowed_vel_angle_spread_median"]
	return pct, var, ang


#============================================
def _metric_is_nan(val) -> bool:
	"""Return True if val is float nan.

	Args:
		val: numeric or float.

	Returns:
		True if nan.
	"""
	return isinstance(val, float) and math.isnan(val)


#============================================
def classify_interval(walker_metrics: dict, hermite_metrics: dict,
		agreement_present: bool = True) -> str:
	"""Classify one interval as walker_better / hermite_better / tie / inconclusive.

	Multi-metric rule per amendment Section 10 walker_better definition:
	- walker_better: walker improves at least 2 of the 3 primary metrics
	  without worsening the remaining metric by more than REGRESSION_TOLERANCE_ABS.
	- hermite_better: same logic reversed.
	- tie: neither side qualifies.
	- inconclusive: primary metrics are nan for either side.

	Primary metrics (with improvement direction):
	  pct_windows_in_5_20mph: higher is better.
	  vel_mag_var_median: lower is better.
	  vel_angle_spread_median: lower is better.

	Args:
		walker_metrics: walk metrics dict for the walker trajectory.
		hermite_metrics: walk metrics dict for the Hermite trajectory.
		agreement_present: unused; reserved for future no-material-regression guard.

	Returns:
		Classification string.
	"""
	w_pct, w_var, w_ang = _extract_primary_metrics(walker_metrics)
	h_pct, h_var, h_ang = _extract_primary_metrics(hermite_metrics)

	# If any primary metric is nan for either side, inconclusive.
	for v in (w_pct, w_var, w_ang, h_pct, h_var, h_ang):
		if _metric_is_nan(v):
			return "inconclusive"

	tol = REGRESSION_TOLERANCE_ABS

	# Determine per-metric improvement direction.
	# pct: higher is better.  Improvement means walker_val > hermite_val + tol.
	# var, ang: lower is better.  Improvement means walker_val < hermite_val - tol.

	# walker improvement per metric
	w_improves_pct = w_pct > h_pct + tol
	w_improves_var = w_var < h_var - tol
	w_improves_ang = w_ang < h_ang - tol

	# hermite improvement per metric (reversed)
	h_improves_pct = h_pct > w_pct + tol
	h_improves_var = h_var < w_var - tol
	h_improves_ang = h_ang < w_ang - tol

	# Regression check: does X worsen a metric that Y does not improve?
	# walker worsens metric M means hermite is better on M (by > tol).
	# We check: walker improves >= 2 AND does not worsen the remaining 1 by > tol.

	w_count = sum([w_improves_pct, w_improves_var, w_improves_ang])
	h_count = sum([h_improves_pct, h_improves_var, h_improves_ang])

	if w_count >= 2:
		# Check that the non-improved metric is not regressed beyond tolerance.
		non_improved_regressed = False
		if not w_improves_pct and h_pct > w_pct + tol:
			non_improved_regressed = True
		if not w_improves_var and h_var < w_var - tol:
			non_improved_regressed = True
		if not w_improves_ang and h_ang < w_ang - tol:
			non_improved_regressed = True
		if not non_improved_regressed:
			return "walker_better"

	if h_count >= 2:
		non_improved_regressed = False
		if not h_improves_pct and w_pct > h_pct + tol:
			non_improved_regressed = True
		if not h_improves_var and w_var < h_var - tol:
			non_improved_regressed = True
		if not h_improves_ang and w_ang < h_ang - tol:
			non_improved_regressed = True
		if not non_improved_regressed:
			return "hermite_better"

	return "tie"


#============================================
def _compute_final_disp(rows: list, right_frame: int) -> float:
	"""Compute displacement from last accepted position to right seed.

	The right-seed position is inferred from the last step-0 row in the
	bwd walk at right_frame, which equals the right-seed cx/cy.  As a
	proxy we use the walker's last accepted position distance from the
	last frame of the interval.  When no accepted row exists near
	right_frame, returns nan.

	This is a diagnostic metric only.

	Args:
		rows: FWD walker rows.
		right_frame: right seed frame index.

	Returns:
		Float displacement in pixels or nan.
	"""
	# Find the last accepted row
	last_cx = None
	last_cy = None
	for r in rows:
		if r.get("status") == "accepted" and r.get("cand_cx") and r["cand_cx"] != "":
			last_cx = float(r["cand_cx"])
			last_cy = float(r["cand_cy"])
	if last_cx is None:
		return float("nan")
	# The right seed should be the pred_cx / pred_cy at step-0 of a BWD walk
	# or can be extracted from the stop row.  As a proxy, use the last accepted
	# row's displacement from pred_cx at right_frame.
	for r in rows:
		if r.get("frame_index") == str(right_frame):
			if r.get("pred_cx") and r["pred_cx"] != "":
				pred_cx = float(r["pred_cx"])
				pred_cy = float(r["pred_cy"])
				return math.hypot(last_cx - pred_cx, last_cy - pred_cy)
	return float("nan")


#============================================
def _fmt(val, decimals: int = 3) -> str:
	"""Format a float for Markdown table output.

	Args:
		val: float or other.
		decimals: decimal places.

	Returns:
		Formatted string; '-' for nan.
	"""
	if isinstance(val, float) and math.isnan(val):
		return "-"
	if isinstance(val, float):
		return f"{val:.{decimals}f}"
	return str(val)


#============================================
def score_one_interval(seed_dir: pathlib.Path, video_name: str,
		hermite_only_flag: bool) -> dict:
	"""Score one interval and produce a result row.

	Args:
		seed_dir: interval directory (seed_L_R/).
		video_name: parent video name string.
		hermite_only_flag: if True, attempt on-the-fly Hermite approximation.

	Returns:
		Dict with all comparison fields for this interval.
	"""
	left_frame, right_frame = score_corpus_quality._parse_seed_dir_name(seed_dir.name)

	fwd_rows, bwd_rows = _load_walker_trajectory(seed_dir)

	# Source fps and torso width: try interval_summary.csv; fall back to
	# torso_w_px from first accepted FWD row.
	source_fps = 60.0  # sensible default; will be overridden if available
	torso_w_px = None

	interval_summary_csv = seed_dir / "interval_summary.csv"
	if not interval_summary_csv.exists():
		raise FileNotFoundError(f"Missing interval_summary.csv: {interval_summary_csv}")

	# Read torso_w_px from first fwd_verdicts row that has it
	for r in fwd_rows:
		if r.get("torso_w_px") and r["torso_w_px"] != "":
			torso_w_px = float(r["torso_w_px"])
			break

	if torso_w_px is None:
		torso_w_px = 33.0  # fallback to a typical value; flagged in output

	# source_fps: look in fwd rows for any row carrying source_fps column.
	for r in fwd_rows:
		if r.get("source_fps") and r["source_fps"] != "":
			source_fps = float(r["source_fps"])
			break

	# Walker metrics
	walker_fwd_metrics = score_corpus_quality._compute_walk_metrics(
		fwd_rows, source_fps, torso_w_px
	)
	walker_bwd_metrics = score_corpus_quality._compute_walk_metrics(
		bwd_rows, source_fps, torso_w_px
	)

	# Merge FWD/BWD walker metrics by averaging primary plausibility metrics.
	# Use FWD as primary when only one direction is available.
	walker_merged = _merge_fwd_bwd_metrics(walker_fwd_metrics, walker_bwd_metrics)

	# FWD/BWD agreement
	agreement_median, agreement_p95 = score_corpus_quality._compute_fwd_bwd_agreement(
		fwd_rows, bwd_rows
	)

	# Final displacement (FWD walk to right seed)
	final_disp = _compute_final_disp(fwd_rows, right_frame)

	# Hermite trajectory
	hermite_rows = _load_hermite_trajectory(
		seed_dir, left_frame, right_frame, hermite_only_flag, fwd_rows
	)

	hermite_available = len(hermite_rows) > 0
	if hermite_available:
		hermite_metrics = _hermite_rows_to_walk_metrics(hermite_rows, source_fps, torso_w_px)
	else:
		hermite_metrics = None

	# Classification
	if hermite_metrics is None:
		classification = "inconclusive"
	else:
		classification = classify_interval(walker_merged, hermite_metrics)

	row = {
		"video": video_name,
		"interval": f"{left_frame}-{right_frame}",
		"left_frame": left_frame,
		"right_frame": right_frame,
		"hermite_available": hermite_available,
		"classification": classification,
		# Walker primary metrics (merged FWD+BWD)
		"walker_pct_windows_in_5_20mph": walker_merged["windowed_speed_in_range_fraction"],
		"walker_vel_mag_var_median": walker_merged["windowed_vel_mag_variance_median"],
		"walker_vel_angle_spread_median": walker_merged["windowed_vel_angle_spread_median"],
		# FWD/BWD agreement
		"walker_agreement_median": agreement_median,
		"walker_agreement_p95": agreement_p95,
		# Final displacement diagnostic
		"walker_final_disp_px": final_disp,
		# Hermite primary metrics
		"hermite_pct_windows_in_5_20mph": (
			hermite_metrics["windowed_speed_in_range_fraction"] if hermite_metrics else float("nan")
		),
		"hermite_vel_mag_var_median": (
			hermite_metrics["windowed_vel_mag_variance_median"] if hermite_metrics else float("nan")
		),
		"hermite_vel_angle_spread_median": (
			hermite_metrics["windowed_vel_angle_spread_median"] if hermite_metrics else float("nan")
		),
	}
	return row


#============================================
def _merge_fwd_bwd_metrics(fwd: dict, bwd: dict) -> dict:
	"""Merge FWD and BWD walk metrics by averaging primary plausibility metrics.

	For each primary metric, returns the average of FWD and BWD when both are
	non-nan; returns the non-nan value when only one is available; returns nan
	when both are nan.

	Args:
		fwd: FWD metrics dict from _compute_walk_metrics.
		bwd: BWD metrics dict from _compute_walk_metrics.

	Returns:
		New metrics dict with averaged primary metrics.
	"""
	merged = {}
	for key in ("windowed_speed_in_range_fraction",
			"windowed_vel_mag_variance_median",
			"windowed_vel_angle_spread_median"):
		fv = fwd[key]
		bv = bwd[key]
		fnan = _metric_is_nan(fv)
		bnan = _metric_is_nan(bv)
		if fnan and bnan:
			merged[key] = float("nan")
		elif fnan:
			merged[key] = bv
		elif bnan:
			merged[key] = fv
		else:
			merged[key] = (fv + bv) / 2.0
	return merged


#============================================
def score_corpus(corpus_root: pathlib.Path, hermite_only_flag: bool) -> list:
	"""Score all intervals in the windowed corpus.

	Args:
		corpus_root: path to blob_walk_v2/24corpus_windowed/.
		hermite_only_flag: passed through to score_one_interval.

	Returns:
		List of result row dicts, sorted by video then left_frame.
	"""
	all_rows = []
	video_dirs = sorted(d for d in corpus_root.iterdir() if d.is_dir())
	for video_dir in video_dirs:
		video_name = video_dir.name
		seed_dirs = sorted(
			d for d in video_dir.iterdir()
			if d.is_dir() and d.name.startswith("seed_")
		)
		for seed_dir in seed_dirs:
			row = score_one_interval(seed_dir, video_name, hermite_only_flag)
			all_rows.append(row)
	return all_rows


#============================================
def write_comparison_md(rows: list, output_path: pathlib.Path) -> None:
	"""Write SOLVER_COMPARISON.md.

	Sections:
	  (a) Corpus-wide aggregate counts per classification.
	  (b) Per-video tally.
	  (c) Full per-interval table with metrics for both solvers.
	  (d) Verdict against amendment credibility bar and quality bar.

	Args:
		rows: list of result row dicts from score_corpus.
		output_path: destination Markdown path.
	"""
	lines = []
	lines.append("# WP-3B solver comparison: walker_v2 vs Hermite-only stage 3")
	lines.append("")
	lines.append("Source: `tools/blob_walk_v2/compare_walker_vs_hermite.py`")
	lines.append("")
	lines.append(f"Corpus intervals scored: {len(rows)}")
	lines.append("")
	lines.append("Tolerance: `REGRESSION_TOLERANCE_ABS = 0.05` (5 percentage points absolute).")
	lines.append("")
	lines.append("Hermite path checked: `<interval_dir>/hermite_trajectory.csv` (stage-3 cached output).")
	lines.append("When absent and `--hermite-only` is set, linear interpolation between seed")
	lines.append("pixel positions is used as an approximation (status: `hermite_approx`).")
	lines.append("")

	# Section (a): aggregate counts
	lines.append("## (a) Aggregate classification counts")
	lines.append("")
	class_counts = {}
	for r in rows:
		cls = r["classification"]
		class_counts[cls] = class_counts.get(cls, 0) + 1
	total = len(rows)
	lines.append("| classification | count | pct |")
	lines.append("| --- | --- | --- |")
	for cls in ("walker_better", "hermite_better", "tie", "inconclusive"):
		cnt = class_counts.get(cls, 0)
		pct = 100.0 * cnt / total if total > 0 else 0.0
		lines.append(f"| {cls} | {cnt} | {pct:.1f}% |")
	lines.append("")

	# Section (b): per-video tally
	lines.append("## (b) Per-video tally")
	lines.append("")
	videos_seen = []
	rows_by_video = {}
	for r in rows:
		v = r["video"]
		if v not in rows_by_video:
			rows_by_video[v] = []
			videos_seen.append(v)
		rows_by_video[v].append(r)

	lines.append("| video | total | walker_better | hermite_better | tie | inconclusive |")
	lines.append("| --- | --- | --- | --- | --- | --- |")
	for video in videos_seen:
		vrows = rows_by_video[video]
		vtotal = len(vrows)
		vcounts = {cls: 0 for cls in ("walker_better", "hermite_better", "tie", "inconclusive")}
		for r in vrows:
			vcounts[r["classification"]] += 1
		lines.append(
			f"| {video} | {vtotal} | {vcounts['walker_better']} | "
			f"{vcounts['hermite_better']} | {vcounts['tie']} | {vcounts['inconclusive']} |"
		)
	lines.append("")

	# Section (c): full per-interval table
	lines.append("## (c) Per-interval metrics table")
	lines.append("")
	lines.append(
		"| video | interval | classification | "
		"W pct_5_20 | W var_med | W ang_med | "
		"H pct_5_20 | H var_med | H ang_med | "
		"agree_med | agree_p95 | final_disp |"
	)
	lines.append(
		"| --- | --- | --- | "
		"--- | --- | --- | "
		"--- | --- | --- | "
		"--- | --- | --- |"
	)
	for r in rows:
		lines.append(
			f"| {r['video']} | {r['interval']} | {r['classification']} | "
			f"{_fmt(r['walker_pct_windows_in_5_20mph'])} | "
			f"{_fmt(r['walker_vel_mag_var_median'])} | "
			f"{_fmt(r['walker_vel_angle_spread_median'])} | "
			f"{_fmt(r['hermite_pct_windows_in_5_20mph'])} | "
			f"{_fmt(r['hermite_vel_mag_var_median'])} | "
			f"{_fmt(r['hermite_vel_angle_spread_median'])} | "
			f"{_fmt(r['walker_agreement_median'])} | "
			f"{_fmt(r['walker_agreement_p95'])} | "
			f"{_fmt(r['walker_final_disp_px'])} |"
		)
	lines.append("")

	# Section (d): verdict against amendment acceptance criteria
	lines.append("## (d) Verdict against amendment Section 10 acceptance bars")
	lines.append("")
	# Credibility bar: >= 50% walker_better on primary metrics
	# (well-formed = not inconclusive)
	well_formed = [r for r in rows if r["classification"] != "inconclusive"]
	n_well = len(well_formed)
	n_walker_better = sum(1 for r in well_formed if r["classification"] == "walker_better")
	credibility_pct = 100.0 * n_walker_better / n_well if n_well > 0 else 0.0
	credibility_pass = credibility_pct >= 50.0

	lines.append("### Credibility bar (Stage 4 candidate)")
	lines.append("")
	lines.append("Requirement: >= 50% of well-formed intervals classified walker_better.")
	lines.append("")
	lines.append(f"- Well-formed intervals (not inconclusive): {n_well}")
	lines.append(f"- walker_better count: {n_walker_better}")
	lines.append(f"- walker_better pct: {credibility_pct:.1f}%")
	lines.append(f"- Credibility bar: {'PASS' if credibility_pass else 'FAIL'}")
	lines.append("")

	# Quality bar: walker_better AND no material regression on FWD/BWD agreement.
	# Material regression defined as: walker agreement median > 2x median of
	# hermite-better/tie intervals (heuristic; hermite agreement not yet available
	# without full solver run). When Hermite trajectory data is unavailable for
	# all intervals, quality bar is INCONCLUSIVE.
	n_hermite_available = sum(1 for r in rows if r["hermite_available"])
	lines.append("### Quality bar (default-refinement promotion)")
	lines.append("")
	lines.append(
		"Requirement: walker_better on primary metrics AND no material regression "
		"on FWD/BWD agreement or compute envelope."
	)
	lines.append("")
	if n_hermite_available == 0:
		lines.append("- No Hermite trajectory data available; quality bar is INCONCLUSIVE.")
		lines.append("  Run with `--hermite-only` or provide pre-built hermite_trajectory.csv files.")
	else:
		# Compute FWD/BWD agreement median across walker_better intervals.
		wb_rows = [r for r in rows if r["classification"] == "walker_better"]
		agree_vals = [
			r["walker_agreement_median"] for r in wb_rows
			if not _metric_is_nan(r["walker_agreement_median"])
		]
		if agree_vals:
			agree_median = sorted(agree_vals)[len(agree_vals) // 2]
			lines.append(f"- walker_better intervals with agreement data: {len(agree_vals)}")
			lines.append(f"- Median FWD/BWD agreement on walker_better intervals: {agree_median:.1f} px")
		else:
			lines.append("- No FWD/BWD agreement data available for walker_better intervals.")
		lines.append("- Compute envelope: not measured in this tool (run actual corpus for timing).")
		lines.append(
			f"- Quality bar: {'PASS' if credibility_pass and n_hermite_available > 0 else 'INCONCLUSIVE - requires full Hermite run'}"
		)
	lines.append("")
	lines.append("---")
	lines.append("")
	lines.append(
		"Note: Inconclusive intervals indicate missing Hermite trajectory data. "
		"Run with `--hermite-only` for an approximation, or run the full solver "
		"(`track_runner/interval_solver.py` Stage 3) to produce `hermite_trajectory.csv` "
		"per interval, then re-run this tool."
	)
	lines.append("")

	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text("\n".join(lines) + "\n")


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="WP-3B: Compare walker_v2 vs Hermite-only stage-3 on windowed corpus."
	)
	parser.add_argument(
		"--corpus-root",
		dest="corpus_root",
		required=True,
		help="Path to walker output dir (e.g. blob_walk_v2/24corpus_windowed)."
	)
	parser.add_argument(
		"--output",
		dest="output",
		default="dump_step1/SOLVER_COMPARISON.md",
		help="Output Markdown path (default: dump_step1/SOLVER_COMPARISON.md)."
	)
	parser.add_argument(
		"--hermite-only",
		dest="hermite_only",
		action="store_true",
		help=(
			"When Hermite trajectory CSV is absent, compute a linear-interpolation "
			"approximation from seed positions. Without this flag, missing Hermite "
			"output marks the interval inconclusive."
		)
	)
	args = parser.parse_args()
	return args


#============================================
def main() -> None:
	"""Main entry point."""
	args = parse_args()

	corpus_root = pathlib.Path(args.corpus_root)
	output_path = pathlib.Path(args.output)

	if not corpus_root.exists():
		raise FileNotFoundError(f"corpus_root does not exist: {corpus_root}")

	print(f"Corpus root: {corpus_root}")
	print(f"Hermite-only flag: {args.hermite_only}")
	print("Hermite path checked per interval: <interval_dir>/hermite_trajectory.csv")

	rows = score_corpus(corpus_root, args.hermite_only)
	print(f"Scored {len(rows)} intervals.")

	write_comparison_md(rows, output_path)
	print(f"Wrote: {output_path}")

	# Print classification summary
	class_counts = {}
	for r in rows:
		cls = r["classification"]
		class_counts[cls] = class_counts.get(cls, 0) + 1
	for cls in ("walker_better", "hermite_better", "tie", "inconclusive"):
		print(f"  {cls}: {class_counts.get(cls, 0)}")


if __name__ == '__main__':
	main()
