#!/usr/bin/env python3
"""All-in-one v2 driver: discover, optionally walk, render, build walk.html.

Carries forward the four-phase model from the legacy v1 driver
(tools/blob_walk/make_walk_html.py) but reimplements every step against
the v2 module stack:

  Phase 0: video discovery + optional --outdoor-400m-only filter + cost-sort
           + max-videos cap. Probes use common_tools.probe_video.
  Phase 1: walker batch (only with --walk). Iterates selected videos,
           enumerates seed-to-seed intervals via walk_io, then calls the
           v2 walker through walk_driver.run_interval_walk.
  Phase 2: render walker tiles. Reuses walk_driver.run_interval_walk in
           render-only mode (CSV+PNG) when --skip-render is not set.
  Phase 3: build walk.html via the modernized walk_html.build_walk_html
           with inline SVG vector overlays.

Usage:
    source source_me.sh && python3 tools/blob_walk_v2/make_walk_html_v2.py
    source source_me.sh && python3 tools/blob_walk_v2/make_walk_html_v2.py \
        --outdoor-400m-only --max-videos 2 --skip-render --dry-run
"""

# Standard Library
import sys
import json
import argparse
import pathlib
import logging
import multiprocessing

# shared sys.path bootstrap. This entry script lives at the package root, so
# its own directory (sys.path[0] when run directly) is the package dir and the
# bare import walk_paths resolves. setup() wires up track_runner, tests, repo
# root, and the core/ and render/ subdirs so the bare-name imports below
# resolve to the moved modules.
import walk_paths
_REPO_ROOT = walk_paths.setup()

# PIP3 modules
import yaml

# local repo modules
import common_tools.probe_video
import walk_io
import walk_util
import walk_driver
import walk_html


TR_CONFIG_DIR = pathlib.Path(_REPO_ROOT) / 'tr_config'
TRACK_VIDEOS_DIR = pathlib.Path(_REPO_ROOT) / 'TRACK_VIDEOS'
SEEDS_SUFFIX = '.track_runner.seeds.json'
CONFIG_SUFFIX = '.track_runner.config.yaml'
VIDEO_EXTENSIONS = ('.mkv', '.mov', '.mp4')


logger = logging.getLogger(__name__)


#============================================

