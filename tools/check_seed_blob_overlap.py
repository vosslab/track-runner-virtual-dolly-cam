#!/usr/bin/env python3
"""Check whether raw residual-motion blobs line up with human seed boxes.

Phase 1 diagnostic: isolates the question of whether the blob detector
itself is seeing the runner at seed frames, before any corridor filtering
or propagator gates are applied.

For each seed with a torso_box the script computes the per-frame residual
inside a torso-scaled ROI around the seed center, extracts raw blobs
with the solver's default threshold, and reports where each blob fell
relative to the human-drawn box. No corridor. No proximity / direction /
path gates.

ROI multiple is tunable via --roi-multiple; default is 3.0 (narrow
"is there any blob near the seed?" question). Pass 8.0 to match the
solver's own ROI in track_runner/residual_motion.py _compute_roi. The
two runs answer different questions: 3x answers "does the detector see
the runner at all," 8x answers "does the detector still pick the runner
out under solver-sized context." Larger ROI gives more scene for
median-background subtraction but can surface more competing blobs that
later gates must reject.

Requires the per-video camera-motion cache to exist on disk (run `solve`
first). Using an identity transform would silently bias the residual on
panning footage and defeat the point of the diagnostic.
"""

# Standard Library
import os
import sys
import csv
import random
import argparse

# add track_runner directory to path so we can import its modules.
# Mirrors the pattern in tools/diagnose_residual_motion.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# add common_tools directory to path
_COMMON_TOOLS_DIR = os.path.join(_REPO_ROOT, "common_tools")
if _COMMON_TOOLS_DIR not in sys.path:
	sys.path.insert(0, _COMMON_TOOLS_DIR)

# PIP3 modules
import cv2

# local repo modules
import camera_motion
import frame_reader
import probe_video
import residual_motion
import scene_coords
import state_io
import tr_paths


# default statuses included when --status is not provided. not_in_frame
# is always excluded because it has no torso_box to score against.
DEFAULT_STATUSES = ("visible", "partial", "approximate")

# default ROI multiple around the seed center. 3x is the phase-1
# question "is there any blob near the seed"; pass --roi-multiple 8.0 to
# match the solver's own ROI (residual_motion.py _compute_roi) for a
# solver-faithful comparison. User-overridable via CLI.
DEFAULT_ROI_MULTIPLE = 3.0

# distance buckets used by the console summary and the CSV. Stated in
# torso-heights so numbers compare across zoom ranges.
DISTANCE_BUCKETS = (0.25, 0.5, 1.0)


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Compare raw residual-motion blobs against human seed boxes "
			"(phase 1, no corridor or gates)."
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
		"--time-range", dest="time_range", default=None,
		help=(
			"Limit to time range in seconds. "
			"Format: 'START:END', 'START:', or ':END'."
		)
	)
	parser.add_argument(
		"--status", dest="status_csv", default=None,
		help=(
			"Comma-separated statuses to include. "
			f"Default: {','.join(DEFAULT_STATUSES)}. "
			"'not_in_frame' is always excluded."
		)
	)
	parser.add_argument(
		"-N", "--max-seeds", dest="max_seeds", type=int, default=None,
		help="Cap on number of seeds checked (random sample if exceeded)."
	)
	parser.add_argument(
		"-R", "--roi-multiple", dest="roi_multiple", type=float,
		default=DEFAULT_ROI_MULTIPLE,
		help=(
			f"ROI side length as a multiple of torso size "
			f"(default: {DEFAULT_ROI_MULTIPLE}). "
			"Try 8.0 to match the solver's own ROI."
		)
	)
	parser.add_argument(
		"-r", "--random-seed", dest="random_seed", type=int, default=0,
		help=(
			"Random seed for --max-seeds sampling (default: 0). "
			"Use -1 for OS entropy."
		)
	)
	parser.add_argument(
		"-c", "--csv", dest="csv_path", default=None,
		help="Write per-seed CSV rows to this path."
	)
	parser.add_argument(
		"-O", "--save-images", dest="images_dir", default=None,
		help="Write one annotated PNG per seed to this directory."
	)
	args = parser.parse_args()
	return args


