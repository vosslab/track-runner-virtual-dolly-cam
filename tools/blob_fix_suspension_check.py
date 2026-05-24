#!/usr/bin/env python3
"""Plan-suspension pre-check for the blob-refinement fix plan (WP-1D).

Reads `<run-root>/oracle/HYPOTHESIS_TESTS.json` and decides whether the
blob-side fix plan should proceed or suspend.

The single suspension hypothesis is `H_RAW_PRED_CORPUS`: when H2 (raw_pred
wrong corpus-wide) is SUPPORTED at the 30% threshold AND in at least 6 of
12 per-video bins at 30%, the blob side is the wrong layer to fix and the
plan suspends in favor of a raw_pred follow-up plan. See plan
`/Users/vosslab/.claude/plans/kind-exploring-cray.md` section
"WP-1D (suspension pre-check)".

The tool writes a Markdown verdict file with two top-level lines:

	H_RAW_PRED_CORPUS: SUPPORTED|REFUTED|INCONCLUSIVE
	Overall: PROCEED|PLAN_SUSPENDED

plus a prose section that lists per-video H2 rates for transparency.

Override path: a human reviewer may later edit the file to add a
`PROCEED_OVERRIDE: <reason>` line; downstream consumers may proceed even
on PLAN_SUSPENDED in that case. This tool only writes the autocomputed
verdict; it does not parse or respect overrides itself.

Usage:
	source source_me.sh && python3 tools/blob_fix_suspension_check.py \\
		-r output_smoke/blob_refine_fix/2026-05-23 \\
		-o output_smoke/blob_refine_fix/2026-05-23/oracle/SUSPENSION_CHECK.md
"""

# Standard Library
import os
import json
import argparse

# decision thresholds for H_RAW_PRED_CORPUS per plan WP-1D
H2_RATE_THRESHOLD = 0.30
H2_MIN_SUPPORTED_VIDEOS = 6

# default run-root parent; the tool resolves the most-recent dated subdir
DEFAULT_RUN_ROOT_PARENT = "output_smoke/blob_refine_fix"


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Read oracle/HYPOTHESIS_TESTS.json and write SUSPENSION_CHECK.md "
			"with the H_RAW_PRED_CORPUS verdict and overall PROCEED / "
			"PLAN_SUSPENDED decision per plan WP-1D."
		),
	)
	parser.add_argument(
		"-r", "--run-root", dest="run_root", default=None,
		help=(
			"Path to a run-root directory (must contain oracle/). "
			"Defaults to output_smoke/blob_refine_fix/<latest_date>."
		),
	)
	parser.add_argument(
		"-o", "--output", dest="output_path", default=None,
		help=(
			"Path to the output SUSPENSION_CHECK.md file. "
			"Defaults to <run-root>/oracle/SUSPENSION_CHECK.md."
		),
	)
	return parser.parse_args()


#============================================
def resolve_latest_run_root(parent_dir: str) -> str:
	"""Return the lexicographically latest date subdirectory under `parent_dir`.

	Plan-run subdirs use the YYYY-MM-DD naming convention. Lexicographic sort
	matches chronological order. Missing parent dir or zero date subdirs
	raises FileNotFoundError so the failure is loud (no dict.get defaults).

	Args:
		parent_dir: Path to the run-root parent (e.g. output_smoke/blob_refine_fix).

	Returns:
		str: Absolute path to the latest dated subdirectory.
	"""
	if not os.path.isdir(parent_dir):
		raise FileNotFoundError(
			f"run-root parent does not exist: {parent_dir}. "
			"Pass --run-root explicitly."
		)
	entries = sorted(os.listdir(parent_dir))
	# keep only directory entries; ignore README and stray files
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
def load_hypothesis_json(run_root: str) -> dict:
	"""Read `<run_root>/oracle/HYPOTHESIS_TESTS.json` and return the dict.

	Args:
		run_root: Run-root directory (must contain oracle/HYPOTHESIS_TESTS.json).

	Returns:
		dict: Parsed JSON content.
	"""
	json_path = os.path.join(run_root, "oracle", "HYPOTHESIS_TESTS.json")
	if not os.path.isfile(json_path):
		raise FileNotFoundError(
			f"HYPOTHESIS_TESTS.json not found at: {json_path}. "
			"Run the WP-1A oracle analyzer before invoking the suspension "
			"check."
		)
	with open(json_path, "r") as f:
		data = json.load(f)
	return data


