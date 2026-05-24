#!/usr/bin/env python3
"""WP-1B Step 1: emit the per-video interval selection CSV for the M2 sweep.

For each `tr_config/*.track_runner.interval_scores.json` (one per video),
this tool builds two disjoint stratified-random samples of intervals:

- Ranking sample (M1 / M2 / M3 testing): up to 5 TRUST-tier intervals
  plus up to 2 WEAK/FAIR-tier intervals per video. Maximum corpus
  footprint is 12 * 7 = 84 rows.
- Held-out sample (M4 confirmation): up to 1 TRUST-tier and up to 1
  WEAK/FAIR-tier interval per video drawn from the same shuffled pool
  AFTER the ranking sample, so the two samples are disjoint. Maximum
  corpus footprint is 12 * 2 = 24 rows.

The selection is reproducible: a single `numpy.random.default_rng(seed)`
instance drives all per-tier shuffles, and the seed is recorded in
every output row. Default seed is 20260523 per plan
`/Users/vosslab/.claude/plans/kind-exploring-cray.md` section
"Per-video discovery and held-out sampling".

Tier mapping (per WP-1B spec):
- TRUST tier  = interval_score.confidence_tier == "high"
- WEAK/FAIR   = interval_score.confidence_tier in ("low", "fair")
- All other tiers ("good", "pre_race") are ignored for selection.

Bucket assignment is intentionally a placeholder: `interval_scores.json`
does not record `blob_accept_fwd` or `blob_accept_bwd`, so the TRUST 0%
/ TRUST high-accept / Mixed / WEAK low-accept buckets defined in the
plan cannot be resolved here. The output CSV uses the placeholders
`trust_pending` and `weak_fair_pending` for the bucket column and the
literal string `pending_sweep` for both blob-accept columns. A later
post-sweep rebucket step (separate tool) finalizes the bucket assignment
once the WP-0B visualizer writes corridor-blob lists.

This tool is READ-ONLY on `tr_config/`. It only writes to the path
passed to `--output`, which by default lives under `output_smoke/`.

Usage:
	source source_me.sh && python3 tools/build_interval_selection.py
"""

# Standard Library
import os
import csv
import json
import glob
import argparse

# PIP3 modules
import numpy

# default paths resolved relative to the working directory at invoke time
DEFAULT_INTERVAL_SCORES_DIR = "tr_config"
DEFAULT_OUTPUT_PATH = (
	"output_smoke/blob_refine_fix/2026-05-23/m2_sweep/per_video_selection.csv"
)
DEFAULT_RNG_SEED = 20260523

# per-video ranking quotas
RANKING_TRUST_QUOTA = 5
RANKING_WEAK_FAIR_QUOTA = 2

# per-video held-out quotas (1 per tier so the held-out corpus is 12 * 2 = 24)
HELD_OUT_TRUST_QUOTA = 1
HELD_OUT_WEAK_FAIR_QUOTA = 1

# tier labels recognized as TRUST / WEAK-FAIR for selection purposes
TRUST_TIERS = ("high",)
WEAK_FAIR_TIERS = ("low", "fair")

# placeholder values populated in the output CSV (see module docstring)
TRUST_BUCKET_PLACEHOLDER = "trust_pending"
WEAK_FAIR_BUCKET_PLACEHOLDER = "weak_fair_pending"
BLOB_ACCEPT_PLACEHOLDER = "pending_sweep"

# CSV column order is locked here so the downstream WP-1C reader
# (tools/per_video_report.py) sees the same header it consumes today.
CSV_FIELDNAMES = (
	"rng_seed",
	"sample",
	"video",
	"interval_start",
	"interval_end",
	"bucket",
	"tier",
	"agreement_score",
	"blob_accept_fwd",
	"blob_accept_bwd",
)

