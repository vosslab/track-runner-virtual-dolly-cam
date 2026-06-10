"""I/O glue layer for the v2 walker.

Loads seeds, opens frame reader, builds scene transform, and enumerates
which seed-to-seed intervals are walkable. Pure orchestration; no compute.
"""

# Standard Library
import json
import subprocess
import dataclasses
import pathlib

# local repo modules
import state_io
import common_tools.probe_video
import common_tools.frame_reader
import camera_motion
import scene_coords


# Repo root, used to locate tr_config/ and TRACK_VIDEOS/. Resolved once via
# git per docs/REPO_STYLE.md (do not derive paths from the current working
# directory). This replaces the former walk_paths.setup() bootstrap.
_REPO_ROOT = subprocess.run(
	["git", "rev-parse", "--show-toplevel"],
	capture_output=True,
	text=True,
	check=True,
).stdout.strip()


#============================================
def _normalize_video_basename(video_basename: str) -> str:
	"""Normalize video basename to the base name without .mkv or .track_runner.

	Examples:
		"video.mkv" -> "video"
		"video.track_runner" -> "video"
		"video.mkv.track_runner" -> "video"
		"video" -> "video"
	"""
	name = video_basename
	if name.endswith('.mkv'):
		name = name[:-4]
	if name.endswith('.track_runner'):
		name = name[:-13]
	return name


#============================================
def load_walker_seeds(video_basename: str) -> dict:
	"""Load seeds for walker from tr_config.

	Returns seeds in source-pixel coords (legacy API).  For the walker
	pipeline under auto-bin use load_walker_seeds_view(video_basename, geometry)
	which returns a SeedsView with processed-pixel coords.

	Args:
		video_basename: Video base name (e.g. '2025-Glenbrook_South-1600m-IMG_1503',
			or '2025-Glenbrook_South-1600m-IMG_1503.mkv', or with .track_runner suffix).

	Returns:
		dict: Loaded seeds with state_io schema. Keys are 'track_runner_seeds'
			(header value) and 'seeds' (list of seed dicts with derived cx/cy/w/h
			in SOURCE-pixel coords).
	"""
	base_name = _normalize_video_basename(video_basename)
	seeds_path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{base_name}.track_runner.seeds.json"
	return state_io.load_seeds(str(seeds_path))


#============================================
def load_walker_seeds_view(
	video_basename: str,
	geometry: object,
) -> state_io.SeedsView:
	"""Load seeds for walker and return a SeedsView in PROCESSED-pixel coords.

	Uses load_walker_seeds internally (source-pixel) and wraps the result in a
	SeedsView keyed to the supplied geometry.  The view's assert_geometry_match
	method guards against use with a mismatched reader geometry.

	Args:
		video_basename: Video base name (accepts .mkv or .track_runner suffixes).
		geometry: FrameGeometry from the walker's FrameReader (reader.geometry).
			Must have source_to_processed, source_to_processed_delta, bin_factor.

	Returns:
		SeedsView exposing processed-pixel coords via view.seeds.
	"""
	base_name = _normalize_video_basename(video_basename)
	seeds_path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{base_name}.track_runner.seeds.json"
	return state_io.load_seeds_view(str(seeds_path), geometry)


#============================================
def open_walker_reader(video_basename: str) -> tuple:
	"""Open frame reader for walker from TRACK_VIDEOS.

	Args:
		video_basename: Video filename (e.g. '2025-Glenbrook_South-1600m-IMG_1503.mkv',
			or base name '2025-Glenbrook_South-1600m-IMG_1503').

	Returns:
		tuple: (FrameReader, probe_info_dict). Probe info includes width, height,
			fps, frame_count, duration_s.

	Raises:
		RuntimeError: If the video file is not found or probe fails.
	"""
	# Try with .mkv extension, or accept as-is if it already has an extension
	video_path = pathlib.Path(_REPO_ROOT) / "TRACK_VIDEOS" / video_basename
	if not video_path.is_file():
		# Try appending .mkv
		video_path_mkv = pathlib.Path(_REPO_ROOT) / "TRACK_VIDEOS" / f"{video_basename}.mkv"
		if video_path_mkv.is_file():
			video_path = video_path_mkv
		else:
			raise RuntimeError(f"Video not found: {video_path} or {video_path_mkv}")
	probe_info = common_tools.probe_video.probe_video(str(video_path))
	# Auto-bin re-enabled after fix: warp scale_factor and override-args source-scale
	# corrected in residual_motion.py (task #99 / auto_bin_coord_stack_audit.md).
	source_width = probe_info["width"]
	bin_factor = common_tools.frame_reader.select_bin_factor_for_analysis(source_width)
	reader = common_tools.frame_reader.FrameReader(
		video_path=str(video_path),
		fps=probe_info["fps"],
		total_frames=probe_info["frame_count"],
		bin_factor=bin_factor,
	)
	return reader, probe_info


