#!/usr/bin/env python3
"""Render ROI-cropped, scaled-up per-frame PNG crops with blob outlines.

Per-frame renderer for microscope verdicts CSV. Crops source video frames to
ROI bounds, scales up by a multiplier, draws corridor blob outlines in cyan
(non-winners) and yellow (winner) with crisp NEAREST-neighbor resampling,
adds frame/pass/gate/winner metadata as text label, borders with verdict
color (lime=accepted, red=rejected, gray=other), and saves to PNG.

Minimal deps: PIL, numpy, cv2. No matplotlib. Uses the frame_reader pattern
from visualize_blob_gates.py.
"""

# Standard Library
import argparse
import csv
import json
import math
import os
import sys

# PIP3 modules
import cv2
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

# add track_runner and common_tools to path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMMON_TOOLS_DIR = os.path.join(_REPO_ROOT, "common_tools")
if _COMMON_TOOLS_DIR not in sys.path:
	sys.path.insert(0, _COMMON_TOOLS_DIR)

# local repo modules
import frame_reader
import probe_video


#============================================
def parse_args() -> argparse.Namespace:
	"""Parse command-line arguments."""
	parser = argparse.ArgumentParser(
		description=(
			"Render ROI-cropped, scaled-up per-frame PNG crops with blob outlines. "
			"Reads a verdicts.csv file, extracts corridor blob geometry, and renders "
			"per-frame visualizations with blob outlines and gate verdict colors."
		)
	)
	parser.add_argument(
		"-v", "--video", dest="video_path", required=True,
		help="Path to source video file (e.g., TRACK_VIDEOS/Conant-4x400-2026_April_15.mkv)."
	)
	parser.add_argument(
		"-c", "--csv", dest="verdicts_csv", required=True,
		help="Path to verdicts.csv file with frame data and corridor blob JSON."
	)
	parser.add_argument(
		"-o", "--output", dest="output_dir", required=True,
		help="Output directory for rendered PNG files."
	)
	parser.add_argument(
		"-f", "--frames", dest="frame_list", default=None,
		help=(
			"Comma-separated frame_index:pass tuples to render. "
			"E.g., '5113:FWD,5114:FWD,5115:FWD'. If absent, render all non-endpoint rows."
		)
	)
	parser.add_argument(
		"--scale", dest="scale", type=float, default=3.0,
		help="Scale multiplier for output crop image (default: 3.0)."
	)
	args = parser.parse_args()
	return args


#============================================
def open_video_reader(video_path: str) -> tuple:
	"""Open video and return (reader, fps, frame_count).

	Args:
		video_path: Path to video file.

	Returns:
		(FrameReader instance, fps, frame_count).

	Raises:
		RuntimeError: If video cannot be opened or probed.
	"""
	probe_info = probe_video.probe_video(video_path)
	fps = float(probe_info["fps"])
	frame_count = probe_info["frame_count"]

	reader = frame_reader.FrameReader(video_path, fps, frame_count)
	return reader, fps, frame_count


#============================================
def read_verdicts_csv(csv_path: str) -> list:
	"""Read verdicts.csv and return list of row dicts.

	Args:
		csv_path: Path to verdicts.csv file.

	Returns:
		List of dicts, one per row (excluding header).

	Raises:
		RuntimeError: If file cannot be read or is malformed.
	"""
	rows = []
	with open(csv_path, "r") as f:
		reader = csv.DictReader(f)
		for row in reader:
			rows.append(row)
	return rows


#============================================
def parse_corridor_blobs(json_str: str) -> list:
	"""Parse corridor_blobs_json column into list of blob dicts.

	Args:
		json_str: JSON string from corridor_blobs_json column.

	Returns:
		List of blob dicts with keys: cx, cy, area, dist_h, etc.
		Empty list if json_str is empty or invalid.
	"""
	if not json_str or json_str.strip() == "":
		return []
	try:
		blobs = json.loads(json_str)
		return blobs if isinstance(blobs, list) else []
	except json.JSONDecodeError:
		return []


