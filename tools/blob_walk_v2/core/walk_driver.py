#!/usr/bin/env python3
"""Walk driver: orchestrates FWD/BWD walks on seed-to-seed intervals for a corpus.

Per-video corpus runner: enumerates intervals, runs walks, renders tiles,
builds HTML. Exception handling is per-interval: walker and renderer raise
loudly; driver catches, records failure, and continues to the next interval.

Sequential single loop (no multiprocessing). Winner mode is shared across
all intervals in one invocation.

Output layout:
  {output_dir}/
    {video_basename}/
      seed_{F_L}_{F_R}/
        fwd_verdicts.csv
        bwd_verdicts.csv
        interval_summary.csv
        fwd/frame_{N}.png
        bwd/frame_{N}.png
    walk.html
"""

# Standard Library
import os
import sys
import csv
import json
import logging
import argparse
import pathlib
import dataclasses

# Standalone-run bootstrap: this module lives at tools/blob_walk_v2/core/, but
# walk_paths.py lives one level up at the package root. When walk_driver is run
# directly (python3 .../core/walk_driver.py), sys.path[0] is core/, so the bare
# import walk_paths below would fail. Put the package root on sys.path first so
# walk_paths resolves; walk_paths.setup() then wires up the rest (track_runner,
# tests, repo root, core/, render/). Library callers that import walk_driver
# have already run setup(), so this insert is a harmless no-op for them.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
	sys.path.insert(0, _PACKAGE_ROOT)

# shared sys.path bootstrap (track_runner, tests, repo root, blob_walk_v2 dirs)
import walk_paths
walk_paths.setup()

# local repo modules
import walk_io
import walk_util
import walk_walker
import walk_debug_log
import walk_render
import walk_html
import blob_trace
import interval_fingerprint
import interval_solver
import state_io
import common_tools.coord_space
import residual_motion


#============================================
# Logging setup
#============================================
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


#============================================
# Interval summary dataclass
#============================================
@dataclasses.dataclass
class IntervalSummary:
	"""Summary of one seed-to-seed interval walk."""
	left_frame: int
	right_frame: int
	walker_run: bool
	skip_reason: str
	fwd_interval_length: int
	bwd_interval_length: int
	fwd_accepted_count: int
	bwd_accepted_count: int
	fwd_last_accepted_offset: int
	bwd_last_accepted_offset: int
	fwd_stop_reason: str
	bwd_stop_reason: str
	gap_frames: int
	render_error: bool
	fwd_mode_disagreement_count: int
	bwd_mode_disagreement_count: int


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Walk driver: orchestrates FWD/BWD walks on seed-to-seed intervals. "
			"Runs on a corpus of videos, enumerates intervals, walks and renders tiles, "
			"builds HTML."
		),
	)
	parser.add_argument(
		'-v', '--videos', dest='video_basenames', nargs='+', required=True,
		help=(
			"Video basename(s) to process (space-separated or repeated flag). "
			"Example: -v video1.mkv video2.mkv"
		),
	)
	parser.add_argument(
		'-i', '--max-intervals', dest='max_intervals', type=int, default=4,
		help=(
			"Cap intervals per video (evenly spread). Default: 4. "
			"A full walk of every interval is intentionally never the default "
			"because it is catastrophically slow on large corpora. "
			"Pass 0 (or any value <= 0) to opt in to ALL post_start intervals."
		),
	)
	parser.add_argument(
		'-o', '--output-dir', dest='output_dir', default='blob_walk_v2',
		help="Output root directory. Default: blob_walk_v2/",
	)
	parser.add_argument(
		'-r', '--resume', dest='resume', action='store_true',
		help=(
			"Skip intervals that already have CSV + at least one tile. "
			"Default: recompute all."
		),
	)
	parser.add_argument(
		'--no-render', dest='render_tiles', action='store_false',
		help="Skip PNG tile rendering (still write CSVs).",
	)
	parser.set_defaults(render_tiles=True)
	parser.add_argument(
		'--no-html', dest='build_html', action='store_false',
		help="Skip HTML build after corpus run.",
	)
	parser.set_defaults(build_html=True)
	parser.add_argument(
		'--heat-movie', dest='heat_movie', action='store_true',
		help=(
			"Encode a per-direction heat movie (.mkv) alongside each interval's "
			"render output.  Requires ffmpeg in PATH.  Encodes only when --walk "
			"is active (live direction_path).  Off by default."
		),
	)
	parser.add_argument(
		'--no-heat-movie', dest='heat_movie', action='store_false',
		help="Do not encode heat movies (default).",
	)
	parser.set_defaults(heat_movie=False)
	parser.add_argument(
		'-w', '--winner-mode', dest='winner_mode',
		choices=['production_winner', 'audit_winner'],
		default='production_winner',
		help=(
			"Winner resolution mode. Default: production_winner. "
			"Audit mode requires --audit-rule."
		),
	)
	parser.add_argument(
		'--audit-rule', dest='audit_rule',
		choices=['center_of_mass', 'strongest_blob', 'body_position'],
		default=None,
		help=(
			"Audit rule for audit_winner mode. "
			"One of: center_of_mass, strongest_blob, body_position. "
			"Only meaningful with -w audit_winner."
		),
	)
	args = parser.parse_args()

	# Validate audit_rule if audit_winner is set
	if args.winner_mode == 'audit_winner' and args.audit_rule is None:
		parser.error(
			"--audit-rule is required when --winner-mode audit_winner is set. "
			"Choose from: center_of_mass, strongest_blob, body_position"
		)

	# Fail early when --heat-movie is requested but ffmpeg is absent.
	# Import heat_movie_encode lazily here so the normal walk path never imports it.
	if args.heat_movie:
		import heat_movie_encode
		heat_movie_encode.check_ffmpeg_available()

	return args


