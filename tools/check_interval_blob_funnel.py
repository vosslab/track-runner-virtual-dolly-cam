#!/usr/bin/env python3
"""Replay the solver's per-frame blob snap on each interval and count
where blobs fall out of the funnel.

Phase 2 diagnostic. Phase 1 (tools/check_seed_blob_overlap.py) showed
raw detection is strong at seed frames (3x ROI) but the runner blob is
much less often the strongest candidate under solver-sized context
(8x ROI). Phase 2 answers: when the real solver runs FWD/BWD on a real
interval, at what stage do blobs disappear -- corridor selection or
later gates?

No changes to track_runner/. This tool imports existing solver helpers
and constants directly, including a small number of private-but-stable
internal helpers (`_compute_raw_pred_forward`,
`_compute_raw_pred_backward`, `_motion_path_ok`), to avoid logic drift.
A companion drift-detection test (tests/test_blob_funnel_tool.py) pins
the tool's per-frame outcomes to the solver's `blob_gate` stamps on a
synthetic fixture, so any future solver change this tool fails to
mirror flips the test.

Funnel shape, per pass per interval:

  eligible        non-endpoint, non-stationary, reader-ok frames
  residual_ok     compute_residual_for_frame returned non-None
  raw_blob        at least one raw blob in the solver's 8x ROI
  corridor        at least one blob survived the corridor filter
  proximity_pass  proximity gate passed for the corridor winner
  direction_pass  direction gate passed
  path_pass       motion-path gate passed
  accepted        all three gates passed (matches solver's
                  blob_gate=="accepted")

Output: console two-line print per interval, CSV, JSON sidecar.
"""

# Standard Library
import os
import sys
import csv
import json
import math
import random
import argparse

# PIP3 modules
import cv2
import numpy

# add track_runner directory to path so we can import its modules.
# Mirrors the pattern used by every other tool under tools/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, "track_runner")
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

# local repo modules (none in stdlib or pip beyond what the solver pulls)
import camera_motion
import interval_fingerprint
import residual_motion
import scene_coords
import state_io
import tr_paths
import velocity_model
import video_io


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Replay the solver's blob-snap pipeline per interval and "
			"count where blobs drop out of the funnel (phase 2)."
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
		"--interval", dest="interval_range", default=None,
		help=(
			"Limit to a range of 0-based interval indices. "
			"Format: 'I:J' inclusive. Example: '120:145'. "
			"Mutually exclusive with --interval-index."
		)
	)
	parser.add_argument(
		"--interval-index", dest="interval_indices", default=None,
		help=(
			"Comma-separated 0-based interval indices to process. "
			"Example: '12,15,42'. Mutually exclusive with --interval."
		)
	)
	parser.add_argument(
		"-c", "--csv", dest="csv_path", default=None,
		help="Write one row per (interval, pass) with funnel counters."
	)
	parser.add_argument(
		"-j", "--json", dest="json_path", default=None,
		help="Write per-interval funnel data as JSON sidecar."
	)
	parser.add_argument(
		"-Q", "--no-print-per-interval", dest="print_per_interval",
		action="store_false",
		help="Suppress per-interval console output. Summary still prints."
	)
	parser.set_defaults(print_per_interval=True)
	parser.add_argument(
		"-F", "--save-frames", dest="frames_dir", default=None,
		help=(
			"Save one side-by-side PNG per eligible frame of each "
			"processed interval (left: real frame ROI; right: motion-"
			"cue heat map). Minimal overlays: predicted center, chosen "
			"blob centroid, interpolated torso box. Use --limit to cap "
			"the number of intervals first."
		)
	)
	parser.add_argument(
		"-N", "--limit", dest="limit", type=int, default=None,
		help=(
			"Cap on number of intervals processed after filtering. "
			"Useful for a quick smoke run."
		)
	)
	parser.add_argument(
		"-S", "--shuffle", dest="shuffle", action="store_true",
		help=(
			"Shuffle the filtered intervals before applying --limit so "
			"the sample is spread across the race rather than only the "
			"first N. Uses OS entropy: each run picks a different sample."
		)
	)
	parser.add_argument(
		"-G", "--gate-trace", dest="gate_trace", action="store_true",
		help=(
			"Print one row per eligible frame per interval showing "
			"blob presence, dist/h, each gate's pass/fail, v_pred magnitude, "
			"and the first gate that rejected (or 'none' / 'absent' / "
			"'no_corridor'). Diagnostic for deciding whether the gate logic "
			"or the corridor is the reason a given interval has a low "
			"blob_accept percentage. Pair with --interval-index to scope "
			"the trace to human-verified intervals."
		)
	)
	parser.add_argument(
		"--diagnostic-blended-reference", dest="diagnostic_blended_reference",
		action="store_true",
		help=(
			"OFFLINE DIAGNOSTIC ONLY. Replace the corridor-best-blob "
			"centroid with the solver's blended interval path "
			"(blended_path) position at that frame before running the "
			"gates. Tests whether the current gates would accept the "
			"solver's own final output location. Not a legal solve-time "
			"input; never wire into production."
		)
	)
	args = parser.parse_args()
	if args.interval_range is not None and args.interval_indices is not None:
		parser.error("--interval and --interval-index are mutually exclusive")
	return args


#============================================
def parse_interval_range(range_str: str) -> tuple:
	"""Parse 'I:J' inclusive interval-index range into (i_min, i_max).

	Empty endpoints are allowed on either side. Returns (None, None)
	when input is None.
	"""
	if range_str is None:
		return (None, None)
	parts = range_str.split(":")
	if len(parts) != 2:
		raise RuntimeError(
			f"invalid --interval {range_str!r}, expected 'I:J'"
		)
	i_min = int(parts[0]) if parts[0].strip() else None
	i_max = int(parts[1]) if parts[1].strip() else None
	return (i_min, i_max)


#============================================
def parse_interval_indices(indices_str: str) -> set:
	"""Parse '1,4,12' into {1, 4, 12}."""
	if indices_str is None:
		return None
	result = set()
	for piece in indices_str.split(","):
		piece = piece.strip()
		if piece:
			result.add(int(piece))
	return result


