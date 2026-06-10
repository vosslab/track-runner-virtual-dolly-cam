#!/usr/bin/env python3
"""Golden-output baseline harness for the blob_walk_v2 windowed walker.

This is a non-browser E2E gate (see docs/E2E_TESTS.md). It walks a FIXED
set of EXACTLY 4 seed-to-seed intervals (2 per video across two videos),
captures the per-frame verdict CSVs plus per-interval accepted_fraction,
and either writes them as a golden snapshot (--update-baseline) or compares
a fresh walk against the snapshot under the "very-very-close" policy.

WHY 4 FIXED INTERVALS: a full walk of every post_start interval takes 25+
minutes (Conant has 339 post_start intervals, Jason has 629). This gate must
run after every refactor patch, so it walks exactly 4 small, fixed intervals
and finishes in well under 15 minutes. The interval set is hard-coded below
and walked EXPLICITLY by (left_frame, right_frame) seed pair; the harness
never relies on the walker's interval-count default, and it never enumerates-
then-walks all intervals.

Compare policy (per WP0.1):
  - per-frame status column and categorical/flag columns match EXACTLY
  - numeric columns (positions, velocities, scores) match within abs <= 0.5
  - per-interval accepted_fraction matches within 1e-6
Any status/flag diff or any numeric field out of tolerance exits non-zero.

This file is an E2E runner (tests/e2e/, e2e_*.py), not a pytest module.
Run it via tests/e2e/e2e_blob_walk_baseline.sh, or directly:
  source source_me.sh && python3 tests/e2e/e2e_blob_walk_baseline.py walk --output-dir <dir>
  source source_me.sh && python3 tests/e2e/e2e_blob_walk_baseline.py compare \\
      --fresh-dir <dir> --baseline-dir <dir>
"""

# Standard Library
import os
import csv
import sys
import argparse
import pathlib

# Resolve repo root from this file: tests/e2e/ -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BLOB_WALK_DIR = os.path.join(_REPO_ROOT, 'tools', 'blob_walk_v2')
# blob_walk_v2 modules (root, core/, render/) and track_runner modules import
# each other by bare name. Put the package root on sys.path so the bare import
# walk_paths resolves, then call walk_paths.setup() to wire up track_runner,
# tests, repo root, and the core/ and render/ subdirs -- matching how the
# entry script and walk_driver bootstrap sys.path at runtime.
if _BLOB_WALK_DIR not in sys.path:
	sys.path.insert(0, _BLOB_WALK_DIR)
import walk_paths
walk_paths.setup()

# local repo modules (blob_walk_v2 + track_runner, imported by bare name)
import blob_walk.walk_io as walk_io
import walk_driver


#============================================
# Fixed baseline interval set.
#============================================
# EXACTLY 4 intervals, walked explicitly by seed-pair frame indices. These are
# consecutive seed pairs verified present in each video's seeds file (enumerated
# via walk_io.enumerate_seed_to_seed_intervals). Two videos x two intervals
# each, mixing a near-bootstrap interval (early, right after race start) with a
# steady-state mid-race interval. Both videos have a dump_step1/ dir and live in
# TRACK_VIDEOS/. Frame counts kept small so the gate stays fast.
#
# Conant race_start_frame = 1064; first post_start interval is [1080, 1111].
# Jason  race_start_frame = 268;  first post_start interval is [348, 450].
BASELINE_INTERVALS = (
	# (video_basename, left_frame, right_frame, role)
	("Conant-4x400-2026_April_15.mkv", 1080, 1111, "bootstrap"),
	("Conant-4x400-2026_April_15.mkv", 1296, 1327, "steady_state"),
	("Jason-3200m-sectionals-IMG_4005.mkv", 564, 583, "early"),
	("Jason-3200m-sectionals-IMG_4005.mkv", 602, 629, "steady_state"),
)

