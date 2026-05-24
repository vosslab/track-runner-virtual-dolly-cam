#!/usr/bin/env python3
"""Per-video rollup report writer for the blob-refinement fix plan (WP-1C).

For each video listed in `<run-root>/m2_sweep/per_video_selection.csv`, this
tool reads the per-interval `verdicts.csv` and `CLASSIFICATION.md` artifacts,
the corpus oracle CSV at `<run-root>/oracle/oracle.csv`, and the corpus
hypothesis verdict JSON at `<run-root>/oracle/HYPOTHESIS_TESTS.json`, then
emits one `<output-dir>/{basename}.md` per video plus a corpus-wide
`corpus_index.md`. A `per_video_summary.csv` sidecar with one row per video
is written alongside the Markdown reports.

The schemas this tool consumes are documented in
`/Users/vosslab/.claude/plans/kind-exploring-cray.md`:

- `per_video_selection.csv`: rng_seed, sample, video, interval_start,
  interval_end, bucket, blob_accept_fwd, blob_accept_bwd, agreement_score,
  tier.
- `verdicts.csv`: per-frame columns including `frame_index`, `pass`,
  `gate`, `lost_at_stage`, `winner_dist_h`, `raw_box` (compact JSON dict
  with x / y / w / h, captured by visualize_blob_gates.py
  --write-corridor-blob-list once WP-0B lands; missing on partial sweeps
  is tolerated and jitter reports NaN).
- `oracle.csv`: per-LOO-hidden-seed-frame columns including `video`.
- `HYPOTHESIS_TESTS.json`: the WP-1A sidecar with `{corpus: {h1, h2, h6,
  h8: {verdict, rate, n_eligible}}, per_video: {video_name: {...}}}`.

This is a skeleton-pass tool. Full-corpus inputs are not yet available;
the tool is verified via the fixture pytest at
`tests/test_per_video_report.py`.

Usage:
	source source_me.sh && python3 tools/per_video_report.py \\
		-r output_smoke/blob_refine_fix/2026-05-23 \\
		-o output_smoke/blob_refine_fix/2026-05-23/per_video_reports
"""

# Standard Library
import os
import csv
import json
import math
import argparse
import statistics
from collections import Counter

# default run-root parent; resolves to the latest dated subdir when --run-root
# is omitted, matching the convention used by tools/blob_fix_suspension_check.py
DEFAULT_RUN_ROOT_PARENT = "output_smoke/blob_refine_fix"

# proximity gate threshold used by classify_corpus_mode-style decisions
# elsewhere in the corpus; kept here so the winner_dist_h histogram in the
# per-video report uses the same bins as tools/blob_distance_histogram.py.
BIN_WIDTH = 0.1
BIN_COUNT_FINITE = 30
OVERFLOW_BIN_INDEX = BIN_COUNT_FINITE
TOTAL_BINS = BIN_COUNT_FINITE + 1

# per-hypothesis support threshold per plan WP-1C (30% per-video bin)
HYPOTHESIS_SUPPORT_RATE = 0.30

# per-frame bucket labels exactly as emitted by classify_blob_failure_mode.py
KNOWN_LOST_AT_STAGES = (
	"accepted",
	"none",
	"lost_at_observer",
	"lost_at_raw_blobs",
	"lost_at_corridor",
	"lost_at_gate_prox",
	"lost_at_gate_dir",
	"lost_at_gate_path",
)


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Emit per-video Markdown rollups, a corpus_index.md, and a "
			"per_video_summary.csv sidecar consuming the M1 corpus run "
			"artifacts (per_video_selection.csv, verdicts.csv, "
			"CLASSIFICATION.md, oracle.csv, HYPOTHESIS_TESTS.json)."
		),
	)
	parser.add_argument(
		"-r", "--run-root", dest="run_root", default=None,
		help=(
			"Path to a run-root directory (must contain m2_sweep/ and "
			"oracle/). Defaults to "
			"output_smoke/blob_refine_fix/<latest_date>."
		),
	)
	parser.add_argument(
		"-o", "--output-dir", dest="output_dir", default=None,
		help=(
			"Path to the output directory. Defaults to "
			"<run-root>/per_video_reports/."
		),
	)
	parser.add_argument(
		"-c", "--tr-config-dir", dest="tr_config_dir", default=None,
		help=(
			"Optional tr_config/ directory to pull seed count and "
			"race-start frame from. Header fields fall back to 'unknown' "
			"when this directory is missing or the per-video file is "
			"absent."
		),
	)
	return parser.parse_args()


#============================================
def resolve_latest_run_root(parent_dir: str) -> str:
	"""Return the lexicographically latest date subdirectory under `parent_dir`.

	Plan-run subdirs use the YYYY-MM-DD naming convention; lexicographic
	sort matches chronological order. Missing parent dir or zero date
	subdirs raise FileNotFoundError so the failure is loud (no
	dict.get defaults).

	Args:
		parent_dir: Path to the run-root parent.

	Returns:
		str: Absolute path to the latest dated subdirectory.
	"""
	if not os.path.isdir(parent_dir):
		raise FileNotFoundError(
			f"run-root parent does not exist: {parent_dir}. "
			"Pass --run-root explicitly."
		)
	entries = sorted(os.listdir(parent_dir))
	# only directory entries; ignore README and stray files
	date_dirs = []
	for entry in entries:
		full = os.path.join(parent_dir, entry)
		if os.path.isdir(full):
			date_dirs.append(entry)
	if not date_dirs:
		raise FileNotFoundError(
			f"no dated subdirectories under: {parent_dir}. "
			"Pass --run-root explicitly."
		)
	latest = date_dirs[-1]
	return os.path.abspath(os.path.join(parent_dir, latest))


