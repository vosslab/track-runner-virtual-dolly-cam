"""Interactive CLI questionnaire for per-video motion-estimator configuration."""

# Standard Library
import os

# local repo modules
import tr_config


#============================================
def run_setup(config_path: str, config: dict) -> dict:
	"""Run interactive CLI setup questionnaire for camera motion.

	Prompts the user for camera settings and stores the results in the
	configuration. The config is written back to config_path.

	Args:
		config_path: Path to save the updated config file.
		config: Current configuration dictionary (may be modified).

	Returns:
		Updated configuration dictionary with camera-motion settings.
	"""
	print("=== Track Runner Setup ===")
	print()

	# 1. Motion-estimator selection
	print("Camera motion estimator:")
	print("  [1] iPhone discrete zoom")
	print("  [2] Fixed zoom")
	print("  [3] Continuous zoom")
	zoom_choice = input("Select zoom type (1-3): ").strip()
	zoom_choice = zoom_choice or "2"  # default to fixed zoom

	zoom_map = {
		"1": ("discrete", "iphone_discrete"),
		"2": ("fixed", "fixed"),
		"3": ("continuous", "continuous"),
	}

	if zoom_choice not in zoom_map:
		print(f"  invalid choice: {zoom_choice}, defaulting to fixed zoom")
		zoom_choice = "2"

	estimator_kind, zoom_estimator = zoom_map[zoom_choice]

	zoom_levels = [1]
	if estimator_kind == "discrete":
		# 3. Ask for zoom levels if discrete zoom
		print()
		zoom_input = input(
			"Zoom levels (comma-separated, default '1,2,5'): "
		).strip()
		if zoom_input:
			try:
				zoom_levels = [int(z.strip()) for z in zoom_input.split(",")]
			except ValueError:
				print("  invalid zoom levels, using default [1, 2, 5]")
				zoom_levels = [1, 2, 5]
		else:
			zoom_levels = [1, 2, 5]

	# Store only the values consumed by camera-motion precomputation.
	config["camera"] = {"zoom_levels": zoom_levels}
	config.pop("detection", None)

	# Set motion estimator type for camera_motion precomputation.
	config.setdefault("motion", {})
	config.setdefault("motion", {}).setdefault("estimator", {})
	config["motion"]["estimator"]["type"] = zoom_estimator

	# Write config to config_path.
	tr_config.write_config(config_path, config)

	# Print success message.
	print()
	print(f"Setup saved to {os.path.abspath(config_path)}")
	print()

	return config