# Walk parameters held fixed for the gate (match walk_driver defaults so the
# snapshot reflects production walker behavior). Render and HTML are disabled:
# the gate only needs the verdict CSVs, and skipping render keeps it fast.
WINNER_MODE = "production_winner"
AUDIT_RULE = None
RENDER_TILES = False


#============================================
# Column classification for the compare policy.
#============================================
# Categorical / identity / flag columns: must match EXACTLY (string equality).
# This includes the per-frame status column and every categorical or
# integer-count column whose meaning is a label or a discrete count, not a
# continuous measurement.
EXACT_MATCH_COLUMNS = frozenset({
	"frame_index",
	"step",
	"direction",
	"dt_for_gate",
	"winner_mode",
	"audit_winner_rule",
	"obs_corridor_n",
	"obs_raw_n",
	"candidates_json",
	"status",
	"reject_reason",
	"stop_reason",
	"roi_anchor_source",
	"candidates_in_window",
})

# Numeric measurement columns: compared with abs(diff) <= NUMERIC_TOLERANCE.
# Positions (cx/cy/scene), velocities, jumps, scores, confidences, and the
# Viterbi path_cost are continuous values where tiny float drift is acceptable.
NUMERIC_TOLERANCE = 0.5
NUMERIC_COLUMNS = frozenset({
	"dt",
	"torso_w_px",
	"torso_h_px",
	"prev_cx",
	"prev_cy",
	"prev_scene_x",
	"prev_scene_y",
	"pred_cx",
	"pred_cy",
	"cand_cx",
	"cand_cy",
	"cand_scene_x",
	"cand_scene_y",
	"v_recent_scene_mag",
	"expected_jump",
	"allowed_jump",
	"actual_jump",
	"production_winner_cx",
	"production_winner_cy",
	"audit_winner_cx",
	"audit_winner_cy",
	"obs_confidence",
	"winner_strength_score",
	"winner_size_score",
	"winner_proximity_score",
	"winner_total_score",
	"path_cost",
	"provisional_cx_px",
	"provisional_cy_px",
})

# accepted_fraction comparison tolerance (per WP0.1).
ACCEPTED_FRACTION_TOLERANCE = 1e-6

# Status values excluded from the total_visited denominator when computing
# accepted_fraction. Diagnostic terminal markers are not real visits.
NON_VISIT_STATUS = frozenset({
	"after_walk_terminated",
})


#============================================
def _interval_dirname(left_frame: int, right_frame: int) -> str:
	"""Return the per-interval subdir name used by walk_driver."""
	# Matches walk_driver.main: video_output_dir / f"seed_{left}_{right}".
	name = f"seed_{left_frame}_{right_frame}"
	return name


#============================================
def walk_baseline(output_dir: pathlib.Path) -> None:
	"""Walk the 4 fixed intervals into output_dir, writing verdict CSVs.

	Opens each video's reader once, loads its processed-pixel seeds, then calls
	walk_driver.run_interval_walk for each chosen interval of that video. Writes
	exactly the four interval directories; never walks any other interval.

	Args:
		output_dir: Root directory for the walk output. One subdir per video,
			and per interval inside it, mirroring walk_driver's layout.
	"""
	output_dir.mkdir(parents=True, exist_ok=True)

	# Group the fixed intervals by video so we open each reader only once.
	intervals_by_video = {}
	for video_basename, left_frame, right_frame, _role in BASELINE_INTERVALS:
		intervals_by_video.setdefault(video_basename, []).append((left_frame, right_frame))

	for video_basename, frame_pairs in intervals_by_video.items():
		print(f"[baseline] opening reader for {video_basename}")
		reader, probe_info = walk_io.open_walker_reader(video_basename)
		# Load processed-pixel seeds (Option A coords) keyed to reader geometry.
		seeds_view = walk_io.load_walker_seeds_view(video_basename, reader.geometry)
		seeds_view.assert_geometry_match(reader.geometry)
		scene_transform = walk_io.load_walker_scene_transform(video_basename)

		# Build a frame_index -> processed-pixel seed lookup, exactly as
		# walk_driver.main does. The walker consumes processed-pixel coords.
		proc_seed_by_frame = {s["frame_index"]: s for s in seeds_view.seeds}

		video_output_dir = output_dir / video_basename
		for left_frame, right_frame in frame_pairs:
			# Fail loudly if a chosen frame is not an actual seed (frame pairs
			# come from enumeration, so a miss means the seeds file changed).
			left_seed = proc_seed_by_frame[left_frame]
			right_seed = proc_seed_by_frame[right_frame]

			interval_dir = video_output_dir / _interval_dirname(left_frame, right_frame)
			print(f"[baseline] walking interval [{left_frame}, {right_frame}] -> {interval_dir}")
			walk_driver.run_interval_walk(
				left_seed=left_seed,
				right_seed=right_seed,
				reader=reader,
				scene_transform=scene_transform,
				probe_info=probe_info,
				output_interval_dir=interval_dir,
				winner_mode=WINNER_MODE,
				audit_rule=AUDIT_RULE,
				render_tiles=RENDER_TILES,
			)

		reader.close()

	print(f"[baseline] walk complete: {output_dir}")