#============================================
def draw_legend(draw: ImageDraw.ImageDraw, width: int, height: int, font: object) -> None:
	"""Draw legend at bottom of image.

	Legend shows color and line-style meanings for blob ellipses and gates.

	Args:
		draw: PIL ImageDraw object.
		width: Image width in pixels.
		height: Image height in pixels.
		font: PIL font object (may be None).
	"""
	# Legend dimensions (doubled from 80 to 160 for larger font)
	legend_height = 160
	legend_y_start = height - legend_height

	# Semi-transparent white background
	draw.rectangle(
		[(0, legend_y_start), (width, height)],
		fill=(255, 255, 255, 200)
	)

	# Black border
	draw.rectangle(
		[(0, legend_y_start), (width - 1, height - 1)],
		outline=(0, 0, 0),
		width=2
	)

	# Legend text entries
	legend_items = [
		"YELLOW thick ellipse = winner blob (cue-conf-selected)",
		"CYAN thin ellipse = alternate corridor blob candidate",
		"MAGENTA + = raw_pred predicted center",
		"LIME outer border = gate ACCEPTED",
		"RED outer border = gate REJECTED",
		"GRAY outer border = gate VACUOUS/SKIPPED",
	]

	# Draw legend text with doubled line height for better readability
	text_color = (0, 0, 0)  # Black text
	x_offset = 10
	y_offset = legend_y_start + 10
	line_height = 24  # Doubled from 12

	for item_idx, item_text in enumerate(legend_items):
		y_pos = y_offset + item_idx * line_height
		draw.text((x_offset, y_pos), item_text, fill=text_color, font=font)


