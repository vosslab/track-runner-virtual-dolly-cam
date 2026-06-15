"""Repo-root setup glue for the standalone blob_walk_v2 HTML tool.

This module is tool glue, not core. It maps a bare video basename to the
canonical tr_config/ and TRACK_VIDEOS/ artifacts under the repo root, then
routes each load to its CORE owner:

	- seeds            -> state_io.load_seeds / state_io.load_seeds_view
	- frame reader     -> common_tools.frame_reader.open_analysis_reader
	- scene transform  -> camera_motion.load_motion_cache + scene_coords
	- race-start frame -> state_io.load_diagnostics (interval_scores.json)

Interval enumeration lives in the core race-semantics layer
(race_phases.enumerate_seed_to_seed_intervals); import it directly.

The tool is video-agnostic and takes a basename, so paths are resolved
under the git repo root (per docs/REPO_STYLE.md, resolved once via git),
not under the current working directory the way tr_paths does for the
production CLI.
"""

# Standard Library
import pathlib
import subprocess

# local repo modules
import state_io
import common_tools.probe_video
import common_tools.frame_reader
import camera_motion
import scene_coords


# Repo root, used to locate tr_config/ and TRACK_VIDEOS/. Resolved once via
# git per docs/REPO_STYLE.md (do not derive paths from the current working
# directory).
_REPO_ROOT = subprocess.run(
	["git", "rev-parse", "--show-toplevel"],
	capture_output=True,
	text=True,
	check=True,
).stdout.strip()


#============================================
def normalize_video_basename(video_basename: str) -> str:
	"""Normalize a video basename to the bare stem used in tr_config filenames.

	Strips a trailing .mkv container extension and a trailing .track_runner
	suffix so callers can pass any of the equivalent forms below.

	Examples:
		"video.mkv" -> "video"
		"video.track_runner" -> "video"
		"video.mkv.track_runner" -> "video"
		"video" -> "video"

	Args:
		video_basename: Video base name in any accepted form.

	Returns:
		str: Bare stem without .mkv or .track_runner.
	"""
	name = video_basename
	if name.endswith('.mkv'):
		name = name[:-4]
	if name.endswith('.track_runner'):
		name = name[:-13]
	return name


#============================================
def _tr_config_path(base_name: str, suffix: str) -> str:
	"""Build a tr_config artifact path under the repo root.

	Args:
		base_name: Normalized video stem (from normalize_video_basename).
		suffix: Filename suffix (e.g. '.track_runner.seeds.json').

	Returns:
		str: Absolute path like {repo_root}/tr_config/{base_name}{suffix}.
	"""
	path = pathlib.Path(_REPO_ROOT) / "tr_config" / f"{base_name}{suffix}"
	return str(path)


#============================================
def load_seeds(video_basename: str) -> dict:
	"""Load seeds for the tool in SOURCE-pixel coords via state_io.

	Args:
		video_basename: Video base name (accepts .mkv or .track_runner suffixes).

	Returns:
		dict: Seeds dict with state_io schema (header value plus a "seeds" list
			of seed dicts with derived cx/cy/w/h in SOURCE-pixel coords).
	"""
	base_name = normalize_video_basename(video_basename)
	seeds_path = _tr_config_path(base_name, ".track_runner.seeds.json")
	return state_io.load_seeds(seeds_path)


#============================================
def load_seeds_view(video_basename: str, geometry: object) -> state_io.SeedsView:
	"""Load seeds and wrap them in a SeedsView in PROCESSED-pixel coords.

	Args:
		video_basename: Video base name (accepts .mkv or .track_runner suffixes).
		geometry: FrameGeometry from the tool's FrameReader (reader.geometry).

	Returns:
		SeedsView exposing processed-pixel coords via view.seeds and the
			original source-pixel seeds dict via view.source.
	"""
	base_name = normalize_video_basename(video_basename)
	seeds_path = _tr_config_path(base_name, ".track_runner.seeds.json")
	return state_io.load_seeds_view(seeds_path, geometry)