#============================================
def parse_time_range(time_range_str: str, fps: float, frame_count: int) -> tuple:
	"""Parse --time-range 'START:END' into an inclusive (start, end) frame range.

	Args:
		time_range_str: String like '30:120', '200:', or ':90'. May be None.
		fps: Video frame rate.
		frame_count: Total frame count (used as open-ended upper bound).

	Returns:
		Tuple (start_frame, end_frame) in inclusive frame index space. When
		`time_range_str` is None, returns (0, frame_count - 1).
	"""
	if time_range_str is None:
		return (0, frame_count - 1)
	parts = time_range_str.split(":")
	if len(parts) != 2:
		raise RuntimeError(
			f"invalid --time-range {time_range_str!r}, expected 'START:END'"
		)
	start_s = float(parts[0]) if parts[0].strip() else 0.0
	end_s = float(parts[1]) if parts[1].strip() else None
	start_frame = int(round(start_s * fps))
	if end_s is None:
		end_frame = frame_count - 1
	else:
		end_frame = int(round(end_s * fps))
	return (start_frame, end_frame)


#============================================
def parse_status_filter(status_csv: str) -> set:
	"""Parse comma-separated status list into a set.

	Args:
		status_csv: Comma-separated string, or None.

	Returns:
		Set of status strings. not_in_frame is always removed.
	"""
	if status_csv is None:
		allowed = set(DEFAULT_STATUSES)
	else:
		allowed = {s.strip() for s in status_csv.split(",") if s.strip()}
	# exclude not_in_frame defensively even if the user names it
	allowed.discard("not_in_frame")
	return allowed


#============================================
def derive_seed_geometry(torso_box: list) -> tuple:
	"""Derive (cx, cy, w, h) from a torso_box [x, y, w, h].

	torso_box convention is top-left plus size, integer pixels (see
	track_runner/state_io.py:139). Centers are computed fresh here so the
	tool does not depend on loader-attached fields.

	Args:
		torso_box: [x, y, w, h] list.

	Returns:
		Tuple (cx, cy, w, h) as floats.
	"""
	tx, ty, tw, th = torso_box
	cx = float(tx) + float(tw) / 2.0
	cy = float(ty) + float(th) / 2.0
	return (cx, cy, float(tw), float(th))


#============================================
def compute_roi(
	cx: float,
	cy: float,
	box_w: float,
	box_h: float,
	frame_w: int,
	frame_h: int,
	roi_multiple: float,
) -> tuple:
	"""Compute a torso-scaled ROI clamped to the frame.

	Both bounds clamp against the frame so the ROI is never degenerate,
	matching the pattern from residual_motion._compute_roi after the
	off-frame-prediction fix.

	Args:
		cx, cy: Seed center in full-frame pixels.
		box_w, box_h: Torso box width and height in pixels.
		frame_w, frame_h: Frame dimensions.
		roi_multiple: ROI side length as a multiple of torso size.

	Returns:
		(x1, y1, x2, y2) ROI bounds in full-frame pixels with
		x1 <= x2 and y1 <= y2.
	"""
	half_w = 0.5 * roi_multiple * box_w
	half_h = 0.5 * roi_multiple * box_h
	x1 = max(0, min(frame_w, int(round(cx - half_w))))
	x2 = max(x1, min(frame_w, int(round(cx + half_w))))
	y1 = max(0, min(frame_h, int(round(cy - half_h))))
	y2 = max(y1, min(frame_h, int(round(cy + half_h))))
	return (x1, y1, x2, y2)


#============================================
def point_in_box(px: float, py: float, torso_box: list) -> bool:
	"""Return True when a point lies inside a torso_box [x, y, w, h]."""
	tx, ty, tw, th = torso_box
	return (tx <= px <= tx + tw) and (ty <= py <= ty + th)