#============================================
def resolve_paths(args: argparse.Namespace) -> tuple:
	"""Resolve the effective run-root and output paths from args.

	Returns:
		tuple: (run_root, output_dir) absolute paths.
	"""
	if args.run_root:
		run_root = os.path.abspath(args.run_root)
	else:
		run_root = resolve_latest_run_root(DEFAULT_RUN_ROOT_PARENT)
	if args.output_dir:
		output_dir = os.path.abspath(args.output_dir)
	else:
		output_dir = os.path.join(run_root, "per_video_reports")
	return run_root, output_dir


#============================================
def load_selection_rows(selection_path: str) -> list:
	"""Read per_video_selection.csv and return the row list.

	Args:
		selection_path: Absolute path to per_video_selection.csv.

	Returns:
		list[dict]: rows keyed by column name; types are str as loaded
		from CSV (call sites cast int / float as needed).
	"""
	if not os.path.isfile(selection_path):
		raise FileNotFoundError(
			f"per_video_selection.csv not found at: {selection_path}. "
			"Run the WP-1B selection step before invoking per_video_report."
		)
	rows = []
	with open(selection_path, "r", newline="") as f:
		reader = csv.DictReader(f)
		for row in reader:
			rows.append(row)
	return rows


#============================================
def group_selection_by_video(rows: list) -> dict:
	"""Group selection rows by video and bucket.

	Args:
		rows: list of dicts from per_video_selection.csv.

	Returns:
		dict[str, dict[str, list[dict]]]: video -> bucket -> list of
		selection rows. Sample (ranking vs held_out) is preserved on each
		row but the report aggregates ranking-sample rows only.
	"""
	grouped: dict = {}
	for row in rows:
		# indexed access fails loudly on missing required columns
		video = row["video"]
		bucket = row["bucket"]
		sample = row["sample"]
		# this plan's WP-1C consumes the ranking sample; held-out rows
		# stay in the grouping but the report ignores them (M4 validation
		# uses held-out separately).
		if sample != "ranking":
			continue
		if video not in grouped:
			grouped[video] = {}
		if bucket not in grouped[video]:
			grouped[video][bucket] = []
		grouped[video][bucket].append(row)
	return grouped


#============================================
def load_hypothesis_json(run_root: str) -> dict:
	"""Read `<run_root>/oracle/HYPOTHESIS_TESTS.json` and return the dict."""
	json_path = os.path.join(run_root, "oracle", "HYPOTHESIS_TESTS.json")
	if not os.path.isfile(json_path):
		raise FileNotFoundError(
			f"HYPOTHESIS_TESTS.json not found at: {json_path}. "
			"Run the WP-1A analyzer before invoking per_video_report."
		)
	with open(json_path, "r") as f:
		data = json.load(f)
	return data


#============================================
def video_hypothesis_rates(data: dict, video_name: str) -> dict:
	"""Return the per-video H1 / H2 / H6 / H8 rates and n_eligible.

	Args:
		data: parsed HYPOTHESIS_TESTS.json content.
		video_name: video basename.

	Returns:
		dict: {"h1": {"rate": float, "n_eligible": int, "supported": bool},
				...} for the requested video. Missing per-video block
		raises KeyError so the caller can decide how to surface it.
	"""
	per_video = data["per_video"]
	# indexed access intentional: missing video means the rollup is being
	# computed before the analyzer covers it, which is a real bug per
	# docs/PYTHON_STYLE.md "do not hide bugs with defaults".
	block = per_video[video_name]
	result: dict = {}
	for h_key in ("h1", "h2", "h6", "h8"):
		entry = block[h_key]
		rate = float(entry["rate"])
		n_eligible = int(entry["n_eligible"])
		supported = rate >= HYPOTHESIS_SUPPORT_RATE
		result[h_key] = {
			"rate": rate,
			"n_eligible": n_eligible,
			"supported": supported,
		}
	return result


#============================================
def iter_interval_dirs(run_root: str, video_name: str) -> list:
	"""Return sorted list of interval directory paths for one video.

	The M1 sweep layout is `<run_root>/m2_sweep/<video>/interval_*/`.

	Args:
		run_root: Run-root directory.
		video_name: Video basename (matches the per_video_selection.csv
			`video` column).

	Returns:
		list[str]: absolute paths to interval_* directories. Empty if
		the per-video sweep directory is missing.
	"""
	video_dir = os.path.join(run_root, "m2_sweep", video_name)
	if not os.path.isdir(video_dir):
		return []
	entries = sorted(os.listdir(video_dir))
	interval_dirs = []
	for entry in entries:
		# strict prefix match so non-interval subdirs do not sneak in
		if not entry.startswith("interval_"):
			continue
		full = os.path.join(video_dir, entry)
		if os.path.isdir(full):
			interval_dirs.append(full)
	return interval_dirs


