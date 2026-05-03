#!/usr/bin/env python3
"""Measure |raw_pred - blended_path| per frame, per pass, per interval.

Phase 3c diagnostic. Phase 2 showed blob snap is gate-dominated on
failing intervals; this tool asks whether the Hermite raw_pred
itself is drifting relative to the solver's final accepted output
(the blended interval path, current code name `blended_path`), or
whether raw_pred is close to that output and something else is
failing.

No residual computation. No video decode. The tool reads only the
geometry_cache NPZ and the seeds, so it runs in seconds on the full
clip.

Per (interval, pass) row:

  median_div_h = median over non-endpoint non-stationary frames of
                 |raw_pred[t] - blended_path[t]| / blended_path[t].h
  p90_div_h    = 90th percentile of the same
  max_div_h    = max

Note: divergence is measured relative to the solver's accepted
output, which is not ground truth. A small value means raw_pred and
the solver's blended path agree; a large value means raw_pred differs
materially from what the solver ultimately shipped.

TODO: upgrade or remove -- references retired geometry_cache.npz (schema v8
retirement); decide whether to update to current loader or delete.
"""

# Standard Library
import os
import sys
import csv
import math
import argparse

# add track_runner directory to sys.path so local imports resolve
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# local repo modules
import camera_motion
import interval_fingerprint
import scene_coords
import state_io
import tr_paths
import velocity_model


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Per-interval, per-pass divergence between Hermite raw_pred "
			"and the solver's blended interval path (blended_path)."
		)
	)
	parser.add_argument(
		"-i", "--input", dest="input_file", required=True,
		help="Path to input video file."
	)
	parser.add_argument(
		"-s", "--seeds", dest="seeds_file", default=None,
		help="Path to seeds JSON (default: canonical per-video path)."
	)
	parser.add_argument(
		"-c", "--csv", dest="csv_path", default=None,
		help="Write one row per (interval, pass) with divergence stats."
	)
	parser.add_argument(
		"-Q", "--no-print-per-interval", dest="print_per_interval",
		action="store_false",
		help="Suppress per-interval console output. Summary still prints."
	)
	parser.set_defaults(print_per_interval=True)
	return parser.parse_args()


