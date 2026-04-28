"""tr_paths.py

Centralized path construction for track_runner config and state files.

By default, config and state files live in a ./tr_config/ subdirectory
under the current working directory. Encoded output stays next to the
source video file. The tr_config/ directory is auto-created as a real
directory on first run; users can replace it with a symlink to any
location (network drive, NAS mount, etc.).
"""

# Standard Library
import os

#============================================

# subdirectory for config and state files relative to cwd
DATA_DIR = "tr_config"

#============================================

def ensure_data_dir() -> str:
	"""Create the tr_config data directory if it does not exist.

	Returns:
		str: Absolute path to the data directory.
	"""
	data_path = os.path.abspath(DATA_DIR)
	os.makedirs(data_path, exist_ok=True)
	return data_path

#============================================

def ensure_parent_dir(path: str) -> None:
	"""Create the parent directory of path if it does not exist.

	Args:
		path: File path whose parent directory should exist.
	"""
	parent = os.path.dirname(os.path.abspath(path))
	os.makedirs(parent, exist_ok=True)

#============================================

def _data_file_path(input_file: str, suffix: str) -> str:
	"""Build a data file path inside DATA_DIR from the input basename.

	Args:
		input_file: Full path to the input video file.
		suffix: File suffix to append (e.g. '.track_runner.seeds.json').

	Returns:
		str: Path like tr_config/{basename}{suffix}.
	"""
	basename = os.path.basename(input_file)
	filename = f"{basename}{suffix}"
	data_path = os.path.join(DATA_DIR, filename)
	return data_path

#============================================

def default_config_path(input_file: str) -> str:
	"""Return the default config YAML path for a given input file.

	Args:
		input_file: Input media file path.

	Returns:
		str: Config file path inside tr_config/.
	"""
	config_path = _data_file_path(input_file, ".track_runner.config.yaml")
	return config_path

#============================================

def default_seeds_path(input_file: str) -> str:
	"""Return the default seeds JSON file path for a given input file.

	Args:
		input_file: Input media file path.

	Returns:
		str: Seeds JSON file path inside tr_config/.
	"""
	seeds_path = _data_file_path(input_file, ".track_runner.seeds.json")
	return seeds_path

#============================================

def default_diagnostics_path(input_file: str) -> str:
	"""Return the default interval-scores JSON file path for a given input file.

	File extension renamed from `.diagnostics.json` to
	`.interval_scores.json` to match the file's sole responsibility
	(per-interval scoring summary). Function name retained for
	callsite compatibility; callers continue to refer to this as the
	"diagnostics path" but the on-disk filename is the new one.

	Args:
		input_file: Input media file path.

	Returns:
		str: Interval-scores JSON file path inside tr_config/.
	"""
	scores_path = _data_file_path(input_file, ".track_runner.interval_scores.json")
	return scores_path

#============================================

def default_intervals_path(input_file: str) -> str:
	"""Return the default torso_box_coords NPZ file path for a given input file.

	Per schema v8, unified artifact containing all per-frame torso boxes
	(forward, backward, blended interval paths) from stage 1-5 solve.
	Replaces separate geometry_cache.npz and debug_paths.npz files.
	Function name retained for callsite compatibility; the returned path
	points at the new unified NPZ file.

	Args:
		input_file: Input media file path.

	Returns:
		str: Torso-box-coords NPZ file path inside tr_config/.
	"""
	coords_path = _data_file_path(input_file, ".track_runner.torso_box_coords.npz")
	return coords_path

#============================================

def default_torso_box_coords_path(input_file: str) -> str:
	"""Return the default torso_box_coords NPZ file path for a given input file.

	Unified artifact containing all per-frame torso boxes (forward, backward,
	blended interval paths) from stage 1-5 solve. Replaces separate
	geometry_cache.npz and debug_paths.npz files as of schema v8.

	Args:
		input_file: Input media file path.

	Returns:
		str: Torso-box-coords NPZ file path inside tr_config/.
	"""
	coords_path = _data_file_path(input_file, ".track_runner.torso_box_coords.npz")
	return coords_path

#============================================

def default_encode_analysis_path(input_file: str) -> str:
	"""Return the default encode analysis YAML path for a given input file.

	Args:
		input_file: Input media file path.

	Returns:
		str: Analysis YAML file path inside tr_config/.
	"""
	analysis_path = _data_file_path(input_file, ".encode_analysis.yaml")
	return analysis_path

#============================================

def default_output_path(input_file: str) -> str:
	"""Return the default encoded output path next to the source video.

	Output stays in the same directory as the input file to keep
	large encoded files on fast local storage. The extension is forced
	to lowercase '.mkv' regardless of the input extension: the encoder
	always writes Matroska bytes via mkvmerge, and MKV's index-based
	seeking is more reliable than MP4/MOV moov-atom seeking. MP4 export
	is opt-in via --mp4 / -o foo.mp4.

	Args:
		input_file: Input media file path.

	Returns:
		str: Output file path like {input_dir}/{stem}_tracked.mkv.
	"""
	stem, _ = os.path.splitext(input_file)
	output_path = f"{stem}_tracked.mkv"
	return output_path

#============================================

def default_camera_motion_path(input_file: str) -> str:
	"""Return the default camera motion cache path for a given input file.

	Args:
		input_file: Input media file path.

	Returns:
		str: Camera motion cache NPZ file path inside tr_config/.
	"""
	motion_path = _data_file_path(input_file, ".track_runner.camera_motion.npz")
	return motion_path

#============================================

def default_race_start_contact_sheet_path(input_file: str) -> str:
	"""Return the default race-start contact sheet PNG path for a given input file.

	The contact sheet is a visual confirmation artifact written alongside the
	source video (not in tr_config) to match the encoded output location
	convention.

	Args:
		input_file: Input media file path.

	Returns:
		str: Contact sheet PNG path like {stem}.track_runner.race_start_check.png.
	"""
	stem, ext = os.path.splitext(input_file)
	contact_sheet_path = f"{stem}.track_runner.race_start_check.png"
	return contact_sheet_path