#============================================
def analyze_seed(
	seed: dict,
	reader: object,
	scene_transform: object,
	frame_cache: dict,
	roi_multiple: float,
) -> dict:
	"""Run the phase-1 pipeline on one seed and return per-seed metrics.

	Args:
		seed: Seed dict with frame_index, torso_box, status, pass.
		reader: VideoReader instance.
		scene_transform: SceneTransform instance.
		frame_cache: Shared cache dict passed into
			compute_residual_for_frame; stores raw frame reads across
			seeds. Opaque to this function.

	Returns:
		Dict with the per-seed metrics described in the plan. All fields
		are always present; undefined numeric fields are None (written as
		empty cells by the CSV writer).
	"""
	frame_index = int(seed["frame_index"])
	torso_box = seed["torso_box"]
	status = seed["status"]
	pass_num = int(seed.get("pass", 1))

	seed_cx, seed_cy, seed_w, seed_h = derive_seed_geometry(torso_box)
	roi = compute_roi(
		seed_cx, seed_cy, seed_w, seed_h,
		reader.width, reader.height,
		roi_multiple,
	)
	roi_x1, roi_y1, roi_x2, roi_y2 = roi

	result = {
		"frame_index": frame_index,
		"pass": pass_num,
		"status": status,
		"seed_cx": seed_cx,
		"seed_cy": seed_cy,
		"seed_w": seed_w,
		"seed_h": seed_h,
		"roi_x1": roi_x1,
		"roi_y1": roi_y1,
		"roi_x2": roi_x2,
		"roi_y2": roi_y2,
		"residual_available": False,
		"raw_blob_count": 0,
		"nearest_blob_exists": False,
		"best_blob_x": None,
		"best_blob_y": None,
		"best_blob_area": None,
		"best_blob_integrated_mag": None,
		"second_best_integrated_mag": None,
		"best_to_second_best_mag_ratio": None,
		"best_blob_distance_px": None,
		"best_blob_distance_torso_h": None,
		"blob_center_inside_seed_box": False,
		"any_blob_within_0p25h": False,
		"any_blob_within_0p5h": False,
		"any_blob_within_0p1h": False,
		"blobs": [],
	}

	# degenerate ROI: nothing to look at
	if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
		return result

	residual, validity_mask = residual_motion.compute_residual_for_frame(
		reader,
		frame_index,
		scene_transform,
		half_window=residual_motion.DEFAULT_HALF_WINDOW,
		cache=frame_cache,
		roi=roi,
	)
	if residual is None or validity_mask is None:
		return result
	result["residual_available"] = True

	raw_blobs = residual_motion.extract_frame_blobs(
		residual,
		validity_mask,
		threshold=residual_motion.DEFAULT_THRESHOLD,
	)
	# extract_frame_blobs returns blob centroids in ROI-local coordinates;
	# restore full-frame coordinates as observe_blob_at does.
	for blob in raw_blobs:
		blob["centroid_x"] = float(blob["centroid_x"]) + roi_x1
		blob["centroid_y"] = float(blob["centroid_y"]) + roi_y1
	result["blobs"] = raw_blobs
	result["raw_blob_count"] = len(raw_blobs)

	if not raw_blobs:
		return result

	# scan every blob so we can report both "best by integrated_mag" and
	# distance-bucket hits; these are different blobs in general.
	nearest_dist = None
	inside_box = False
	for blob in raw_blobs:
		bx = blob["centroid_x"]
		by = blob["centroid_y"]
		dist = ((bx - seed_cx) ** 2 + (by - seed_cy) ** 2) ** 0.5
		if nearest_dist is None or dist < nearest_dist:
			nearest_dist = dist
		if point_in_box(bx, by, torso_box):
			inside_box = True
	# best_blob is defined as highest integrated_mag, matching
	# extract_frame_blobs' sort order (first entry = highest integrated_mag).
	best = raw_blobs[0]
	best_bx = best["centroid_x"]
	best_by = best["centroid_y"]
	best_dist = ((best_bx - seed_cx) ** 2 + (best_by - seed_cy) ** 2) ** 0.5

	result["nearest_blob_exists"] = True
	result["best_blob_x"] = best_bx
	result["best_blob_y"] = best_by
	result["best_blob_area"] = int(best["area"])
	best_mag = float(best["integrated_mag"])
	result["best_blob_integrated_mag"] = best_mag
	# blob-contrast ratio: how dominant the top blob is over the runner-up.
	# > 2.0 means the pipeline has a clear single winner; values near 1.0
	# mean multiple competing motion sources of comparable strength.
	if len(raw_blobs) >= 2:
		second_mag = float(raw_blobs[1]["integrated_mag"])
		result["second_best_integrated_mag"] = second_mag
		if second_mag > 0.0:
			result["best_to_second_best_mag_ratio"] = best_mag / second_mag
	result["best_blob_distance_px"] = best_dist
	if seed_h > 0.0:
		result["best_blob_distance_torso_h"] = best_dist / seed_h
	result["blob_center_inside_seed_box"] = inside_box
	if seed_h > 0.0 and nearest_dist is not None:
		nearest_h = nearest_dist / seed_h
		result["any_blob_within_0p25h"] = nearest_h <= 0.25
		result["any_blob_within_0p5h"] = nearest_h <= 0.5
		result["any_blob_within_0p1h"] = nearest_h <= 1.0
	return result