#============================================
# Per-frame replay of _build_snap_pred's gate block. The two inline gate
# expressions (proximity and direction) duplicate exactly these lines
# from track_runner/velocity_model.py _build_snap_pred:
#
#     proximity_ok = dist <= BLOB_SNAP_ALPHA * h
#     if v_pred_mag > BLOB_SNAP_VELOCITY_FLOOR:
#         direction_ok = (dx * v_pred[0] + dy * v_pred[1]) >= 0.0
#     else:
#         direction_ok = True
#
# The motion-path gate reuses the existing public helper
# velocity_model._motion_path_ok and all threshold constants come from
# velocity_model module-level symbols. The drift-detection test in
# tests/test_blob_funnel_tool.py verifies the replay gate outcome here
# matches the solver's snap_pred[i]["blob_gate"] for every frame of a
# synthetic fixture. Any future change to the inline gate math that is
# not mirrored here flips that test.
def _replay_frame_funnel(
	raw_prev: tuple,
	raw_curr: tuple,
	raw_next: tuple,
	reader: object,
	scene_transform: object,
	residual_cache: dict,
	blended_override: tuple = None,
) -> dict:
	"""Run the snap pipeline on one frame and return the funnel stamp.

	Args:
		raw_prev, raw_curr, raw_next: raw_pred tuples from
			_compute_raw_pred_forward/_backward, shape
			(frame_index, cx, cy, w, h, conf, is_stationary).
		reader, scene_transform, residual_cache: same arguments the
			solver passes to observe_blob_at.

	Returns:
		Dict with keys: residual_ok, raw_blob_count, corridor_count,
		proximity_ok (bool or None), direction_ok, path_ok, accepted,
		gate_outcome (str: "absent", "rejected", "accepted").
	"""
	frame_index, raw_cx, raw_cy, w, h, _conf, _is_stat = raw_curr
	_, raw_prev_cx, raw_prev_cy, _, _, _, _ = raw_prev
	_, raw_next_cx, raw_next_cy, _, _, _, _ = raw_next

	# local motion vectors in iteration order (duplicated from
	# velocity_model._build_snap_pred so no solver change is required)
	v_prev = (raw_cx - raw_prev_cx, raw_cy - raw_prev_cy)
	v_next = (raw_next_cx - raw_cx, raw_next_cy - raw_cy)
	v_pred = (
		0.5 * (v_prev[0] + v_next[0]),
		0.5 * (v_prev[1] + v_next[1]),
	)
	v_pred_mag = math.sqrt(v_pred[0] * v_pred[0] + v_pred[1] * v_pred[1])
	if v_pred_mag > 1e-6:
		t_x = v_pred[0] / v_pred_mag
		t_y = v_pred[1] / v_pred_mag
		local_tangent = (t_x, t_y, -t_y, t_x)
	else:
		local_tangent = (1.0, 0.0, 0.0, 1.0)

	# drive the residual + raw-blob + corridor stages directly so we can
	# count survivors at each stage. This is the same sequence of calls
	# observe_blob_at makes internally, just with the counts kept.
	frame_w = getattr(reader, "width", 1920)
	frame_h = getattr(reader, "height", 1080)
	roi = residual_motion._compute_roi(raw_cx, raw_cy, h, frame_w, frame_h)

	cached = residual_cache.get((frame_index, roi))
	if cached is None:
		frame_read_cache = residual_cache.setdefault("_frames", {})
		residual, validity_mask = residual_motion.compute_residual_for_frame(
			reader, frame_index, scene_transform,
			residual_motion.DEFAULT_HALF_WINDOW,
			frame_read_cache, roi,
		)
		if residual is None:
			residual_cache[(frame_index, roi)] = {"raw_blobs": []}
			return {
				"residual_ok": False,
				"raw_blob_count": 0,
				"corridor_count": 0,
				"proximity_ok": None,
				"direction_ok": None,
				"path_ok": None,
				"accepted": False,
				"gate_outcome": "absent",
				"blob_present": False,
				"v_pred_mag": v_pred_mag,
				"first_failed_gate": "no_residual",
			}
		raw_blobs = residual_motion.extract_frame_blobs(
			residual, validity_mask, residual_motion.DEFAULT_THRESHOLD,
		)
		# restore full-frame coordinates (observe_blob_at does this too)
		for blob in raw_blobs:
			blob["centroid_x"] += roi[0]
			blob["centroid_y"] += roi[1]
		residual_cache[(frame_index, roi)] = {"raw_blobs": raw_blobs}
	raw_blobs = residual_cache[(frame_index, roi)]["raw_blobs"]
	raw_blob_count = len(raw_blobs)

	if raw_blob_count == 0:
		return {
			"residual_ok": True,
			"raw_blob_count": 0,
			"corridor_count": 0,
			"proximity_ok": None,
			"direction_ok": None,
			"path_ok": None,
			"accepted": False,
			"gate_outcome": "absent",
			"blob_present": False,
			"v_pred_mag": v_pred_mag,
			"first_failed_gate": "no_raw_blob",
		}

	corridor_radius = max(1.5 * w, 0.75 * h)
	corridor_blobs = residual_motion.filter_blobs_to_corridor(
		raw_blobs, raw_cx, raw_cy, local_tangent, corridor_radius,
	)
	corridor_count = len(corridor_blobs)
	if corridor_count == 0:
		return {
			"residual_ok": True,
			"raw_blob_count": raw_blob_count,
			"corridor_count": 0,
			"proximity_ok": None,
			"direction_ok": None,
			"path_ok": None,
			"accepted": False,
			"gate_outcome": "absent",
			"blob_present": False,
			"v_pred_mag": v_pred_mag,
			"first_failed_gate": "no_corridor",
		}

	# best-confidence corridor blob, identical to observe_blob_at's pick
	best_blob = None
	best_score = -1.0
	for blob in corridor_blobs:
		score = residual_motion.compute_cue_confidence(
			blob, raw_cx, raw_cy, w, h, local_tangent,
		)
		if score > best_score:
			best_score = score
			best_blob = blob
	if best_blob is None:
		return {
			"residual_ok": True,
			"raw_blob_count": raw_blob_count,
			"corridor_count": corridor_count,
			"proximity_ok": None,
			"direction_ok": None,
			"path_ok": None,
			"accepted": False,
			"gate_outcome": "absent",
			"blob_present": False,
			"v_pred_mag": v_pred_mag,
			"first_failed_gate": "no_best",
		}

	real_bx = best_blob["centroid_x"]
	real_by = best_blob["centroid_y"]
	# real-blob-to-blended distance: only meaningful when a blended_override
	# exists for this frame. Captured before the override so the CSV can
	# show how far the real corridor pick was from the solver's output.
	real_to_blended_h = None
	if blended_override is not None:
		fx, fy = blended_override
		real_to_blended_h = (
			math.sqrt((real_bx - fx) ** 2 + (real_by - fy) ** 2) / h
			if h > 0.0 else None
		)
		bx = fx
		by = fy
	else:
		bx = real_bx
		by = real_by
	dx = bx - raw_cx
	dy = by - raw_cy
	dist = math.sqrt(dx * dx + dy * dy)

	# --- Gate block: duplicated from velocity_model._build_snap_pred ---
	proximity_ok = dist <= velocity_model.BLOB_SNAP_ALPHA * h
	if v_pred_mag > velocity_model.BLOB_SNAP_VELOCITY_FLOOR:
		direction_ok = (dx * v_pred[0] + dy * v_pred[1]) >= 0.0
	else:
		direction_ok = True
	path_ok_prev = velocity_model._motion_path_ok(
		(raw_prev_cx, raw_prev_cy), v_prev, (bx, by),
		velocity_model.BLOB_SNAP_PATH_SLACK,
		velocity_model.BLOB_SNAP_PATH_PERP_FRACTION,
		velocity_model.BLOB_SNAP_VELOCITY_FLOOR,
	)
	path_ok = path_ok_prev is not False
	# --- end gate block ---

	# perpendicular distance from blob to prev-anchor motion line, in
	# pixels, for gate-margin analysis. Only defined when the motion
	# vector is above the velocity floor (below that, the motion-path
	# gate is vacuous and returns None). Computed once here so both the
	# per-pass accumulator and downstream diagnostics can reuse it.
	path_perp_px = None
	v_prev_mag_sq = v_prev[0] * v_prev[0] + v_prev[1] * v_prev[1]
	if v_prev_mag_sq > velocity_model.BLOB_SNAP_VELOCITY_FLOOR ** 2:
		v_prev_mag = math.sqrt(v_prev_mag_sq)
		adx = bx - raw_prev_cx
		ady = by - raw_prev_cy
		proj = (adx * v_prev[0] + ady * v_prev[1]) / v_prev_mag
		scale = proj / v_prev_mag
		perp_x = adx - scale * v_prev[0]
		perp_y = ady - scale * v_prev[1]
		path_perp_px = math.sqrt(perp_x * perp_x + perp_y * perp_y)

	accepted = proximity_ok and direction_ok and path_ok
	# first_failed_gate: earliest of (proximity, direction, path) that
	# returned False, in the same order velocity_model evaluates them.
	# "none" when all three pass. direction_ok can be True-as-vacuous
	# below the velocity floor; that still counts as a pass here because
	# the solver accepts it.
	if accepted:
		first_failed_gate = "none"
	elif not proximity_ok:
		first_failed_gate = "proximity"
	elif not direction_ok:
		first_failed_gate = "direction"
	else:
		first_failed_gate = "path"
	return {
		"residual_ok": True,
		"raw_blob_count": raw_blob_count,
		"corridor_count": corridor_count,
		"proximity_ok": bool(proximity_ok),
		"direction_ok": bool(direction_ok),
		"path_ok": bool(path_ok),
		"accepted": bool(accepted),
		"gate_outcome": "accepted" if accepted else "rejected",
		"blob_dist_px": dist,
		"blob_dist_h": dist / h if h > 0.0 else None,
		"path_perp_px": path_perp_px,
		"path_perp_h": (
			path_perp_px / h if path_perp_px is not None and h > 0.0
			else None
		),
		"real_to_blended_h": real_to_blended_h,
		"blob_present": True,
		"v_pred_mag": v_pred_mag,
		"first_failed_gate": first_failed_gate,
	}