#============================================
def check_resume_needed(
	interval_dir: pathlib.Path,
	resume: bool,
) -> bool:
	"""Check if interval should be resumed (skipped).

	Returns True if resume is set AND interval has CSV + at least one tile
	(or interval_summary.csv even if tiles are missing).
	"""
	if not resume:
		return False

	fwd_csv = interval_dir / 'fwd_verdicts.csv'
	bwd_csv = interval_dir / 'bwd_verdicts.csv'
	summary_csv = interval_dir / 'interval_summary.csv'
	fwd_tiles = list((interval_dir / 'fwd').glob('frame_*.png')) if (interval_dir / 'fwd').exists() else []
	bwd_tiles = list((interval_dir / 'bwd').glob('frame_*.png')) if (interval_dir / 'bwd').exists() else []

	# Resume if:
	# - interval_summary.csv exists (work was completed, even without tiles)
	# OR (CSV exists AND at least one tile exists)
	if summary_csv.exists():
		return True

	has_csv = fwd_csv.exists() or bwd_csv.exists()
	has_tile = len(fwd_tiles) > 0 or len(bwd_tiles) > 0

	return has_csv and has_tile


#============================================
def _sampled_offsets(interval_length: int) -> list:
	"""Power-of-2 offsets from 0 up to interval_length, plus final boundary."""
	offsets = [0]
	n = 1
	while n < interval_length:
		offsets.append(n)
		n *= 2
	if interval_length not in offsets:
		offsets.append(interval_length)
	return offsets


#============================================
def _normalize_video_basename(video_basename: str) -> str:
	"""Strip .mkv and .track_runner suffixes from a video basename.

	Mirrors walk_io._normalize_video_basename for path construction.
	"""
	name = video_basename
	if name.endswith('.mkv'):
		name = name[:-4]
	if name.endswith('.track_runner'):
		name = name[:-13]
	return name


#============================================
def _video_npz_path(video_basename: str) -> str:
	"""Return the torso_box_coords.npz path for this video.

	Mirrors the walk_io convention: tr_config lives under walk_paths.setup()
	(the repo root), not under the current working directory.

	Args:
		video_basename: Video base name (with or without .mkv).

	Returns:
		str: Absolute path to the torso_box_coords.npz for this video.
	"""
	# walk_paths.setup() returns the repo root; tr_config lives there.
	repo_root = walk_paths.setup()
	base_name = _normalize_video_basename(video_basename)
	# Match tr_paths._data_file_path pattern: tr_config/{stem}.track_runner.torso_box_coords.npz
	npz_filename = f"{base_name}.track_runner.torso_box_coords.npz"
	npz_path = os.path.join(repo_root, "tr_config", npz_filename)
	return npz_path


#============================================
def _project_path_to_source(direction_path: list, geometry) -> list:
	"""Project a processed-space direction path to source-frame space.

	For each frame dict in direction_path, converts cx,cy from processed to
	source using geometry.processed_to_source, and converts w,h using
	geometry.processed_to_source_delta.  Preserves frame_index and conf.
	Box math stays in float throughout; rounding is deferred to write_torso_box_coords.

	Args:
		direction_path: List of {frame_index, cx, cy, w, h, conf} in processed pixels.
		geometry: FrameGeometry with processed_to_source and processed_to_source_delta.

	Returns:
		New list of {frame_index, cx, cy, w, h, conf} in source full-frame pixels.
	"""
	projected = []
	for frame_dict in direction_path:
		cx_p = float(frame_dict["cx"])
		cy_p = float(frame_dict["cy"])
		w_p = float(frame_dict["w"])
		h_p = float(frame_dict["h"])
		# project center point
		cx_s, cy_s = geometry.processed_to_source(cx_p, cy_p)
		# project width and height as deltas (not point pairs)
		w_s, _ = geometry.processed_to_source_delta(w_p, 0.0)
		_, h_s = geometry.processed_to_source_delta(0.0, h_p)
		projected.append({
			"frame_index": frame_dict["frame_index"],
			"cx": cx_s,
			"cy": cy_s,
			"w": w_s,
			"h": h_s,
			"conf": float(frame_dict["conf"]),
		})
	return projected


#============================================
def _fill_seed_pred(row_dict: dict, seed: dict) -> None:
	"""In-place: ensure pred_cx/cy/torso_w_px/torso_h_px have values from seed.

	Used for bootstrap (step=0) rows and after_walk_terminated diagnostic rows
	where walker did not populate the prediction fields.
	"""
	if row_dict.get('pred_cx') in (None, ''):
		row_dict['pred_cx'] = str(seed['cx'])
	if row_dict.get('pred_cy') in (None, ''):
		row_dict['pred_cy'] = str(seed['cy'])
	if row_dict.get('torso_w_px') in (None, ''):
		row_dict['torso_w_px'] = str(seed['w'])
	if row_dict.get('torso_h_px') in (None, ''):
		row_dict['torso_h_px'] = str(seed['h'])


