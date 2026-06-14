"""
config.py

Configuration loading, validation, and default schema for the track_runner tool (v2).
Seeds and diagnostics are handled separately in state_io.py.
"""

# Standard Library
import copy
import os

# PIP3 modules
import yaml

# local repo modules
import tr_paths

#============================================

TOOL_CONFIG_HEADER_KEY = "track_runner"
TOOL_CONFIG_HEADER_VALUE = 3
# v2 is still accepted for auto-migration at load time
TOOL_CONFIG_HEADER_ALLOWED = (2, 3)

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
		"zoom_type": "fixed",
		"zoom_levels": [1],
		"camera_height": "elevated",
		"camera_position": "side",
		"track_size": 400,
		"venue_type": "outdoor",
		"lighting": "daylight_sunny",
	}

#============================================

def _migrate_crop_fill_ratio(config: dict) -> bool:
	"""Migrate legacy crop_fill_ratio to torso_height_multiple in place.

	Semantic inversion: torso_height_multiple = 1 / crop_fill_ratio.
	Bigger number means wider crop. No-op when the new key is already
	present.

	Args:
		config: Configuration dictionary, mutated in place.

	Returns:
		True if the migration fired (config was modified), False
		otherwise. Callers that own the source file use this to decide
		whether to write the migrated config back to disk.
	"""
	processing = config.get("processing")
	if not isinstance(processing, dict):
		return False
	# new key already present wins; legacy key is discarded silently
	if "torso_height_multiple" in processing:
		if "crop_fill_ratio" in processing:
			processing.pop("crop_fill_ratio", None)
			return True
		return False
	if "crop_fill_ratio" not in processing:
		return False
	old_value = float(processing["crop_fill_ratio"])
	if old_value <= 0.0:
		raise RuntimeError(
			f"legacy crop_fill_ratio must be > 0, got {old_value}"
		)
	new_value = 1.0 / old_value
	processing["torso_height_multiple"] = new_value
	del processing["crop_fill_ratio"]
	return True


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
	# torso_height_multiple is required on v3 configs after migration
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
	# accept current (v3) and legacy (v2) schema values; v2 is migrated
	# in place at load time by _migrate_crop_fill_ratio
	if header_value not in TOOL_CONFIG_HEADER_ALLOWED:
		raise RuntimeError(
			f"config header value mismatch: expected one of "
			f"{TOOL_CONFIG_HEADER_ALLOWED}, got {header_value}"
		)
	# check required top-level sections
	required_sections = ["detection", "processing"]
	for section in required_sections:
		if section not in config:
			raise RuntimeError(f"config missing required key: {section}")
	# camera section is optional; fill with defaults if missing
	if "camera" not in config:
		config["camera"] = _get_default_camera_config()
	# validate processing keys (torso_height_multiple contract)
	_validate_processing(config)

#============================================

def load_config(path: str, auto_save_migration: bool = False) -> dict:
	"""
	Read a YAML config file and validate the header.

	Args:
		path: Path to the YAML config file.
		auto_save_migration: When True, any legacy-key migration that
			fires during load is written back to `path` so future
			runs see the canonical shape. Off by default; callers
			that load the read-only built-in default (via
			`read_default_config`) must not auto-write.

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
	# auto-migrate v2 keys (crop_fill_ratio -> torso_height_multiple)
	# before returning so every caller sees the v3-shaped dict
	migrated = _migrate_crop_fill_ratio(config)
	if migrated and auto_save_migration:
		# persist the v3 shape so the deprecation notice never
		# reappears for this user on this config
		write_config(path, config)
		multiple = config["processing"].get("torso_height_multiple")
		print(
			f"  [config auto-migrated] {path}: "
			f"crop_fill_ratio -> torso_height_multiple={multiple:.3f}"
		)
	return config

#============================================

def resolve_config(input_file: str, config_path: str | None = None) -> tuple:
	"""Resolve the config for a video: per-video file if present, else default.

	Centralizes the resolution previously inlined at the cli call site: when a
	per-video config file exists it is loaded with legacy-key auto-migration;
	otherwise the read-only built-in default is returned. Both the cli site and
	the blob_walk_v2 walk driver call this so the two cannot drift on resolution
	semantics.

	Args:
		input_file: Input video path; used to derive the default per-video
			config path when config_path is not supplied.
		config_path: Optional explicit config path (the cli honors an
			--config-file override here). None derives the default per-video
			path from input_file via tr_paths.default_config_path.

	Returns:
		Tuple (config_dict, had_config_file): the loaded/merged config and a
		bool that is True when a per-video config file existed on disk. The
		caller validates the returned config (validate_config is not called
		here to preserve the existing call ordering at each site).
	"""
	# Default per-video path unless the caller supplied an explicit override.
	if config_path is None:
		config_path = tr_paths.default_config_path(input_file)
	had_config_file = os.path.isfile(config_path)
	if had_config_file:
		# per-video config: auto-save legacy-key migrations so the deprecation
		# notice fires exactly once per file (matches the prior cli behavior).
		config = load_config(config_path, auto_save_migration=True)
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
	# ensure header is present before writing
	if TOOL_CONFIG_HEADER_KEY not in config:
		config[TOOL_CONFIG_HEADER_KEY] = TOOL_CONFIG_HEADER_VALUE
	with open(path, "w") as fh:
		yaml.dump(config, fh, default_flow_style=False, sort_keys=False)

#============================================

def merge_config(base: dict, override: dict) -> dict:
	"""
	Deep merge override into base config.

	Only dict values are merged recursively; scalars and lists
	from override replace the base value.

	Args:
		base: Base configuration dictionary.
		override: Override dictionary with partial values.

	Returns:
		dict: Merged configuration dictionary.
	"""
	result = copy.deepcopy(base)
	for key, value in override.items():
		# recursive merge only for dict-to-dict
		if key in result and isinstance(result[key], dict) and isinstance(value, dict):
			result[key] = merge_config(result[key], value)
		else:
			result[key] = copy.deepcopy(value)
	return result