#============================================
def _empty_funnel() -> dict:
	"""Fresh zeroed funnel dict.

	Counter keys are strict frame counts per stage. The two list keys
	(`blob_dist_h_samples`, `path_perp_h_samples`) collect normalized
	gate-margin samples so downstream summaries can compute median/p90/
	max across the interval without re-running the replay.
	"""
	return {
		"eligible": 0,
		"residual_ok": 0,
		"raw_blob": 0,
		"corridor": 0,
		"proximity_pass": 0,
		"direction_pass": 0,
		"path_pass": 0,
		"accepted": 0,
		"blob_dist_h_samples": [],
		"path_perp_h_samples": [],
		"real_to_blended_h_samples": [],
	}


#============================================
def _funnel_add_frame(funnel: dict, stamp: dict) -> None:
	"""Increment per-stage counters from one frame's replay stamp."""
	funnel["eligible"] += 1
	if stamp["residual_ok"]:
		funnel["residual_ok"] += 1
	if stamp["raw_blob_count"] > 0:
		funnel["raw_blob"] += 1
	if stamp["corridor_count"] > 0:
		funnel["corridor"] += 1
	if stamp.get("proximity_ok"):
		funnel["proximity_pass"] += 1
	if stamp.get("direction_ok"):
		funnel["direction_pass"] += 1
	if stamp.get("path_ok"):
		funnel["path_pass"] += 1
	if stamp.get("accepted"):
		funnel["accepted"] += 1
	# gate-margin samples: only collected when a corridor blob was
	# actually picked (otherwise the distances are undefined). Stored as
	# raw floats so _summarize_samples can produce median/p90/max later.
	if stamp.get("blob_dist_h") is not None:
		funnel["blob_dist_h_samples"].append(stamp["blob_dist_h"])
	if stamp.get("path_perp_h") is not None:
		funnel["path_perp_h_samples"].append(stamp["path_perp_h"])
	if stamp.get("real_to_blended_h") is not None:
		funnel["real_to_blended_h_samples"].append(stamp["real_to_blended_h"])


#============================================
def _render_side_by_side(
	frame_bgr: "numpy.ndarray",
	residual_mag: "numpy.ndarray",
	validity_mask: "numpy.ndarray",
	roi: tuple,
	raw_cx: float,
	raw_cy: float,
	pred_w: float,
	pred_h: float,
	best_blob_xy: tuple,
	title: str,
	footer: str,
) -> "numpy.ndarray":
	"""Build a side-by-side PNG: real ROI crop | colorized heat map.

	Overlays are drawn identically on both panels so the viewer can
	match position between image and motion cue. Coordinates are all in
	full-frame pixels; the function subtracts the ROI origin internally.

	Args:
		frame_bgr: Full-frame BGR read via VideoReader.read_frame.
		residual_mag: ROI-sized magnitude array from
			compute_residual_for_frame.
		validity_mask: ROI-sized validity mask (255 valid).
		roi: (x1, y1, x2, y2) ROI bounds in full-frame pixels.
		raw_cx, raw_cy: Predicted center (full frame).
		pred_w, pred_h: Hermite-interpolated torso box size.
		best_blob_xy: (bx, by) chosen corridor blob centroid, or None
			when no blob was picked for this frame.
		title, footer: single-line strings drawn above / below.

	Returns:
		BGR image with both panels stacked horizontally.
	"""
	x1, y1, x2, y2 = roi
	real_crop = frame_bgr[y1:y2, x1:x2].copy()

	# colorize residual magnitude via the shared residual_motion primitive.
	# Same fixed-max scale the annotation GUI and
	# tools/diagnose_residual_motion.py use, so all callers produce visually
	# identical heat maps.
	heat = residual_motion.colorize_jet(residual_mag)
	# darken invalid pixels so visually clipped regions are obvious
	if validity_mask is not None:
		invalid = validity_mask == 0
		heat[invalid] = heat[invalid] // 3

	# pad panels to the same height (compute_residual may return a
	# slightly smaller mask than the frame crop due to ROI quantization)
	h_panel = max(real_crop.shape[0], heat.shape[0])
	w_real = real_crop.shape[1]
	w_heat = heat.shape[1]

	def _pad(img: "numpy.ndarray", h_target: int, w_target: int
		) -> "numpy.ndarray":
		if img.shape[0] == h_target and img.shape[1] == w_target:
			return img
		padded = numpy.zeros((h_target, w_target, 3), dtype=numpy.uint8)
		padded[:img.shape[0], :img.shape[1]] = img
		return padded

	real_padded = _pad(real_crop, h_panel, w_real)
	heat_padded = _pad(heat, h_panel, w_heat)

	# draw overlays on both panels. ROI-local coords: subtract roi origin.
	def _draw_overlays(img: "numpy.ndarray") -> None:
		# Hermite-interpolated torso box (green, thin)
		tx = int(round(raw_cx - pred_w / 2.0)) - x1
		ty = int(round(raw_cy - pred_h / 2.0)) - y1
		tw = int(round(pred_w))
		th = int(round(pred_h))
		cv2.rectangle(
			img, (tx, ty), (tx + tw, ty + th), (0, 255, 0), thickness=1,
		)
		# predicted center (red crosshair)
		pcx = int(round(raw_cx)) - x1
		pcy = int(round(raw_cy)) - y1
		cv2.drawMarker(
			img, (pcx, pcy), (0, 0, 255),
			markerType=cv2.MARKER_CROSS, markerSize=12, thickness=1,
		)
		# chosen blob centroid (yellow dot)
		if best_blob_xy is not None:
			bx, by = best_blob_xy
			bcx = int(round(bx)) - x1
			bcy = int(round(by)) - y1
			cv2.circle(img, (bcx, bcy), 6, (0, 255, 255), thickness=2)

	_draw_overlays(real_padded)
	_draw_overlays(heat_padded)

	# stack side by side with a thin separator
	separator = numpy.zeros((h_panel, 4, 3), dtype=numpy.uint8)
	combined = numpy.hstack([real_padded, separator, heat_padded])

	# title bar on top, footer on bottom
	bar_h = 18
	title_bar = numpy.zeros((bar_h, combined.shape[1], 3), dtype=numpy.uint8)
	footer_bar = numpy.zeros((bar_h, combined.shape[1], 3), dtype=numpy.uint8)
	cv2.putText(
		title_bar, title, (6, 13),
		cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA,
	)
	cv2.putText(
		footer_bar, footer, (6, 13),
		cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA,
	)
	return numpy.vstack([title_bar, combined, footer_bar])