#============================================
def read_verdict_rows(csv_path: pathlib.Path) -> list:
	"""Read a verdict CSV into a list of dict rows (one per frame)."""
	rows = []
	with open(csv_path, newline='') as f:
		reader = csv.DictReader(f)
		for row in reader:
			rows.append(row)
	return rows


#============================================
def compute_accepted_fraction(rows: list) -> float:
	"""Compute accepted_fraction for one verdict CSV.

	accepted_fraction = accepted_count / total_visited, where accepted_count is
	the number of rows with status == "accepted", and total_visited excludes
	diagnostic terminal-marker rows (after_walk_terminated).

	Returns 0.0 when total_visited is 0 (no real visits).
	"""
	accepted_count = 0
	total_visited = 0
	for row in rows:
		status = row["status"]
		if status in NON_VISIT_STATUS:
			continue
		total_visited += 1
		if status == "accepted":
			accepted_count += 1
	if total_visited == 0:
		return 0.0
	fraction = accepted_count / total_visited
	return fraction


#============================================
def _parse_optional_float(text: str):
	"""Parse a CSV cell to float, or return None for a blank cell."""
	# Walker writes blank cells (empty string) for unavailable numeric values.
	if text is None or text.strip() == "":
		return None
	value = float(text)
	return value


#============================================
def compare_row(
	column_names: list,
	baseline_row: dict,
	fresh_row: dict,
	frame_label: str,
	csv_label: str,
) -> list:
	"""Compare one frame row across all columns. Returns a list of diff strings."""
	diffs = []
	for column in column_names:
		baseline_cell = baseline_row[column]
		fresh_cell = fresh_row[column]
		if column in EXACT_MATCH_COLUMNS:
			# Categorical / flag / identity column: exact string match required.
			if baseline_cell != fresh_cell:
				diffs.append(
					f"{csv_label} {frame_label} column '{column}' (exact): "
					f"baseline={baseline_cell!r} fresh={fresh_cell!r}"
				)
		elif column in NUMERIC_COLUMNS:
			# Numeric measurement column: blank must stay blank; otherwise
			# abs(diff) must be within tolerance.
			baseline_value = _parse_optional_float(baseline_cell)
			fresh_value = _parse_optional_float(fresh_cell)
			if baseline_value is None or fresh_value is None:
				# A blank-vs-present mismatch is a real change in availability.
				if baseline_value is not fresh_value:
					diffs.append(
						f"{csv_label} {frame_label} column '{column}' (numeric blankness): "
						f"baseline={baseline_cell!r} fresh={fresh_cell!r}"
					)
				continue
			delta = abs(baseline_value - fresh_value)
			if delta > NUMERIC_TOLERANCE:
				diffs.append(
					f"{csv_label} {frame_label} column '{column}' (numeric): "
					f"baseline={baseline_value} fresh={fresh_value} "
					f"|diff|={delta:.4f} > {NUMERIC_TOLERANCE}"
				)
		else:
			# Any unclassified column is compared exactly so a new column can
			# never silently pass. This forces a deliberate classification edit
			# when the walker CSV schema changes.
			if baseline_cell != fresh_cell:
				diffs.append(
					f"{csv_label} {frame_label} column '{column}' (unclassified, exact): "
					f"baseline={baseline_cell!r} fresh={fresh_cell!r}"
				)
	return diffs


