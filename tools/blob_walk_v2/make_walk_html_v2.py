#!/usr/bin/env python3
"""Single-video v2 driver: walk one video, render tiles, build walk.html.

Processes exactly ONE video per invocation. Video-agnostic: it walks whatever
video it is handed and has no opinion about which videos exist or where the
corpus lives. Batch runs loop over a corpus list in the shell, calling this
once per video (see run_random_walk.sh).

  Phase 1: walker batch (only with --walk). Enumerates seed-to-seed intervals
           via race_phases, randomly samples up to N visible-both intervals, then
           walks each via walk_driver.run_interval_walk.
  Phase 2: render walker tiles (CSV + PNG) unless --skip-render.
  Phase 3: build walk.html via walk_html.build_walk_html.

Usage:
    source source_me.sh && python3 tools/blob_walk_v2/make_walk_html_v2.py \
        --walk -v TRACK_VIDEOS/IMG_3823.mkv -n 20 -o output_smoke_random20
"""

# Standard Library
import random
import argparse
import pathlib
import logging

# shared sys.path bootstrap. This entry script lives at the package root, so
# its own directory (sys.path[0] when run directly) is the package dir and the
# bare import walk_paths resolves. setup() wires up track_runner, tests, repo
# root, and the core/ and render/ subdirs so the bare-name imports below
# resolve to the moved modules.
import walk_paths
walk_paths.setup()

# local repo modules
import walk_tool_setup
import walk_util
import walk_driver
import walk_html
import race_phases  # track_runner/race_phases.py: seed-to-seed interval enumerator
import interval_solver  # track_runner/interval_solver.py: reused progress renderer


logger = logging.getLogger(__name__)


#============================================