#============================================
def _save_frame_png(
	out_dir: str,
	idx: int,
	frame_index: int,
	pass_name: str,
	raw_curr: tuple,
	raw_prev: tuple,
	raw_next: tuple,
	reader: object,
	scene_transform: object,
	stamp: dict,
) -> None:
	"""Re-compute residual and write a side-by-side PNG for one frame.

	The replay loop clears image data per frame after extraction, so the
	render path recomputes residual here. Duplicate work is acceptable
	for diagnostic renders, which are bounded by the user's --limit.
	"""
	_fi, raw_cx, raw_cy, pred_w, pred_h, _conf, _is_stat = raw_curr
	frame_w = getattr(reader, "width", 1920)
	frame_h = getattr(reader, "height", 1080)
	roi = residual_motion._compute_roi(
		raw_cx, raw_cy, pred_h, frame_w, frame_h,
	)
	frame_bgr = reader.read_frame(frame_index)
	if frame_bgr is None:
		return
	residual, validity_mask = residual_motion.compute_residual_for_frame(
		reader, frame_index, scene_transform,
		residual_motion.DEFAULT_HALF_WINDOW,
		None, roi,
	)
	if residual is None:
		return

	# best blob centroid: duplicate the corridor pick so the overlay
	# matches the gated frame's decision exactly
	best_blob_xy = None
	raw_blobs = residual_motion.extract_frame_blobs(
		residual, validity_mask, residual_motion.DEFAULT_THRESHOLD,
	)
	for blob in raw_blobs:
		blob["centroid_x"] += roi[0]
		blob["centroid_y"] += roi[1]
	if raw_blobs:
		v_prev = (raw_curr[1] - raw_prev[1], raw_curr[2] - raw_prev[2])
		v_next = (raw_next[1] - raw_curr[1], raw_next[2] - raw_curr[2])
		v_pred = (
			0.5 * (v_prev[0] + v_next[0]),
			0.5 * (v_prev[1] + v_next[1]),
		)
		v_pred_mag = math.sqrt(
			v_pred[0] * v_pred[0] + v_pred[1] * v_pred[1]
		)
		if v_pred_mag > 1e-6:
			t_x = v_pred[0] / v_pred_mag
			t_y = v_pred[1] / v_pred_mag
			tangent = (t_x, t_y, -t_y, t_x)
		else:
			tangent = (1.0, 0.0, 0.0, 1.0)
		corridor_radius = max(1.5 * pred_w, 0.75 * pred_h)
		corridor_blobs = residual_motion.filter_blobs_to_corridor(
			raw_blobs, raw_cx, raw_cy, tangent, corridor_radius,
		)
		best_score = -1.0
		best_blob = None
		for blob in corridor_blobs:
			score = residual_motion.compute_cue_confidence(
				blob, raw_cx, raw_cy, pred_w, pred_h, tangent,
			)
			if score > best_score:
				best_score = score
				best_blob = blob
		if best_blob is not None:
			best_blob_xy = (best_blob["centroid_x"], best_blob["centroid_y"])

	outcome = stamp.get("gate_outcome", "absent")
	dist_h = stamp.get("blob_dist_h")
	perp_h = stamp.get("path_perp_h")
	title = (
		f"iv[{idx}] frame {frame_index}  pass={pass_name}  {outcome}"
	)
	footer_parts = []
	if dist_h is not None:
		footer_parts.append(f"pred_to_blob={dist_h:.2f}h")
	if perp_h is not None:
		footer_parts.append(f"path_perp={perp_h:.2f}h")
	footer_parts.append(
		f"proximity={'Y' if stamp.get('proximity_ok') else 'N'}"
	)
	footer_parts.append(
		f"path={'Y' if stamp.get('path_ok') else 'N'}"
	)
	footer = "  ".join(footer_parts)

	out_name = (
		f"iv{idx:03d}_f{frame_index:06d}_{pass_name.lower()}_{outcome}.png"
	)
	out_path = os.path.join(out_dir, out_name)
	img = _render_side_by_side(
		frame_bgr, residual, validity_mask, roi,
		raw_cx, raw_cy, pred_w, pred_h, best_blob_xy, title, footer,
	)
	cv2.imwrite(out_path, img)


#============================================
def replay_pass(
	raw_pred: list,
	reader: object,
	scene_transform: object,
	residual_cache: dict,
	on_frame: object = None,
	blended_lookup: dict = None,
) -> dict:
	"""Replay one pass (FWD or BWD) and return its funnel counts.

	Args:
		raw_pred: List of raw_pred tuples from
			_compute_raw_pred_forward/_backward.
		reader, scene_transform, residual_cache: solver-style args.
		on_frame: Optional callback(i, raw_prev, raw_curr, raw_next,
			stamp) fired once per eligible frame. Used by the
			--save-frames renderer; None disables.

	Returns:
		Funnel dict with per-stage counts. Stationary intervals and
		endpoint frames count as zero eligible, matching the solver's
		"skipped" classification.
	"""
	funnel = _empty_funnel()
	num = len(raw_pred)
	for i in range(num):
		is_endpoint = (i == 0) or (i == num - 1)
		is_stat = bool(raw_pred[i][6])
		# solver's skip rule: endpoints, stationary-lock, or reader
		# unavailable. Reader availability is a whole-interval property;
		# if the tool got this far, the reader is real.
		if is_endpoint or is_stat:
			continue
		# blended_lookup, when provided, maps frame_index -> (cx, cy).
		# Skip the override (treat as pure real-blob run) when the
		# blended track has no entry for this frame so the gate math
		# still runs against the real corridor pick.
		blended_override = None
		if blended_lookup is not None:
			blended_override = blended_lookup.get(int(raw_pred[i][0]))
		stamp = _replay_frame_funnel(
			raw_pred[i - 1], raw_pred[i], raw_pred[i + 1],
			reader, scene_transform, residual_cache,
			blended_override=blended_override,
		)
		_funnel_add_frame(funnel, stamp)
		if on_frame is not None:
			on_frame(i, raw_pred[i - 1], raw_pred[i], raw_pred[i + 1], stamp)
	return funnel


#============================================
def _interval_keep(
	end_frame: int,
	idx: int,
	interval_range: tuple,
	interval_indices: set,
	race_start_cutoff: int | None,
) -> bool:
	"""Return True when an interval passes every active filter.

	When `--interval-index` is set it overrides every other filter --
	the user chose those intervals explicitly. Otherwise the
	race-start cutoff and interval-index range both apply.
	"""
	if interval_indices is not None:
		return idx in interval_indices
	if race_start_cutoff is not None and end_frame <= race_start_cutoff:
		return False
	if interval_range != (None, None):
		i_min, i_max = interval_range
		if i_min is not None and idx < i_min:
			return False
		if i_max is not None and idx > i_max:
			return False
	return True


#============================================
def _format_one_interval_line(
	idx: int,
	start_frame: int,
	end_frame: int,
	fps: float,
	fwd_funnel: dict,
	bwd_funnel: dict,
	score_row: dict | None,
) -> str:
	"""Build the two-line per-interval console output.

	Reads confidence_tier and blob_coverage_fwd/bwd from the scores row
	(may be None for intervals missing from the scoring file).
	"""
	duration_s = (end_frame - start_frame) / fps
	if score_row is not None:
		tier = score_row.get("confidence_tier", "?")
		cov_fwd = score_row.get("blob_coverage_fwd")
		cov_bwd = score_row.get("blob_coverage_bwd")
		fwd_str = f"{cov_fwd * 100:.0f}%" if cov_fwd is not None else "n/a"
		bwd_str = f"{cov_bwd * 100:.0f}%" if cov_bwd is not None else "n/a"
	else:
		tier = "?"
		fwd_str = "?"
		bwd_str = "?"
	header = (
		f"interval [{idx:3d}] {start_frame:5d}-{end_frame:5d} "
		f"({duration_s:.1f}s)  blob_accept={fwd_str}/{bwd_str}  "
		f"tier={tier}"
	)
	def _fmt_pass(label: str, funnel: dict) -> str:
		counter_line = (
			f"  {label}: eligible={funnel['eligible']} "
			f"residual={funnel['residual_ok']} "
			f"raw={funnel['raw_blob']} "
			f"corridor={funnel['corridor']} "
			f"proximity={funnel['proximity_pass']} "
			f"direction={funnel['direction_pass']} "
			f"path={funnel['path_pass']} "
			f"accepted={funnel['accepted']}"
		)
		dist = _summarize_samples(funnel["blob_dist_h_samples"])
		perp = _summarize_samples(funnel["path_perp_h_samples"])
		# gate-margin medians expressed in torso heights so values
		# compare across zoom. Proximity fails when dist > 0.60 (= the
		# imported BLOB_SNAP_ALPHA). path fails when perp > slack line
		# (roughly 0.75 * motion_vec magnitude).
		if dist["median"] is None:
			margin_line = "       blob_dist_h: n/a (no corridor survivors)"
		else:
			margin_line = (
				f"       blob_dist_h median={dist['median']:.2f} "
				f"p90={dist['p90']:.2f} max={dist['max']:.2f}"
			)
			if perp["median"] is not None:
				margin_line += (
					f"  path_perp_h median={perp['median']:.2f} "
					f"p90={perp['p90']:.2f} max={perp['max']:.2f}"
				)
		return f"{counter_line}\n{margin_line}"

	return f"{header}\n{_fmt_pass('FWD', fwd_funnel)}\n{_fmt_pass('BWD', bwd_funnel)}"