#============================================
def compare_csv(baseline_csv: pathlib.Path, fresh_csv: pathlib.Path, csv_label: str) -> list:
	"""Compare two verdict CSVs under the very-very-close policy.

	Returns a list of human-readable diff strings (empty list means a pass).
	Also enforces matching row counts, matching column sets, and
	accepted_fraction within tolerance.
	"""
	diffs = []

	if not baseline_csv.exists():
		diffs.append(f"{csv_label}: baseline CSV missing: {baseline_csv}")
		return diffs
	if not fresh_csv.exists():
		diffs.append(f"{csv_label}: fresh CSV missing: {fresh_csv}")
		return diffs

	baseline_rows = read_verdict_rows(baseline_csv)
	fresh_rows = read_verdict_rows(fresh_csv)

	if len(baseline_rows) != len(fresh_rows):
		diffs.append(
			f"{csv_label}: row count differs: "
			f"baseline={len(baseline_rows)} fresh={len(fresh_rows)}"
		)
		# Row-count mismatch makes per-row alignment meaningless; stop here.
		return diffs

	# Column set must be identical (catches schema drift between snapshot/run).
	baseline_columns = list(baseline_rows[0].keys()) if baseline_rows else []
	fresh_columns = list(fresh_rows[0].keys()) if fresh_rows else []
	if baseline_columns != fresh_columns:
		diffs.append(
			f"{csv_label}: column set differs: "
			f"baseline={baseline_columns} fresh={fresh_columns}"
		)
		return diffs

	# Per-frame comparison, aligned by row order (frame_index is exact-matched
	# inside compare_row so any reordering surfaces as a diff).
	for index, (baseline_row, fresh_row) in enumerate(zip(baseline_rows, fresh_rows)):
		frame_label = f"row#{index}(frame_index={baseline_row['frame_index']})"
		diffs.extend(compare_row(baseline_columns, baseline_row, fresh_row, frame_label, csv_label))

	# accepted_fraction comparison within 1e-6.
	baseline_fraction = compute_accepted_fraction(baseline_rows)
	fresh_fraction = compute_accepted_fraction(fresh_rows)
	fraction_delta = abs(baseline_fraction - fresh_fraction)
	if fraction_delta > ACCEPTED_FRACTION_TOLERANCE:
		diffs.append(
			f"{csv_label}: accepted_fraction differs: "
			f"baseline={baseline_fraction:.9f} fresh={fresh_fraction:.9f} "
			f"|diff|={fraction_delta:.9f} > {ACCEPTED_FRACTION_TOLERANCE}"
		)

	return diffs