#============================================
def clamp_rect(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> tuple:
	"""Clamp rectangle to frame bounds.

	Args:
		x1, y1, x2, y2: Original bounds.
		w, h: Frame width and height.

	Returns:
		Clamped (x1, y1, x2, y2).
	"""
	x1 = max(0, min(x1, w - 1))
	y1 = max(0, min(y1, h - 1))
	x2 = max(0, min(x2, w))
	y2 = max(0, min(y2, h))
	return (x1, y1, x2, y2)


#============================================
def draw_blob_ellipse(
	draw: ImageDraw.ImageDraw,
	cx: float,
	cy: float,
	area: float,
	aspect_ratio: float,
	color: tuple,
	width: int = 2,
) -> None:
	"""Draw a blob as an ellipse outline on scaled image.

	Draws an ellipse with the given area and aspect ratio (height/width).
	Given area A and aspect a = height/width:
	  b_x = sqrt(A / (pi * a))  (horizontal semi-axis)
	  b_y = a * b_x             (vertical semi-axis)

	Args:
		draw: PIL ImageDraw object.
		cx, cy: Blob centroid in scaled image coordinates.
		area: Blob area in scaled image pixels.
		aspect_ratio: Height/width ratio for the ellipse (e.g. 2.0 for tall).
		color: RGB tuple for line color.
		width: Line width in pixels.
	"""
	if area < 1:
		return
	if aspect_ratio <= 0:
		aspect_ratio = 2.0
	b_x = math.sqrt(area / (math.pi * aspect_ratio))
	b_y = aspect_ratio * b_x
	x1 = cx - b_x
	y1 = cy - b_y
	x2 = cx + b_x
	y2 = cy + b_y
	draw.ellipse([x1, y1, x2, y2], outline=color, width=width)


#============================================
def render_frame(
	reader: object,
	frame_index: int,
	roi_x1: int,
	roi_y1: int,
	roi_x2: int,
	roi_y2: int,
	corridor_blobs: list,
	gate: str,
	winner_rank_by_confidence: int,
	winner_dist_h: float,
	dir_dot: float,
	scale: float,
) -> Image.Image:
	"""Render a single frame crop with blob overlays.

	Args:
		reader: FrameReader instance.
		frame_index: Frame index to read.
		roi_x1, roi_y1, roi_x2, roi_y2: ROI bounds in source pixels.
		corridor_blobs: List of blob dicts from corridor_blobs_json.
		gate: Gate verdict string ('accepted', 'rejected', etc.).
		winner_rank_by_confidence: Rank of winner blob (1=first).
		winner_dist_h: Winner distance in torso units.
		dir_dot: Direction gate dot product value.
		scale: Scale multiplier for output.

	Returns:
		PIL Image with rendered crop and overlays.

	Raises:
		RuntimeError: If frame cannot be read.
	"""
	# Read source frame
	frame_bgr = reader.read_frame(frame_index)
	if frame_bgr is None:
		raise RuntimeError(f"Failed to read frame {frame_index}")

	frame_height, frame_width = frame_bgr.shape[:2]

	# Apply 10% margin on each side, clamped
	margin_x = int((roi_x2 - roi_x1) * 0.10)
	margin_y = int((roi_y2 - roi_y1) * 0.10)

	x1 = max(0, roi_x1 - margin_x)
	y1 = max(0, roi_y1 - margin_y)
	x2 = min(frame_width, roi_x2 + margin_x)
	y2 = min(frame_height, roi_y2 + margin_y)

	# Crop to ROI + margin
	crop_bgr = frame_bgr[y1:y2, x1:x2]

	# Convert BGR to RGB
	crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)

	# Create PIL Image from crop
	pil_img = Image.fromarray(crop_rgb)

	# Scale up using NEAREST
	scaled_w = int(pil_img.width * scale)
	scaled_h = int(pil_img.height * scale)
	pil_scaled = pil_img.resize((scaled_w, scaled_h), Image.NEAREST)

	# Draw overlays on scaled image
	draw = ImageDraw.Draw(pil_scaled)

	# Scale factor for blob coordinates
	scale_factor = scale

	# Draw corridor blobs (aspect ratio defaults to 2.0 for tall runner shape)
	default_aspect_ratio = 2.0
	for idx, blob in enumerate(corridor_blobs):
		blob_cx = float(blob.get("cx", 0))
		blob_cy = float(blob.get("cy", 0))
		blob_area = float(blob.get("area", 0))

		# Transform to cropped coordinates
		cropped_cx = blob_cx - x1
		cropped_cy = blob_cy - y1

		# Check if blob is in cropped region
		if cropped_cx < 0 or cropped_cy < 0 or cropped_cx >= pil_img.width or cropped_cy >= pil_img.height:
			continue

		# Scale blob center and area
		scaled_cx = cropped_cx * scale_factor
		scaled_cy = cropped_cy * scale_factor
		scaled_area = blob_area * (scale_factor ** 2)

		# Determine color: yellow if winner (index 0 = highest confidence, always winner)
		# corridor_blobs_json is sorted by confidence descending, so index 0 is the winner.
		is_winner = (idx == 0)
		if is_winner:
			color = (255, 255, 0)  # Yellow (RGB)
			width = 6  # Doubled from 3
		else:
			color = (0, 255, 255)  # Cyan (RGB)
			width = 4  # Doubled from 2

		draw_blob_ellipse(draw, scaled_cx, scaled_cy, scaled_area, default_aspect_ratio, color, width)

	# Add outer border color based on gate
	border_color = None
	if gate == "accepted":
		border_color = (0, 255, 0)  # Lime green
	elif gate == "rejected":
		border_color = (255, 0, 0)  # Red
	else:
		border_color = (128, 128, 128)  # Gray

	# Draw border (8px total width, doubled from 4px)
	for i in range(8):
		draw.rectangle(
			[(i, i), (scaled_w - 1 - i, scaled_h - 1 - i)],
			outline=border_color,
			width=1
		)

	# Add text label
	try:
		font = ImageFont.load_default()
	except:
		font = None

	label_text = f"f={frame_index} pass={gate[:3]} gate={gate[:3]} dist={winner_dist_h:.2f} dot={dir_dot:.1f}"
	label_color = (255, 255, 255)  # White
	label_bg_color = (0, 0, 0)  # Black

	# Draw black background for text
	bbox = draw.textbbox((5, 5), label_text, font=font)
	draw.rectangle(
		[(bbox[0] - 2, bbox[1] - 2), (bbox[2] + 2, bbox[3] + 2)],
		fill=label_bg_color
	)
	draw.text((5, 5), label_text, fill=label_color, font=font)

	# Draw legend at bottom
	draw_legend(draw, scaled_w, scaled_h, font)

	return pil_scaled