#============================================
def _load_scores_by_interval(diag_path: str) -> dict:
	"""Load interval_scores.json and key interval_score dicts by
	(start_frame, end_frame). Empty dict if the file is missing.
	"""
	if not os.path.isfile(diag_path):
		return {}
	data = state_io.load_diagnostics(diag_path)
	by_key = {}
	for iv in data.get("intervals", []):
		key = (int(iv["start_frame"]), int(iv["end_frame"]))
		by_key[key] = iv.get("interval_score", {})
	return by_key


#============================================
def _load_race_start_frame(diag_path: str) -> int | None:
	"""Read race_start_frame from interval_scores.json, or None.

	The solver writes race_phase metadata into interval_scores.json when
	race_phases.detect_race_start produces a valid detection. Pre-race
	intervals anchor on stationary seeds (contract C2), so their tangent
	is near zero and gate evaluation is uninformative. The tool's
	default is to skip those intervals.
	"""
	if not os.path.isfile(diag_path):
		return None
	data = state_io.load_diagnostics(diag_path)
	race_phase = data.get("race_phase") or {}
	rsf = race_phase.get("race_start_frame")
	if rsf is None:
		return None
	return int(rsf)


#============================================
CSV_COLUMNS = (
	"interval_index", "start_frame", "end_frame", "duration_s",
	"scene_displacement_px", "pass",
	"confidence_tier", "blob_coverage_existing",
	"eligible", "residual_ok", "raw_blob", "corridor",
	"proximity_pass", "direction_pass", "path_pass", "accepted",
	"accepted_fraction_replay",
	"median_blob_dist_h", "p90_blob_dist_h", "max_blob_dist_h",
	"median_path_perp_h", "p90_path_perp_h", "max_path_perp_h",
	"median_real_to_blended_h", "p90_real_to_blended_h",
)


#============================================
def _accepted_fraction_replay(funnel: dict) -> float | None:
	"""Accepted / corridor. None when no corridor survivors."""
	if funnel["corridor"] <= 0:
		return None
	return funnel["accepted"] / funnel["corridor"]