#============================================
def evaluate_corpus_h2(data: dict) -> dict:
	"""Decide H_RAW_PRED_CORPUS from the parsed hypothesis JSON.

	Per plan WP-1D and the WP-1A sidecar schema:
		{
		  "corpus": {"h2": {"verdict": "...", "rate": float, "n_eligible": int}},
		  "per_video": {"VideoName": {"h2": {...}, ...}, ...}
		}

	H_RAW_PRED_CORPUS is SUPPORTED when:
		- corpus h2 verdict is SUPPORTED, AND
		- at least 6 per-video h2 entries have rate >= 0.30

	The plan says "SUPPORTED at 30% means rate >= 0.30" for the per-video
	count; we apply that rule via the per-video `rate` field so the
	threshold is uniform regardless of how each video's own verdict was
	computed.

	Args:
		data: Parsed HYPOTHESIS_TESTS.json content with the spec schema.

	Returns:
		dict: Decision payload with keys:
			- h_raw_pred_corpus: "SUPPORTED" | "REFUTED" | "INCONCLUSIVE"
			- overall: "PROCEED" | "PLAN_SUSPENDED"
			- corpus_h2_verdict: raw corpus h2 verdict string
			- corpus_h2_rate: float (0-1)
			- corpus_h2_n_eligible: int
			- per_video_rows: list of (video, rate, n_eligible, supported_at_30)
			- n_supported_videos: int
	"""
	# indexed access fails loudly when the analyzer omits required keys
	corpus = data["corpus"]
	per_video = data["per_video"]
	corpus_h2 = corpus["h2"]
	corpus_verdict = corpus_h2["verdict"]
	corpus_rate = corpus_h2["rate"]
	corpus_n_eligible = corpus_h2["n_eligible"]

	# per-video sweep: count videos with H2 rate >= 0.30 at the 30% threshold
	per_video_rows = []
	n_supported_videos = 0
	for video_name in sorted(per_video.keys()):
		video_block = per_video[video_name]
		video_h2 = video_block["h2"]
		video_rate = video_h2["rate"]
		video_n_eligible = video_h2["n_eligible"]
		supported_at_30 = video_rate >= H2_RATE_THRESHOLD
		if supported_at_30:
			n_supported_videos += 1
		per_video_rows.append((
			video_name,
			video_rate,
			video_n_eligible,
			supported_at_30,
		))

	# corpus H_RAW_PRED_CORPUS supported requires BOTH conditions
	corpus_supported = (corpus_verdict == "SUPPORTED")
	video_quorum_met = (n_supported_videos >= H2_MIN_SUPPORTED_VIDEOS)

	if corpus_supported and video_quorum_met:
		h_raw_pred_corpus = "SUPPORTED"
		overall = "PLAN_SUSPENDED"
	elif corpus_verdict == "REFUTED":
		# corpus call explicitly refutes -> we proceed regardless of per-video
		h_raw_pred_corpus = "REFUTED"
		overall = "PROCEED"
	else:
		# any other case: inconclusive corpus, or corpus SUPPORTED but
		# per-video quorum not met. Treat as INCONCLUSIVE and proceed.
		h_raw_pred_corpus = "INCONCLUSIVE"
		overall = "PROCEED"

	decision = {
		"h_raw_pred_corpus": h_raw_pred_corpus,
		"overall": overall,
		"corpus_h2_verdict": corpus_verdict,
		"corpus_h2_rate": corpus_rate,
		"corpus_h2_n_eligible": corpus_n_eligible,
		"per_video_rows": per_video_rows,
		"n_supported_videos": n_supported_videos,
	}
	return decision