#============================================
def parse_interval_range_from_dir(interval_dir: str) -> tuple:
	"""Return (start_frame, end_frame) parsed from interval_<start>_<end>.

	Returns (None, None) when the directory name does not match the
	expected pattern (e.g. user-provided sweep with custom naming).
	"""
	name = os.path.basename(interval_dir)
	if not name.startswith("interval_"):
		return (None, None)
	tail = name[len("interval_"):]
	parts = tail.split("_")
	if len(parts) != 2:
		return (None, None)
	# parts may contain non-integer strings on hand-built fixtures; the
	# tool tolerates that and just reports (None, None)
	left = parts[0]
	right = parts[1]
	if not left.isdigit() or not right.isdigit():
		return (None, None)
	return (int(left), int(right))


#============================================
def read_verdicts_rows(verdicts_path: str) -> list:
	"""Read all rows from one verdicts.csv as plain dicts."""
	rows = []
	with open(verdicts_path, "r", newline="") as f:
		reader = csv.DictReader(f)
		for row in reader:
			rows.append(row)
	return rows


#============================================
def extract_raw_box_h_series(rows: list, pass_filter: str) -> list:
	"""Pull torso heights from `raw_box` JSON column on one pass.

	The `raw_box` column carries a compact JSON dict shaped like
	`{"x": <float>, "y": <float>, "w": <float>, "h": <float>}`. Missing,
	empty, or malformed values are skipped silently so the jitter
	computation reports NaN cleanly when no rows are usable.

	Args:
		rows: list of verdicts.csv rows.
		pass_filter: "FWD" or "BWD"; only rows on that pass contribute.

	Returns:
		list[float]: torso-h values in source order.
	"""
	heights = []
	for row in rows:
		# pass_filter is required; rows without it are bugs upstream
		if row["pass"] != pass_filter:
			continue
		raw_box_str = row["raw_box"] if "raw_box" in row else ""
		if not raw_box_str:
			continue
		# parse JSON; malformed values skip silently rather than crash
		# the report. A partial sweep is a real artifact this skeleton
		# tolerates.
		parsed = _safe_parse_raw_box(raw_box_str)
		if parsed is None:
			continue
		# "h" must be present; missing key surfaces as a skip rather
		# than a KeyError so the jitter reads as NaN with no rows.
		if "h" not in parsed:
			continue
		heights.append(float(parsed["h"]))
	return heights


#============================================
def _safe_parse_raw_box(raw_box_str: str) -> dict:
	"""Parse a raw_box JSON column entry; return None on malformed input.

	The tool uses a tiny try/except here, scoped to one expression, to
	survive partial sweeps where raw_box may be absent or stringified
	differently. This is the rare two-line try/except permitted by
	docs/PYTHON_STYLE.md.
	"""
	try:
		parsed = json.loads(raw_box_str)
	except (json.JSONDecodeError, TypeError):
		return None
	if not isinstance(parsed, dict):
		return None
	return parsed


#============================================
def compute_torso_h_jitter(heights: list) -> float:
	"""Return relative torso-h jitter `std(diff) / median(h)`.

	Plan WP-1C body:
		`std(raw_box.h[t] - raw_box.h[t-1]) / median(raw_box.h)`

	Args:
		heights: torso heights in source order on one pass and one
			interval.

	Returns:
		float: jitter value, or NaN when too few measurements exist
		(fewer than 2 heights, or median(h) == 0).
	"""
	# need at least two heights to form one consecutive diff
	if len(heights) < 2:
		return float("nan")
	diffs = []
	for index in range(1, len(heights)):
		diffs.append(heights[index] - heights[index - 1])
	median_h = statistics.median(heights)
	if median_h == 0:
		return float("nan")
	# statistics.pstdev to use the population std (treats the available
	# heights as the population); pstdev tolerates a length-1 diffs list
	# returning 0.0, which is fine for one-step intervals.
	std_diff = statistics.pstdev(diffs)
	return std_diff / median_h


#============================================
def winner_dist_h_bin_index(distance: float) -> int:
	"""Return the bin index for a winner_dist_h value.

	Bins 0..29 cover [0.0, 0.1) ... [2.9, 3.0); bin 30 is the [3.0, inf)
	overflow bin. NaN distances map to the overflow bin so they never
	silently corrupt mode selection.
	"""
	if math.isnan(distance):
		return OVERFLOW_BIN_INDEX
	if distance >= 3.0:
		return OVERFLOW_BIN_INDEX
	if distance < 0.0:
		return 0
	# round-to-tenth before floor-dividing so 0.7 lands in bin 7 rather
	# than bin 6 via binary-float drift (mirrors blob_distance_histogram)
	scaled = round(distance * 10.0, 6)
	index = int(scaled)
	if index >= BIN_COUNT_FINITE:
		return BIN_COUNT_FINITE - 1
	return index


#============================================
def winner_dist_h_bin_label(index: int) -> str:
	"""Return a human-readable label for a bin index."""
	if index == OVERFLOW_BIN_INDEX:
		return "[3.0, inf)"
	low = index * BIN_WIDTH
	high = low + BIN_WIDTH
	return f"[{low:.1f}, {high:.1f})"


#============================================
def winner_dist_h_bin_low(index: int) -> float:
	"""Return the inclusive low edge of a bin in units of torso height."""
	if index == OVERFLOW_BIN_INDEX:
		return 3.0
	return index * BIN_WIDTH