#============================================
def load_walker_scene_transform(video_basename: str) -> scene_coords.SceneTransform:
	"""Load SceneTransform for walker from camera_motion.npz.

	Args:
		video_basename: Video base name (e.g. '2025-Glenbrook_South-1600m-IMG_1503',
			or with .mkv suffix, or with .track_runner suffix).

	Returns:
		SceneTransform: Built from the canonical camera_motion.npz artifact.

	Raises:
		RuntimeError: If the motion cache is missing or stale.
	"""
	base_name = _normalize_video_basename(video_basename)
	motion_cache_path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{base_name}.track_runner.camera_motion.npz"
	motion_track = camera_motion.load_motion_cache(str(motion_cache_path))
	if motion_track is None:
		raise RuntimeError(
			f"Camera motion cache not found: {motion_cache_path}. "
			f"Run solve first."
		)
	return scene_coords.SceneTransform(motion_track)


#============================================
def load_race_start_frame(video_basename: str) -> int:
	"""Load race_start_frame for walker from interval_scores.json.

	The race_start_frame is the end_frame of the last interval with
	confidence_tier == 'pre_race'. All intervals before this frame are
	pre-race (stationary).

	Args:
		video_basename: Video base name.

	Returns:
		int: Frame index where the race starts.

	Raises:
		RuntimeError: If the interval scores file is missing.
	"""
	base_name = _normalize_video_basename(video_basename)
	iscores_path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{base_name}.track_runner.interval_scores.json"
	if not iscores_path.exists():
		raise RuntimeError(
			f"Interval scores file not found: {iscores_path}. "
			f"Run solve first."
		)
	with open(iscores_path) as f:
		iscores = json.load(f)

	# Find the last interval with confidence_tier == 'pre_race'
	race_start = 0
	if 'intervals' in iscores:
		for interval in iscores['intervals']:
			interval_score = interval.get('interval_score', {})
			if interval_score.get('confidence_tier') == 'pre_race':
				race_start = interval.get('end_frame', 0)

	return race_start


#============================================
@dataclasses.dataclass
class SeedToSeedInterval:
	"""A seed-to-seed interval with race-phase classification."""
	left_seed: dict
	right_seed: dict
	label: str  # one of {"post_start", "crossing_race_start", "pre_start"}


#============================================
def enumerate_seed_to_seed_intervals(
	seeds_dict: dict,
	race_start_frame: int,
) -> list:
	"""Enumerate consecutive seed-pair intervals with race-phase labels.

	Args:
		seeds_dict: Loaded seeds dict from load_walker_seeds (or state_io.load_seeds).
		race_start_frame: Frame index where the race begins.

	Returns:
		list: List of SeedToSeedInterval dicts (or named tuples), one per
			consecutive seed pair. Each interval has left_seed, right_seed,
			and label (post_start, crossing_race_start, or pre_start).
	"""
	seeds = seeds_dict["seeds"]
	intervals = []
	for i in range(len(seeds) - 1):
		left_seed = seeds[i]
		right_seed = seeds[i + 1]
		left_frame = left_seed["frame_index"]
		right_frame = right_seed["frame_index"]
		# classify by race-start position
		if left_frame > race_start_frame and right_frame > race_start_frame:
			label = "post_start"
		elif left_frame <= race_start_frame and right_frame > race_start_frame:
			label = "crossing_race_start"
		else:
			# right_frame <= race_start_frame
			label = "pre_start"
		interval = SeedToSeedInterval(
			left_seed=left_seed,
			right_seed=right_seed,
			label=label,
		)
		intervals.append(interval)
	return intervals