#============================================
def ensure_output_directory(path: str) -> None:
	"""Create directory if it doesn't exist."""
	os.makedirs(path, exist_ok=True)


#============================================
def main() -> None:
	"""Main entry point."""
	args = parse_args()

	# Validate inputs
	if not os.path.exists(args.video_path):
		raise RuntimeError(f"Video file not found: {args.video_path}")
	if not os.path.exists(args.verdicts_csv):
		raise RuntimeError(f"Verdicts CSV not found: {args.verdicts_csv}")

	ensure_output_directory(args.output_dir)

	# Open video
	print("Opening video...")
	reader, fps, frame_count = open_video_reader(args.video_path)
	print(f"  {frame_count} frames at {fps} fps, {reader.width}x{reader.height}")

	# Read verdicts CSV
	print("Reading verdicts CSV...")
	rows = read_verdicts_csv(args.verdicts_csv)
	print(f"  {len(rows)} rows")

	# Determine which rows to render
	if args.frame_list:
		# Parse frame_index:pass tuples
		requested_pairs = set()
		for pair_str in args.frame_list.split(","):
			parts = pair_str.strip().split(":")
			if len(parts) == 2:
				frame_idx = int(parts[0])
				pass_name = parts[1]
				requested_pairs.add((frame_idx, pass_name))
		rows_to_render = [
			row for row in rows
			if (int(row["frame_index"]), row["pass"]) in requested_pairs
		]
	else:
		# Render all non-endpoint rows (gate != "skipped")
		rows_to_render = [row for row in rows if row["gate"] != "skipped"]

	print(f"Rendering {len(rows_to_render)} frames...")

	# Render each frame
	for idx, row in enumerate(rows_to_render):
		frame_index = int(row["frame_index"])
		pass_name = row["pass"]
		gate = row["gate"]
		winner_dist_h_str = row.get("winner_dist_h", "0.0")
		winner_dist_h = float(winner_dist_h_str) if winner_dist_h_str and winner_dist_h_str.strip() else 0.0
		dir_dot_str = row.get("dir_dot", "0.0")
		dir_dot = float(dir_dot_str) if dir_dot_str and dir_dot_str.strip() else 0.0
		winner_rank_str = row.get("winner_rank_by_confidence", "0")
		winner_rank_by_confidence = int(winner_rank_str) if winner_rank_str and winner_rank_str.strip() else 0

		roi_x1 = int(row["roi_x1"])
		roi_y1 = int(row["roi_y1"])
		roi_x2 = int(row["roi_x2"])
		roi_y2 = int(row["roi_y2"])

		corridor_blobs_json = row.get("corridor_blobs_json", "")
		corridor_blobs = parse_corridor_blobs(corridor_blobs_json)

		# Render
		try:
			pil_scaled = render_frame(
				reader, frame_index,
				roi_x1, roi_y1, roi_x2, roi_y2,
				corridor_blobs, gate,
				winner_rank_by_confidence, winner_dist_h, dir_dot,
				args.scale
			)

			# Save PNG
			output_path = os.path.join(
				args.output_dir, f"frame_{frame_index:06d}_{pass_name}.png"
			)
			pil_scaled.save(output_path)
			print(f"  [{idx + 1}/{len(rows_to_render)}] {output_path}")
		except Exception as e:
			print(f"  ERROR at frame {frame_index} {pass_name}: {e}")

	print("Done.")


#============================================
if __name__ == "__main__":
	main()