#============================================
def save_annotated_png(
	per_seed: dict,
	reader: object,
	out_dir: str,
) -> None:
	"""Render one annotated PNG showing seed box and blobs in ROI crop.

	All overlays draw in ROI-local coordinates (subtract ROI origin from
	every full-frame coord) so they land on the cropped image.
	"""
	frame_index = per_seed["frame_index"]
	pass_num = per_seed["pass"]
	status = per_seed["status"]
	roi_x1 = per_seed["roi_x1"]
	roi_y1 = per_seed["roi_y1"]
	roi_x2 = per_seed["roi_x2"]
	roi_y2 = per_seed["roi_y2"]
	if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
		return
	frame = reader.read_frame(frame_index)
	if frame is None:
		return
	crop = frame[roi_y1:roi_y2, roi_x1:roi_x2].copy()

	# seed torso_box (green) in ROI-local coords
	tx = int(round(per_seed["seed_cx"] - per_seed["seed_w"] / 2.0)) - roi_x1
	ty = int(round(per_seed["seed_cy"] - per_seed["seed_h"] / 2.0)) - roi_y1
	tw = int(round(per_seed["seed_w"]))
	th = int(round(per_seed["seed_h"]))
	cv2.rectangle(
		crop, (tx, ty), (tx + tw, ty + th), (0, 255, 0), thickness=2,
	)

	# every raw blob centroid (red), radius proportional to sqrt(area)
	for blob in per_seed["blobs"]:
		bx = int(round(blob["centroid_x"] - roi_x1))
		by = int(round(blob["centroid_y"] - roi_y1))
		radius = max(3, int(round((blob["area"] ** 0.5) / 2.0)))
		cv2.circle(crop, (bx, by), radius, (0, 0, 255), thickness=2)

	# best blob by integrated_mag (yellow)
	if per_seed["best_blob_x"] is not None:
		best_bx = int(round(per_seed["best_blob_x"] - roi_x1))
		best_by = int(round(per_seed["best_blob_y"] - roi_y1))
		cv2.circle(crop, (best_bx, best_by), 8, (0, 255, 255), thickness=2)

	# seed center crosshair (blue)
	scx = int(round(per_seed["seed_cx"] - roi_x1))
	scy = int(round(per_seed["seed_cy"] - roi_y1))
	cv2.drawMarker(
		crop, (scx, scy), (255, 0, 0),
		markerType=cv2.MARKER_CROSS, markerSize=14, thickness=2,
	)

	# footer text
	dist_px = per_seed["best_blob_distance_px"]
	dist_h = per_seed["best_blob_distance_torso_h"]
	if dist_px is None:
		dist_str = "best_dist=n/a"
	else:
		dist_str = f"best_dist={dist_px:.1f}px ({dist_h:.2f}h)"
	inside_str = "yes" if per_seed["blob_center_inside_seed_box"] else "no"
	footer = (
		f"f{frame_index} status={status} blobs={per_seed['raw_blob_count']} "
		f"{dist_str} inside_box={inside_str}"
	)
	crop_h = crop.shape[0]
	cv2.putText(
		crop, footer, (6, crop_h - 8),
		cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), thickness=1,
		lineType=cv2.LINE_AA,
	)

	out_name = f"seed_{frame_index:06d}_pass_{pass_num}_{status}.png"
	out_path = os.path.join(out_dir, out_name)
	cv2.imwrite(out_path, crop)


