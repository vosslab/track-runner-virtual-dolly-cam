"""
config.py

Configuration loading, validation, and default schema for the track_runner tool.
Seeds and diagnostics are handled separately in state_io.py.
"""

# Standard Library
import os

# PIP3 modules
import yaml

# local repo modules
import tr_paths

#============================================

TOOL_CONFIG_HEADER_KEY = "track_runner"
TOOL_CONFIG_HEADER_VALUE = 3

#============================================

def read_default_config() -> dict:
	"""
	Read the global default config from track_runner.config.yaml.

	The file lives alongside this module in emwy_tools/track_runner/.

	Returns:
		dict: Parsed and validated configuration dictionary.

	Raises:
		RuntimeError: If the default config file is missing or invalid.
	"""
	module_dir = os.path.dirname(os.path.abspath(__file__))
	default_path = os.path.join(module_dir, "track_runner.config.yaml")
	config = load_config(default_path)
	return config

#============================================

def _get_default_camera_config() -> dict:
	"""
	Get default camera configuration section.

	Returns:
		dict: Default camera config with all required keys.
	"""
	return {
		"zoom_levels": [1],
	}

#============================================

def _validate_processing(config: dict) -> None:
	"""Validate keys inside the processing section.

	Enforces the torso_height_multiple contract (>= 1).
	Other processing keys are read defensively by the code and do not
	need schema-level validation here.

	Args:
		config: Full configuration dictionary.

	Raises:
		RuntimeError: If a processing key violates the contract.
	"""
	processing = config["processing"]
	# torso_height_multiple is required on current configs.
	if "torso_height_multiple" not in processing:
		raise RuntimeError(
			"config missing processing.torso_height_multiple "
			"(set crop height as a multiple of tracked torso height, "
			"e.g. 8)"
		)
	multiple = float(processing["torso_height_multiple"])
	# contract: bigger number = wider view; must be at least 1 so the
	# crop never shrinks below the torso box
	if multiple < 1.0:
		raise RuntimeError(
			f"torso_height_multiple must be >= 1 (got {multiple}). "
			"Crop height = this * tracked torso height; values < 1 would "
			"crop smaller than the torso itself."
		)


#============================================

def validate_config(config: dict) -> None:
	"""
	Validate that required keys are present in the config.

	Args:
		config: Configuration dictionary to validate.

	Raises:
		RuntimeError: If required keys are missing or the header is wrong.
	"""
	# check header key
	if TOOL_CONFIG_HEADER_KEY not in config:
		raise RuntimeError(
			f"config missing required header key: {TOOL_CONFIG_HEADER_KEY}"
		)
	header_value = config[TOOL_CONFIG_HEADER_KEY]
	if header_value != TOOL_CONFIG_HEADER_VALUE:
		raise RuntimeError(
			f"config header value mismatch: expected {TOOL_CONFIG_HEADER_VALUE}, "
			f"got {header_value}"
		)
	# processing is the sole required current configuration section.
	required_sections = ["processing"]
	for section in required_sections:
		if section not in config:
			raise RuntimeError(f"config missing required key: {section}")
	# camera section is optional; fill with defaults if missing
	if "camera" not in config:
		config["camera"] = _get_default_camera_config()
	# validate processing keys (torso_height_multiple contract)
	_validate_processing(config)

#============================================

def load_config(path: str) -> dict:
	"""
	Read a YAML config file and validate the header.

	Args:
		path: Path to the YAML config file.
	Returns:
		dict: Parsed and validated configuration.

	Raises:
		RuntimeError: If the file cannot be read or header is missing.
	"""
	if not os.path.isfile(path):
		raise RuntimeError(f"config file not found: {path}")
	with open(path, "r") as fh:
		config = yaml.safe_load(fh)
	if not isinstance(config, dict):
		raise RuntimeError(f"config file did not parse as a mapping: {path}")
	# check header key exists
	if TOOL_CONFIG_HEADER_KEY not in config:
		raise RuntimeError(
			f"config missing required header key: "
			f"{TOOL_CONFIG_HEADER_KEY} in {path}"
		)
	validate_config(config)
	return config

#============================================

def resolve_config(input_file: str, config_path: str | None = None) -> tuple:
	"""Resolve the config for a video: per-video file if present, else default.

	Centralizes the resolution previously inlined at the cli call site: when a
	per-video config file exists it is validated as the current configuration;
	otherwise the read-only built-in default is returned.

	Args:
		input_file: Input video path; used to derive the default per-video
			config path when config_path is not supplied.
		config_path: Optional explicit config path (the cli honors an
			--config-file override here). None derives the default per-video
			path from input_file via tr_paths.default_config_path.

	Returns:
		Tuple (config_dict, had_config_file): the selected current config and a
		bool that is True when a per-video config file existed on disk. Both
		load_config() and read_default_config() validate before returning.
	"""
	# Default per-video path unless the caller supplied an explicit override.
	if config_path is None:
		config_path = tr_paths.default_config_path(input_file)
	had_config_file = os.path.isfile(config_path)
	if had_config_file:
		config = load_config(config_path)
	else:
		config = read_default_config()
	return config, had_config_file


#============================================

def write_config(path: str, config: dict) -> None:
	"""
	Write a config dictionary to a YAML file.

	Args:
		path: Output file path.
		config: Configuration dictionary to write.
	"""
	config[TOOL_CONFIG_HEADER_KEY] = TOOL_CONFIG_HEADER_VALUE
	with open(path, "w") as fh:
		yaml.dump(config, fh, default_flow_style=False, sort_keys=False)