#============================================
def winner_dist_h_histogram(rows: list) -> tuple:
	"""Return (counts, mode_index) for proximity-rejected winner_dist_h.

	Args:
		rows: verdicts.csv rows for one interval (all passes mixed).

	Returns:
		tuple: (counts: list[int], mode_index: int). When no rows are
		prox-rejected, counts is all zeros and mode_index is 0.
	"""
	counts = [0] * TOTAL_BINS
	for row in rows:
		lost_at_stage = row["lost_at_stage"]
		if lost_at_stage != "lost_at_gate_prox":
			continue
		raw_dist = row["winner_dist_h"]
		if raw_dist == "" or raw_dist is None:
			continue
		distance = float(raw_dist)
		bin_idx = winner_dist_h_bin_index(distance)
		counts[bin_idx] += 1
	# stable-tie convention: lowest index wins ties, matching
	# blob_distance_histogram.mode_bin_index.
	max_count = -1
	mode_index = 0
	for index, count in enumerate(counts):
		if count > max_count:
			max_count = count
			mode_index = index
	return counts, mode_index


#============================================
def per_pass_stage_percents(rows: list, pass_filter: str) -> list:
	"""Return per-pass lost_at_stage percent list, top-3 by count.

	Args:
		rows: verdicts.csv rows for one interval.
		pass_filter: "FWD" or "BWD".

	Returns:
		list[tuple[str, float]]: up to 3 (stage_name, percent) pairs
		sorted by descending count. Stages that do not appear are
		omitted; percent is computed against the total non-empty
		`lost_at_stage` row count on that pass.
	"""
	pass_rows = [row for row in rows if row["pass"] == pass_filter]
	stage_counter: Counter = Counter()
	total = 0
	for row in pass_rows:
		stage = row["lost_at_stage"]
		if not stage:
			continue
		stage_counter[stage] += 1
		total += 1
	if total == 0:
		return []
	top = stage_counter.most_common(3)
	result = []
	for stage, count in top:
		percent = 100.0 * count / total
		result.append((stage, percent))
	return result


#============================================
def dominant_lost_at_stage(rows: list, pass_filter: str) -> str:
	"""Return the dominant lost_at_stage for one pass, or 'none'."""
	top = per_pass_stage_percents(rows, pass_filter)
	if not top:
		return "none"
	return top[0][0]


#============================================
def read_classification_summary(interval_dir: str) -> dict:
	"""Read CLASSIFICATION.md if present and surface its summary lines.

	The tool consumes Markdown lines starting with `**Top runs:**`,
	`**Top transitions:**`, `**Longest rejected run:**` if present, but
	a missing CLASSIFICATION.md is tolerated (the report falls back to
	verdicts.csv-only stats).

	Args:
		interval_dir: per-interval directory.

	Returns:
		dict: {"has_classification": bool, "raw_text": str}
	"""
	classification_path = os.path.join(interval_dir, "CLASSIFICATION.md")
	if not os.path.isfile(classification_path):
		return {"has_classification": False, "raw_text": ""}
	with open(classification_path, "r") as f:
		raw_text = f.read()
	return {"has_classification": True, "raw_text": raw_text}


#============================================
def compute_blob_accept_pcts(rows: list) -> tuple:
	"""Return (fwd_pct, bwd_pct) blob acceptance percent for one interval.

	Computed from the verdicts.csv rows directly (independent of
	per_video_selection.csv's blob_accept_fwd field, which may be
	synthesized on early sweeps). Returns (NaN, NaN) when a pass has
	no rows.

	Args:
		rows: verdicts.csv rows for one interval.

	Returns:
		tuple: (fwd_pct, bwd_pct).
	"""
	fwd_total = 0
	fwd_accept = 0
	bwd_total = 0
	bwd_accept = 0
	for row in rows:
		pass_name = row["pass"]
		gate = row["gate"]
		if pass_name == "FWD":
			fwd_total += 1
			if gate == "accepted":
				fwd_accept += 1
		elif pass_name == "BWD":
			bwd_total += 1
			if gate == "accepted":
				bwd_accept += 1
	fwd_pct = (100.0 * fwd_accept / fwd_total) if fwd_total > 0 else float("nan")
	bwd_pct = (100.0 * bwd_accept / bwd_total) if bwd_total > 0 else float("nan")
	return fwd_pct, bwd_pct