# trailing token stripped from interval_scores.json basenames to derive
# the human-readable video identifier used in the CSV
INTERVAL_SCORES_SUFFIX = ".track_runner.interval_scores.json"


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Build the per-video interval selection CSV used by the "
			"WP-1B M2 sweep. Reads tr_config/*.interval_scores.json and "
			"writes a stratified-random ranking + held-out sample as CSV."
		),
	)
	parser.add_argument(
		"-i", "--interval-scores-dir", dest="interval_scores_dir",
		default=DEFAULT_INTERVAL_SCORES_DIR,
		help=(
			"Directory holding *.track_runner.interval_scores.json files. "
			f"Defaults to {DEFAULT_INTERVAL_SCORES_DIR}."
		),
	)
	parser.add_argument(
		"-o", "--output", dest="output_path",
		default=DEFAULT_OUTPUT_PATH,
		help=(
			"Destination CSV path. The tool creates parent directories "
			f"as needed. Defaults to {DEFAULT_OUTPUT_PATH}."
		),
	)
	parser.add_argument(
		"-s", "--rng-seed", dest="rng_seed", type=int,
		default=DEFAULT_RNG_SEED,
		help=(
			"Integer seed for numpy.random.default_rng. The value is "
			"recorded in every output row. Defaults to "
			f"{DEFAULT_RNG_SEED}."
		),
	)
	args = parser.parse_args()
	return args


#============================================
def discover_interval_scores_paths(scores_dir: str) -> list:
	"""Return sorted *.interval_scores.json paths under `scores_dir`.

	Args:
		scores_dir: Path to the directory holding interval_scores files.

	Returns:
		list[str]: Sorted absolute paths to each interval_scores.json file.
	"""
	if not os.path.isdir(scores_dir):
		raise FileNotFoundError(
			f"interval-scores directory does not exist: {scores_dir}"
		)
	# glob pattern matches the canonical file name produced by the solver
	pattern = os.path.join(scores_dir, "*" + INTERVAL_SCORES_SUFFIX)
	paths = sorted(glob.glob(pattern))
	if not paths:
		raise FileNotFoundError(
			f"no {INTERVAL_SCORES_SUFFIX} files found under: {scores_dir}"
		)
	# normalize to absolute paths so downstream messages are unambiguous
	absolute_paths = []
	for path in paths:
		absolute_paths.append(os.path.abspath(path))
	return absolute_paths


#============================================
def video_basename_from_path(scores_path: str) -> str:
	"""Strip the canonical suffix from an interval_scores.json basename."""
	base = os.path.basename(scores_path)
	if not base.endswith(INTERVAL_SCORES_SUFFIX):
		raise ValueError(
			f"unexpected interval-scores filename: {base}; "
			f"expected suffix {INTERVAL_SCORES_SUFFIX}"
		)
	# slice off the suffix to leave the original video identifier
	stem = base[: -len(INTERVAL_SCORES_SUFFIX)]
	return stem


#============================================
def load_intervals(scores_path: str) -> list:
	"""Load the `intervals` list from one interval_scores.json file.

	Args:
		scores_path: Path to the interval_scores.json file.

	Returns:
		list[dict]: Interval records exactly as stored on disk.
	"""
	with open(scores_path, "r") as handle:
		document = json.load(handle)
	# required keys -- fail loudly when the schema is missing fields
	intervals = document["intervals"]
	return intervals


#============================================
def partition_intervals_by_tier(intervals: list) -> tuple:
	"""Split intervals into TRUST-tier and WEAK/FAIR-tier pools.

	Tier names are read directly from `interval_score.confidence_tier`.
	`good` and `pre_race` tiers are intentionally dropped per the WP-1B
	selection rule.

	Args:
		intervals: The `intervals` list from interval_scores.json.

	Returns:
		tuple[list[dict], list[dict]]: (trust_pool, weak_fair_pool).
	"""
	trust_pool = []
	weak_fair_pool = []
	for interval in intervals:
		# fail loudly when interval_score block is missing
		score_block = interval["interval_score"]
		tier = score_block["confidence_tier"]
		if tier in TRUST_TIERS:
			trust_pool.append(interval)
		elif tier in WEAK_FAIR_TIERS:
			weak_fair_pool.append(interval)
		# all other tiers (good, pre_race) are skipped for selection
	return trust_pool, weak_fair_pool


