#!/usr/bin/env python3
"""Machine-checkable gate over a walker render_manifest.json (WS2-C).

The walk driver writes one render_manifest.json per video under the run output
dir, with one record per rendered tile. This checker fails the build WITHOUT a
human opening walk.html when either failure mode is present:

  1. A non-seed frame has no solved box (the "magenta + only" symptom). Recorded
     per tile as non_seed_missing_solved_box=True, or derivable from
     seed_box_present=False AND solved_box_present=False.
  2. A tile reports conversion_count != 1 (a double or zero coordinate
     conversion -- the most likely coordinate-space defect).

Both are contract checks, not brittle value pins: no frame counts, no exact
coordinates, no hardcoded constants are asserted.

Usage:
  source source_me.sh && python3 tools/blob_walk_v2/check_render_manifest.py PATH ...

PATH may be a render_manifest.json file or a directory searched recursively for
files named render_manifest.json. Exits 0 on PASS, 1 on FAIL.
"""

# Standard Library
import os
import sys
import json
import argparse


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Gate a walker render_manifest.json: every non-seed tile "
		"must have a solved box and every tile must have conversion_count == 1."
	)
	parser.add_argument(
		'-i', '--input', dest='input_paths', nargs='+', required=True,
		help="render_manifest.json file(s) or directory(ies) to search recursively",
	)
	args = parser.parse_args()
	return args


#============================================
def find_manifest_files(input_paths: list) -> list:
	"""Resolve input paths to a flat list of render_manifest.json file paths.

	A path that is a file is used directly. A path that is a directory is
	searched recursively for files named render_manifest.json.

	Args:
		input_paths: list of file or directory path strings.

	Returns:
		Sorted list of manifest file path strings.
	"""
	# Collect resolved manifest files here.
	found = []
	for path in input_paths:
		# A direct file path is used as given.
		if os.path.isfile(path):
			found.append(path)
			continue
		# A directory is walked recursively for render_manifest.json files.
		if os.path.isdir(path):
			for root, _dirs, files in os.walk(path):
				if "render_manifest.json" in files:
					found.append(os.path.join(root, "render_manifest.json"))
			continue
		# Neither file nor directory: fail loudly so a typo is visible.
		raise FileNotFoundError(f"check_render_manifest: path not found: {path}")
	found_sorted = sorted(set(found))
	return found_sorted


#============================================
def _group_by_direction(records: list) -> dict:
	"""Group records by direction string.

	Args:
		records: list of per-tile manifest dicts.

	Returns:
		Dict mapping direction string to list of records in that direction.
	"""
	# Build a mapping from direction -> list of records.
	by_direction = {}
	for record in records:
		# Use "unknown" for manifests that pre-date the direction field.
		direction = record.get("direction", "unknown")
		if direction not in by_direction:
			by_direction[direction] = []
		by_direction[direction].append(record)
	return by_direction


#============================================
def report_heat_fraction(records: list, source: str, direction: str) -> None:
	"""Print a per-interval heat-present fraction REPORT line.

	This is report-only: it does NOT affect the exit code or return any failure.
	Eligible tiles are those where in_box_heat_computed == true. Computed-cold
	tiles (computed=true, present=false) stay in the denominator; only
	not-computed tiles are excluded from the denominator.

	If no record in this direction carries in_box_heat_computed (older manifest),
	the heat report is silently skipped for this interval -- no crash, no error.

	Args:
		records: list of per-tile manifest dicts for one direction.
		source: manifest file path, for the report line prefix.
		direction: direction label (e.g. "fwd", "bwd").
	"""
	# Check whether the new heat fields are present at all. Use explicit key
	# presence checks rather than dict.get defaults so a real missing field is
	# not silently hidden (PYTHON_STYLE: do not hide bugs with defaults).
	computed_present_count = sum(
		1 for r in records if "in_box_heat_computed" in r
	)
	# If no record carries the field, this is an older manifest -- skip cleanly.
	if computed_present_count == 0:
		return

	# Collect threshold from the first record that carries it (all tiles in one
	# direction share the same threshold).
	threshold_used = None
	for record in records:
		if "heat_threshold_used" in record:
			threshold_used = record["heat_threshold_used"]
			break

	# Eligible = in_box_heat_computed is explicitly True. Not-computed tiles are
	# excluded from the denominator (no implicit default: we require the field).
	eligible_records = [r for r in records if r["in_box_heat_computed"] is True]
	total_count = len(records)
	eligible_count = len(eligible_records)
	not_computed_count = total_count - eligible_count

	# Among eligible tiles, count how many are heat-present.
	heat_present_count = sum(1 for r in eligible_records if r["in_box_heat_present"] is True)

	# Compute fraction with a guard for a zero eligible-count denominator.
	if eligible_count > 0:
		fraction = heat_present_count / eligible_count
		fraction_str = f"{fraction:.1%}"
	else:
		fraction_str = "n/a (no eligible tiles)"

	# Build the threshold part of the report line.
	threshold_str = f"threshold={threshold_used}" if threshold_used is not None else "threshold=n/a"

	print(
		f"  HEAT REPORT {source} [{direction}]: "
		f"heat-present {heat_present_count}/{eligible_count} eligible "
		f"({fraction_str}); "
		f"{not_computed_count} not-computed (skipped); "
		f"total {total_count} tiles; "
		f"{threshold_str}"
	)