def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments matching v1 flag set."""
	parser = argparse.ArgumentParser(
		description=(
			'V2 all-in-one driver: discover, optionally walk, render walker tiles, '
			'build walk.html with M2-aligned SVG vector overlays.'
		),
	)
	parser.add_argument(
		'-o', '--output-root', dest='output_root', default='blob_walk_v2',
		help='Root output directory (default: blob_walk_v2).',
	)
	parser.add_argument(
		'-w', '--walk', dest='walk', action='store_true',
		help='Run the walker batch before rendering (default: skip walker).',
	)
	parser.add_argument(
		'-r', '--resume', dest='resume', action='store_true',
		help='Skip seed dirs whose PNGs and CSVs already exist on disk.',
	)
	parser.add_argument(
		'-j', '--workers', dest='workers', type=int, default=1,
		help='Number of parallel video workers for render (default: 1).',
	)
	parser.add_argument(
		'-m', '--max-videos', '--limit', dest='max_videos', type=int, default=None,
		help='Cap number of videos to process (default: all discovered).',
	)
	parser.add_argument(
		'-s', '--sort-by-size', dest='sort_by_size', action='store_true',
		help='Sort videos ascending by width*height*fps*frames (cheap first).',
	)
	parser.add_argument(
		'-n', '--sample-intervals-per-video',
		dest='sample_intervals_per_video', type=int, default=4,
		help=(
			'Cap intervals processed per video (evenly spread). Default: 4. '
			'A full walk of every interval is intentionally never the default '
			'because it is catastrophically slow on large corpora. '
			'Pass 0 (or any value <= 0) to opt in to ALL post_start intervals. '
			'Ignored when --intervals-from-corpus is set.'
		),
	)
	parser.add_argument(
		'--skip-render', dest='skip_render', action='store_true',
		help='Skip render phase; rebuild walk.html from existing CSVs and PNGs.',
	)
	parser.add_argument(
		'-d', '--dry-run', dest='dry_run', action='store_true',
		help='Print selected videos and intended work, then exit.',
	)
	parser.add_argument(
		'-v', '--videos', dest='videos', nargs='+', default=None,
		help='Explicit video basename list (overrides discovery).',
	)
	parser.add_argument(
		'--outdoor-400m-only', dest='outdoor_400m_only', action='store_true',
		help='Filter discovery to videos with camera.track_size == 400 in config.yaml.',
	)
	parser.add_argument(
		'--intervals-from-corpus', dest='intervals_from_corpus', default=None,
		help=(
			'Path to a dump_step1 corpus directory (e.g. dump_step1/24corpus/). '
			'Reads per-video interval left_frame lists from JSON filenames under '
			'<corpus>/<video>/FWD_<left_frame>.json. Restricts walk to those intervals only. '
			'Overrides --sample-intervals-per-video.'
		),
	)
	parser.add_argument(
		'--heat-movie', dest='heat_movie', action='store_true',
		help=(
			'Encode a per-direction heat movie (.mkv) alongside each interval\'s '
			'render output.  Requires ffmpeg in PATH.  Only active when --walk is '
			'set.  Off by default.'
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

def _video_path_for(basename: str) -> pathlib.Path | None:
	"""Return the first matching TRACK_VIDEOS file for a basename, or None."""
	for ext in VIDEO_EXTENSIONS:
		candidate = TRACK_VIDEOS_DIR / f'{basename}{ext}'
		if candidate.is_file():
			return candidate
	return None


#============================================

def discover_videos() -> list:
	"""Return basenames with both tr_config seeds and a TRACK_VIDEOS file."""
	out = []
	if not TR_CONFIG_DIR.exists():
		return out
	for seeds_path in sorted(TR_CONFIG_DIR.glob(f'*{SEEDS_SUFFIX}')):
		basename = seeds_path.name[:-len(SEEDS_SUFFIX)]
		if _video_path_for(basename) is not None:
			out.append(basename)
	return out


#============================================

def filter_outdoor_400m(videos: list) -> list:
	"""Keep only videos whose config.yaml has camera.track_size == 400."""
	kept = []
	for basename in videos:
		config_path = TR_CONFIG_DIR / f'{basename}{CONFIG_SUFFIX}'
		if not config_path.is_file():
			logger.info(f'  {basename}: no config.yaml; dropping from 400m filter')
			continue
		with open(config_path) as f:
			cfg = yaml.safe_load(f)
		# Direct access; missing or non-400 drops the video.
		camera = cfg['camera'] if 'camera' in cfg else None
		if camera is None:
			continue
		track_size = camera.get('track_size')
		if track_size == 400:
			kept.append(basename)
	return kept


#============================================

def cost_components(basename: str) -> dict:
	"""Probe video for size/fps/frames and compute a cheap-first cost score."""
	video_path = _video_path_for(basename)
	if video_path is None:
		return {'width': 0, 'height': 0, 'fps': 0.0, 'frame_count': 0, 'cost': 0}
	probe = common_tools.probe_video.probe_video(str(video_path))
	w = int(probe['width'])
	h = int(probe['height'])
	fps = float(probe['fps'])
	n = int(probe['frame_count'])
	return {
		'width': w,
		'height': h,
		'fps': fps,
		'frame_count': n,
		'cost': int(w * h * fps * n),
	}


#============================================

def select_videos(videos: list, max_videos: int | None, sort_by_size: bool) -> list:
	"""Apply sort and cap to the discovered video list."""
	selected = list(videos)
	if sort_by_size:
		selected = sorted(selected, key=lambda b: cost_components(b)['cost'])
	if max_videos is not None and max_videos > 0:
		selected = selected[:max_videos]
	return selected


#============================================

def print_dry_run(videos: list, args: argparse.Namespace) -> None:
	"""Print selection details and intended work, then return."""
	print('=== DRY RUN ===')
	print(f'output_root: {args.output_root}')
	print(f'walk: {args.walk}')
	print(f'resume: {args.resume}')
	print(f'workers: {args.workers}')
	print(f'sample_intervals_per_video: {args.sample_intervals_per_video}')
	print(f'sort_by_size: {args.sort_by_size}')
	print(f'max_videos: {args.max_videos}')
	print(f'skip_render: {args.skip_render}')
	print(f'outdoor_400m_only: {args.outdoor_400m_only}')
	print()
	print(f'{len(videos)} videos selected:')
	header = (
		f'  {"video":<48} {"width":>6} {"height":>6} {"fps":>6} '
		f'{"frames":>8} {"cost":>22}'
	)
	print(header)
	print('  ' + '-' * (len(header) - 2))
	for v in videos:
		c = cost_components(v)
		print(
			f'  {v:<48} {c["width"]:>6} {c["height"]:>6} {c["fps"]:>6.2f} '
			f'{c["frame_count"]:>8} {c["cost"]:>22,}'
		)
	print()
	print('No work performed.')


#============================================

def load_corpus_intervals(corpus_dir: pathlib.Path) -> dict:
	"""Load per-video interval left_frame sets from a dump_step1 corpus directory.

	Scans <corpus_dir>/<video>/FWD_<left_frame>.json files and returns
	a dict mapping video_basename -> frozenset of left_frame ints.

	Args:
		corpus_dir: Path to corpus root (e.g. dump_step1/24corpus/).

	Returns:
		dict: {video_basename: frozenset({left_frame, ...})}
	"""
	result = {}
	if not corpus_dir.is_dir():
		raise RuntimeError(f'Corpus directory not found: {corpus_dir}')
	for video_dir in sorted(corpus_dir.iterdir()):
		if not video_dir.is_dir():
			continue
		video_basename = video_dir.name
		left_frames = set()
		for json_path in video_dir.glob('FWD_*.json'):
			stem = json_path.stem  # FWD_1389
			parts = stem.split('_', 1)
			if len(parts) == 2:
				try:
					left_frames.add(int(parts[1]))
				except ValueError:
					logger.warning(f'Could not parse left_frame from: {json_path.name}')
		if left_frames:
			result[video_basename] = frozenset(left_frames)
			logger.info(
				f'corpus: {video_basename}: {len(left_frames)} intervals: '
				f'{sorted(left_frames)}'
			)
	return result


#============================================

def process_video(
	video_basename: str,
	output_root: pathlib.Path,
	max_intervals: int | None,
	resume: bool,
	render_tiles: bool,
	corpus_left_frames: frozenset | None = None,
	heat_movie: bool = False,
) -> dict:
	"""Walk + render one video. Returns counters dict."""
	logger.info(f'Processing video: {video_basename}')
	reader, probe_info = walk_io.open_walker_reader(video_basename)
	# WS2-D (2026-05-29): load seeds as a SeedsView in processed-pixel coords,
	# mirroring walk_driver.main, so run_interval_walk receives PROCESSED seeds
	# natively. Previously this path loaded SOURCE seeds via load_walker_seeds
	# and fed them straight into run_interval_walk; at bin > 1 a source cx (near
	# the right edge) built a degenerate ROI clamped against the processed width
	# (bug #101), and after WS2-C that degenerate ROI soft-missed every frame.
	# assert_geometry_match guards against any reader/view bin mismatch.
	seeds_view = walk_io.load_walker_seeds_view(video_basename, reader.geometry)
	seeds_view.assert_geometry_match(reader.geometry)
	scene_transform = walk_io.load_walker_scene_transform(video_basename)
	race_start_frame = walk_io.load_race_start_frame(video_basename)

	# Processed-pixel seed map keyed by frame_index. This is BOTH the walk input
	# (passed to run_interval_walk per interval below) and the renderer's seed-box
	# source (proc_seed_by_frame). Per the coordinate contract (docs/COORDINATE_SPACES.md)
	# the walker steps in PROCESSED space; SeedsView.seeds is PROCESSED.
	proc_seed_by_frame = {s['frame_index']: s for s in seeds_view.seeds}

	# Enumerate intervals using the source-pixel seeds dict for frame-index
	# bookkeeping only (mirrors walk_driver.main). Seed coords for walking come
	# from proc_seed_by_frame (processed), never from seeds_view.source.
	intervals = walk_io.enumerate_seed_to_seed_intervals(seeds_view.source, race_start_frame)
	post_start = [i for i in intervals if i.label == 'post_start']

	# If corpus filter is set, restrict to those left_frames only.
	if corpus_left_frames is not None:
		before = len(post_start)
		post_start = [
			i for i in post_start
			if i.left_seed['frame_index'] in corpus_left_frames
		]
		logger.info(
			f'  corpus filter: {len(post_start)}/{before} intervals kept '
			f'(left_frames: {sorted(i.left_seed["frame_index"] for i in post_start)})'
		)
	else:
		# Default cap is 4; a value <= 0 is the explicit opt-in to ALL intervals.
		cap = None if (max_intervals is not None and max_intervals <= 0) else max_intervals
		post_start = walk_util._evenly_spread(post_start, cap)

	counters = {'walked': 0, 'skipped_resume': 0, 'errors': 0}
	video_output_dir = output_root / video_basename
	# WS2-C: accumulate per-tile render manifests across all intervals for this
	# video; written once to render_manifest.json below (machine-checkable gate
	# consumed by check_render_manifest.py).
	video_manifest_records = []

	for idx, interval in enumerate(post_start):
		# interval.left_seed/right_seed are SOURCE-pixel dicts (used only for
		# frame-index bookkeeping). Walk inputs are the PROCESSED seed dicts
		# looked up by frame_index, identical in space to walk_driver.main.
		left_frame = interval.left_seed['frame_index']
		right_frame = interval.right_seed['frame_index']
		left_seed = proc_seed_by_frame[left_frame]
		right_seed = proc_seed_by_frame[right_frame]
		interval_dir = video_output_dir / f'seed_{left_frame}_{right_frame}'

		if walk_driver.check_resume_needed(interval_dir, resume):
			logger.info(f'  Skip (resume): interval [{left_frame}, {right_frame}]')
			counters['skipped_resume'] += 1
			continue

		logger.info(f'  [{idx + 1}/{len(post_start)}] Interval [{left_frame}, {right_frame}]')
		try:
			_summary, _fp_key, _solved_entry, fwd_manifests, bwd_manifests = (
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
					heat_movie=heat_movie,
				)
			)
			# WS2-C: tag each per-tile manifest with its interval and accumulate.
			for manifest in fwd_manifests + bwd_manifests:
				manifest['interval_left_frame'] = left_frame
				manifest['interval_right_frame'] = right_frame
				video_manifest_records.append(manifest)
			counters['walked'] += 1
		except Exception as e:
			logger.error(f'  Exception in interval [{left_frame}, {right_frame}]: {e}')
			counters['errors'] += 1

	reader.close()

	# WS2-C: write the machine-checkable render manifest for this video. One JSON
	# record per rendered tile; consumed by check_render_manifest.py which fails
	# the gate if any non-seed frame lacks a solved box or any tile has
	# conversion_count != 1 (catches the "magenta + only" symptom).
	if video_manifest_records:
		video_output_dir.mkdir(parents=True, exist_ok=True)
		manifest_path = video_output_dir / 'render_manifest.json'
		with open(manifest_path, 'w') as f:
			json.dump(video_manifest_records, f, indent=2)
		logger.info(
			f'  Wrote render manifest: {manifest_path} '
			f'({len(video_manifest_records)} tile records)'
		)

	return counters


#============================================

def _video_worker(payload: tuple) -> dict:
	"""Pool worker entry point."""
	(video_basename, output_root_str, max_intervals, resume, render_tiles,
		corpus_left_frames, heat_movie) = payload
	return process_video(
		video_basename=video_basename,
		output_root=pathlib.Path(output_root_str),
		max_intervals=max_intervals,
		resume=resume,
		render_tiles=render_tiles,
		corpus_left_frames=corpus_left_frames,
		heat_movie=heat_movie,
	)


#============================================

def main() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s - %(levelname)s - %(message)s',
	)
	args = parse_args()

	# Fail early when --heat-movie is requested but ffmpeg is absent.
	# Import heat_movie_encode lazily so the normal walk path never imports it.
	if args.heat_movie:
		import heat_movie_encode
		heat_movie_encode.check_ffmpeg_available()

	# Load corpus interval filter if --intervals-from-corpus is set.
	# Maps video_basename -> frozenset of left_frame ints.
	corpus_intervals: dict | None = None
	if args.intervals_from_corpus:
		corpus_dir = pathlib.Path(args.intervals_from_corpus)
		corpus_intervals = load_corpus_intervals(corpus_dir)
		logger.info(
			f'phase 0: corpus filter loaded for {len(corpus_intervals)} videos '
			f'from {corpus_dir}'
		)

	# Phase 0: discover or accept explicit list, then filter, sort, cap.
	if args.videos:
		discovered = list(args.videos)
		logger.info(f'phase 0: using {len(discovered)} explicit videos from --videos')
	elif corpus_intervals is not None:
		# Corpus mode: use exactly the videos named in the corpus directory.
		discovered = sorted(corpus_intervals.keys())
		logger.info(f'phase 0: corpus mode: {len(discovered)} videos from corpus')
	else:
		discovered = discover_videos()
		logger.info(f'phase 0: discovered {len(discovered)} candidate videos')

	if args.outdoor_400m_only:
		before = len(discovered)
		discovered = filter_outdoor_400m(discovered)
		logger.info(f'phase 0: outdoor 400m filter kept {len(discovered)}/{before}')

	if not discovered:
		print('No videos selected. Aborting.')
		sys.exit(1)

	videos = select_videos(discovered, args.max_videos, args.sort_by_size)

	if args.dry_run:
		print_dry_run(videos, args)
		return

	print(f'phase 0: {len(videos)} videos selected (from {len(discovered)} discovered)')
	for v in videos:
		print(f'  {v}')

	output_root = pathlib.Path(args.output_root)
	output_root.mkdir(parents=True, exist_ok=True)

	# Phases 1+2: walker batch and tile render are fused under the v2 driver
	# helper (the walker always writes its CSV; render_tiles toggles PNGs).
	run_walker = args.walk
	render_tiles = not args.skip_render

	if run_walker:
		logger.info(
			f'phase 1+2: walker batch (workers={args.workers}, '
			f'resume={args.resume}, render_tiles={render_tiles})'
		)
		payloads = [
			(
				v,
				str(output_root),
				args.sample_intervals_per_video,
				args.resume,
				render_tiles,
				# Pass per-video corpus set, or None if not in corpus mode.
				corpus_intervals[v] if (corpus_intervals is not None and v in corpus_intervals) else None,
				args.heat_movie,
			)
			for v in videos
		]
		if args.workers > 1 and len(payloads) > 1:
			with multiprocessing.Pool(processes=args.workers) as pool:
				results = pool.map(_video_worker, payloads)
		else:
			results = [_video_worker(p) for p in payloads]
		total_walked = sum(r['walked'] for r in results)
		total_skipped = sum(r['skipped_resume'] for r in results)
		total_errors = sum(r['errors'] for r in results)
		print(
			f'phase 1+2: walked={total_walked}, '
			f'skipped(resume)={total_skipped}, errors={total_errors}'
		)
	else:
		print('phase 1+2: SKIPPED (no --walk; rebuilding walk.html only)')

	# Phase 3: build walk.html from on-disk artifacts.
	print('phase 3: building walk.html...')
	html_path = walk_html.build_walk_html(output_root)
	print(f'wrote {html_path}')


#============================================

if __name__ == '__main__':
	main()