#============================================
def _render_direction_tiles(
	direction_label: str,
	csv_path: pathlib.Path,
	tile_dir: pathlib.Path,
	anchor_seed: dict,
	default_direction_sign: str,
	reader,
	scene_transform,
	fps: float,
	frame_trace_map: dict | None = None,
	direction_path: list | None = None,
	proc_seed_by_frame: dict | None = None,
	npz_path: str | None = None,
	interval_start_frame: int | None = None,
	interval_end_frame: int | None = None,
) -> tuple:
	"""Render PNG tiles for one walk direction (FWD or BWD) from a CSV file.

	Reads the CSV written by DebugLogWriter, reconstructs a DebugLogRow object
	for each renderable frame, and calls render_walk_tile. The per-frame
	BlobObserverTrace comes from the live walk (frame_trace_map, WS2-A) when
	available; this is what restores the winner/corridor/raw blob ellipses and
	the acceptance box. When a frame has no captured trace (render-only mode
	with no --walk run in this process, or a diagnostic/miss frame), a minimal
	empty stub trace is used so box/marker overlays still render and ellipses
	silently skip.

	Per-frame solved_box comes from direction_path (WS1-A processed-space box
	path): looked up by frame_index and passed as solved_box to render_walk_tile.
	Per-frame seed_box comes from proc_seed_by_frame (SeedsView.seeds keyed by
	frame_index): non-None only on seed frames (those whose frame_index appears
	in the dict and have cx/cy/w/h populated). Boxes are in PROCESSED pixels
	(coordinate contract: render_walk_tile subtracts roi_origin exactly once).

	Render-only mode (no --walk, direction_path=None): when npz_path,
	interval_start_frame, and interval_end_frame are provided, loads the
	source-frame torso box npz via state_io.load_torso_box_coords, finds the
	matching interval by start/end frame, and down-projects the blended_path
	to PROCESSED pixels using reader.geometry.source_to_processed (for cx,cy)
	and reader.geometry.source_to_processed_delta (for w,h). Box math is kept
	in float throughout (rounding is deferred to the draw boundary). The
	down-projected PROCESSED boxes are then used as solved_box per frame,
	exactly as the --walk path does. If the npz does not exist, or no matching
	interval is found, or any required parameter is absent, solved_box falls
	back to None (render-only with no prior walk is permitted).

	Coordinate contract: the npz holds source-frame pixels; this function
	applies exactly one source->processed conversion (via reader.geometry)
	before building direction_box_by_frame. render_walk_tile then subtracts
	roi_origin exactly once. Total conversions = 1.

	Args:
		direction_label: Human-readable label used in log messages, e.g. 'FWD' or 'BWD'.
		csv_path: Path to the verdicts CSV file for this direction.
		tile_dir: Output directory for PNG tiles.
		anchor_seed: Seed dict at the anchor end (left for FWD, right for BWD).
		default_direction_sign: Fallback value for direction field, '+' for FWD, '-' for BWD.
		reader: FrameReader instance.
		scene_transform: SceneTransform instance.
		fps: Frames per second.
		frame_trace_map: Optional frame_index -> lightened BlobObserverTrace map
			from the live walk (WS2-A). Trace geometry is PROCESSED/ROI-relative
			with roi_origin_xy in processed coords (handoff to WS2-B2). None or a
			missing key falls back to the empty stub (no ellipses).
		direction_path: Optional per-frame box path list from WS1-A (PROCESSED
			pixels), each entry {frame_index, cx, cy, w, h, conf}. Used to supply
			solved_box per frame. None in render-only mode (no --walk).
		proc_seed_by_frame: Optional dict mapping frame_index -> processed-pixel
			seed dict ({frame_index, cx, cy, w, h, ...}) from SeedsView.seeds.
			Used to supply seed_box on seed frames. None means no seed boxes drawn.
		npz_path: Optional path to the torso_box_coords.npz for this video.
			Used in render-only mode (direction_path=None) to load source-frame
			solved boxes and down-project to PROCESSED for solved_box rendering.
		interval_start_frame: Optional start frame of this interval (inclusive).
			Used with npz_path to find the matching interval entry in the npz.
		interval_end_frame: Optional end frame of this interval (inclusive).
			Used with npz_path to find the matching interval entry in the npz.

	Returns:
		Tuple (render_error: bool, manifests: list) where render_error is True if
		any render error occurred, and manifests is the list of render manifest
		dicts returned by render_walk_tile (one per successfully rendered frame).
		Manifests are in frame_index order. For WS2-C manifest assembly.
	"""
	if frame_trace_map is None:
		frame_trace_map = {}
	if proc_seed_by_frame is None:
		proc_seed_by_frame = {}

	# Build frame_index -> {cx,cy,w,h} lookup in PROCESSED pixels.
	# Used to supply solved_box per frame to render_walk_tile.
	#
	# --walk path: direction_path is provided directly in PROCESSED pixels (WS1-A).
	#   Build the lookup by iterating over direction_path entries.
	#
	# Render-only path (direction_path=None): load source-frame boxes from the npz
	#   and down-project to PROCESSED via reader.geometry (one conversion here;
	#   render_walk_tile subtracts roi_origin once = total 1 conversion).
	#   Falls back to empty dict (solved_box=None per frame) when npz is absent,
	#   no matching interval is found, or npz_path/interval frames are not provided.
	#   This fallback is the one acceptable optional path, not a bug-hiding default:
	#   render-only with no prior walk is a supported use case.
	direction_box_by_frame = {}
	if direction_path is not None:
		# --walk path: direction_path carries PROCESSED-pixel boxes with frame_index.
		for entry in direction_path:
			fi = entry["frame_index"]
			# Extract only the box fields; drop conf (not needed by render_walk_tile)
			# M1-G: also carry the walk-time-cached in-box heat scalars so the
			# renderer reads them instead of re-deriving the residual.
			direction_box_by_frame[fi] = {
				"cx": entry["cx"],
				"cy": entry["cy"],
				"w": entry["w"],
				"h": entry["h"],
				"in_box_hot_mean": entry["in_box_hot_mean"],
				"in_box_hot_count": entry["in_box_hot_count"],
			}
	elif (
		npz_path is not None
		and interval_start_frame is not None
		and interval_end_frame is not None
	):
		# Render-only path: load source-frame npz boxes and down-project to PROCESSED.
		# load_torso_box_coords returns an empty skeleton (no exception) when npz missing.
		npz_data = state_io.load_torso_box_coords(npz_path)
		# Find the interval matching start_frame and end_frame.
		# Iterate solved_intervals.values(); key is fingerprint (merge identity only).
		matching_interval = None
		for interval_entry in npz_data["solved_intervals"].values():
			if (
				interval_entry["start_frame"] == interval_start_frame
				and interval_entry["end_frame"] == interval_end_frame
			):
				matching_interval = interval_entry
				break
		if matching_interval is not None:
			blended_path = matching_interval["blended_path"]
			if blended_path is not None:
				geometry = reader.geometry
				# blended_path entries are positional: index i -> frame (start_frame + i).
				# Each entry has {cx, cy, w, h} in SOURCE full-frame pixels.
				# Down-project to PROCESSED: source_to_processed for center point,
				# source_to_processed_delta for width and height (scalar deltas, not pairs).
				# Box math stays in float throughout; rounding deferred to draw boundary.
				for i, source_box in enumerate(blended_path):
					frame_idx = interval_start_frame + i
					cx_s = float(source_box["cx"])
					cy_s = float(source_box["cy"])
					w_s = float(source_box["w"])
					h_s = float(source_box["h"])
					# Source -> processed: center point
					cx_p, cy_p = geometry.source_to_processed(cx_s, cy_s)
					# Source -> processed: width and height as independent deltas
					w_p, _ = geometry.source_to_processed_delta(w_s, 0.0)
					_, h_p = geometry.source_to_processed_delta(0.0, h_s)
					direction_box_by_frame[frame_idx] = {
						"cx": cx_p,
						"cy": cy_p,
						"w": w_p,
						"h": h_p,
					}
		else:
			logger.debug(
				f"_render_direction_tiles ({direction_label}): no matching interval "
				f"[{interval_start_frame},{interval_end_frame}] found in npz {npz_path}; "
				f"solved_box will be None for all frames"
			)

	render_error = False
	# Collected render manifest dicts (one per successfully rendered frame, WS2-C).
	manifests = []

	# Read CSV rows keyed by frame_index
	rows = {}
	with open(csv_path) as f:
		for row in csv.DictReader(f):
			try:
				frame_idx = int(row['frame_index'])
				rows[frame_idx] = row
			except (ValueError, KeyError):
				pass

	renderable_statuses = {
		'accepted',
		'interpolated',
		'extrapolated',
		'soft_miss_no_blob',
		'soft_miss_no_path',
		'after_walk_terminated',
	}
	for frame_index in sorted(rows.keys()):
		row_dict = rows[frame_index]
		status = row_dict.get('status', '')
		if status not in renderable_statuses:
			continue
		_fill_seed_pred(row_dict, anchor_seed)

		# Skip frames without prediction data (e.g., bootstrap frame)
		if not row_dict.get('pred_cx') or not row_dict.get('pred_cy') or \
				not row_dict.get('torso_w_px') or not row_dict.get('torso_h_px'):
			logger.debug(f"Skipping {direction_label} frame {frame_index}: missing prediction fields")
			continue

		try:
			# Use the real per-frame trace captured during the live walk
			# (WS2-A): this carries raw_blobs/corridor_blobs/winner_blob/
			# acceptance_box/roi_origin_xy in PROCESSED/ROI-relative space and
			# is what restores the blob ellipses + acceptance box. Falls back to
			# an empty stub when no live trace exists for this frame (render-only
			# mode with no --walk in this process, or a diagnostic/miss frame),
			# in which case ellipses silently skip but box/marker overlays still
			# draw. Heavy arrays were already dropped at capture time.
			trace = frame_trace_map.get(frame_index)
			if trace is None:
				trace = blob_trace.BlobObserverTrace(
					frame_index=frame_index,
					roi_bounds=None,
					has_residual=False,
					residual_dog=None,
					residual_pre_dog=None,
					validity_mask=None,
					raw_blobs=[],
					corridor_blobs=[],
					winner_blob=None,
					winner_score=None,
					local_tangent=(1.0, 0.0, 0.0, 1.0),
				)

			# SOLVED BOX (WS2-INT): per-frame walker-solved box in PROCESSED pixels.
			# Sourced from direction_path (WS1-A) via direction_box_by_frame lookup.
			# None when direction_path was not provided (render-only mode; follow-up
			# task: load source-frame npz and down-project via reader.geometry).
			solved_box = direction_box_by_frame.get(frame_index)

			# SEED BOX (WS2-INT): present only on seed frames (frame_index in
			# proc_seed_by_frame with cx/cy/w/h populated). Uses processed-pixel
			# seed dict from SeedsView.seeds; passes as-is (already PROCESSED).
			seed_entry = proc_seed_by_frame.get(frame_index)
			if seed_entry is not None and seed_entry.get("cx") is not None:
				# Extract only the box fields needed by render_walk_tile
				seed_box = {
					"cx": seed_entry["cx"],
					"cy": seed_entry["cy"],
					"w": seed_entry["w"],
					"h": seed_entry["h"],
				}
			else:
				seed_box = None

			# Reconstruct DebugLogRow from CSV dict
			debug_row = walk_debug_log.DebugLogRow(
				frame_index=walk_util._to_int(row_dict.get('frame_index'), frame_index),
				step=walk_util._to_int(row_dict.get('step')),
				direction=row_dict.get('direction', default_direction_sign),
				status=row_dict.get('status', 'unknown'),
				dt=walk_util._to_float(row_dict.get('dt')),
				torso_w_px=walk_util._to_float(row_dict.get('torso_w_px')),
				torso_h_px=walk_util._to_float(row_dict.get('torso_h_px')),
				pred_cx=walk_util._to_float(row_dict.get('pred_cx')),
				pred_cy=walk_util._to_float(row_dict.get('pred_cy')),
				cand_cx=walk_util._to_float(row_dict.get('cand_cx')),
				cand_cy=walk_util._to_float(row_dict.get('cand_cy')),
				cand_scene_x=walk_util._to_float(row_dict.get('cand_scene_x')),
				cand_scene_y=walk_util._to_float(row_dict.get('cand_scene_y')),
				reject_reason=row_dict.get('reject_reason', ''),
			)
			out_png_path = tile_dir / f"frame_{frame_index:06d}.png"
			# Collect manifest dict returned by render_walk_tile (WS2-C assembly).
			manifest = walk_render.render_walk_tile(
				frame_index=frame_index,
				debug_row=debug_row,
				trace=trace,
				reader=reader,
				scene_transform=scene_transform,
				fps=fps,
				out_png_path=out_png_path,
				seed_box=seed_box,
				solved_box=solved_box,
			)
			# WS2-C manifest enrichment (additive; render_walk_tile is not edited).
			# roi_origin_xy: processed-space ROI origin carried on the live trace
			#   (None on a stub frame). This is the single offset WS2-B1 subtracts.
			# crop_origin_xy: always (0, 0). FrameGeometry is origin-preserving:
			#   the goodbox crop only trims the right/bottom edges, so the top-left
			#   origin never moves (common_tools/frame_reader.py FrameGeometry).
			# bin_factor: from reader.geometry; processed->source is a pure scale.
			# non_seed_missing_solved_box: the "magenta + only" warning symptom;
			#   True when a non-seed frame has no solved box (gate-failing condition).
			roi_origin_xy = trace.roi_origin_xy
			manifest["roi_origin_xy"] = list(roi_origin_xy) if roi_origin_xy is not None else None
			manifest["crop_origin_xy"] = [0, 0]
			manifest["bin_factor"] = reader.geometry.bin_factor
			manifest["direction"] = direction_label
			is_seed_frame = manifest["seed_box_present"]
			manifest["non_seed_missing_solved_box"] = (
				not is_seed_frame and not manifest["solved_box_present"]
			)
			# In-box motion-cue heat fields (M1-B, M1-G).
			# The scalars are CACHED at walk time by walk_walker, where the
			#   residual is still live on the trace and is measured against the
			#   FINAL solved box (the emitted cx/cy/w/h). The renderer reads the
			#   cached scalars from the per-frame direction_path entry instead of
			#   re-deriving the residual (M1-F's render-time decode is removed):
			#   the residual was already computed during the walk, so no extra
			#   decode is paid.  Memory is bounded -- only the <=9 in-buffer window
			#   frames ever hold a live residual at once.
			# in_box_heat_computed: True when a real walk produced a solved box AND
			#   the cached scalars are present.  computed=False records a
			#   NOT-COMPUTED frame (no solved box, or render-only npz mode that has
			#   no cached scalars); computed=True with hot_count=0 records a
			#   COMPUTED-COLD frame.  The gate (M1-C) keeps COMPUTED-COLD in the
			#   denominator and skips NOT-COMPUTED.
			heat_threshold = residual_motion.DEFAULT_THRESHOLD
			hot_mean = None
			hot_count = 0
			in_box_heat_computed = False
			# Cached scalars are present only on the --walk path (direction_path),
			# carried into direction_box_by_frame above; render-only npz boxes lack
			# them, so the metric is NOT-COMPUTED there.
			if solved_box is not None and "in_box_hot_count" in solved_box:
				hot_mean = solved_box["in_box_hot_mean"]
				hot_count = solved_box["in_box_hot_count"]
				in_box_heat_computed = True
			manifest["in_box_hot_mean"] = hot_mean
			manifest["in_box_hot_count"] = hot_count
			manifest["in_box_heat_present"] = (hot_count > 0)
			manifest["in_box_heat_computed"] = in_box_heat_computed
			manifest["heat_threshold_used"] = heat_threshold
			manifests.append(manifest)
		except RuntimeError as e:
			logger.error(f"Render error ({direction_label} frame {frame_index}): {e}")
			render_error = True
			continue

	return render_error, manifests


