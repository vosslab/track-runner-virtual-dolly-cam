"""Race-start confirmation contact sheet renderer.

Generates a visual artifact showing 11 tiles around the detected race-start
frame: five pre-race tiles, the center detection tile, and five post-race
tiles. Each tile displays the detected pre-race reference box and labels for
frame number, offset, and role. The contact sheet is a required output
whenever Stage 2 computes race_start_frame.
"""

# Standard Library
import os

# PIP3 modules
import cv2
import numpy

# local repo modules
import common_tools.frame_reader as frame_reader
import draw_utils
import overlay_config


# Crop size constant: multiplied by max(torso_w, torso_h) to get square crop side.
CROP_SIZE_SCALE = 6

# Color for the "CLAMPED" warning label (red in BGR).
CLAMPED_BGR = (0, 0, 255)

# Font settings for overlay text
FONT_FACE = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.5
FONT_THICKNESS = 1


#============================================
def render_race_start_contact_sheet(
	video_path: str,
	fps: float,
	total_frames: int,
	tiles: list,
	pre_race_reference: dict,
	scene_transform,
	output_path: str,
) -> None:
	"""Render an 11-tile race-start contact sheet and write to PNG.

	Args:
		video_path: Path to the source video file.
		fps: Frame rate (frames per second) for title text.
		total_frames: Total frame count for clamping.
		tiles: List of tile dicts from choose_race_start_confirmation_frames.
		pre_race_reference: Dict from compute_pre_race_reference containing
			race_start_frame, torso_w, torso_h, scene_anchor_x, scene_anchor_y.
		scene_transform: SceneTransform for pixel-to-scene mapping.
		output_path: Output PNG file path.

	Raises:
		RuntimeError: If any frame read fails, write fails, or malformed input.
	"""
	# Validate input
	if not tiles or len(tiles) != 11:
		raise RuntimeError(
			f"expected 11 tiles for contact sheet, got {len(tiles)}"
		)

	# Extract pre_race_reference fields (strict indexing per spec)
	race_start_frame = int(pre_race_reference["race_start_frame"])
	torso_w = float(pre_race_reference["torso_w"])
	torso_h = float(pre_race_reference["torso_h"])
	scene_anchor_x = float(pre_race_reference["scene_anchor_x"])
	scene_anchor_y = float(pre_race_reference["scene_anchor_y"])

	# Create FrameReader using mediainfo-derived metadata
	reader = frame_reader.FrameReader(video_path, fps, total_frames)

	# Read and process all 11 tiles
	processed_tiles = []
	for tile_idx, tile in enumerate(tiles):
		frame_idx = tile["frame_index"]
		offset_s = tile["offset_s"]
		label = tile["label"]
		clamped = tile["clamped"]

		# Read the frame
		frame = reader.read_frame(frame_idx)
		if frame is None:
			raise RuntimeError(
				f"failed to read frame {frame_idx} (offset {offset_s:+.1f}s) "
				f"for tile {tile_idx}; output path: {output_path}"
			)

		# Crop around the pre-race reference box in scene coordinates
		crop_pixels = CROP_SIZE_SCALE * max(torso_w, torso_h)

		# Project scene anchor to pixel coordinates for this frame
		pixel_x, pixel_y = scene_transform.scene_to_pixel(
			frame_idx, scene_anchor_x, scene_anchor_y
		)

		# Crop rectangle (clamped to frame bounds)
		crop_x1 = int(pixel_x - crop_pixels / 2)
		crop_y1 = int(pixel_y - crop_pixels / 2)
		crop_x2 = int(pixel_x + crop_pixels / 2)
		crop_y2 = int(pixel_y + crop_pixels / 2)

		frame_h, frame_w = frame.shape[:2]

		# Store original coordinates before clamping
		orig_x1, orig_y1 = crop_x1, crop_y1
		orig_x2, orig_y2 = crop_x2, crop_y2

		# Clamp to frame bounds
		crop_x1 = max(0, crop_x1)
		crop_y1 = max(0, crop_y1)
		crop_x2 = min(frame_w, crop_x2)
		crop_y2 = min(frame_h, crop_y2)

		# Extract the cropped region
		cropped = frame[crop_y1:crop_y2, crop_x1:crop_x2].copy()

		# Calculate padding using the correct formula
		pad_left = max(0, -orig_x1)
		pad_top = max(0, -orig_y1)
		pad_right = max(0, orig_x2 - frame_w)
		pad_bottom = max(0, orig_y2 - frame_h)

		# Pad with black (padding math ensures final size is exactly crop_pixels x crop_pixels)
		if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
			padded = cv2.copyMakeBorder(
				cropped,
				int(pad_top), int(pad_bottom),
				int(pad_left), int(pad_right),
				cv2.BORDER_CONSTANT,
				value=(0, 0, 0)
			)
		else:
			padded = cropped

		target_size = int(crop_pixels)

		# Draw the torso reference box on the padded tile
		# Box center is at (crop_pixels/2, crop_pixels/2) in the padded image
		box_cx = target_size / 2
		box_cy = target_size / 2
		box_half_w = torso_w / 2
		box_half_h = torso_h / 2

		box_x1 = int(box_cx - box_half_w)
		box_y1 = int(box_cy - box_half_h)
		box_x2 = int(box_cx + box_half_w)
		box_y2 = int(box_cy + box_half_h)

		# Clamp box to tile bounds
		box_x1 = max(0, min(box_x1, target_size - 1))
		box_y1 = max(0, min(box_y1, target_size - 1))
		box_x2 = max(0, min(box_x2, target_size - 1))
		box_y2 = max(0, min(box_y2, target_size - 1))

		# Get pre_race_reference color
		ref_bgr = overlay_config.get_pre_race_reference_bgr()

		# Draw dashed rectangle
		draw_utils.draw_dashed_rect(
			padded, box_x1, box_y1, box_x2, box_y2,
			ref_bgr, thickness=2, dash_len=6
		)

		# Draw border if tile was padded (clamped)
		if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
			cv2.rectangle(
				padded, (0, 0), (target_size - 1, target_size - 1),
				(50, 50, 50), thickness=1
			)

		# Overlay text labels
		text_color_white = (255, 255, 255)

		# Top-left: frame number
		cv2.putText(
			padded,
			f"Frame {frame_idx}",
			(5, 20),
			FONT_FACE, FONT_SCALE, text_color_white, FONT_THICKNESS
		)

		# Top-right: offset label
		offset_label = f"{offset_s:+.1f}s"
		text_size = cv2.getTextSize(
			offset_label, FONT_FACE, FONT_SCALE, FONT_THICKNESS
		)[0]
		cv2.putText(
			padded,
			offset_label,
			(target_size - text_size[0] - 5, 20),
			FONT_FACE, FONT_SCALE, text_color_white, FONT_THICKNESS
		)

		# Bottom-left: role label
		cv2.putText(
			padded,
			label,
			(5, target_size - 5),
			FONT_FACE, FONT_SCALE, text_color_white, FONT_THICKNESS
		)

		# Bottom-right: CLAMPED warning if applicable
		if clamped:
			clamped_label = "CLAMPED"
			clamped_size = cv2.getTextSize(
				clamped_label, FONT_FACE, FONT_SCALE, FONT_THICKNESS
			)[0]
			cv2.putText(
				padded,
				clamped_label,
				(target_size - clamped_size[0] - 5, target_size - 5),
				FONT_FACE, FONT_SCALE, CLAMPED_BGR, FONT_THICKNESS
			)

		processed_tiles.append(padded)

	# Assemble the three rows: 5 PRE, 1 START, 5 POST
	pre_tiles = processed_tiles[0:5]
	center_tile = processed_tiles[5]
	post_tiles = processed_tiles[6:11]

	# Concatenate rows horizontally
	pre_row = cv2.hconcat(pre_tiles)
	post_row = cv2.hconcat(post_tiles)

	# Center tile should span the full width
	# Pad center tile horizontally to match row widths
	center_padded = cv2.copyMakeBorder(
		center_tile,
		0, 0,  # top, bottom
		int((pre_row.shape[1] - center_tile.shape[1]) / 2),
		int((pre_row.shape[1] - center_tile.shape[1]) / 2),
		cv2.BORDER_CONSTANT,
		value=(0, 0, 0)
	)

	# Ensure exact width match
	if center_padded.shape[1] != pre_row.shape[1]:
		target_width = pre_row.shape[1]
		current_width = center_padded.shape[1]
		if current_width < target_width:
			pad_right = target_width - current_width
			center_padded = cv2.copyMakeBorder(
				center_padded,
				0, 0, 0, pad_right,
				cv2.BORDER_CONSTANT,
				value=(0, 0, 0)
			)
		elif current_width > target_width:
			center_padded = center_padded[:, :target_width]

	# Concatenate all three rows vertically
	composite = cv2.vconcat([pre_row, center_padded, post_row])

	# Prepare title strip
	video_filename = os.path.basename(video_path)
	race_start_s = race_start_frame / fps if fps > 0 else 0.0
	title_text = (
		f"{video_filename} | race_start_frame={race_start_frame} | "
		f"time={race_start_s:.2f}s"
	)

	# Add title strip (thin row at top)
	title_height = 30
	title_strip = numpy.zeros((title_height, composite.shape[1], 3), dtype=numpy.uint8)
	cv2.putText(
		title_strip,
		title_text,
		(5, 20),
		FONT_FACE, FONT_SCALE, (255, 255, 255), FONT_THICKNESS
	)

	# Prepend title to composite
	final_composite = cv2.vconcat([title_strip, composite])

	# Write PNG
	success = cv2.imwrite(output_path, final_composite)
	if not success:
		raise RuntimeError(
			f"cv2.imwrite failed to write {output_path}"
		)

	# Close reader after successful write
	reader.close()