#============================================
def collect_per_interval_results(run_root: str, video_name: str, selection: dict) -> list:
	"""Build per-interval result dicts for one video.

	Args:
		run_root: Run-root directory.
		video_name: Video basename.
		selection: dict[bucket -> list of selection rows] for this video.

	Returns:
		list[dict]: one entry per interval matched in selection. Each
		dict carries:
			interval_id, bucket, interval_start, interval_end,
			selection_blob_accept_fwd, selection_blob_accept_bwd,
			fwd_blob_accept_pct, bwd_blob_accept_pct,
			fwd_dominant_stage, bwd_dominant_stage,
			fwd_top3, bwd_top3,
			fwd_torso_h_jitter, bwd_torso_h_jitter,
			winner_dist_h_counts, winner_dist_h_mode_index,
			has_classification.
	"""
	results = []
	# walk every interval listed in selection (rather than the on-disk
	# tree) so the report stays anchored on the plan's chosen sample
	for bucket, selection_rows in selection.items():
		for selection_row in selection_rows:
			start_frame = int(selection_row["interval_start"])
			end_frame = int(selection_row["interval_end"])
			interval_id = f"interval_{start_frame}_{end_frame}"
			interval_dir = os.path.join(
				run_root, "m2_sweep", video_name, interval_id,
			)
			verdicts_path = os.path.join(interval_dir, "verdicts.csv")
			if not os.path.isfile(verdicts_path):
				# missing verdicts.csv means the WP-1B sweep has not
				# reached this interval yet. Skip; the report will
				# still cover what is available.
				continue
			rows = read_verdicts_rows(verdicts_path)
			fwd_pct, bwd_pct = compute_blob_accept_pcts(rows)
			fwd_top3 = per_pass_stage_percents(rows, "FWD")
			bwd_top3 = per_pass_stage_percents(rows, "BWD")
			fwd_dominant = dominant_lost_at_stage(rows, "FWD")
			bwd_dominant = dominant_lost_at_stage(rows, "BWD")
			fwd_h = extract_raw_box_h_series(rows, "FWD")
			bwd_h = extract_raw_box_h_series(rows, "BWD")
			fwd_jitter = compute_torso_h_jitter(fwd_h)
			bwd_jitter = compute_torso_h_jitter(bwd_h)
			counts, mode_index = winner_dist_h_histogram(rows)
			classification = read_classification_summary(interval_dir)
			result = {
				"interval_id": interval_id,
				"bucket": bucket,
				"interval_start": start_frame,
				"interval_end": end_frame,
				"selection_blob_accept_fwd": int(selection_row["blob_accept_fwd"]),
				"selection_blob_accept_bwd": int(selection_row["blob_accept_bwd"]),
				"fwd_blob_accept_pct": fwd_pct,
				"bwd_blob_accept_pct": bwd_pct,
				"fwd_dominant_stage": fwd_dominant,
				"bwd_dominant_stage": bwd_dominant,
				"fwd_top3": fwd_top3,
				"bwd_top3": bwd_top3,
				"fwd_torso_h_jitter": fwd_jitter,
				"bwd_torso_h_jitter": bwd_jitter,
				"winner_dist_h_counts": counts,
				"winner_dist_h_mode_index": mode_index,
				"has_classification": classification["has_classification"],
			}
			results.append(result)
	# stable, deterministic ordering for the report
	results.sort(key=lambda r: (r["bucket"], r["interval_start"]))
	return results


#============================================
def read_tr_config_header(tr_config_dir: str, video_name: str) -> dict:
	"""Read seed count and race-start frame from tr_config artifacts.

	Returns dict with keys `seed_count`, `race_start_frame`,
	`total_intervals`, each holding either an int (when the source
	artifact resolves cleanly) or the sentinel "unknown" string.

	The tool tolerates missing or unreadable tr_config artifacts so the
	report can be generated on partial-corpus runs.
	"""
	header = {
		"seed_count": "unknown",
		"race_start_frame": "unknown",
		"total_intervals": "unknown",
	}
	if not tr_config_dir or not os.path.isdir(tr_config_dir):
		return header
	# seeds.json: list of seed dicts under `seeds`
	seeds_path = os.path.join(
		tr_config_dir, f"{video_name}.track_runner.seeds.json",
	)
	if os.path.isfile(seeds_path):
		with open(seeds_path, "r") as f:
			seeds_data = json.load(f)
		# indexed access fails loudly when the schema is malformed; that
		# is preferred to silently reporting unknown.
		seeds_list = seeds_data["seeds"]
		header["seed_count"] = len(seeds_list)
	# interval_scores.json: race_start_frame at top level; intervals list
	scores_path = os.path.join(
		tr_config_dir, f"{video_name}.track_runner.interval_scores.json",
	)
	if os.path.isfile(scores_path):
		with open(scores_path, "r") as f:
			scores_data = json.load(f)
		# race_start_frame is the canonical key per plan WP-1A
		if "race_start_frame" in scores_data:
			header["race_start_frame"] = int(scores_data["race_start_frame"])
		if "intervals" in scores_data:
			header["total_intervals"] = len(scores_data["intervals"])
	return header


#============================================
def format_top3(top3: list) -> str:
	"""Render the top-3 stage list as a short comma-separated string."""
	if not top3:
		return "(no rows)"
	parts = []
	for stage, percent in top3:
		parts.append(f"{stage} {percent:.1f}%")
	return ", ".join(parts)


#============================================
def format_jitter(jitter: float) -> str:
	"""Render a jitter value or NaN sentinel."""
	if math.isnan(jitter):
		return "NaN"
	return f"{jitter:.4f}"


#============================================
def format_accept_pct(pct: float) -> str:
	"""Render an accept percent or NaN sentinel."""
	if math.isnan(pct):
		return "NaN"
	return f"{pct:.1f}%"