#============================================
def run_interval_walk(
	left_seed: dict,
	right_seed: dict,
	reader,
	scene_transform,
	probe_info: dict,
	output_interval_dir: pathlib.Path,
	winner_mode: str,
	audit_rule: str | None,
	render_tiles: bool,
	proc_seed_by_frame: dict | None = None,
	npz_path: str | None = None,
	heat_movie: bool = False,
	heat_movie_scratch_parent: str | None = None,
) -> tuple:
	"""Run FWD and BWD walks on one interval, render tiles, write CSVs.

	Args:
		left_seed: Left seed dict with frame_index, cx, cy, w, h in PROCESSED
			pixels (docs/COORDINATE_SPACES.md). The space is enforced at the top
			of this function via coord_space.ProcessedBox + require_processed_box;
			a SOURCE-derived seed fails loud here (WS2-D, bug #101 cure).
		right_seed: Right seed dict with frame_index, cx, cy, w, h in PROCESSED
			pixels. Same boundary guard as left_seed.
		reader: FrameReader instance (must have .geometry for coordinate projection).
		scene_transform: SceneTransform instance.
		probe_info: Probe dict with fps, frame_count.
		output_interval_dir: Path to {output_dir}/{video_basename}/seed_{F_L}_{F_R}/.
		winner_mode: "production_winner" or "audit_winner".
		audit_rule: Audit rule name (or None).
		render_tiles: Whether to render PNG tiles.
		proc_seed_by_frame: Optional dict mapping frame_index -> processed-pixel seed
			dict from SeedsView.seeds. Used to supply seed_box on seed frames during
			tile rendering. When None, no seed boxes are drawn on tiles.
		npz_path: Optional path to the torso_box_coords.npz for this video.
			Passed to _render_direction_tiles for render-only solved_box fallback
			(loads source-frame boxes and down-projects to PROCESSED when
			direction_path is None).
		heat_movie: When True, encode a per-direction heat movie (.mkv) beside the
			tile output after the live walk completes.  Requires ffmpeg in PATH
			(check_ffmpeg_available() is called at parse_args time).  Off by default.
			The encode runs only when a live walk produced a non-empty direction_path;
			render-only mode (direction_path=None) produces no movie.
		heat_movie_scratch_parent: Optional base dir under /tmp for a deterministic
			scratch path (injectable for tests).  None uses a fresh tempdir.

	Returns:
		Tuple (IntervalSummary, fingerprint_key, solved_entry, fwd_manifests,
		bwd_manifests) where solved_entry is
		{start_frame, end_frame, forward_path, backward_path, blended_path} in
		SOURCE-frame coordinates, or (IntervalSummary, None, None, [], []) if
		projection fails (e.g. empty direction_path). fwd_manifests and
		bwd_manifests are lists of render manifest dicts (one per rendered frame)
		for WS2-C assembly.
	"""
	left_frame = left_seed["frame_index"]
	right_frame = right_seed["frame_index"]
	interval_length = right_frame - left_frame

	# WS2-D typed seed boundary (bug #101 cure): the walker steps in PROCESSED
	# space (docs/COORDINATE_SPACES.md). Both entry points (walk_driver.main and
	# make_walk_html_v2.process_video) must feed PROCESSED seeds. Construct a
	# typed ProcessedBox for each seed and assert the space at the boundary so a
	# future caller that hands in a SOURCE-derived seed fails loud here instead of
	# building a degenerate ROI deep inside observe_blob_at. Seeds arrive PROCESSED
	# by contract; do NOT convert source->processed here. not_in_frame seeds carry
	# no cx/cy and are not valid walk anchors, so this guard assumes box fields.
	left_box = common_tools.coord_space.require_processed_box(
		common_tools.coord_space.ProcessedBox(
			cx=float(left_seed["cx"]),
			cy=float(left_seed["cy"]),
			w=float(left_seed["w"]),
			h=float(left_seed["h"]),
		)
	)
	right_box = common_tools.coord_space.require_processed_box(
		common_tools.coord_space.ProcessedBox(
			cx=float(right_seed["cx"]),
			cy=float(right_seed["cy"]),
			w=float(right_seed["w"]),
			h=float(right_seed["h"]),
		)
	)

	# Create output subdirs
	output_interval_dir.mkdir(parents=True, exist_ok=True)
	fwd_dir = output_interval_dir / "fwd"
	bwd_dir = output_interval_dir / "bwd"
	if render_tiles:
		fwd_dir.mkdir(parents=True, exist_ok=True)
		bwd_dir.mkdir(parents=True, exist_ok=True)

	fps = probe_info["fps"]
	stride = 1  # Frame stride for residual cache (can be tuned).

	# Compute sampled-offset frame lists so every interval gets diagnostic
	# tiles even when the walker terminates after step 1.
	offsets = _sampled_offsets(interval_length)
	fwd_sampled_frames = sorted({left_frame + off for off in offsets if 0 < off <= interval_length})
	bwd_sampled_frames = sorted({right_frame - off for off in offsets if 0 < off <= interval_length}, reverse=True)

	# Run FWD walk
	logger.info(f"Running FWD walk: interval [{left_frame}, {right_frame}]")
	fwd_csv_path = output_interval_dir / "fwd_verdicts.csv"
	fwd_debug_log = walk_debug_log.DebugLogWriter(fwd_csv_path)

	fwd_summary = walk_walker.walk_one_direction(
		seed=left_seed,
		neighbor_seed_frame=right_frame,
		reader=reader,
		scene_transform=scene_transform,
		fps=fps,
		stride=stride,
		sign=1,  # FWD
		debug_log=fwd_debug_log,
		winner_mode=winner_mode,
		audit_rule=audit_rule,
		extra_diagnostic_frames=fwd_sampled_frames,
		# neighbor coords come from the validated PROCESSED right_box (WS2-D).
		neighbor_seed_cx=right_box.cx,
		neighbor_seed_cy=right_box.cy,
		neighbor_seed_w=right_box.w,
		neighbor_seed_h=right_box.h,
	)
	fwd_debug_log.close()

	# Run BWD walk
	logger.info(f"Running BWD walk: interval [{left_frame}, {right_frame}]")
	bwd_csv_path = output_interval_dir / "bwd_verdicts.csv"
	bwd_debug_log = walk_debug_log.DebugLogWriter(bwd_csv_path)

	bwd_summary = walk_walker.walk_one_direction(
		seed=right_seed,
		neighbor_seed_frame=left_frame,
		reader=reader,
		scene_transform=scene_transform,
		fps=fps,
		stride=stride,
		sign=-1,  # BWD
		debug_log=bwd_debug_log,
		winner_mode=winner_mode,
		audit_rule=audit_rule,
		extra_diagnostic_frames=bwd_sampled_frames,
		# neighbor coords come from the validated PROCESSED left_box (WS2-D).
		neighbor_seed_cx=left_box.cx,
		neighbor_seed_cy=left_box.cy,
		neighbor_seed_w=left_box.w,
		neighbor_seed_h=left_box.h,
	)
	bwd_debug_log.close()

	# Determine if rendering encountered errors
	render_error = False

	# Render tiles if requested.
	# v13 walker emits: accepted, interpolated, extrapolated,
	# soft_miss_no_blob, soft_miss_no_path, after_walk_terminated.
	# Legacy values (rejected_motion_gate, miss_no_blob, miss_low_conf)
	# are never emitted by the v13 walker; omit from render set.
	# Fill seed pred for bootstrap (step=0) and after_walk_terminated rows
	# so the user always sees at least the seed frame and the first stepped
	# frame, even on early stop.
	# Per-direction render manifest lists (WS2-C assembly).
	fwd_manifests = []
	bwd_manifests = []

	if render_tiles:
		# WS2-A: thread the live per-frame trace maps (FWD and BWD independent,
		# C9) from the just-completed walks into rendering so the renderer draws
		# real blob ellipses + acceptance box instead of the old empty stub.
		# WS2-INT: also thread direction_path for solved_box and proc_seed_by_frame
		# for seed_box per frame. FWD and BWD direction_paths are kept independent (C9).
		fwd_had_error, fwd_manifests = _render_direction_tiles(
			direction_label='FWD',
			csv_path=fwd_csv_path,
			tile_dir=fwd_dir,
			anchor_seed=left_seed,
			default_direction_sign='+',
			reader=reader,
			scene_transform=scene_transform,
			fps=fps,
			frame_trace_map=fwd_summary.direction_trace_map,
			direction_path=fwd_summary.direction_path,
			proc_seed_by_frame=proc_seed_by_frame,
			npz_path=npz_path,
			interval_start_frame=left_frame,
			interval_end_frame=right_frame,
		)
		bwd_had_error, bwd_manifests = _render_direction_tiles(
			direction_label='BWD',
			csv_path=bwd_csv_path,
			tile_dir=bwd_dir,
			anchor_seed=right_seed,
			default_direction_sign='-',
			reader=reader,
			scene_transform=scene_transform,
			fps=fps,
			frame_trace_map=bwd_summary.direction_trace_map,
			direction_path=bwd_summary.direction_path,
			proc_seed_by_frame=proc_seed_by_frame,
			npz_path=npz_path,
			interval_start_frame=left_frame,
			interval_end_frame=right_frame,
		)
		render_error = fwd_had_error or bwd_had_error

	# Compute gap: frames between FWD stop and BWD stop
	fwd_stop_frame = fwd_summary.stop_frame
	bwd_stop_frame = bwd_summary.stop_frame
	gap_frames = max(0, bwd_stop_frame - fwd_stop_frame - 1) if fwd_stop_frame < bwd_stop_frame else 0

	# Compute accepted_count and last_accepted_offset for each direction.
	fwd_accepted_count = len(fwd_summary.accepts)
	fwd_last_accepted_offset = abs(fwd_summary.accepts[-1] - left_frame) if fwd_summary.accepts else 0

	bwd_accepted_count = len(bwd_summary.accepts)
	bwd_last_accepted_offset = abs(bwd_summary.accepts[-1] - right_frame) if bwd_summary.accepts else 0

	# Write interval_summary.csv
	summary_csv_path = output_interval_dir / "interval_summary.csv"
	interval_summary = IntervalSummary(
		left_frame=left_frame,
		right_frame=right_frame,
		walker_run=True,
		skip_reason="",
		fwd_interval_length=interval_length,
		bwd_interval_length=interval_length,
		fwd_accepted_count=fwd_accepted_count,
		bwd_accepted_count=bwd_accepted_count,
		fwd_last_accepted_offset=fwd_last_accepted_offset,
		bwd_last_accepted_offset=bwd_last_accepted_offset,
		fwd_stop_reason=fwd_summary.stop_reason,
		bwd_stop_reason=bwd_summary.stop_reason,
		gap_frames=gap_frames,
		render_error=render_error,
		fwd_mode_disagreement_count=fwd_summary.mode_disagreement_count,
		bwd_mode_disagreement_count=bwd_summary.mode_disagreement_count,
	)

	with open(summary_csv_path, 'w', newline='') as f:
		writer = csv.DictWriter(
			f,
			fieldnames=[
				'left_frame', 'right_frame', 'walker_run', 'skip_reason',
				'fwd_interval_length', 'bwd_interval_length',
				'fwd_accepted_count', 'bwd_accepted_count',
				'fwd_last_accepted_offset', 'bwd_last_accepted_offset',
				'fwd_stop_reason', 'bwd_stop_reason',
				'gap_frames', 'render_error',
				'fwd_mode_disagreement_count', 'bwd_mode_disagreement_count',
			],
		)
		writer.writeheader()
		writer.writerow(dataclasses.asdict(interval_summary))

	# Assemble solved interval: project processed->source, blend, return for npz write.
	# direction_path is per WS1-A: list of {frame_index, cx, cy, w, h, conf} in processed pixels.
	fwd_raw = fwd_summary.direction_path
	bwd_raw = bwd_summary.direction_path

	# Heat movie encode (M2-B): per-direction, FWD and BWD independent (C9).
	# Runs only when --heat-movie is set AND a live walk produced a direction_path.
	# Import heat_movie_encode lazily so the normal walk path does not import it.
	if heat_movie:
		import heat_movie_encode
		# left_box and right_box are the validated ProcessedBox seeds from above.
		if fwd_raw:
			try:
				heat_movie_encode.encode_heat_movie_for_direction(
					direction_label='FWD',
					left_seed_box=left_box,
					right_seed_box=right_box,
					direction_path=fwd_raw,
					reader=reader,
					scene_transform=scene_transform,
					fps=fps,
					output_interval_dir=output_interval_dir,
					scratch_parent=heat_movie_scratch_parent,
				)
			except Exception as e:
				logger.error(f"heat-movie FWD encode failed for [{left_frame},{right_frame}]: {e}")
		if bwd_raw:
			try:
				heat_movie_encode.encode_heat_movie_for_direction(
					direction_label='BWD',
					left_seed_box=left_box,
					right_seed_box=right_box,
					direction_path=bwd_raw,
					reader=reader,
					scene_transform=scene_transform,
					fps=fps,
					output_interval_dir=output_interval_dir,
					scratch_parent=heat_movie_scratch_parent,
				)
			except Exception as e:
				logger.error(f"heat-movie BWD encode failed for [{left_frame},{right_frame}]: {e}")

	fingerprint_key = None
	solved_entry = None

	if fwd_raw and bwd_raw:
		# Project processed-space paths to source-frame using reader.geometry.
		# Per WS1-C contract: center = processed_to_source(cx_p, cy_p);
		# width = processed_to_source_delta(w_p, 0)[0]; height = processed_to_source_delta(0, h_p)[1].
		geometry = reader.geometry
		fwd_source = _project_path_to_source(fwd_raw, geometry)
		bwd_source = _project_path_to_source(bwd_raw, geometry)

		# Blend AFTER projecting to source; blended path is source-frame.
		blended_source = interval_solver.blend_paths(fwd_source, bwd_source)

		# Compute fingerprint key using the interval_fingerprint helper.
		# Seeds are processed-space dicts from SeedsView.seeds; compute_interval_fingerprint
		# reads frame_index only (not cx/cy), so processed vs source does not matter here.
		fingerprint_key = interval_fingerprint.compute_interval_fingerprint(left_seed, right_seed)

		solved_entry = {
			"start_frame": left_frame,
			"end_frame": right_frame,
			"forward_path": fwd_source,
			"backward_path": bwd_source,
			"blended_path": blended_source,
		}
	else:
		logger.warning(
			f"Interval [{left_frame}, {right_frame}]: empty direction_path for "
			f"FWD ({len(fwd_raw)}) or BWD ({len(bwd_raw)}); skipping npz entry."
		)

	return interval_summary, fingerprint_key, solved_entry, fwd_manifests, bwd_manifests