#============================================
def open_reader(video_basename: str) -> tuple["common_tools.frame_reader.FrameReader", dict]:
	"""Open a frame reader for the tool from TRACK_VIDEOS via the shared opener.

	Args:
		video_basename: Video filename or bare base name. A bare base name is
			resolved by appending .mkv under TRACK_VIDEOS.

	Returns:
		tuple: (FrameReader, probe_info_dict). probe_info carries width, height,
			fps, frame_count, duration_s.

	Raises:
		RuntimeError: If no matching video file is found under TRACK_VIDEOS.
	"""
	video_path = pathlib.Path(_REPO_ROOT) / "TRACK_VIDEOS" / video_basename
	if not video_path.is_file():
		video_path_mkv = pathlib.Path(_REPO_ROOT) / "TRACK_VIDEOS" / f"{video_basename}.mkv"
		if video_path_mkv.is_file():
			video_path = video_path_mkv
		else:
			raise RuntimeError(f"Video not found: {video_path} or {video_path_mkv}")
	probe_info = common_tools.probe_video.probe_video(str(video_path))
	# Delegate reader construction + default-bin policy to the single shared
	# opener. bin_factor=None means the opener computes the project default via
	# select_default_bin_factor(source_width).
	reader = common_tools.frame_reader.open_analysis_reader(
		video_path=str(video_path),
		fps=probe_info["fps"],
		total_frames=probe_info["frame_count"],
		source_width=probe_info["width"],
		bin_factor=None,
	)
	return reader, probe_info


#============================================
def load_scene_transform(video_basename: str) -> scene_coords.SceneTransform:
	"""Build a SceneTransform from the canonical camera_motion.npz artifact.

	Args:
		video_basename: Video base name (accepts .mkv or .track_runner suffixes).

	Returns:
		SceneTransform: Built from the camera-motion cache.

	Raises:
		RuntimeError: If the motion cache is missing or stale.
	"""
	base_name = normalize_video_basename(video_basename)
	motion_cache_path = _tr_config_path(base_name, ".track_runner.camera_motion.npz")
	motion_track = camera_motion.load_motion_cache(motion_cache_path)
	if motion_track is None:
		raise RuntimeError(
			f"Camera motion cache not found: {motion_cache_path}. Run solve first."
		)
	return scene_coords.SceneTransform(motion_track)


#============================================
def load_race_start_frame(video_basename: str) -> int:
	"""Load race_start_frame from the authoritative interval_scores artifact.

	The authoritative value is stored by state_io via
	race_start.pick_race_start_frame_midpoint into
	pre_race_reference["race_start_frame"] inside the interval_scores.json
	artifact. state_io.load_diagnostics is the sole owner of that file.

	Args:
		video_basename: Video base name (accepts .mkv or .track_runner suffixes).

	Returns:
		int: Frame index where the race starts.

	Raises:
		RuntimeError: If the interval_scores artifact is missing, if
			pre_race_reference is None (legacy file -- re-solve required), or if
			the race_start_frame key is absent. The artifact path is named in
			every error message. Never returns a silent zero.
	"""
	base_name = normalize_video_basename(video_basename)
	iscores_path = _tr_config_path(base_name, ".track_runner.interval_scores.json")
	# load_diagnostics synthesizes an empty structure when the file is missing;
	# detect that here and raise loudly rather than returning 0.
	if not pathlib.Path(iscores_path).exists():
		raise RuntimeError(
			f"Interval scores artifact not found: {iscores_path}. "
			f"Run solve first to populate pre_race_reference."
		)
	data = state_io.load_diagnostics(iscores_path)
	# pre_race_reference is None for legacy files written before race-start
	# detection; that is not recoverable without re-solving.
	pre_race_reference = data["pre_race_reference"]
	if pre_race_reference is None:
		raise RuntimeError(
			f"pre_race_reference is absent in {iscores_path}. "
			f"This is a legacy file written before race-start detection; "
			f"re-solve to populate it."
		)
	if "race_start_frame" not in pre_race_reference:
		raise RuntimeError(
			f"pre_race_reference missing race_start_frame in {iscores_path}; re-run solve"
		)
	race_start_frame = int(pre_race_reference["race_start_frame"])
	return race_start_frame