#============================================
def format_interval_table(interval_results: list) -> list:
	"""Format an interval table for one bucket.

	Returns a list of Markdown lines (no trailing newline on the list
	itself; the caller joins).
	"""
	if not interval_results:
		return ["(none in this bucket)", ""]
	lines = []
	lines.append(
		"| interval | accept FWD | accept BWD | FWD dominant | "
		"BWD dominant | torso-h jitter FWD | torso-h jitter BWD |"
	)
	lines.append(
		"| --- | --- | --- | --- | --- | --- | --- |"
	)
	for result in interval_results:
		interval_id = result["interval_id"]
		fwd_pct = format_accept_pct(result["fwd_blob_accept_pct"])
		bwd_pct = format_accept_pct(result["bwd_blob_accept_pct"])
		fwd_dom = result["fwd_dominant_stage"]
		bwd_dom = result["bwd_dominant_stage"]
		fwd_jit = format_jitter(result["fwd_torso_h_jitter"])
		bwd_jit = format_jitter(result["bwd_torso_h_jitter"])
		lines.append(
			f"| {interval_id} | {fwd_pct} | {bwd_pct} | {fwd_dom} | "
			f"{bwd_dom} | {fwd_jit} | {bwd_jit} |"
		)
	lines.append("")
	# render top-3 stage percent lists under the table so per-pass detail
	# is captured without bloating the table width
	lines.append("Top-3 stage percents per pass:")
	lines.append("")
	for result in interval_results:
		interval_id = result["interval_id"]
		fwd_top3 = format_top3(result["fwd_top3"])
		bwd_top3 = format_top3(result["bwd_top3"])
		lines.append(f"- {interval_id} FWD: {fwd_top3}")
		lines.append(f"- {interval_id} BWD: {bwd_top3}")
	lines.append("")
	return lines


#============================================
def bucket_jitter_mean(interval_results: list) -> float:
	"""Return the mean of FWD and BWD jitters across a bucket.

	NaN jitters are dropped before averaging so partial sweeps do not
	corrupt the bucket mean.
	"""
	values = []
	for result in interval_results:
		fwd = result["fwd_torso_h_jitter"]
		bwd = result["bwd_torso_h_jitter"]
		if not math.isnan(fwd):
			values.append(fwd)
		if not math.isnan(bwd):
			values.append(bwd)
	if not values:
		return float("nan")
	return statistics.mean(values)


#============================================
def aggregate_winner_dist_h_mode(interval_results: list) -> tuple:
	"""Return (mode_index, mode_count) aggregated over all results."""
	totals = [0] * TOTAL_BINS
	for result in interval_results:
		counts = result["winner_dist_h_counts"]
		for index, value in enumerate(counts):
			totals[index] += value
	max_count = -1
	mode_index = 0
	for index, count in enumerate(totals):
		if count > max_count:
			max_count = count
			mode_index = index
	return mode_index, totals[mode_index]


#============================================
def bucket_dominant_stage(interval_results: list) -> str:
	"""Return the dominant FWD-dominant lost_at_stage across a bucket."""
	stage_counter: Counter = Counter()
	for result in interval_results:
		stage_counter[result["fwd_dominant_stage"]] += 1
	if not stage_counter:
		return "none"
	return stage_counter.most_common(1)[0][0]


#============================================
def build_video_report(
	video_name: str,
	header: dict,
	results_by_bucket: dict,
	hypothesis_rates: dict,
) -> str:
	"""Build the per-video Markdown report.

	Args:
		video_name: video basename.
		header: result of read_tr_config_header.
		results_by_bucket: dict[bucket -> list of per-interval results].
		hypothesis_rates: result of video_hypothesis_rates.

	Returns:
		str: report text.
	"""
	# pull the three plan-named buckets explicitly; "WEAK/FAIR low-accept"
	# is treated as positive-control noise here but kept in the corpus
	# rollup. The plan's body asks for TRUST 0%, positive-control, and
	# Mixed sections; "TRUST high-accept" is the positive-control bucket.
	trust_0 = results_by_bucket.get("TRUST 0%", [])
	pos_ctrl = results_by_bucket.get("TRUST high-accept", [])
	mixed = results_by_bucket.get("Mixed", [])
	# torso-h jitter summaries (per plan WP-1C body one-line summary)
	trust_0_jitter_mean = bucket_jitter_mean(trust_0)
	pos_ctrl_jitter_mean = bucket_jitter_mean(pos_ctrl)
	# aggregated winner_dist_h mode across all buckets for the per-video
	# histogram summary
	all_results = trust_0 + pos_ctrl + mixed
	winner_mode_index, winner_mode_count = aggregate_winner_dist_h_mode(all_results)

	lines = []
	lines.append(f"# Per-video blob-refinement report: {video_name}")
	lines.append("")
	lines.append("## Header")
	lines.append("")
	lines.append(f"- Video: {video_name}")
	lines.append(f"- Seed count: {header['seed_count']}")
	lines.append(f"- Race-start frame: {header['race_start_frame']}")
	lines.append(f"- Total intervals: {header['total_intervals']}")
	lines.append("")
	lines.append("## TRUST 0% intervals")
	lines.append("")
	lines.extend(format_interval_table(trust_0))
	lines.append("## Positive-control intervals (TRUST high-accept)")
	lines.append("")
	lines.extend(format_interval_table(pos_ctrl))
	lines.append("## Mixed intervals")
	lines.append("")
	lines.extend(format_interval_table(mixed))
	lines.append("## winner_dist_h histogram summary")
	lines.append("")
	if winner_mode_count > 0:
		lines.append(
			f"- Mode bin: {winner_dist_h_bin_label(winner_mode_index)} "
			f"({winner_mode_count} prox-rejected frames across all buckets)"
		)
	else:
		lines.append("- Mode bin: (no proximity-rejected frames in sample)")
	lines.append("")
	lines.append("## Hypothesis verdicts at 30% threshold")
	lines.append("")
	lines.append(
		"H1, H2, H6, H8 verdicts pulled from the per-video block of "
		"`oracle/HYPOTHESIS_TESTS.json`. Supported when rate >= 0.30."
	)
	lines.append("")
	lines.append("| hypothesis | rate | n_eligible | supported |")
	lines.append("| --- | --- | --- | --- |")
	for h_key in ("h1", "h2", "h6", "h8"):
		entry = hypothesis_rates[h_key]
		supported_label = "YES" if entry["supported"] else "NO"
		lines.append(
			f"| {h_key.upper()} | {entry['rate']:.4f} | "
			f"{entry['n_eligible']} | {supported_label} |"
		)
	lines.append("")
	lines.append("## Torso-h jitter summary")
	lines.append("")
	lines.append(
		f"- trust_0_jitter_mean: {format_jitter(trust_0_jitter_mean)}"
	)
	lines.append(
		f"- pos_ctrl_jitter_mean: {format_jitter(pos_ctrl_jitter_mean)}"
	)
	lines.append("")
	return "\n".join(lines)