#============================================
def _summarize(samples: list) -> dict:
	"""Median, p90, max of a list of floats. Empty -> all None."""
	if not samples:
		return {"median": None, "p90": None, "max": None}
	sorted_samples = sorted(samples)
	n = len(sorted_samples)
	median = sorted_samples[n // 2]
	p90_idx = max(0, min(n - 1, int(round(0.9 * (n - 1)))))
	return {
		"median": median,
		"p90": sorted_samples[p90_idx],
		"max": sorted_samples[-1],
	}


#============================================
def _divergence_for_pass(
	raw_pred: list,
	blended_by_frame: dict,
) -> tuple:
	"""Return (samples, eligible, blended_available).

	samples: per-frame |raw_pred - blended| / blended.h on non-endpoint,
	non-stationary frames that have a blended entry.
	eligible: count of non-endpoint non-stationary frames.
	blended_available: how many of those had a blended entry.
	"""
	samples = []
	eligible = 0
	blended_available = 0
	num = len(raw_pred)
	for i in range(num):
		is_endpoint = (i == 0) or (i == num - 1)
		if is_endpoint:
			continue
		eligible += 1
		frame_index = int(raw_pred[i][0])
		entry = blended_by_frame.get(frame_index)
		if entry is None:
			continue
		blended_available += 1
		raw_cx = float(raw_pred[i][1])
		raw_cy = float(raw_pred[i][2])
		blended_cx, blended_cy, blended_h = entry
		if blended_h <= 0.0:
			continue
		dist = math.sqrt(
			(raw_cx - blended_cx) ** 2 + (raw_cy - blended_cy) ** 2
		)
		samples.append(dist / blended_h)
	return samples, eligible, blended_available


#============================================
CSV_COLUMNS = (
	"interval_index", "start_frame", "end_frame", "duration_s",
	"eligible_frames", "blended_frames_available", "pass",
	"median_div_h", "p90_div_h", "max_div_h",
)


#============================================
def _cell(val) -> str:
	"""Render None as empty, floats at 4 decimals, else str()."""
	if val is None:
		return ""
	if isinstance(val, float):
		return f"{val:.4f}"
	return str(val)


#============================================
def _csv_row(
	idx: int,
	start_frame: int,
	end_frame: int,
	duration_s: float,
	eligible: int,
	blended_available: int,
	pass_name: str,
	summary: dict,
) -> list:
	"""Build one CSV row for a single (interval, pass)."""
	row = [
		idx, start_frame, end_frame, f"{duration_s:.4f}",
		eligible, blended_available, pass_name,
		_cell(summary["median"]), _cell(summary["p90"]),
		_cell(summary["max"]),
	]
	return row


#============================================
def _print_per_interval(
	idx: int,
	start_frame: int,
	end_frame: int,
	duration_s: float,
	fwd_summary: dict,
	fwd_eligible: int,
	fwd_blended: int,
	bwd_summary: dict,
	bwd_eligible: int,
	bwd_blended: int,
) -> None:
	"""Two-line per-interval console summary."""
	header = (
		f"interval [{idx:3d}] {start_frame:5d}-{end_frame:5d} "
		f"({duration_s:.1f}s)"
	)
	def _fmt(label, summary, eligible, blended):
		if summary["median"] is None:
			return (
				f"  {label}: eligible={eligible} blended={blended} "
				"div_h: n/a"
			)
		return (
			f"  {label}: eligible={eligible} blended={blended} "
			f"div_h median={summary['median']:.2f} "
			f"p90={summary['p90']:.2f} max={summary['max']:.2f}"
		)
	print(header, flush=True)
	print(_fmt("FWD", fwd_summary, fwd_eligible, fwd_blended), flush=True)
	print(_fmt("BWD", bwd_summary, bwd_eligible, bwd_blended), flush=True)


#============================================
def _print_summary(per_interval: list) -> None:
	"""Whole-run aggregate."""
	if not per_interval:
		print("\n  no intervals processed")
		return
	fwd_all = []
	bwd_all = []
	for item in per_interval:
		fwd_all.extend(item["fwd_samples"])
		bwd_all.extend(item["bwd_samples"])
	print("")
	print(f"  summary over {len(per_interval)} intervals:")
	for label, samples in (("FWD", fwd_all), ("BWD", bwd_all)):
		summary = _summarize(samples)
		if summary["median"] is None:
			print(f"    {label}: (no samples)")
		else:
			print(
				f"    {label}: n={len(samples)} "
				f"div_h median={summary['median']:.2f} "
				f"p90={summary['p90']:.2f} "
				f"max={summary['max']:.2f}"
			)


#============================================
def main() -> None:
	args = parse_args()

	seeds_path = args.seeds_file or tr_paths.default_seeds_path(
		args.input_file
	)
	motion_path = tr_paths.default_camera_motion_path(args.input_file)
	geom_path = tr_paths.default_intervals_path(args.input_file)

	seeds_data = state_io.load_seeds(seeds_path)
	all_seeds = list(seeds_data.get("seeds", []))
	if not all_seeds:
		raise RuntimeError(f"no seeds in {seeds_path}")

	motion_track = camera_motion.load_motion_cache(motion_path, None)
	if motion_track is None:
		raise RuntimeError(
			f"camera motion cache missing or unreadable: {motion_path}\n"
			"run 'track_runner.py -i VIDEO solve' first."
		)
	transform = scene_coords.SceneTransform(motion_track)

	if not os.path.isfile(geom_path):
		raise RuntimeError(
			f"geometry cache missing: {geom_path}\n"
			"run 'track_runner.py -i VIDEO solve' first."
		)
	geom = state_io.load_geometry_cache(geom_path)
	solved = geom.get("solved_intervals", {}) or {}
	# build per-interval frame -> (cx, cy, h) lookup, keyed on
	# (start_frame, end_frame) so the seed-driven loop can join.
	blended_by_key = {}
	for _fp, iv in solved.items():
		start = int(iv["start_frame"])
		end = int(iv["end_frame"])
		blended_path = iv.get("blended_path") or []
		per_frame = {}
		for offset, entry in enumerate(blended_path):
			fi = start + offset
			per_frame[fi] = (
				float(entry["cx"]), float(entry["cy"]), float(entry["h"])
			)
		blended_by_key[(start, end)] = per_frame
	print(
		f"loaded {len(all_seeds)} seeds from {seeds_path}"
	)
	print(
		f"loaded blended paths for {len(blended_by_key)} solved intervals"
	)

	usable_seeds = interval_fingerprint.filter_usable_seeds_sorted(
		all_seeds, verbose=True,
	)
	if len(usable_seeds) < 2:
		raise RuntimeError(
			f"need at least 2 usable seeds; have {len(usable_seeds)}"
		)

	# build all_seeds_scene the same way solve does so curve fitting
	# reads the exact same context. Not strictly required for the
	# divergence calculation, but keeps the raw_pred math identical to
	# the solver's.
	all_seeds_scene = []
	for seed in all_seeds:
		if seed.get("status") == "not_in_frame":
			continue
		frame_index = int(seed["frame_index"])
		cx = float(seed["cx"])
		cy = float(seed["cy"])
		w = float(seed["w"])
		h = float(seed["h"])
		sx, sy, sw, sh = transform.pixel_box_to_scene(
			frame_index, cx, cy, w, h,
		)
		all_seeds_scene.append((frame_index, sx, sy, sw, sh))

	# video fps for duration column. Avoid opening a VideoReader since
	# this tool does no image I/O otherwise.
	diag_path = tr_paths.default_diagnostics_path(args.input_file)
	fps = 30.0
	if os.path.isfile(diag_path):
		diag = state_io.load_diagnostics(diag_path)
		fps_guess = diag.get("video_info", {}).get("fps")
		if fps_guess:
			fps = float(fps_guess)

	csv_fh = None
	csv_writer = None
	if args.csv_path is not None:
		csv_fh = open(args.csv_path, "w", newline="")
		csv_writer = csv.writer(csv_fh)
		csv_writer.writerow(list(CSV_COLUMNS))
		csv_fh.flush()

	per_interval_results = []
	total_pairs = len(usable_seeds) - 1
	try:
		for idx in range(total_pairs):
			left = usable_seeds[idx]
			right = usable_seeds[idx + 1]
			start_frame = int(left["frame_index"])
			end_frame = int(right["frame_index"])
			blended_by_frame = blended_by_key.get((start_frame, end_frame))
			if blended_by_frame is None:
				# interval has no solved blended path; skip silently.
				# Pre-race or missing-seed intervals land here.
				continue

			curves = velocity_model.fit_interval_curves(
				left, right, all_seeds_scene, transform,
			)
			raw_pred_fwd = velocity_model._compute_raw_pred_forward(
				curves, transform,
			)
			raw_pred_bwd = velocity_model._compute_raw_pred_backward(
				curves, transform,
			)

			fwd_samples, fwd_eligible, fwd_blended = _divergence_for_pass(
				raw_pred_fwd, blended_by_frame,
			)
			bwd_samples, bwd_eligible, bwd_blended = _divergence_for_pass(
				raw_pred_bwd, blended_by_frame,
			)
			fwd_summary = _summarize(fwd_samples)
			bwd_summary = _summarize(bwd_samples)

			duration_s = (end_frame - start_frame) / fps
			per_interval_results.append({
				"interval_index": idx,
				"start_frame": start_frame,
				"end_frame": end_frame,
				"fwd_samples": fwd_samples,
				"bwd_samples": bwd_samples,
			})

			if args.print_per_interval:
				_print_per_interval(
					idx, start_frame, end_frame, duration_s,
					fwd_summary, fwd_eligible, fwd_blended,
					bwd_summary, bwd_eligible, bwd_blended,
				)
			if csv_writer is not None:
				csv_writer.writerow(_csv_row(
					idx, start_frame, end_frame, duration_s,
					fwd_eligible, fwd_blended, "FWD", fwd_summary,
				))
				csv_writer.writerow(_csv_row(
					idx, start_frame, end_frame, duration_s,
					bwd_eligible, bwd_blended, "BWD", bwd_summary,
				))
				csv_fh.flush()
	finally:
		if csv_fh is not None:
			csv_fh.close()

	_print_summary(per_interval_results)
	if args.csv_path is not None:
		print(f"  CSV written to {args.csv_path}")


if __name__ == "__main__":
	main()