def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments for a single-video walk."""
	parser = argparse.ArgumentParser(
		description=(
			'Single-video v2 driver: walk one video, render walker tiles, '
			'build walk.html with SVG vector overlays.'
		),
	)
	parser.add_argument(
		'-v', '--video', dest='video', required=True,
		help='Video path or basename to walk (e.g. TRACK_VIDEOS/IMG_3823.mkv).',
	)
	parser.add_argument(
		'-o', '--output-root', dest='output_root', default='blob_walk_v2',
		help='Root output directory (default: blob_walk_v2).',
	)
	parser.add_argument(
		'-w', '--walk', dest='walk', action='store_true',
		help='Run the walker before rendering (default: skip walker).',
	)
	parser.add_argument(
		'-n', '--sample-intervals', dest='sample_intervals', type=int, default=20,
		help=(
			'Number of random visible-both intervals to walk (default: 20). '
			'Both bracketing seeds must have status == "visible"; "partial" and '
			'"not_in_frame" intervals are excluded. Pass 0 (or any value <= 0) '
			'to walk ALL visible-both intervals.'
		),
	)
	parser.add_argument(
		'--random-seed', dest='random_seed', type=int, default=None,
		help=(
			'Seed the RNG for reproducible interval selection. Omit for a fresh '
			'(nondeterministic) sample each run.'
		),
	)
	parser.add_argument(
		'-r', '--resume', dest='resume', action='store_true',
		help='Skip interval dirs whose PNGs and CSVs already exist on disk.',
	)
	parser.add_argument(
		'--skip-render', dest='skip_render', action='store_true',
		help='Skip render phase; rebuild walk.html from existing CSVs and PNGs.',
	)
	parser.add_argument(
		'-d', '--dry-run', dest='dry_run', action='store_true',
		help='Print the resolved video and output dir, then exit.',
	)
	parser.add_argument(
		'--heat-movie', dest='heat_movie', action='store_true',
		help=(
			"Encode a per-direction heat movie (.mkv) alongside each interval's "
			'render output. Requires ffmpeg in PATH. Only active with --walk.'
		),
	)
	parser.add_argument(
		'--no-heat-movie', dest='heat_movie', action='store_false',
		help='Do not encode heat movies (default).',
	)
	parser.set_defaults(heat_movie=False)
	args = parser.parse_args()
	return args


#============================================

def process_video(
	video_basename: str,
	output_root: pathlib.Path,
	sample_intervals: int,
	resume: bool,
	render_tiles: bool,
	random_seed: int | None,
	heat_movie: bool = False,
) -> dict:
	"""Walk + render the sampled intervals of one video. Returns counters dict."""
	logger.info(f'Processing video: {video_basename}')
	reader, probe_info = walk_tool_setup.open_reader(video_basename)
	# Decode cost is resolution-bound and pre-bin: binning happens after decode so
	# auto-bin cannot reduce seek cost on 4K HEVC sources (see common_tools/README.md).
	if reader.geometry.source_width >= 3840 or reader.geometry.source_height >= 2160:
		logger.info(
			'4K+ source: random-access decode is slow (~0.5 s/seek '
			'single-process, multi-second under parallel load); expect long '
			'runtimes on scattered reads (see docs/TROUBLESHOOTING.md)'
		)
	# Load seeds as a SeedsView in processed-pixel coords so run_interval_walk
	# receives PROCESSED seeds natively (the bug #101 cure). At bin > 1 a
	# source cx near the right edge built a degenerate ROI clamped against the
	# processed width (bug #101); the SeedsView path avoids that.
	seeds_view = walk_tool_setup.load_seeds_view(video_basename, reader.geometry)
	seeds_view.assert_geometry_match(reader.geometry)
	scene_transform = walk_tool_setup.load_scene_transform(video_basename)
	race_start_frame = walk_tool_setup.load_race_start_frame(video_basename)

	# Processed-pixel seed map keyed by frame_index. This is BOTH the walk input
	# and the renderer's seed-box source. The walker steps in PROCESSED space
	# (docs/COORDINATE_SPACES.md); SeedsView.seeds is PROCESSED.
	proc_seed_by_frame = {s['frame_index']: s for s in seeds_view.seeds}

	# Enumerate intervals using the source-pixel seeds dict for frame-index
	# bookkeeping only. Walk coords come from proc_seed_by_frame (processed),
	# never from seeds_view.source.
	intervals = race_phases.enumerate_seed_to_seed_intervals(seeds_view.source, race_start_frame)
	post_start = [i for i in intervals if i.label == 'post_start']

	# Random visible-both sampling: pick up to sample_intervals random intervals
	# where both bracketing seeds have status == "visible".
	n = sample_intervals if sample_intervals > 0 else len(post_start)
	rng = random.Random(random_seed)
	visible_count = sum(
		1 for i in post_start
		if i.left_seed['status'] == 'visible' and i.right_seed['status'] == 'visible'
	)
	post_start = walk_util.select_random_visible(post_start, n, rng)
	logger.info(
		f'  random-sample: {len(post_start)}/{visible_count} visible-both intervals '
		f'selected (n={n}, rng_seed={random_seed})'
	)

	counters = {'walked': 0, 'skipped_resume': 0}
	video_output_dir = output_root / video_basename
	# Per-video torso_box_coords.npz path; passed to run_interval_walk (render-only
	# lookup) and the solved-interval write below. The walker IS the solver.
	npz_path = walk_driver.video_npz_path(video_basename)
	# Accumulators written once at the end via the walk_driver write_* helpers:
	# solved interval entries -> npz, per-tile manifests -> render_manifest.json,
	# per-(interval, direction) heat -> render_heat_summary.json (the file the
	# check_render_manifest.py heat report reads).
	accumulated_solved = {}
	video_manifest_records = []
	video_heat_summaries = []

	# Frame-weighted progress: span is the inclusive seed-to-seed length (both
	# endpoints are seeds). Used only as an ETA cost proxy so the bar's ETA stays
	# smooth across intervals that range from ~10s to several minutes.
	spans = [
		i.right_seed['frame_index'] - i.left_seed['frame_index'] + 1
		for i in post_start
	]
	total_frames = sum(spans)

	# Empty selection: skip the progress bar entirely -- a 0-of-0 bar and its ETA
	# are meaningless. Writers below are no-ops on empty accumulators, so behavior
	# is otherwise unchanged.
	if not post_start:
		logger.info('  no visible-both intervals to walk')
	else:
		# Reuse track_runner's solve/refine progress renderer (rich): one bar per
		# video, M/N by interval, ETA frame-weighted via the shared frame counter.
		frame_counter = [0]
		eta_column = interval_solver.FrameETAColumn(frame_counter, total_frames)
		with interval_solver.make_solve_progress(eta_column) as progress:
			task = progress.add_task(video_basename, total=len(post_start))
			for interval in post_start:
				# left/right seeds are SOURCE-pixel dicts (frame-index bookkeeping
				# only); walk inputs are the PROCESSED seeds looked up by frame_index
				# (the bug #101 cure; see run_interval_walk).
				left_frame = interval.left_seed['frame_index']
				right_frame = interval.right_seed['frame_index']
				span = right_frame - left_frame + 1
				interval_dir = video_output_dir / f'seed_{left_frame}_{right_frame}'

				if walk_driver.check_resume_needed(interval_dir, resume):
					logger.debug(f'Skip (resume): interval [{left_frame}, {right_frame}]')
					counters['skipped_resume'] += 1
					# A resume-skip still advances the bar and the frame counter so
					# M/N reaches N and the ETA does not look stuck.
					frame_counter[0] += span
					progress.update(task, advance=1)
					continue

				left_seed = proc_seed_by_frame[left_frame]
				right_seed = proc_seed_by_frame[right_frame]
				# No try/except: a failure here is a real bug, not an expected
				# per-interval outcome (an empty direction_path is handled inside
				# run_interval_walk, not raised). Let it propagate, not be swallowed.
				_summary, fp_key, solved_entry, fwd_manifests, bwd_manifests = (
					walk_driver.run_interval_walk(
						left_seed=left_seed,
						right_seed=right_seed,
						reader=reader,
						scene_transform=scene_transform,
						probe_info=probe_info,
						output_interval_dir=interval_dir,
						winner_mode='production_winner',
						audit_rule=None,
						render_tiles=render_tiles,
						proc_seed_by_frame=proc_seed_by_frame,
						npz_path=npz_path,
						heat_movie=heat_movie,
					)
				)
				# Tag each per-tile manifest with its interval and accumulate.
				for manifest in fwd_manifests + bwd_manifests:
					manifest['interval_left_frame'] = left_frame
					manifest['interval_right_frame'] = right_frame
					video_manifest_records.append(manifest)
				# Aggregate the per-frame heat carrier into interval-direction
				# summaries and strip the carrier so render_manifest.json holds no
				# heat keys.
				video_heat_summaries.extend(
					walk_driver.summarize_and_strip_heat(
						left_frame, right_frame, fwd_manifests, bwd_manifests
					)
				)
				# Accumulate the solved interval entry for the npz write (None when
				# the direction_path was empty; logged inside run_interval_walk).
				if fp_key is not None and solved_entry is not None:
					accumulated_solved[fp_key] = solved_entry
				counters['walked'] += 1
				frame_counter[0] += span
				progress.update(task, advance=1)

	reader.close()

	# Persist the three per-video artifacts via the shared walk_driver writers,
	# OUTSIDE the progress `with` block so the bar has closed before these (slow,
	# disk-bound) writes log: solved torso boxes (npz), the machine-checkable
	# render manifest, and the heat summary check_render_manifest.py reads.
	walk_driver.write_solved_intervals_npz(npz_path, accumulated_solved)
	walk_driver.write_render_manifest(video_output_dir, video_manifest_records)
	walk_driver.write_heat_summary(video_output_dir, video_heat_summaries)

	return counters


#============================================

def main() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s - %(levelname)s - %(message)s',
	)
	args = parse_args()

	# Accept a path or a bare basename; stem strips the directory and extension
	# (e.g. TRACK_VIDEOS/IMG_3823.mkv -> IMG_3823).
	video_basename = pathlib.Path(args.video).stem

	# Fail early when --heat-movie is requested but ffmpeg is absent.
	# Import heat_movie_encode lazily so the normal walk path never imports it.
	if args.heat_movie:
		import heat_movie_encode
		heat_movie_encode.check_ffmpeg_available()

	output_root = pathlib.Path(args.output_root)

	if args.dry_run:
		print('=== DRY RUN ===')
		print(f'video:            {video_basename}')
		print(f'output_root:      {output_root}')
		print(f'walk:             {args.walk}')
		print(f'sample_intervals: {args.sample_intervals}')
		print(f'random_seed:      {args.random_seed}')
		print('No work performed.')
		return

	output_root.mkdir(parents=True, exist_ok=True)

	render_tiles = not args.skip_render

	if args.walk:
		logger.info(
			f'phase 1+2: walk {video_basename} '
			f'(resume={args.resume}, render_tiles={render_tiles})'
		)
		counters = process_video(
			video_basename=video_basename,
			output_root=output_root,
			sample_intervals=args.sample_intervals,
			resume=args.resume,
			render_tiles=render_tiles,
			random_seed=args.random_seed,
			heat_movie=args.heat_movie,
		)
		print(
			f"phase 1+2: walked={counters['walked']}, "
			f"skipped(resume)={counters['skipped_resume']}"
		)
	else:
		print('phase 1+2: SKIPPED (no --walk; rebuilding walk.html only)')

	# Phase 3: build walk.html from on-disk artifacts (accumulates across the
	# per-video invocations that share one output_root).
	print('phase 3: building walk.html...')
	html_path = walk_html.build_walk_html(output_root)
	print(f'wrote {html_path}')


#============================================

if __name__ == '__main__':
	main()