#============================================
def check_records(records: list, source: str) -> list:
	"""Check one manifest's records and return a list of failure strings.

	Also prints per-direction HEAT REPORT lines (report-only, does not affect
	the returned failure list or the exit code).

	Failure conditions (per tile):
	  - conversion_count != 1 (double or zero coordinate conversion).
	  - non-seed frame with no solved box (magenta + only symptom).

	Args:
		records: list of per-tile manifest dicts.
		source: manifest file path, for failure messages.

	Returns:
		List of human-readable failure strings (empty if all records pass).
	"""
	# Accumulate failure messages here.
	failures = []
	for record in records:
		frame_index = record["frame_index"]
		conversion_count = record["conversion_count"]
		# Gate 1: exactly one coordinate conversion per tile.
		if conversion_count != 1:
			failures.append(
				f"{source} frame {frame_index}: conversion_count="
				f"{conversion_count} (expected 1)"
			)
		# Gate 2: a non-seed frame must carry a solved box. Prefer the explicit
		# warning flag when present; otherwise derive it from the box-presence
		# fields so older manifests without the flag still gate correctly.
		seed_present = record["seed_box_present"]
		solved_present = record["solved_box_present"]
		if "non_seed_missing_solved_box" in record:
			missing = record["non_seed_missing_solved_box"]
		else:
			missing = (not seed_present) and (not solved_present)
		if missing:
			failures.append(
				f"{source} frame {frame_index}: non-seed frame has no solved "
				f"box (magenta + only symptom)"
			)

	# Per-direction heat report (report-only; does not affect failures or exit code).
	by_direction = _group_by_direction(records)
	for direction in sorted(by_direction):
		report_heat_fraction(by_direction[direction], source, direction)

	return failures


#============================================
def main() -> None:
	"""Resolve manifests, run checks, print PASS/FAIL with counts, exit code."""
	args = parse_args()

	manifest_files = find_manifest_files(args.input_paths)
	if not manifest_files:
		print("FAIL: no render_manifest.json found under the given path(s)")
		sys.exit(1)

	# Aggregate counts across all manifests for the summary line.
	total_tiles = 0
	all_failures = []
	for manifest_file in manifest_files:
		with open(manifest_file) as f:
			records = json.load(f)
		total_tiles += len(records)
		failures = check_records(records, manifest_file)
		all_failures.extend(failures)

	manifest_count = len(manifest_files)
	if all_failures:
		# Print each failure so the exact bad frame is visible without walk.html.
		for failure in all_failures:
			print(f"  {failure}")
		print(
			f"FAIL: {len(all_failures)} failing tile(s) across {total_tiles} "
			f"tiles in {manifest_count} manifest(s)"
		)
		sys.exit(1)

	print(
		f"PASS: {total_tiles} tiles in {manifest_count} manifest(s); "
		f"every non-seed tile has a solved box and conversion_count == 1"
	)


#============================================
if __name__ == '__main__':
	main()