#============================================
def _summarize_samples(samples: list) -> dict:
	"""Return median/p90/max of a list of floats, or None values.

	Empty input returns a dict with all None values so downstream CSV
	writing can emit empty cells rather than sentinel zeros.
	"""
	if not samples:
		return {"median": None, "p90": None, "max": None}
	samples_sorted = sorted(samples)
	n = len(samples_sorted)
	median = samples_sorted[n // 2]
	# p90: floor, to avoid sampling past the end on short intervals
	p90_idx = max(0, min(n - 1, int(round(0.9 * (n - 1)))))
	p90 = samples_sorted[p90_idx]
	return {"median": median, "p90": p90, "max": samples_sorted[-1]}


#============================================
def _csv_row(
	idx: int,
	start_frame: int,
	end_frame: int,
	fps: float,
	pass_name: str,
	funnel: dict,
	score_row: dict | None,
	scene_displacement_px: float,
) -> list:
	"""Build one CSV row for a single (interval, pass)."""
	duration_s = (end_frame - start_frame) / fps
	tier = (score_row or {}).get("confidence_tier", "")
	if pass_name == "FWD":
		cov_key = "blob_coverage_fwd"
	else:
		cov_key = "blob_coverage_bwd"
	cov_existing = (score_row or {}).get(cov_key)
	accepted_frac = _accepted_fraction_replay(funnel)

	def _cell(val):
		# consistent rendering: None -> "" so spreadsheet filters can
		# distinguish "no data" from "zero"
		if val is None:
			return ""
		if isinstance(val, bool):
			return "1" if val else "0"
		if isinstance(val, float):
			return f"{val:.4f}"
		return val

	dist_summary = _summarize_samples(funnel["blob_dist_h_samples"])
	perp_summary = _summarize_samples(funnel["path_perp_h_samples"])
	rf_summary = _summarize_samples(
		funnel.get("real_to_blended_h_samples", [])
	)
	row = [
		idx, start_frame, end_frame, f"{duration_s:.4f}",
		f"{scene_displacement_px:.3f}", pass_name,
		tier, _cell(cov_existing),
		funnel["eligible"], funnel["residual_ok"], funnel["raw_blob"],
		funnel["corridor"], funnel["proximity_pass"],
		funnel["direction_pass"], funnel["path_pass"], funnel["accepted"],
		_cell(accepted_frac),
		_cell(dist_summary["median"]), _cell(dist_summary["p90"]),
		_cell(dist_summary["max"]),
		_cell(perp_summary["median"]), _cell(perp_summary["p90"]),
		_cell(perp_summary["max"]),
		_cell(rf_summary["median"]), _cell(rf_summary["p90"]),
	]
	return row


#============================================
def _sanitize_funnel_for_json(funnel: dict) -> dict:
	"""Replace raw sample lists with median/p90/max summaries.

	The per-frame float lists bloat the sidecar quickly on dense runs;
	keep the information by writing summaries instead. Counter fields
	pass through unchanged.
	"""
	out = {}
	for key, value in funnel.items():
		if key == "blob_dist_h_samples":
			out["blob_dist_h"] = _summarize_samples(value)
		elif key == "path_perp_h_samples":
			out["path_perp_h"] = _summarize_samples(value)
		elif key == "real_to_blended_h_samples":
			out["real_to_blended_h"] = _summarize_samples(value)
		else:
			out[key] = value
	return out


#============================================
def _write_json_sidecar(
	path: str,
	video_basename: str,
	per_interval: list,
) -> None:
	"""Write the JSON sidecar with self-describing gate constants.

	`per_interval` is a list of dicts built in main(). Raw per-frame
	sample lists are collapsed to summaries to keep the file small.
	"""
	sanitized = []
	for item in per_interval:
		sanitized.append({
			**{k: v for k, v in item.items() if k not in ("fwd", "bwd")},
			"fwd": _sanitize_funnel_for_json(item["fwd"]),
			"bwd": _sanitize_funnel_for_json(item["bwd"]),
		})
	payload = {
		"header": "blob_funnel_v1",
		"video_basename": video_basename,
		"tool": "tools/check_interval_blob_funnel.py",
		"gate_constants": {
			"BLOB_SNAP_ALPHA": velocity_model.BLOB_SNAP_ALPHA,
			"BLOB_SNAP_VELOCITY_FLOOR": velocity_model.BLOB_SNAP_VELOCITY_FLOOR,
			"BLOB_SNAP_PATH_SLACK": velocity_model.BLOB_SNAP_PATH_SLACK,
			"BLOB_SNAP_PATH_PERP_FRACTION": (
				velocity_model.BLOB_SNAP_PATH_PERP_FRACTION
			),
			"DEFAULT_THRESHOLD": residual_motion.DEFAULT_THRESHOLD,
			"DEFAULT_HALF_WINDOW": residual_motion.DEFAULT_HALF_WINDOW,
			"ROI_MULTIPLIER": residual_motion.ROI_MULTIPLIER,
		},
		"intervals": sanitized,
	}
	with open(path, "w") as fh:
		json.dump(payload, fh, indent=2)


#============================================
def _fmt_bool_cell(value: object) -> str:
	"""Render a gate-pass bool: True -> 'OK', False -> 'FAIL', None -> '-'."""
	if value is None:
		return "-"
	if value:
		return "OK"
	return "FAIL"


#============================================
def _fmt_float_cell(value: object, width: int = 5, decimals: int = 2) -> str:
	"""Render a float cell or '-' when missing. Fixed width for alignment."""
	if value is None:
		return "-".rjust(width)
	return f"{float(value):{width}.{decimals}f}"


#============================================
def _print_gate_trace(
	per_interval: list,
	gate_trace_rows: dict,
) -> None:
	"""Print per-frame gate trace for each processed interval.

	Emits one table per (interval, pass) with columns: frame, blob?,
	dist/h, proximity, direction, path, |v_pred|, first_failed_gate.
	Intended for manual inspection of a small number of intervals the
	user has visually verified -- not for bulk analysis. Follow with a
	per-interval rollup summarizing where the frames fall among the
	absent / rejected-by-gate / accepted buckets so the trace is
	self-contained even when the surrounding funnel summary is also
	printed.

	Args:
		per_interval: main loop's per_interval_results list.
		gate_trace_rows: collected per-frame stamps keyed by interval
			index and pass label. Empty entries are tolerated (they
			mean the interval had only endpoint/stationary frames).
	"""
	if not gate_trace_rows:
		return
	print("")
	print("  gate trace (per-frame, --gate-trace):")
	# deterministic order: iterate per_interval so the trace follows the
	# same interval ordering as the funnel summary above.
	header = (
		"    frame  blob?  dist/h  prox  dir   path  |v_pred|  first_fail"
	)
	for item in per_interval:
		idx = item["interval_index"]
		if idx not in gate_trace_rows:
			continue
		start_frame = item["start_frame"]
		end_frame = item["end_frame"]
		pass_rows = gate_trace_rows[idx]
		print("")
		print(
			f"  interval [{idx:3d}] {start_frame:5d}-{end_frame:5d}"
		)
		for pass_label in ("FWD", "BWD"):
			rows = pass_rows.get(pass_label, [])
			print(f"    pass={pass_label}  eligible={len(rows)}")
			if not rows:
				print("      (no eligible frames)")
				continue
			print(header)
			# per-frame table. Width choices keep the row under ~80 cols
			# so ssh/tmux windows do not wrap. first_fail is last and may
			# overflow -- that is OK, it's the key column and a long name
			# like "no_corridor" is better unwrapped at line end than
			# truncated mid-word.
			for row in rows:
				fi = int(row["frame_index"])
				blob_flag = "Y" if row.get("blob_present") else "N"
				dist_h = _fmt_float_cell(row.get("blob_dist_h"), 6, 2)
				prox = _fmt_bool_cell(row.get("proximity_ok"))
				direc = _fmt_bool_cell(row.get("direction_ok"))
				path = _fmt_bool_cell(row.get("path_ok"))
				v_mag = _fmt_float_cell(row.get("v_pred_mag"), 6, 2)
				first_fail = row.get("first_failed_gate", "-")
				print(
					f"    {fi:5d}   {blob_flag}   "
					f"{dist_h}  {prox:4s}  {direc:4s}  {path:4s}  "
					f"{v_mag}    {first_fail}"
				)
			# per-pass rollup: how the frames partitioned. Numbers should
			# add up to len(rows); discrepancy is a bug in the stamp
			# writer, not the rollup.
			n_absent = sum(1 for r in rows if not r.get("blob_present"))
			n_accepted = sum(1 for r in rows if r.get("accepted"))
			n_rejected = sum(
				1 for r in rows
				if r.get("blob_present") and not r.get("accepted")
			)
			n_prox = sum(
				1 for r in rows
				if r.get("first_failed_gate") == "proximity"
			)
			n_dir = sum(
				1 for r in rows
				if r.get("first_failed_gate") == "direction"
			)
			n_path = sum(
				1 for r in rows
				if r.get("first_failed_gate") == "path"
			)
			print(
				f"      rollup: absent={n_absent} "
				f"rejected={n_rejected} "
				f"(prox={n_prox} dir={n_dir} path={n_path}) "
				f"accepted={n_accepted}"
			)
			# 5-bucket semantic summary. Reframes raw accept/reject into
			# what the blob did (or should have done) to the Hermite path.
			# AGREE:  blob on prediction (dist/h < 0.3), no correction needed
			# REFINE: accepted correction (0.3 <= dist/h < 1.0), Hermite moved
			# MISSED: gate-rejected blob in the refine band; lost improvement
			# FAR:    dist/h >= 1.0; correctly (or plausibly) too distant
			# ABSENT: no blob this frame (corridor empty or no residual)
			n_agree = 0
			n_refine = 0
			n_missed = 0
			n_far = 0
			for r in rows:
				if not r.get("blob_present"):
					continue
				dh = r.get("blob_dist_h")
				if dh is None:
					continue
				if dh >= 1.0:
					n_far += 1
				elif dh < 0.3:
					n_agree += 1
				elif r.get("accepted"):
					n_refine += 1
				else:
					n_missed += 1
			print(
				f"      buckets: AGREE={n_agree} REFINE={n_refine} "
				f"MISSED={n_missed} FAR={n_far} ABSENT={n_absent}"
			)
	_print_seed_distance_summary(per_interval, gate_trace_rows)


#============================================
def _classify_bucket(row: dict) -> str:
	"""Map a per-frame stamp to one of AGREE/REFINE/MISSED/FAR/ABSENT.

	Keeps the classification in one place so the per-interval rollup and
	the seed-distance summary cannot drift.
	"""
	if not row.get("blob_present"):
		return "ABSENT"
	dh = row.get("blob_dist_h")
	if dh is None:
		return "ABSENT"
	if dh >= 1.0:
		return "FAR"
	if dh < 0.3:
		return "AGREE"
	if row.get("accepted"):
		return "REFINE"
	return "MISSED"


#============================================
def _seed_distance_bin(d: int) -> str:
	"""Label a frame by distance-to-nearest-seed endpoint.

	Bins: 1, 2, 3-5, 6-10, 11-20, 21+. Bins widen with distance because
	the quantity of interest is the regime change -- "at what N does the
	blob signal collapse" -- not fine-grained distance-by-distance.
	"""
	if d <= 1:
		return "1"
	if d == 2:
		return "2"
	if d <= 5:
		return "3-5"
	if d <= 10:
		return "6-10"
	if d <= 20:
		return "11-20"
	return "21+"


#============================================
def _print_seed_distance_summary(
	per_interval: list,
	gate_trace_rows: dict,
) -> None:
	"""Aggregate traced frames by distance to nearest seed endpoint.

	Answers: "blobs work on seeds; at what N frames out from a seed does
	AGREE/REFINE collapse into MISSED/FAR/ABSENT?" Each traced frame
	contributes its `min(frame - start_seed, end_seed - frame)` to a bin
	and its 5-bucket classification to the column totals.
	"""
	if not gate_trace_rows:
		return
	bin_order = ["1", "2", "3-5", "6-10", "11-20", "21+"]
	tally = {b: {"AGREE": 0, "REFINE": 0, "MISSED": 0, "FAR": 0, "ABSENT": 0}
		for b in bin_order}
	total_frames = 0
	for item in per_interval:
		idx = item["interval_index"]
		if idx not in gate_trace_rows:
			continue
		start_frame = item["start_frame"]
		end_frame = item["end_frame"]
		pass_rows = gate_trace_rows[idx]
		for pass_label in ("FWD", "BWD"):
			for row in pass_rows.get(pass_label, []):
				fi = int(row["frame_index"])
				d = min(fi - start_frame, end_frame - fi)
				if d < 0:
					continue
				bucket = _classify_bucket(row)
				tally[_seed_distance_bin(d)][bucket] += 1
				total_frames += 1
	if total_frames == 0:
		return
	print("")
	print("  by distance to nearest seed endpoint (FWD+BWD combined):")
	print(
		"    dist    n   AGREE REFINE MISSED  FAR  ABSENT  "
		"good%  (good = AGREE+REFINE)"
	)
	for b in bin_order:
		row = tally[b]
		n = sum(row.values())
		if n == 0:
			print(f"    {b:5s}   0   (empty)")
			continue
		good = row["AGREE"] + row["REFINE"]
		pct = 100.0 * good / n
		print(
			f"    {b:5s}  {n:3d}   {row['AGREE']:5d} {row['REFINE']:6d} "
			f"{row['MISSED']:6d} {row['FAR']:4d} {row['ABSENT']:6d}  "
			f"{pct:4.0f}%"
		)


#============================================
def _print_summary(per_interval: list, fps: float) -> None:
	"""Aggregate counters and duration-bucketed accept rates."""
	n = len(per_interval)
	if n == 0:
		print("\n  no intervals processed")
		return
	# per-stage totals: only sum the int counter fields, not the sample
	# lists (those get aggregated by the duration buckets below).
	counter_keys = (
		"eligible", "residual_ok", "raw_blob", "corridor",
		"proximity_pass", "direction_pass", "path_pass", "accepted",
	)
	totals_fwd = {k: 0 for k in counter_keys}
	totals_bwd = {k: 0 for k in counter_keys}
	for item in per_interval:
		for key in counter_keys:
			totals_fwd[key] += item["fwd"][key]
			totals_bwd[key] += item["bwd"][key]
	print("")
	print(f"  summary over {n} intervals:")
	for pass_name, totals in (("FWD", totals_fwd), ("BWD", totals_bwd)):
		print(
			f"    {pass_name}: "
			f"eligible={totals['eligible']} "
			f"residual={totals['residual_ok']} "
			f"raw={totals['raw_blob']} "
			f"corridor={totals['corridor']} "
			f"proximity={totals['proximity_pass']} "
			f"direction={totals['direction_pass']} "
			f"path={totals['path_pass']} "
			f"accepted={totals['accepted']}"
		)

	# duration buckets: answers "does the failure concentrate on short
	# intervals?" which drives the short-interval policy question. A
	# bucket's accept-rate is (sum accepted)/(sum corridor) across all
	# intervals whose duration falls in that bucket. Corridor rather
	# than eligible as the denominator because we already know every
	# eligible frame has a corridor survivor on this data.
	buckets = (
		(0.0, 0.2, "0.0-0.2s"),
		(0.2, 0.5, "0.2-0.5s"),
		(0.5, 1.0, "0.5-1.0s"),
		(1.0, float("inf"), "1.0s+"),
	)
	print("")
	print("  by interval duration (FWD+BWD combined):")
	for lo, hi, label in buckets:
		bucket_items = [
			item for item in per_interval
			if lo <= (item["end_frame"] - item["start_frame"]) / fps < hi
		]
		if not bucket_items:
			print(f"    {label:<9} (n=  0): (empty)")
			continue
		corridor_sum = sum(
			item[p]["corridor"] for item in bucket_items for p in ("fwd", "bwd")
		)
		prox_sum = sum(
			item[p]["proximity_pass"] for item in bucket_items
			for p in ("fwd", "bwd")
		)
		path_sum = sum(
			item[p]["path_pass"] for item in bucket_items
			for p in ("fwd", "bwd")
		)
		accepted_sum = sum(
			item[p]["accepted"] for item in bucket_items
			for p in ("fwd", "bwd")
		)
		if corridor_sum == 0:
			rates = "(no corridor survivors)"
		else:
			rates = (
				f"proximity={prox_sum / corridor_sum:.0%} "
				f"path={path_sum / corridor_sum:.0%} "
				f"accepted={accepted_sum / corridor_sum:.0%}"
			)
		print(f"    {label:<9} (n={len(bucket_items):3d}): {rates}")

	# eligible-frame-count buckets: sharper than duration because it
	# does not conflate fps and seed density. Denominator is the sum of
	# eligible frames across intervals in the bucket (not the average
	# of per-interval percentages), so a 40-frame interval is weighted
	# more than a 3-frame one.
	frame_buckets = (
		(0, 5, "<   5 frames"),
		(5, 15, "5-15 frames "),
		(15, 40, "15-40 frames"),
		(40, 10 ** 9, "40+  frames "),
	)
	print("")
	print("  by eligible-frame count (FWD+BWD summed):")
	for lo, hi, label in frame_buckets:
		bucket_pairs = []
		for item in per_interval:
			for pass_key in ("fwd", "bwd"):
				elig = item[pass_key]["eligible"]
				if lo <= elig < hi:
					bucket_pairs.append(item[pass_key])
		if not bucket_pairs:
			print(f"    {label} (n=  0): (empty)")
			continue
		elig_sum = sum(p["eligible"] for p in bucket_pairs)
		prox_sum = sum(p["proximity_pass"] for p in bucket_pairs)
		path_sum = sum(p["path_pass"] for p in bucket_pairs)
		accepted_sum = sum(p["accepted"] for p in bucket_pairs)
		if elig_sum == 0:
			rates = "(no eligible frames)"
		else:
			rates = (
				f"proximity={prox_sum / elig_sum:.0%} "
				f"path={path_sum / elig_sum:.0%} "
				f"accepted={accepted_sum / elig_sum:.0%}"
			)
		print(f"    {label} (n={len(bucket_pairs):3d}): {rates}")


#============================================
def main() -> None:
	args = parse_args()
	interval_range = parse_interval_range(args.interval_range)
	interval_indices = parse_interval_indices(args.interval_indices)

	# resolve canonical paths
	seeds_path = args.seeds_file or tr_paths.default_seeds_path(args.input_file)
	motion_path = tr_paths.default_camera_motion_path(args.input_file)
	diag_path = tr_paths.default_diagnostics_path(args.input_file)

	# load seeds + motion cache (motion is required; no identity fallback)
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
	reader = video_io.VideoReader(args.input_file)
	info = reader.get_info()
	fps = float(info["fps"])
	print(f"loaded {len(all_seeds)} seeds from {seeds_path}")
	print(f"loaded motion track ({motion_track.dx.shape[0]} frames)")
	print(
		f"video: {info['width']}x{info['height']} "
		f"@ {fps:.3f} fps, {info['frame_count']} frames"
	)

	# reuse the solver's usable-seed filter so interval enumeration
	# matches solve exactly (adjacent-pair ordering after dedup)
	usable_seeds = interval_fingerprint.filter_usable_seeds_sorted(
		all_seeds, verbose=True,
	)
	if len(usable_seeds) < 2:
		raise RuntimeError(
			f"need at least 2 usable seeds; have {len(usable_seeds)}"
		)
	print(f"usable seeds: {len(usable_seeds)} "
		f"-> {len(usable_seeds) - 1} intervals")

	# build all_seeds_scene identical to solve_all_intervals
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

	# existing interval_scores.json keyed by (start, end) for the header
	# fields (confidence_tier, blob_coverage_fwd/bwd). Absent file -> {}.
	scores_by_key = _load_scores_by_interval(diag_path)

	# --diagnostic-blended-reference: load geometry_cache once and build a
	# per-interval lookup from (start_frame, end_frame) -> dict mapping
	# absolute frame_index -> (cx, cy). Off by default.
	blended_by_interval_key = {}
	if args.diagnostic_blended_reference:
		geom_path = tr_paths.default_intervals_path(args.input_file)
		if not os.path.isfile(geom_path):
			raise RuntimeError(
				f"geometry cache missing: {geom_path}\n"
				"run 'track_runner.py -i VIDEO solve' first; "
				"--diagnostic-blended-reference needs a solved blended path."
			)
		geom = state_io.load_geometry_cache(geom_path)
		solved = geom.get("solved_intervals", {}) or {}
		for _fp, iv in solved.items():
			start = int(iv["start_frame"])
			end = int(iv["end_frame"])
			blended_path = iv.get("blended_path") or []
			per_frame = {}
			for offset, entry in enumerate(blended_path):
				fi = start + offset
				per_frame[fi] = (float(entry["cx"]), float(entry["cy"]))
			blended_by_interval_key[(start, end)] = per_frame
		print(
			f"  diagnostic-blended-reference: loaded blended paths for "
			f"{len(blended_by_interval_key)} intervals"
		)

	# race-start cutoff: contract C2 says pre-race frames anchor to a
	# stationary reference with averaged seed geometry, not per-frame
	# velocity. The tool skips intervals ending at or before
	# race_start_frame because gate evaluation on a near-zero tangent
	# is uninformative. When the user wants to audit a pre-race
	# interval specifically, --interval-index overrides this default.
	race_start_cutoff = _load_race_start_frame(diag_path)
	if race_start_cutoff is None:
		print(
			"  race_start_frame not recorded in interval_scores.json; "
			"skipping no intervals"
		)
	else:
		print(
			f"  race_start_frame = {race_start_cutoff}; skipping "
			"pre-race intervals (contract C2). Use --interval-index to "
			"audit a specific pre-race interval anyway."
		)

	# open CSV once so rows stream per interval; partial CSV survives
	# interrupts and `tail -f` gives live progress
	csv_fh = None
	csv_writer = None
	if args.csv_path is not None:
		csv_fh = open(args.csv_path, "w", newline="")
		csv_writer = csv.writer(csv_fh)
		csv_writer.writerow(list(CSV_COLUMNS))
		csv_fh.flush()

	frames_dir = args.frames_dir
	if frames_dir is not None:
		os.makedirs(frames_dir, exist_ok=True)
		print(f"  saving annotated frame PNGs to {frames_dir}")

	# first pass: enumerate every interval and apply filters (range /
	# index-list / race-start cutoff). Collect surviving (idx, left,
	# right, start, end) tuples so --shuffle and --limit can be applied
	# before the expensive residual-compute loop.
	total_pairs = len(usable_seeds) - 1
	kept = []
	for idx in range(total_pairs):
		left = usable_seeds[idx]
		right = usable_seeds[idx + 1]
		start_frame = int(left["frame_index"])
		end_frame = int(right["frame_index"])
		if not _interval_keep(
			end_frame, idx,
			interval_range, interval_indices,
			race_start_cutoff,
		):
			continue
		kept.append((idx, left, right, start_frame, end_frame))

	if args.shuffle:
		# no fixed seed: different sample every run. Users who want
		# reproducibility pin intervals with --interval-index.
		random.shuffle(kept)
		print(f"  shuffled {len(kept)} kept intervals")
	if args.limit is not None and len(kept) > args.limit:
		kept = kept[:args.limit]
		# re-sort by interval_index so the output is time-ordered even
		# after shuffle+limit; this makes the two-line console output
		# easier to compare across runs
		kept.sort(key=lambda item: item[0])
		print(f"  limited to {len(kept)} intervals")

	# per-interval per-pass lists of per-frame stamps, populated only when
	# --gate-trace is set. Keyed by interval_index -> {"FWD": [...], "BWD": [...]}.
	# Empty dict when the flag is off; _print_gate_trace handles both cases.
	gate_trace_rows: dict = {}
	per_interval_results = []
	try:
		for idx, left, right, start_frame, end_frame in kept:

			# fit curves and get raw_pred arrays (same math the solver uses)
			curves = velocity_model.fit_interval_curves(
				left, right, all_seeds_scene, transform,
			)
			raw_pred_fwd = velocity_model._compute_raw_pred_forward(
				curves, transform,
			)
			raw_pred_bwd = velocity_model._compute_raw_pred_backward(
				curves, transform,
			)

			# per-interval residual cache, scoped and dropped per interval
			# so no cross-interval state survives (contract C3)
			residual_cache = {}

			def _make_saver(pass_label: str):
				# closure captures idx + pass_label. Handles two optional
				# per-frame side-effects: PNG rendering (frames_dir) and
				# gate-trace row collection (--gate-trace). Returns None
				# only when neither is active so replay_pass can skip the
				# per-frame hook entirely.
				want_save = frames_dir is not None
				want_trace = args.gate_trace
				if not want_save and not want_trace:
					return None
				if want_trace:
					trace_iv = gate_trace_rows.setdefault(idx, {})
					trace_iv.setdefault(pass_label, [])

				def _hook(i, raw_prev, raw_curr, raw_next, stamp):
					frame_index = int(raw_curr[0])
					if want_trace:
						row = dict(stamp)
						row["frame_index"] = frame_index
						gate_trace_rows[idx][pass_label].append(row)
					if want_save:
						_save_frame_png(
							frames_dir, idx, frame_index, pass_label,
							raw_curr, raw_prev, raw_next,
							reader, transform, stamp,
						)

				return _hook

			# scene-space endpoint displacement: one geometry number per
			# interval, lets post-hoc analysis scatter (displacement,
			# accept rate) without re-running the tool.
			l_scene = transform.pixel_box_to_scene(
				int(left["frame_index"]), float(left["cx"]),
				float(left["cy"]), float(left["w"]), float(left["h"]),
			)
			r_scene = transform.pixel_box_to_scene(
				int(right["frame_index"]), float(right["cx"]),
				float(right["cy"]), float(right["w"]), float(right["h"]),
			)
			scene_displacement_px = math.sqrt(
				(r_scene[0] - l_scene[0]) ** 2
				+ (r_scene[1] - l_scene[1]) ** 2
			)

			blended_lookup = blended_by_interval_key.get(
				(start_frame, end_frame)
			) if args.diagnostic_blended_reference else None

			fwd_funnel = replay_pass(
				raw_pred_fwd, reader, transform, residual_cache,
				on_frame=_make_saver("FWD"),
				blended_lookup=blended_lookup,
			)
			bwd_funnel = replay_pass(
				raw_pred_bwd, reader, transform, residual_cache,
				on_frame=_make_saver("BWD"),
				blended_lookup=blended_lookup,
			)
			residual_cache.clear()

			score_row = scores_by_key.get((start_frame, end_frame))
			per_interval_results.append({
				"interval_index": idx,
				"start_frame": start_frame,
				"end_frame": end_frame,
				"scene_displacement_px": scene_displacement_px,
				"confidence_tier": (score_row or {}).get(
					"confidence_tier", ""
				),
				"blob_coverage_existing_fwd": (score_row or {}).get(
					"blob_coverage_fwd"
				),
				"blob_coverage_existing_bwd": (score_row or {}).get(
					"blob_coverage_bwd"
				),
				"fwd": fwd_funnel,
				"bwd": bwd_funnel,
			})
			if args.print_per_interval:
				print(
					_format_one_interval_line(
						idx, start_frame, end_frame, fps,
						fwd_funnel, bwd_funnel, score_row,
					),
					flush=True,
				)
			if csv_writer is not None:
				csv_writer.writerow(_csv_row(
					idx, start_frame, end_frame, fps, "FWD",
					fwd_funnel, score_row, scene_displacement_px,
				))
				csv_writer.writerow(_csv_row(
					idx, start_frame, end_frame, fps, "BWD",
					bwd_funnel, score_row, scene_displacement_px,
				))
				csv_fh.flush()
	finally:
		if csv_fh is not None:
			csv_fh.close()

	_print_summary(per_interval_results, fps)

	if args.gate_trace:
		_print_gate_trace(per_interval_results, gate_trace_rows)

	if args.json_path is not None:
		_write_json_sidecar(
			args.json_path,
			os.path.basename(args.input_file),
			per_interval_results,
		)
		print(f"  JSON written to {args.json_path}")
	if args.csv_path is not None:
		print(f"  CSV  written to {args.csv_path}")


if __name__ == "__main__":
	main()
