"""Interactive CLI questionnaire for per-video camera configuration.

Provides a simple command-line interface for users to specify camera settings
specific to each video, including zoom type, camera height, position, and
track size. Settings are stored in the video-specific config file.
"""

# Standard Library
import os

# local repo modules
import tr_config


#============================================
def run_setup(config_path: str, config: dict) -> dict:
	"""Run interactive CLI setup questionnaire for camera configuration.

	Prompts the user for camera settings and stores the results in the
	configuration. The config is written back to config_path.

	Args:
		config_path: Path to save the updated config file.
		config: Current configuration dictionary (may be modified).

	Returns:
		Updated configuration dictionary with camera settings.
	"""
	print("=== Track Runner Setup ===")
	print()

	# 1. Zoom type selection
	print("Zoom type:")
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

	zoom_type, zoom_estimator = zoom_map[zoom_choice]

	zoom_levels = [1]
	if zoom_type == "discrete":
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

	# 4. Camera height selection
	print()
	print("Camera height:")
	print("  [1] Elevated (above track)")
	print("  [2] Track level")
	height_choice = input("Select camera height (1-2): ").strip()
	height_choice = height_choice or "1"  # default to elevated

	height_map = {
		"1": "elevated",
		"2": "track_level",
	}

	if height_choice not in height_map:
		print(f"  invalid choice: {height_choice}, defaulting to elevated")
		height_choice = "1"

	camera_height = height_map[height_choice]

	# 5. Camera position selection
	print()
	print("Camera position:")
	print("  [1] Center/short end of track")
	print("  [2] Side/long end of track")
	position_choice = input("Select camera position (1-2): ").strip()
	position_choice = position_choice or "1"  # default to center

	position_map = {
		"1": "center",
		"2": "side",
	}

	if position_choice not in position_map:
		print(f"  invalid choice: {position_choice}, defaulting to center")
		position_choice = "1"

	camera_position = position_map[position_choice]

	# 6. Track size selection
	print()
	print("Track size:")
	print("  [1] 160m indoor")
	print("  [2] 200m indoor")
	print("  [3] 400m outdoor")
	size_choice = input("Select track size (1-3): ").strip()
	size_choice = size_choice or "3"  # default to 400m outdoor

	size_map = {
		"1": 160,
		"2": 200,
		"3": 400,
	}

	if size_choice not in size_map:
		print(f"  invalid choice: {size_choice}, defaulting to 400m outdoor")
		size_choice = "3"

	track_size = size_map[size_choice]

	# 7. Store answers in config["camera"] section
	config.setdefault("camera", {})
	config["camera"]["zoom_type"] = zoom_type
	config["camera"]["zoom_levels"] = zoom_levels
	config["camera"]["camera_height"] = camera_height
	config["camera"]["camera_position"] = camera_position
	config["camera"]["track_size"] = track_size

	# Set motion estimator type for camera_motion precomputation
	config.setdefault("motion", {})
	config.setdefault("motion", {}).setdefault("estimator", {})
	config["motion"]["estimator"]["type"] = zoom_estimator

	# 8. Write config to config_path
	tr_config.write_config(config_path, config)

	# 9. Print success message
	print()
	print(f"Setup saved to {os.path.abspath(config_path)}")
	print()

	# 10. Return updated config
	return config