#============================================
def shuffle_pool(pool: list, rng: numpy.random.Generator) -> list:
	"""Return a new list of `pool` permuted by `rng`.

	The shuffle is performed via numpy's permutation index so the
	per-pool draw advances the same RNG state across the whole tool
	run (ranking sample first, held-out sample second).

	Args:
		pool: List of interval dicts (untouched by this function).
		rng: Shared numpy RNG instance.

	Returns:
		list[dict]: Shuffled copy of `pool`.
	"""
	if not pool:
		return []
	# numpy.permutation copies the input so the original list is preserved
	order = rng.permutation(len(pool))
	shuffled = []
	for index in order:
		shuffled.append(pool[int(index)])
	return shuffled


#============================================
def split_ranking_held_out(
	shuffled_pool: list,
	ranking_quota: int,
	held_out_quota: int,
) -> tuple:
	"""Split a shuffled pool into a ranking slice and a held-out slice.

	If `len(shuffled_pool) <= ranking_quota`, the held-out slice is
	empty. The two slices are always disjoint.

	Args:
		shuffled_pool: Output of `shuffle_pool`.
		ranking_quota: Maximum ranking-sample size.
		held_out_quota: Maximum held-out-sample size taken AFTER ranking.

	Returns:
		tuple[list[dict], list[dict]]: (ranking_slice, held_out_slice).
	"""
	ranking_slice = shuffled_pool[:ranking_quota]
	# held-out picks up where ranking left off so the two are disjoint
	held_out_start = ranking_quota
	held_out_end = ranking_quota + held_out_quota
	held_out_slice = shuffled_pool[held_out_start:held_out_end]
	return ranking_slice, held_out_slice


#============================================
def build_row(
	rng_seed: int,
	sample: str,
	video: str,
	interval: dict,
	bucket: str,
) -> dict:
	"""Build one output CSV row for the given interval selection.

	Args:
		rng_seed: Integer seed recorded in every row.
		sample: Either "ranking" or "held_out".
		video: Video identifier (basename without the canonical suffix).
		interval: Interval dict as loaded from interval_scores.json.
		bucket: Placeholder bucket label (`trust_pending` /
			`weak_fair_pending`).

	Returns:
		dict: Row keyed by `CSV_FIELDNAMES`.
	"""
	# all keys here are required by the contract; missing ones must fail
	score_block = interval["interval_score"]
	row = {
		"rng_seed": rng_seed,
		"sample": sample,
		"video": video,
		"interval_start": interval["start_frame"],
		"interval_end": interval["end_frame"],
		"bucket": bucket,
		"tier": score_block["confidence_tier"],
		"agreement_score": score_block["agreement"],
		"blob_accept_fwd": BLOB_ACCEPT_PLACEHOLDER,
		"blob_accept_bwd": BLOB_ACCEPT_PLACEHOLDER,
	}
	return row


#============================================
def select_for_video(
	video: str,
	intervals: list,
	rng: numpy.random.Generator,
	rng_seed: int,
) -> list:
	"""Run the per-video ranking + held-out selection.

	The ranking sample is drawn first for both tiers so the held-out
	sample state always advances the same RNG sequence.

	Args:
		video: Video identifier.
		intervals: `intervals` list from interval_scores.json.
		rng: Shared numpy RNG instance.
		rng_seed: Integer seed recorded in each row.

	Returns:
		list[dict]: Ranking rows followed by held-out rows for this video.
	"""
	trust_pool, weak_fair_pool = partition_intervals_by_tier(intervals)

	# shuffle each pool with the shared RNG before slicing; the ranking
	# slice (first 5 / 2) and the held-out slice (next 1 / 1) come from
	# the same shuffle so they are disjoint by construction
	shuffled_trust = shuffle_pool(trust_pool, rng)
	shuffled_weak_fair = shuffle_pool(weak_fair_pool, rng)

	ranking_trust, held_out_trust = split_ranking_held_out(
		shuffled_trust, RANKING_TRUST_QUOTA, HELD_OUT_TRUST_QUOTA,
	)
	ranking_weak_fair, held_out_weak_fair = split_ranking_held_out(
		shuffled_weak_fair,
		RANKING_WEAK_FAIR_QUOTA,
		HELD_OUT_WEAK_FAIR_QUOTA,
	)

	# build ranking rows: trust slice then weak/fair slice
	rows = []
	for interval in ranking_trust:
		row = build_row(
			rng_seed, "ranking", video, interval, TRUST_BUCKET_PLACEHOLDER,
		)
		rows.append(row)
	for interval in ranking_weak_fair:
		row = build_row(
			rng_seed, "ranking", video, interval,
			WEAK_FAIR_BUCKET_PLACEHOLDER,
		)
		rows.append(row)

	# then held-out rows in the same per-tier order so the CSV layout is
	# predictable when a human eyeballs the file
	for interval in held_out_trust:
		row = build_row(
			rng_seed, "held_out", video, interval, TRUST_BUCKET_PLACEHOLDER,
		)
		rows.append(row)
	for interval in held_out_weak_fair:
		row = build_row(
			rng_seed, "held_out", video, interval,
			WEAK_FAIR_BUCKET_PLACEHOLDER,
		)
		rows.append(row)

	return rows