#============================================
def compare_baseline(fresh_dir: pathlib.Path, baseline_dir: pathlib.Path) -> int:
	"""Compare every fresh interval CSV against the snapshot.

	Returns 0 on a full pass, 1 on any diff. Prints a per-CSV PASS/FAIL line and
	a final summary.
	"""
	all_diffs = []
	csv_count = 0
	row_count_total = 0

	for video_basename, left_frame, right_frame, _role in BASELINE_INTERVALS:
		interval_subpath = pathlib.Path(video_basename) / _interval_dirname(left_frame, right_frame)
		for direction_csv in ("fwd_verdicts.csv", "bwd_verdicts.csv"):
			csv_count += 1
			baseline_csv = baseline_dir / interval_subpath / direction_csv
			fresh_csv = fresh_dir / interval_subpath / direction_csv
			csv_label = f"{video_basename}/{_interval_dirname(left_frame, right_frame)}/{direction_csv}"

			diffs = compare_csv(baseline_csv, fresh_csv, csv_label)
			if diffs:
				print(f"[compare] FAIL {csv_label} ({len(diffs)} diff(s))")
				all_diffs.extend(diffs)
			else:
				fresh_rows = read_verdict_rows(fresh_csv)
				row_count_total += len(fresh_rows)
				fraction = compute_accepted_fraction(fresh_rows)
				print(
					f"[compare] PASS {csv_label} "
					f"rows={len(fresh_rows)} accepted_fraction={fraction:.6f}"
				)

	print(
		f"[compare] checked {csv_count} verdict CSVs across "
		f"{len(BASELINE_INTERVALS)} intervals"
	)

	if all_diffs:
		print("[compare] DIFFERENCES FOUND:")
		for diff in all_diffs:
			print(f"  - {diff}")
		print(f"[compare] RESULT: FAIL ({len(all_diffs)} diff(s))")
		return 1

	print(
		f"[compare] RESULT: PASS -- baseline matches (very-very-close policy), "
		f"{row_count_total} total verdict rows"
	)
	return 0


#============================================
def report_row_counts(walk_dir: pathlib.Path) -> None:
	"""Print row counts for every produced verdict CSV (proves non-empty)."""
	for video_basename, left_frame, right_frame, _role in BASELINE_INTERVALS:
		interval_subpath = pathlib.Path(video_basename) / _interval_dirname(left_frame, right_frame)
		for direction_csv in ("fwd_verdicts.csv", "bwd_verdicts.csv"):
			csv_path = walk_dir / interval_subpath / direction_csv
			rows = read_verdict_rows(csv_path)
			fraction = compute_accepted_fraction(rows)
			label = f"{video_basename}/{_interval_dirname(left_frame, right_frame)}/{direction_csv}"
			print(f"[rows] {label}: rows={len(rows)} accepted_fraction={fraction:.6f}")


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Golden-output baseline harness for the blob_walk_v2 walker. "
			"Walks exactly 4 fixed intervals; writes or compares verdict CSVs."
		),
	)
	subparsers = parser.add_subparsers(dest='mode', metavar='MODE')
	subparsers.required = True

	# walk: run the 4 fixed intervals into --output-dir.
	walk_p = subparsers.add_parser('walk', help='Walk the 4 fixed intervals.')
	walk_p.add_argument(
		'-o', '--output-dir', dest='output_dir', required=True,
		help='Root directory to write verdict CSVs into.',
	)

	# compare: diff a fresh walk dir against a baseline snapshot dir.
	compare_p = subparsers.add_parser('compare', help='Compare fresh walk vs baseline.')
	compare_p.add_argument(
		'-f', '--fresh-dir', dest='fresh_dir', required=True,
		help='Directory with the fresh walk output.',
	)
	compare_p.add_argument(
		'-b', '--baseline-dir', dest='baseline_dir', required=True,
		help='Directory with the golden baseline snapshot.',
	)

	# rows: print row counts for a walk directory (proves non-empty CSVs).
	rows_p = subparsers.add_parser('rows', help='Print verdict CSV row counts.')
	rows_p.add_argument(
		'-d', '--dir', dest='walk_dir', required=True,
		help='Walk directory to report row counts for.',
	)

	args = parser.parse_args()
	return args


#============================================
def main() -> None:
	"""Dispatch on subcommand: walk, compare, or rows."""
	args = parse_args()

	if args.mode == 'walk':
		walk_baseline(pathlib.Path(args.output_dir))
		return

	if args.mode == 'compare':
		exit_code = compare_baseline(
			pathlib.Path(args.fresh_dir),
			pathlib.Path(args.baseline_dir),
		)
		sys.exit(exit_code)

	if args.mode == 'rows':
		report_row_counts(pathlib.Path(args.walk_dir))
		return

	# argparse with required subparsers prevents reaching here.
	raise RuntimeError(f"Unknown mode: {args.mode}")


#============================================
if __name__ == '__main__':
	main()
