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
def heat_summary_present(summary: dict) -> int:
	"""Return the heat-present count for one interval-direction summary.

	A small pure helper kept testable in isolation: it reads the required
	summary keys directly (no dict.get defaults) so a malformed summary fails
	loudly per PYTHON_STYLE.

	Args:
		summary: one interval-direction summary dict from render_heat_summary.json.

	Returns:
		The heat_present count (eligible frames with at least one in-box hot
		pixel) for the summary.
	"""
	# Direct key access: these keys are required in a summary record.
	present = summary["heat_present"]
	return present


#============================================
def report_heat_fraction(summary: dict, source: str) -> None:
	"""Print a per-interval-direction heat-present fraction REPORT line.

	Report-only: does NOT affect the exit code. Eligible frames are those the
	walk actually observed (heat_eligible). not_computed frames are surfaced as
	coverage, not hidden.

	Args:
		summary: one interval-direction summary dict.
		source: heat-summary file path, for the report line prefix.
	"""
	direction = summary["direction"]
	left_frame = summary["left_frame"]
	right_frame = summary["right_frame"]
	heat_eligible = summary["heat_eligible"]
	heat_present = heat_summary_present(summary)
	not_computed = summary["not_computed"]
	threshold = summary["threshold"]
	# New stats: read as required keys (no .get defaults per PYTHON_STYLE).
	heat_present_pct = summary["heat_present_pct"]
	mean_heat = summary["mean_heat"]

	# Format the percent fraction (stored as [0,1], displayed as percent).
	fraction_str = f"{heat_present_pct:.1%}"
	# Format mean_heat: rounded float when present, n/a when null.
	if mean_heat is not None:
		mean_heat_str = f"{mean_heat:.2f}"
	else:
		mean_heat_str = "n/a"

	print(
		f"  HEAT REPORT {source} [{left_frame},{right_frame}] [{direction}]: "
		f"heat-present {heat_present}/{heat_eligible} ({fraction_str}); "
		f"mean_heat={mean_heat_str}; "
		f"{not_computed} not-computed; "
		f"threshold={threshold}"
	)


#============================================
def report_seed_cold(summary: dict, source: str) -> None:
	"""Print a per-interval-direction seed-cold REPORT line.

	Flags human seed boxes that came back heat-cold -- a likely indicator of a
	stale or background-parked annotation. Report-only: does NOT affect the exit
	code or the gate failure list.

	Args:
		summary: one interval-direction summary dict.
		source: heat-summary file path, for the report line prefix.
	"""
	direction = summary["direction"]
	left_frame = summary["left_frame"]
	right_frame = summary["right_frame"]
	seed_cold_frames = summary["seed_cold_frames"]
	cold_count = len(seed_cold_frames)
	print(
		f"  SEED-COLD {source} [{left_frame},{right_frame}] [{direction}]: "
		f"{cold_count} seed tiles heat-cold; frames={seed_cold_frames}"
	)


#============================================
def report_heat_summaries(summaries: list, source: str) -> None:
	"""Print the heat and seed-cold REPORT lines for one video's summaries.

	Report-only: does NOT affect the exit code. Iterates the interval-direction
	summary records and prints one HEAT REPORT line and one SEED-COLD line each.

	Args:
		summaries: list of interval-direction summary dicts.
		source: heat-summary file path, for the report line prefix.
	"""
	# Stable ordering: by interval left frame, then direction.
	ordered = sorted(summaries, key=lambda s: (s["left_frame"], s["direction"]))
	for summary in ordered:
		report_heat_fraction(summary, source)
		report_seed_cold(summary, source)


#============================================
def check_records(records: list, source: str) -> list:
	"""Check one manifest's records and return a list of failure strings.

	Failure conditions (per tile):
	  - conversion_count != 1 (double or zero coordinate conversion).
	  - non-seed frame with no solved box (magenta + only symptom).

	Heat is no longer stored per tile (C13); the heat and seed-cold reports read
	the sibling render_heat_summary.json (see report_heat_summaries / main).

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

		# Heat + seed-cold reports read the sibling render_heat_summary.json
		# (interval data per C13). The walk writes it whenever it writes the
		# manifest, so a manifest WITHOUT the sibling is a stale run or a driver
		# bug -- fail loudly rather than skip silently (a silent skip is exactly
		# what hid the heat report being unwired for an entire release).
		heat_summary_file = os.path.join(
			os.path.dirname(manifest_file), "render_heat_summary.json"
		)
		if not os.path.isfile(heat_summary_file):
			all_failures.append(
				f"{manifest_file}: render_heat_summary.json sibling missing; the walk "
				f"did not write it (stale pre-2026-06-02 run or driver bug). Re-walk."
			)
		else:
			with open(heat_summary_file) as f:
				summaries = json.load(f)
			report_heat_summaries(summaries, heat_summary_file)

	manifest_count = len(manifest_files)
	if all_failures:
		# Print each failure so the exact bad frame is visible without walk.html.
		for failure in all_failures:
			print(f"  {failure}")
		print(
			f"FAIL: {len(all_failures)} failing check(s) across {total_tiles} "
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