#============================================
def build_report(decision: dict, run_root: str) -> str:
	"""Format the SUSPENSION_CHECK.md report body.

	The first two non-header lines are the canonical verdict lines that
	downstream consumers read:

		H_RAW_PRED_CORPUS: <verdict>
		Overall: <PROCEED|PLAN_SUSPENDED>

	A prose section then lists per-video H2 rates for transparency.

	Args:
		decision: Result of evaluate_corpus_h2().
		run_root: Run-root directory the verdict was computed from.

	Returns:
		str: Full report text (newline-terminated).
	"""
	per_video_rows = decision["per_video_rows"]
	corpus_verdict = decision["corpus_h2_verdict"]
	corpus_rate = decision["corpus_h2_rate"]
	corpus_n_eligible = decision["corpus_h2_n_eligible"]
	n_supported = decision["n_supported_videos"]
	h_raw = decision["h_raw_pred_corpus"]
	overall = decision["overall"]
	n_videos = len(per_video_rows)

	lines = []
	lines.append("# Plan-suspension pre-check (WP-1D)")
	lines.append("")
	# canonical machine-readable verdict lines: keep these two consecutive
	# so downstream consumers can scan for them without parsing structure
	lines.append(f"H_RAW_PRED_CORPUS: {h_raw}")
	lines.append(f"Overall: {overall}")
	lines.append("")
	lines.append("## Inputs")
	lines.append("")
	lines.append(f"- Run root: `{run_root}`")
	lines.append("- Source: `<run-root>/oracle/HYPOTHESIS_TESTS.json`")
	lines.append(f"- Per-video count: {n_videos}")
	lines.append(
		f"- Per-video H2 SUPPORTED at 30%: "
		f"{n_supported} / {n_videos} "
		f"(quorum requires >= {H2_MIN_SUPPORTED_VIDEOS})"
	)
	lines.append("")
	lines.append("## Decision")
	lines.append("")
	lines.append(
		"H_RAW_PRED_CORPUS is SUPPORTED when the corpus H2 verdict is "
		"SUPPORTED AND at least 6 of 12 per-video H2 rates are >= 0.30. "
		"A SUPPORTED verdict suspends the plan in favor of a raw_pred "
		"follow-up; REFUTED or INCONCLUSIVE allow the plan to proceed."
	)
	lines.append("")
	lines.append(f"- Corpus H2 verdict: **{corpus_verdict}**")
	lines.append(f"- Corpus H2 rate: {corpus_rate:.4f}")
	lines.append(f"- Corpus H2 n_eligible: {corpus_n_eligible}")
	lines.append("")
	lines.append("## Per-video H2 rates")
	lines.append("")
	lines.append("| Video | H2 rate | n_eligible | SUPPORTED at 30% |")
	lines.append("| --- | --- | --- | --- |")
	for (video_name, rate, n_eligible, supported_at_30) in per_video_rows:
		supported_label = "YES" if supported_at_30 else "NO"
		lines.append(
			f"| {video_name} | {rate:.4f} | {n_eligible} | "
			f"{supported_label} |"
		)
	lines.append("")
	lines.append("## Override path")
	lines.append("")
	lines.append(
		"A human reviewer may add a line beginning with "
		"`PROCEED_OVERRIDE:` followed by a reason to allow downstream "
		"consumers to proceed even when this file reports PLAN_SUSPENDED. "
		"This tool does not write or read overrides; it only emits the "
		"autocomputed verdict above."
	)
	lines.append("")
	report = "\n".join(lines)
	return report


#============================================
def write_report(output_path: str, report: str) -> None:
	"""Write the report text to disk, creating parent directories as needed."""
	parent = os.path.dirname(os.path.abspath(output_path))
	if parent and not os.path.isdir(parent):
		os.makedirs(parent, exist_ok=True)
	with open(output_path, "w") as f:
		f.write(report)


#============================================
def resolve_paths(args: argparse.Namespace) -> tuple:
	"""Resolve the effective run-root and output paths from args.

	Args:
		args: argparse Namespace from parse_args().

	Returns:
		tuple: (run_root, output_path) absolute paths.
	"""
	if args.run_root:
		run_root = os.path.abspath(args.run_root)
	else:
		run_root = resolve_latest_run_root(DEFAULT_RUN_ROOT_PARENT)
	if args.output_path:
		output_path = os.path.abspath(args.output_path)
	else:
		output_path = os.path.join(run_root, "oracle", "SUSPENSION_CHECK.md")
	return run_root, output_path


#============================================
def main() -> None:
	"""Entry point."""
	args = parse_args()
	run_root, output_path = resolve_paths(args)
	data = load_hypothesis_json(run_root)
	decision = evaluate_corpus_h2(data)
	report = build_report(decision, run_root)
	write_report(output_path, report)
	# brief stdout summary so the verdict is visible without opening the file
	h_raw = decision["h_raw_pred_corpus"]
	overall = decision["overall"]
	n_supported = decision["n_supported_videos"]
	n_videos = len(decision["per_video_rows"])
	print(
		f"wrote {output_path}: H_RAW_PRED_CORPUS={h_raw} "
		f"Overall={overall} per_video_supported={n_supported}/{n_videos}"
	)


#============================================
if __name__ == "__main__":
	main()