#============================================
def main() -> None:
	"""Main driver loop: iterate corpus, walk intervals, render, build HTML."""
	args = parse_args()

	output_root = pathlib.Path(args.output_dir)
	output_root.mkdir(parents=True, exist_ok=True)

	# Process each video
	for video_basename in args.video_basenames:
		logger.info(f"Processing video: {video_basename}")

		# Load seeds and video
		try:
			reader, probe_info = walk_io.open_walker_reader(video_basename)
			# Option A (2026-05-29): load seeds as a SeedsView in processed-pixel
			# coords so walk_one_direction receives processed coords natively.
			# assert_geometry_match guards against any reader/view bin mismatch.
			seeds_view = walk_io.load_walker_seeds_view(video_basename, reader.geometry)
			seeds_view.assert_geometry_match(reader.geometry)
			scene_transform = walk_io.load_walker_scene_transform(video_basename)
			race_start_frame = walk_io.load_race_start_frame(video_basename)
		except RuntimeError as e:
			logger.error(f"Failed to load video {video_basename}: {e}")
			continue

		# Enumerate intervals using the source-pixel seeds dict for frame-index
		# bookkeeping only. Seed coords for walking come from seeds_view.seeds.
		intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_view.source, race_start_frame)
		# Build a fast lookup from frame_index to processed-pixel seed dict.
		_proc_seed_by_frame = {s["frame_index"]: s for s in seeds_view.seeds}

		# Filter to post_start intervals only
		post_start_intervals = [
			i for i in intervals if i.label == "post_start"
		]

		# Spread by max_intervals: evenly distributed, not first-N.
		# Default is 4; a value <= 0 is the explicit opt-in to ALL intervals.
		if args.max_intervals > 0:
			post_start_intervals = walk_util._evenly_spread(post_start_intervals, args.max_intervals)

		logger.info(f"  Found {len(post_start_intervals)} post_start intervals")

		# Compute the npz path once per video; passed to run_interval_walk so that
		# _render_direction_tiles can load source-frame solved boxes for render-only
		# mode (direction_path=None). The same path is also used for the npz write
		# at the end of the video loop.
		video_npz_path = _video_npz_path(video_basename)

		video_output_dir = output_root / video_basename

		# Surface crossing_race_start intervals (skip with reason)
		crossing_intervals = [i for i in intervals if i.label == "crossing_race_start"]
		if crossing_intervals:
			logger.info(f"  Found {len(crossing_intervals)} crossing_race_start intervals (skipping)")

		# Process each interval.
		# Accumulate all solved interval entries here; write the npz ONCE at
		# the end of the video loop (no per-interval read-modify-write, which
		# would overwrite earlier intervals on each call).
		interval_summaries = []
		accumulated_solved = {}  # fingerprint_key -> solved_entry, source-frame
		# WS2-C: accumulate per-tile render manifests across all intervals for this
		# video; written once to render_manifest.json below (machine-checkable gate).
		video_manifest_records = []
		for idx, interval in enumerate(post_start_intervals):
			# Use processed-pixel seeds for walk (Option A, 2026-05-29).
			left_frame = interval.left_seed["frame_index"]
			right_frame = interval.right_seed["frame_index"]
			left_seed = _proc_seed_by_frame[left_frame]
			right_seed = _proc_seed_by_frame[right_frame]

			interval_dir = video_output_dir / f"seed_{left_frame}_{right_frame}"

			# Check resume
			if check_resume_needed(interval_dir, args.resume):
				logger.info(f"  Skipping (resume): interval [{left_frame}, {right_frame}]")
				continue

			logger.info(f"  [{idx + 1}/{len(post_start_intervals)}] Interval [{left_frame}, {right_frame}]")

			try:
				summary, fp_key, solved_entry, fwd_manifests, bwd_manifests = run_interval_walk(
					left_seed=left_seed,
					right_seed=right_seed,
					reader=reader,
					scene_transform=scene_transform,
					probe_info=probe_info,
					output_interval_dir=interval_dir,
					winner_mode=args.winner_mode,
					audit_rule=args.audit_rule,
					render_tiles=args.render_tiles,
					proc_seed_by_frame=_proc_seed_by_frame,
					npz_path=video_npz_path,
					heat_movie=args.heat_movie,
				)
				# WS2-C: tag each per-tile manifest with its interval identity and
				# accumulate for the video-level render_manifest.json artifact.
				for manifest in fwd_manifests + bwd_manifests:
					manifest["interval_left_frame"] = left_frame
					manifest["interval_right_frame"] = right_frame
					video_manifest_records.append(manifest)
				interval_summaries.append(summary)
				# Accumulate solved interval entry for later npz write.
				# None means direction_path was empty (logged inside run_interval_walk).
				if fp_key is not None and solved_entry is not None:
					accumulated_solved[fp_key] = solved_entry
			except Exception as e:
				logger.error(f"  Exception in interval [{left_frame}, {right_frame}]: {e}")
				# Continue to next interval

		# Write solved intervals to npz ONCE for this video (load-merge-write-back).
		# Overwrite at the interval-key level is intentional (walker IS the solver).
		if accumulated_solved:
			os.makedirs(os.path.dirname(video_npz_path), exist_ok=True)
			# Load existing data to merge with (returns empty skeleton if missing).
			existing = state_io.load_torso_box_coords(video_npz_path)
			existing_solved = existing["solved_intervals"]
			# Merge: accumulated entries overwrite any same-key existing entries.
			merged_solved = dict(existing_solved)
			merged_solved.update(accumulated_solved)
			cache_data = dict(existing)
			cache_data["solved_intervals"] = merged_solved
			state_io.write_torso_box_coords(video_npz_path, cache_data)
			logger.info(
				f"  Wrote {len(accumulated_solved)} solved interval(s) to {video_npz_path} "
				f"(total in file: {len(merged_solved)})"
			)

		# WS2-C: write the machine-checkable render manifest for this video.
		# One JSON record per rendered tile; consumed by the manifest checker
		# (tools/blob_walk_v2/check_render_manifest.py) which fails the gate if
		# any non-seed frame lacks a solved box or any tile has conversion_count
		# != 1 -- catching the "magenta + only" symptom without opening walk.html.
		if video_manifest_records:
			video_output_dir.mkdir(parents=True, exist_ok=True)
			manifest_path = video_output_dir / "render_manifest.json"
			with open(manifest_path, 'w') as f:
				json.dump(video_manifest_records, f, indent=2)
			logger.info(
				f"  Wrote render manifest: {manifest_path} "
				f"({len(video_manifest_records)} tile records)"
			)

		reader.close()

	# Build HTML if requested
	if args.build_html:
		logger.info("Building HTML...")
		try:
			walk_html.build_walk_html(str(output_root))
			logger.info(f"HTML built: {output_root / 'walk.html'}")
		except Exception as e:
			logger.error(f"Failed to build HTML: {e}")


#============================================
if __name__ == '__main__':
	main()