#============================================
def build_corpus_index(video_payloads: list) -> str:
	"""Build the corpus_index.md text linking every per-video file.

	Args:
		video_payloads: list of dicts emitted by build_summary_row, plus
			the rendered filename so corpus_index.md can link it.

	Returns:
		str: report text.
	"""
	lines = []
	lines.append("# Corpus-wide per-video index")
	lines.append("")
	lines.append(
		"One row per video. Click through to the per-video report for "
		"per-interval detail. Hypothesis columns reflect the 30% "
		"threshold per plan WP-1C."
	)
	lines.append("")
	lines.append("## Per-video links")
	lines.append("")
	for payload in video_payloads:
		# link to the per-video file inside this same directory; the
		# report references the bare filename so the link works from
		# corpus_index.md.
		link_target = payload["filename"]
		lines.append(f"- [{link_target}]({link_target})")
	lines.append("")
	lines.append("## Per-video summary table")
	lines.append("")
	lines.append(
		"| video | trust_0_count | pos_ctrl_count | mixed_count | "
		"dom stage trust_0 | dom stage pos_ctrl | h1 | h2 | h6 | h8 | "
		"trust_0 jitter | pos_ctrl jitter |"
	)
	lines.append(
		"| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
		"--- | --- |"
	)
	for payload in video_payloads:
		row = payload["summary_row"]
		lines.append(
			"| {video} | {trust_0_count} | {pos_ctrl_count} | "
			"{mixed_count} | {dom_t0} | {dom_pc} | {h1} | {h2} | "
			"{h6} | {h8} | {jit_t0} | {jit_pc} |".format(
				video=row["video"],
				trust_0_count=row["trust_0_count"],
				pos_ctrl_count=row["pos_ctrl_count"],
				mixed_count=row["mixed_count"],
				dom_t0=row["dominant_lost_stage_trust_0"],
				dom_pc=row["dominant_lost_stage_pos_ctrl"],
				h1=row["h1_supported"],
				h2=row["h2_supported"],
				h6=row["h6_supported"],
				h8=row["h8_supported"],
				jit_t0=format_jitter(row["trust_0_jitter_mean"]),
				jit_pc=format_jitter(row["pos_ctrl_jitter_mean"]),
			)
		)
	lines.append("")
	return "\n".join(lines)


#============================================
def build_summary_row(
	video_name: str,
	results_by_bucket: dict,
	hypothesis_rates: dict,
) -> dict:
	"""Return one per_video_summary.csv row dict for the given video.

	Columns match plan WP-1C exactly:
		video, trust_0_count, pos_ctrl_count, mixed_count,
		dominant_lost_stage_trust_0, dominant_lost_stage_pos_ctrl,
		h1_supported, h2_supported, h6_supported, h8_supported,
		trust_0_jitter_mean, pos_ctrl_jitter_mean,
		winner_dist_h_mode_bin_low.
	"""
	trust_0 = results_by_bucket.get("TRUST 0%", [])
	pos_ctrl = results_by_bucket.get("TRUST high-accept", [])
	mixed = results_by_bucket.get("Mixed", [])
	all_results = trust_0 + pos_ctrl + mixed
	mode_index, _ = aggregate_winner_dist_h_mode(all_results)
	row = {
		"video": video_name,
		"trust_0_count": len(trust_0),
		"pos_ctrl_count": len(pos_ctrl),
		"mixed_count": len(mixed),
		"dominant_lost_stage_trust_0": bucket_dominant_stage(trust_0),
		"dominant_lost_stage_pos_ctrl": bucket_dominant_stage(pos_ctrl),
		"h1_supported": 1 if hypothesis_rates["h1"]["supported"] else 0,
		"h2_supported": 1 if hypothesis_rates["h2"]["supported"] else 0,
		"h6_supported": 1 if hypothesis_rates["h6"]["supported"] else 0,
		"h8_supported": 1 if hypothesis_rates["h8"]["supported"] else 0,
		"trust_0_jitter_mean": bucket_jitter_mean(trust_0),
		"pos_ctrl_jitter_mean": bucket_jitter_mean(pos_ctrl),
		"winner_dist_h_mode_bin_low": winner_dist_h_bin_low(mode_index),
	}
	return row


