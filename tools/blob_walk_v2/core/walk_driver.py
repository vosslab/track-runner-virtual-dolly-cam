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
def _fill_seed_pred(row_dict: dict, seed: dict) -> None:
	"""In-place: ensure pred_cx/cy/torso_w_px/torso_h_px have values from seed.

	Used for bootstrap (step=0) rows and after_walk_terminated diagnostic rows
	where walker did not populate the prediction fields.
	"""
	if not row_dict.get('pred_cx'):
		row_dict['pred_cx'] = str(seed['cx'])
	if not row_dict.get('pred_cy'):
		row_dict['pred_cy'] = str(seed['cy'])
	if not row_dict.get('torso_w_px'):
		row_dict['torso_w_px'] = str(seed['w'])
	if not row_dict.get('torso_h_px'):
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
) -> bool:
	"""Render PNG tiles for one walk direction (FWD or BWD) from a CSV file.

	Reads the CSV written by DebugLogWriter, reconstructs minimal trace and
	DebugLogRow objects for each renderable frame, and calls render_walk_tile.

	Args:
		direction_label: Human-readable label used in log messages, e.g. 'FWD' or 'BWD'.
		csv_path: Path to the verdicts CSV file for this direction.
		tile_dir: Output directory for PNG tiles.
		anchor_seed: Seed dict at the anchor end (left for FWD, right for BWD).
		default_direction_sign: Fallback value for direction field, '+' for FWD, '-' for BWD.
		reader: FrameReader instance.
		scene_transform: SceneTransform instance.
		fps: Frames per second.

	Returns:
		True if any render error occurred, False otherwise.
	"""
	render_error = False

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
			# Reconstruct trace (minimal version for rendering)
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
			walk_render.render_walk_tile(
				frame_index=frame_index,
				debug_row=debug_row,
				trace=trace,
				reader=reader,
				scene_transform=scene_transform,
				fps=fps,
				out_png_path=out_png_path,
			)
		except RuntimeError as e:
			logger.error(f"Render error ({direction_label} frame {frame_index}): {e}")
			render_error = True
			continue

	return render_error


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
) -> IntervalSummary:
	"""Run FWD and BWD walks on one interval, render tiles, write CSVs.

	Args:
		left_seed: Left seed dict with frame_index, cx, cy, w, h.
		right_seed: Right seed dict with frame_index, cx, cy, w, h.
		reader: FrameReader instance.
		scene_transform: SceneTransform instance.
		probe_info: Probe dict with fps, frame_count.
		output_interval_dir: Path to {output_dir}/{video_basename}/seed_{F_L}_{F_R}/.
		winner_mode: "production_winner" or "audit_winner".
		audit_rule: Audit rule name (or None).
		render_tiles: Whether to render PNG tiles.

	Returns:
		IntervalSummary with walk results and error flags.
	"""
	left_frame = left_seed["frame_index"]
	right_frame = right_seed["frame_index"]
	interval_length = right_frame - left_frame

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
		neighbor_seed_cx=right_seed["cx"],
		neighbor_seed_cy=right_seed["cy"],
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
		neighbor_seed_cx=left_seed["cx"],
		neighbor_seed_cy=left_seed["cy"],
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
	if render_tiles:
		fwd_had_error = _render_direction_tiles(
			direction_label='FWD',
			csv_path=fwd_csv_path,
			tile_dir=fwd_dir,
			anchor_seed=left_seed,
			default_direction_sign='+',
			reader=reader,
			scene_transform=scene_transform,
			fps=fps,
		)
		bwd_had_error = _render_direction_tiles(
			direction_label='BWD',
			csv_path=bwd_csv_path,
			tile_dir=bwd_dir,
			anchor_seed=right_seed,
			default_direction_sign='-',
			reader=reader,
			scene_transform=scene_transform,
			fps=fps,
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

	return interval_summary


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

		video_output_dir = output_root / video_basename

		# Surface crossing_race_start intervals (skip with reason)
		crossing_intervals = [i for i in intervals if i.label == "crossing_race_start"]
		if crossing_intervals:
			logger.info(f"  Found {len(crossing_intervals)} crossing_race_start intervals (skipping)")

		# Process each interval
		interval_summaries = []
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
				summary = run_interval_walk(
					left_seed=left_seed,
					right_seed=right_seed,
					reader=reader,
					scene_transform=scene_transform,
					probe_info=probe_info,
					output_interval_dir=interval_dir,
					winner_mode=args.winner_mode,
					audit_rule=args.audit_rule,
					render_tiles=args.render_tiles,
				)
				interval_summaries.append(summary)
			except Exception as e:
				logger.error(f"  Exception in interval [{left_frame}, {right_frame}]: {e}")
				# Continue to next interval

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