#============================================
def print_console_summary(
	results: list, total_seeds_in_file: int, roi_multiple: float,
) -> None:
	"""Print the phase-1 funnel summary as described in the plan."""
	checked = len(results)
	residual_ok = sum(1 for r in results if r["residual_available"])
	any_blob = sum(
		1 for r in results
		if r["residual_available"] and r["raw_blob_count"] > 0
	)
	best_defined = sum(
		1 for r in results if r["nearest_blob_exists"]
	)
	inside_box = sum(
		1 for r in results if r["blob_center_inside_seed_box"]
	)
	within_025 = sum(1 for r in results if r["any_blob_within_0p25h"])
	within_050 = sum(1 for r in results if r["any_blob_within_0p5h"])
	within_100 = sum(1 for r in results if r["any_blob_within_0p1h"])

	print("")
	print(f"  ROI multiple:                  {roi_multiple:.1f}x torso")
	print(f"  total seeds (in file):         {total_seeds_in_file}")
	print(f"  seeds checked (after filters): {checked}")
	if checked == 0:
		print("  (nothing to score)")
		return
	print(f"  residual available:            {residual_ok} / {checked}")
	print(f"  any raw blob in ROI:           {any_blob} / {residual_ok}")
	print(f"  best blob distance defined:    {best_defined} / {residual_ok}")
	print(f"  blob center inside seed box:   {inside_box} / {residual_ok}")
	print(f"  nearest blob <= 0.25 torso_h:  {within_025} / {residual_ok}")
	print(f"  nearest blob <= 0.50 torso_h:  {within_050} / {residual_ok}")
	print(f"  nearest blob <= 1.00 torso_h:  {within_100} / {residual_ok}")

	# blob-contrast ratio: distribution of best/second-best integrated_mag.
	# Values >> 1.0 mean a single dominant blob (good); values near 1.0
	# mean ambiguous field with competing motion sources. Only defined
	# when at least two raw blobs were extracted.
	ratios = [
		r["best_to_second_best_mag_ratio"]
		for r in results
		if r["best_to_second_best_mag_ratio"] is not None
	]
	if ratios:
		ratios_sorted = sorted(ratios)
		n = len(ratios_sorted)
		median = ratios_sorted[n // 2]
		p25 = ratios_sorted[n // 4]
		p75 = ratios_sorted[(3 * n) // 4]
		dominant = sum(1 for r in ratios if r >= 2.0)
		ambiguous = sum(1 for r in ratios if r < 1.25)
		print(
			f"  best/2nd integrated_mag (n={n}): "
			f"p25={p25:.2f}  median={median:.2f}  p75={p75:.2f}"
		)
		print(
			f"    dominant (ratio>=2.0):       {dominant} / {n}"
		)
		print(
			f"    ambiguous (ratio<1.25):      {ambiguous} / {n}"
		)

	# per-status breakdown so visible / partial / approximate are not
	# averaged together. partial and approximate seeds legitimately have
	# weaker geometry than visible; mixing them hides the signal.
	by_status = {}
	for r in results:
		by_status.setdefault(r["status"], []).append(r)
	print("")
	print("  by status:")
	for status, group in sorted(by_status.items()):
		count = len(group)
		inside = sum(1 for r in group if r["blob_center_inside_seed_box"])
		within_h = sum(1 for r in group if r["any_blob_within_0p5h"])
		print(
			f"    {status:<12} ({count:3d}): "
			f"inside-box {inside:3d}, within-0.5h {within_h:3d}"
		)


#============================================
CSV_COLUMNS = (
	"frame_index", "pass", "status",
	"seed_cx", "seed_cy", "seed_w", "seed_h",
	"roi_x1", "roi_y1", "roi_x2", "roi_y2",
	"residual_available", "raw_blob_count", "nearest_blob_exists",
	"best_blob_x", "best_blob_y", "best_blob_area",
	"best_blob_integrated_mag", "second_best_integrated_mag",
	"best_to_second_best_mag_ratio", "best_blob_distance_px",
	"best_blob_distance_torso_h", "blob_center_inside_seed_box",
	"any_blob_within_0p25h", "any_blob_within_0p5h",
	"any_blob_within_0p1h",
)


#============================================
def _csv_row_from_result(per_seed: dict) -> list:
	"""Format one CSV row from a per-seed result dict.

	Undefined numeric metrics are written as empty cells (not 0 / -1 /
	"None") so downstream filters can distinguish "no data" from "zero".
	"""
	row = []
	for col in CSV_COLUMNS:
		val = per_seed.get(col)
		if val is None:
			row.append("")
		elif isinstance(val, bool):
			row.append("1" if val else "0")
		elif isinstance(val, float):
			row.append(f"{val:.4f}")
		else:
			row.append(val)
	return row


#============================================
def main() -> None:
	args = parse_args()

	# resolve paths
	seeds_path = args.seeds_file
	if seeds_path is None:
		seeds_path = tr_paths.default_seeds_path(args.input_file)
	motion_path = tr_paths.default_camera_motion_path(args.input_file)

	# load seeds
	seeds_data = state_io.load_seeds(seeds_path)
	all_seeds = list(seeds_data.get("seeds", []))
	total_seeds_in_file = len(all_seeds)
	if total_seeds_in_file == 0:
		raise RuntimeError(f"no seeds in {seeds_path}")
	print(f"loaded {total_seeds_in_file} seeds from {seeds_path}")

	# require motion cache: an identity transform would silently bias the
	# residual against the truth, defeating the diagnostic
	motion_track = camera_motion.load_motion_cache(motion_path, None)
	if motion_track is None:
		raise RuntimeError(
			f"camera motion cache missing or unreadable: {motion_path}\n"
			"run 'track_runner.py -i VIDEO solve' first to populate it"
		)
	print(f"loaded camera motion track ({motion_track.dx.shape[0]} frames)")
	transform = scene_coords.SceneTransform(motion_track)

	# open video reader
	probe_info = probe_video.probe_video(args.input_file)
	reader = frame_reader.FrameReader(
		args.input_file, probe_info["fps"], probe_info["frame_count"],
	)
	fps = float(probe_info["fps"])
	frame_count = int(probe_info["frame_count"])
	print(
		f"video: {probe_info['width']}x{probe_info['height']} "
		f"@ {fps:.3f} fps, {frame_count} frames"
	)

	# apply filters: status -> time-range -> torso_box presence
	allowed_statuses = parse_status_filter(args.status_csv)
	start_f, end_f = parse_time_range(args.time_range, fps, frame_count)
	filtered = []
	for seed in all_seeds:
		if seed.get("status") not in allowed_statuses:
			continue
		if seed.get("torso_box") is None:
			continue
		fi = int(seed["frame_index"])
		if fi < start_f or fi > end_f:
			continue
		filtered.append(seed)
	print(
		f"filters: statuses={sorted(allowed_statuses)}  "
		f"frames=[{start_f}, {end_f}]  "
		f"kept {len(filtered)} / {total_seeds_in_file}"
	)

	# reproducible sampling. default random_seed=0 so repeated runs give
	# the same sample set; -1 means use OS entropy.
	if args.max_seeds is not None and len(filtered) > args.max_seeds:
		if args.random_seed < 0:
			rng = random.Random()
		else:
			rng = random.Random(args.random_seed)
		filtered = rng.sample(filtered, args.max_seeds)
		# sort for deterministic iteration/output order even after sampling
		filtered.sort(key=lambda s: int(s["frame_index"]))
		print(
			f"sampled {len(filtered)} seeds "
			f"(--max-seeds={args.max_seeds}, --random-seed={args.random_seed})"
		)

	# prepare images dir. Auto-append the ROI tag to the user-provided
	# path so runs at different --roi-multiple values land in separate
	# directories without the user having to remember to vary the path.
	# Example: `-O /tmp/seed_blob_png -R 8.0` -> /tmp/seed_blob_png_roi8p0/
	images_dir = None
	if args.images_dir is not None:
		roi_tag = f"roi{args.roi_multiple:.1f}".replace(".", "p")
		images_dir = f"{args.images_dir.rstrip('/')}_{roi_tag}"
		os.makedirs(images_dir, exist_ok=True)
		print(f"saving annotated PNGs to {images_dir}")

	# shared frame-reads cache for compute_residual_for_frame. Opaque to
	# this tool; scoped to the full run so overlapping temporal windows
	# across seeds reuse frame decodes. Decision-free by construction.
	frame_cache = {}

	# open CSV once and stream rows as each seed finishes so partial
	# results survive interrupts and `tail -f <csv>` gives live progress.
	csv_fh = None
	csv_writer = None
	if args.csv_path is not None:
		csv_fh = open(args.csv_path, "w", newline="")
		csv_writer = csv.writer(csv_fh)
		csv_writer.writerow(list(CSV_COLUMNS))
		csv_fh.flush()

	# run pipeline
	results = []
	try:
		for idx, seed in enumerate(filtered):
			per_seed = analyze_seed(
				seed, reader, transform, frame_cache, args.roi_multiple,
			)
			results.append(per_seed)
			# one-line per-seed progress so long runs show life. flush
			# explicitly because this tool is commonly piped through
			# `tee` / `tail` / redirected, which can leave the stdout
			# buffer block-buffered even with PYTHONUNBUFFERED set.
			if per_seed["residual_available"]:
				if per_seed["nearest_blob_exists"]:
					dist_h = per_seed["best_blob_distance_torso_h"]
					tag = (
						f"blobs={per_seed['raw_blob_count']} "
						f"best={dist_h:.2f}h "
						f"inside={'Y' if per_seed['blob_center_inside_seed_box'] else 'N'}"
					)
				else:
					tag = "blobs=0"
			else:
				tag = "no residual"
			print(
				f"  [{idx + 1}/{len(filtered)}] "
				f"f{per_seed['frame_index']:>6d} "
				f"status={per_seed['status']:<11} {tag}",
				flush=True,
			)
			if csv_writer is not None:
				csv_writer.writerow(_csv_row_from_result(per_seed))
				csv_fh.flush()
			if images_dir is not None:
				save_annotated_png(per_seed, reader, images_dir)
	finally:
		if csv_fh is not None:
			csv_fh.close()
		reader.close()

	# summary + outputs
	print_console_summary(results, total_seeds_in_file, args.roi_multiple)
	if args.csv_path is not None:
		print("")
		print(f"  CSV written to {args.csv_path}")


if __name__ == "__main__":
	main()