#============================================
SUMMARY_CSV_FIELDS = [
	"video",
	"trust_0_count",
	"pos_ctrl_count",
	"mixed_count",
	"dominant_lost_stage_trust_0",
	"dominant_lost_stage_pos_ctrl",
	"h1_supported",
	"h2_supported",
	"h6_supported",
	"h8_supported",
	"trust_0_jitter_mean",
	"pos_ctrl_jitter_mean",
	"winner_dist_h_mode_bin_low",
]


#============================================
def write_summary_csv(output_dir: str, summary_rows: list) -> str:
	"""Write per_video_summary.csv with the plan WP-1C columns.

	Args:
		output_dir: output directory.
		summary_rows: list of dicts (one per video) with SUMMARY_CSV_FIELDS.

	Returns:
		str: absolute path to the written CSV.
	"""
	csv_path = os.path.join(output_dir, "per_video_summary.csv")
	with open(csv_path, "w", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=SUMMARY_CSV_FIELDS)
		writer.writeheader()
		for row in summary_rows:
			# Format NaN jitter values as empty cells rather than the
			# literal string "nan" so downstream CSV consumers can spot
			# missing data without string-comparing.
			out_row = dict(row)
			for jitter_key in ("trust_0_jitter_mean", "pos_ctrl_jitter_mean"):
				value = out_row[jitter_key]
				if isinstance(value, float) and math.isnan(value):
					out_row[jitter_key] = ""
				else:
					out_row[jitter_key] = f"{float(value):.6f}"
			out_row["winner_dist_h_mode_bin_low"] = (
				f"{float(out_row['winner_dist_h_mode_bin_low']):.1f}"
			)
			writer.writerow(out_row)
	return csv_path


#============================================
def write_text_file(output_path: str, text: str) -> None:
	"""Write `text` to `output_path`, creating parent dirs as needed."""
	parent = os.path.dirname(os.path.abspath(output_path))
	if parent and not os.path.isdir(parent):
		os.makedirs(parent, exist_ok=True)
	with open(output_path, "w") as f:
		f.write(text)


#============================================
def render_all(
	run_root: str,
	output_dir: str,
	tr_config_dir: str,
) -> dict:
	"""Run the full pipeline and write all output files.

	Args:
		run_root: run-root directory.
		output_dir: output directory.
		tr_config_dir: optional tr_config directory (may be empty string).

	Returns:
		dict: {"per_video_paths": list[str], "index_path": str,
			"summary_csv_path": str} for the artifacts that landed on
			disk.
	"""
	selection_path = os.path.join(
		run_root, "m2_sweep", "per_video_selection.csv",
	)
	selection_rows = load_selection_rows(selection_path)
	grouped = group_selection_by_video(selection_rows)
	hypothesis_data = load_hypothesis_json(run_root)

	if not os.path.isdir(output_dir):
		os.makedirs(output_dir, exist_ok=True)

	per_video_paths = []
	video_payloads = []
	summary_rows = []
	for video_name in sorted(grouped.keys()):
		results = collect_per_interval_results(
			run_root, video_name, grouped[video_name],
		)
		# regroup by bucket for the report so each section reads cleanly
		results_by_bucket: dict = {}
		for result in results:
			bucket = result["bucket"]
			if bucket not in results_by_bucket:
				results_by_bucket[bucket] = []
			results_by_bucket[bucket].append(result)
		hypothesis_rates = video_hypothesis_rates(hypothesis_data, video_name)
		header = read_tr_config_header(tr_config_dir, video_name)
		report = build_video_report(
			video_name, header, results_by_bucket, hypothesis_rates,
		)
		filename = f"{video_name}.md"
		output_path = os.path.join(output_dir, filename)
		write_text_file(output_path, report)
		per_video_paths.append(output_path)
		summary_row = build_summary_row(
			video_name, results_by_bucket, hypothesis_rates,
		)
		summary_rows.append(summary_row)
		video_payloads.append({
			"filename": filename,
			"summary_row": summary_row,
		})

	index_text = build_corpus_index(video_payloads)
	index_path = os.path.join(output_dir, "corpus_index.md")
	write_text_file(index_path, index_text)
	summary_csv_path = write_summary_csv(output_dir, summary_rows)
	return {
		"per_video_paths": per_video_paths,
		"index_path": index_path,
		"summary_csv_path": summary_csv_path,
	}


#============================================
def main() -> None:
	"""Entry point."""
	args = parse_args()
	run_root, output_dir = resolve_paths(args)
	# tr_config_dir is optional; default to "" so the header reads
	# "unknown" without crashing when the directory is absent
	tr_config_dir = args.tr_config_dir if args.tr_config_dir else ""
	written = render_all(run_root, output_dir, tr_config_dir)
	# brief stdout summary so the user has a count line without opening
	# the report files
	n_per_video = len(written["per_video_paths"])
	print(
		f"wrote {n_per_video} per-video reports under {output_dir}: "
		f"index={written['index_path']} "
		f"summary_csv={written['summary_csv_path']}"
	)


#============================================
if __name__ == "__main__":
	main()
