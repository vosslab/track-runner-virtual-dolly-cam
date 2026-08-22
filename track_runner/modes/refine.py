"""Implementation for the track_runner refine CLI mode."""

import argparse
import os

import fastread_video
import modes.shared as mode_shared
import race_start
import solve_queue
import torso_box_coords_io


def run(
	args: argparse.Namespace,
	cfg: dict,
	video_info: dict,
	seeds_path: str,
	diag_path: str,
	intervals_path: str,
	video_context: fastread_video.VideoContext,
	video_identity: dict,
) -> None:
	"""Refine mode: re-solve only changed intervals, reuse prior results.

	Requires existing solved intervals from a prior solve. Only
	intervals whose fingerprint changed (due to edited seeds) are
	re-solved; prior results are reused for unchanged intervals.

	Args:
		args: Parsed argparse namespace.
		cfg: Configuration dict.
		video_info: Video metadata dict.
		seeds_path: Path to the seeds JSON file.
		diag_path: Path to diagnostics JSON file.
		intervals_path: Path to solved torso-coordinate NPZ file.
		video_context: Resolved per-run routing; refine decodes from
			video_context.working_decode.path.
		video_identity: Identity of the source video that owns output artifacts.
	"""
	fastread_video.print_video_routing_banner(
		video_context.original_video_path,
		video_context.working_decode.path,
	)
	seeds = mode_shared._load_and_deduplicate_seeds(seeds_path)
	if not seeds:
		raise RuntimeError(f"no seeds found in {seeds_path}")
	print(f"loaded {len(seeds)} seeds from {seeds_path}")
	if not os.path.isfile(intervals_path):
		raise RuntimeError(
			f"no solved intervals at {intervals_path}; run 'solve' first"
		)

	# Fingerprint membership in the schema-15 solve artifact is the only reuse
	# authority. solve_queue.plan_interval_work owns seed filtering, fingerprint
	# comparison, and orphan pruning for both solve and refine.
	intervals_file = torso_box_coords_io.load_torso_box_coords(intervals_path)
	solved_intervals = dict(intervals_file.get("solved_intervals", {}))
	# Race start is a solve-artifact fact. It is optional only when solve found
	# no pre-race phase; when present, validate it through its public owner so
	# planning keeps the same pre-race partition without diagnostics.
	race_start_interval = None
	if "race_start" in intervals_file:
		race_start_data = race_start.load_race_start_from_artifact(intervals_file)
		race_start_interval = tuple(race_start_data["race_start_interval"])
	# Interval reuse identity is bin-invariant: stored torso boxes are always
	# unbinned SOURCE-frame, so the fingerprint carries seed-pair geometry plus
	# the current schema tag only. The refine partition therefore
	# reuses solved intervals regardless of which bin solve or refine uses;
	# mode_shared._run_solve resolves the run's bin for the actual solve downstream.
	plan = solve_queue.plan_interval_work(
		seeds, solved_intervals, race_start_interval=race_start_interval,
	)
	total_expected = plan.total_intervals

	# degenerate early-exit: current seeds yield no solvable intervals
	# (fewer than 2 usable seeds, or a seed-set mismatch that wiped all
	# pairs) while the stored solve still holds intervals from a prior run.
	# return without writing anything -- the existing store remains unchanged.
	# The C7 guard below handles the distinct case where
	# work exists but zero prior fingerprints matched (full-solve-disguised-
	# as-refine); this branch handles the no-intervals-at-all case.
	if plan.total_intervals == 0 and len(solved_intervals) > 0:
		print(
			f"  refine: current seeds yielded no solvable intervals "
			f"(seeds dropped below 2 usable, or a seed-set mismatch). "
			f"The existing solved store ({len(solved_intervals)} interval(s)) "
			f"is preserved unchanged. Add or restore seeds and re-run refine."
		)
		return

	# fast-exit (no write). enforces contract rule 1: unchanged seeds
	# produce no computation AND no disk write. pending_count == 0 is
	# the membership-complete signal; solve_complete on disk is advisory
	# only.
	if plan.pending_count == 0:
		# solve_complete is advisory, not a correctness gate. if
		# membership is complete but the advisory completion flag is
		# false, surface the discrepancy but trust the store.
		if intervals_file.get("solve_complete") is False:
			print("  warning: solve_complete=false on disk, "
				"store membership complete; trusting store")
		print(f"  refine: all {total_expected} intervals already solved, "
			f"nothing to do")
		return

	# Contract C7 requires refine to retain untouched intervals. If the planned
	# prior store has no reusable fingerprint but work remains, refine would be
	# a full solve under the refine label. Fail before writing anything.
	if (plan.reused_count == 0 and plan.total_intervals > 0
			and plan.pending_count == plan.total_intervals):
		raise RuntimeError(
			f"refine would re-solve all {plan.total_intervals} intervals "
			f"(no prior fingerprints matched); this is a full solve -- "
			f"run 'solve' instead. Likely cause: geometry tag changed or the store file is for a "
			f"different seed set."
		)

	# Work is needed and the plan retained prior results, so writes are allowed.
	# Persist exactly the partition that _run_solve will load and extend.
	before = len(solved_intervals)
	pruned_count = before - len(plan.pruned_prior)
	if pruned_count > 0:
		intervals_file["solved_intervals"] = dict(plan.pruned_prior)
		torso_box_coords_io.write_torso_box_coords(intervals_path, intervals_file)
		print(f"  store: pruned {pruned_count} stale intervals "
			f"({before} -> {len(plan.pruned_prior)})")

	print(f"  refine: {plan.pending_count} of {total_expected} intervals "
		f"need solving ({plan.reused_count} will be reused)")

	num_workers = mode_shared._resolve_workers(args)
	mode_shared._run_solve(
		args, cfg, seeds, video_info,
		intervals_path, diag_path, num_workers, video_identity,
		is_refine=True,
		decode_video_path=video_context.working_decode.path,
	)