#============================================
def build_all_rows(
	interval_scores_dir: str,
	rng_seed: int,
) -> list:
	"""Walk every interval_scores file and build the full row list.

	Videos are processed in sorted basename order so the RNG advances
	deterministically regardless of filesystem walk order.

	Args:
		interval_scores_dir: Directory holding interval_scores.json files.
		rng_seed: Seed for `numpy.random.default_rng`.

	Returns:
		list[dict]: All ranking + held-out rows across every video.
	"""
	paths = discover_interval_scores_paths(interval_scores_dir)
	# single shared RNG so per-video draws share one advancing state
	rng = numpy.random.default_rng(rng_seed)
	all_rows = []
	for scores_path in paths:
		video = video_basename_from_path(scores_path)
		intervals = load_intervals(scores_path)
		video_rows = select_for_video(video, intervals, rng, rng_seed)
		all_rows.extend(video_rows)
	return all_rows


#============================================
def write_csv(rows: list, output_path: str) -> None:
	"""Write the selection rows to `output_path` as CSV.

	Parent directories are created as needed so the default
	output_smoke/... path works on a clean checkout.

	Args:
		rows: List of row dicts keyed by `CSV_FIELDNAMES`.
		output_path: Absolute or relative destination path.
	"""
	parent_dir = os.path.dirname(os.path.abspath(output_path))
	os.makedirs(parent_dir, exist_ok=True)
	# newline='' is required on Windows-friendly csv writers; harmless on unix
	with open(output_path, "w", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDNAMES))
		writer.writeheader()
		writer.writerows(rows)


#============================================
def summarize_rows(rows: list) -> dict:
	"""Build a counts summary for printing after a successful write.

	Args:
		rows: List of selection row dicts.

	Returns:
		dict: Aggregated counts (total, ranking, held_out, per-bucket).
	"""
	total = len(rows)
	ranking = 0
	held_out = 0
	bucket_counts = {}
	for row in rows:
		if row["sample"] == "ranking":
			ranking += 1
		elif row["sample"] == "held_out":
			held_out += 1
		bucket_key = row["bucket"]
		if bucket_key not in bucket_counts:
			bucket_counts[bucket_key] = 0
		bucket_counts[bucket_key] += 1
	summary = {
		"total": total,
		"ranking": ranking,
		"held_out": held_out,
		"bucket_counts": bucket_counts,
	}
	return summary


#============================================
def main() -> None:
	"""Tool entry point."""
	args = parse_args()
	rows = build_all_rows(args.interval_scores_dir, args.rng_seed)
	write_csv(rows, args.output_path)
	summary = summarize_rows(rows)
	# emit a compact human-readable summary so the operator confirms the
	# write succeeded without re-opening the CSV
	print(f"wrote {summary['total']} rows to {args.output_path}")
	print(f"  ranking: {summary['ranking']}")
	print(f"  held_out: {summary['held_out']}")
	for bucket, count in sorted(summary["bucket_counts"].items()):
		print(f"  bucket {bucket}: {count}")


if __name__ == "__main__":
	main()
