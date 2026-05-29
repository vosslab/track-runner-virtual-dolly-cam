"""Render per-frame walker tiles with heat-map overlay and blob geometry.

Produces one PNG per visited frame showing:
- Source frame at the ROI crop
- Transparent JET heat-map overlay alpha-composited at the ROI
- Blob ellipses: yellow=walker-selected winner, cyan=other corridor,
  faded gray=raw blobs outside corridor, light-red=motion-gate-rejected winner
- Magenta + at the walker prior (pred_cx, pred_cy)
- Dashed acceptance-box rectangle on step==0 bootstrap tile only
- Audit-mode disagreement marker (small open square) when production and
  audit winners differ
- Velocity vector: arrowed line from pred to pred + (vx*dt, vy*dt)
- Allowed-jump circle: dashed circle at pred with radius max_displacement_px
- Residual line: pred to candidate, green if accepted, red if rejected

Renderer raises loudly on missing input with frame index; the driver
(WP-3C) is responsible for per-interval error containment.
"""

# Standard Library
import math
import pathlib

# PIP3 modules
import cv2
import numpy

# local repo modules
import residual_heat_map
import overlay_config
import draw_utils
import blob_trace
import scene_coords

# Importer workaround: tools/blob_walk_v2 is not in standard path
import sys
import os
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRACK_RUNNER_DIR = os.path.join(_REPO_ROOT, 'track_runner')
if _TRACK_RUNNER_DIR not in sys.path:
	sys.path.insert(0, _TRACK_RUNNER_DIR)

import walk_debug_log

# Load walk overlay palette (colors for velocity vector, circle, residual line).
import yaml as _yaml
_OVERLAY_STYLES_PATH = os.path.join(_REPO_ROOT, 'track_runner', 'overlay_styles.yaml')
with open(_OVERLAY_STYLES_PATH) as _f:
	_WALK_PALETTE = _yaml.safe_load(_f)['walk_overlays']


#============================================
def _ellipse_area_from_blob(blob: dict) -> float:
	"""Extract area from a blob dict, defaulting to zero."""
	return float(blob.get("area", 0.0))


#============================================
def _compute_thickness_from_torso_h(torso_h_px: float) -> int:
	"""Compute line thickness as 4% of torso height, minimum 2 pixels."""
	thickness = max(2, int(round(0.04 * torso_h_px)))
	return thickness


#============================================
def _get_walker_overlay_color_bgr(color_key: str) -> tuple:
	"""Retrieve a walker overlay color from the palette.

	Args:
		color_key: Key name in walker_overlays (e.g., 'winner', 'corridor_non_winner').

	Returns:
		BGR tuple (uint8, uint8, uint8).

	Raises:
		KeyError: If the key is not in walker_overlays.
	"""
	palette = overlay_config.load_palette()
	walker_palette = palette["walker_overlays"]
	hex_color = walker_palette[color_key]
	bgr = overlay_config.hex_to_bgr(hex_color)
	return bgr


#============================================
def _hex_to_bgr(hex_color: str) -> tuple:
	"""Convert #RRGGBB hex string to BGR tuple for OpenCV.

	Args:
		hex_color: Color string like '#FF0000'.

	Returns:
		BGR tuple of ints (0-255).
	"""
	h = hex_color.lstrip('#')
	r = int(h[0:2], 16)
	g = int(h[2:4], 16)
	b = int(h[4:6], 16)
	return (b, g, r)


#============================================
def _draw_dashed_circle(
	frame: numpy.ndarray,
	cx: int,
	cy: int,
	radius: int,
	color: tuple,
	thickness: int,
	num_dashes: int = 20,
) -> None:
	"""Draw a dashed circle using short arc segments.

	Args:
		frame: BGR image to draw on (modified in-place).
		cx, cy: Circle center in pixels.
		radius: Circle radius in pixels.
		color: BGR color tuple.
		thickness: Line thickness.
		num_dashes: Approximate number of dashes around the circle.
	"""
	if radius <= 0:
		return
	# Draw alternating arcs to simulate dashes.
	step = 360 // (num_dashes * 2)
	for i in range(0, 360, step * 2):
		start_angle = i
		end_angle = min(i + step, 360)
		cv2.ellipse(
			frame,
			center=(cx, cy),
			axes=(radius, radius),
			angle=0,
			startAngle=start_angle,
			endAngle=end_angle,
			color=color,
			thickness=thickness,
			lineType=cv2.LINE_AA,
		)


#============================================
def _draw_velocity_vector(
	frame: numpy.ndarray,
	pred_local_cx: int,
	pred_local_cy: int,
	vx_px: float,
	vy_px: float,
	dt: float,
	color: tuple,
	thickness: int,
) -> None:
	"""Draw a velocity vector arrow from pred in (vx*dt, vy*dt) direction.

	Args:
		frame: BGR image to draw on (modified in-place).
		pred_local_cx, pred_local_cy: Prediction center in tile coordinates.
		vx_px, vy_px: Velocity in px/frame.
		dt: Time delta (frames).
		color: BGR color tuple.
		thickness: Line thickness.
	"""
	# Skip if velocity is effectively zero.
	if abs(vx_px) < 1e-9 and abs(vy_px) < 1e-9:
		return
	end_x = int(round(pred_local_cx + vx_px * dt))
	end_y = int(round(pred_local_cy + vy_px * dt))
	cv2.arrowedLine(
		frame,
		(pred_local_cx, pred_local_cy),
		(end_x, end_y),
		color=color,
		thickness=thickness,
		tipLength=0.3,
		line_type=cv2.LINE_AA,
	)


#============================================
def _draw_allowed_jump_circle(
	frame: numpy.ndarray,
	pred_local_cx: int,
	pred_local_cy: int,
	max_displacement_px: float,
	color: tuple,
	thickness: int,
) -> None:
	"""Draw a dashed circle showing the allowed-jump radius.

	Args:
		frame: BGR image to draw on (modified in-place).
		pred_local_cx, pred_local_cy: Center in tile coordinates.
		max_displacement_px: Circle radius in pixels.
		color: BGR color tuple.
		thickness: Line thickness.
	"""
	radius = int(round(max_displacement_px))
	_draw_dashed_circle(frame, pred_local_cx, pred_local_cy, radius, color, thickness)


#============================================
def _draw_residual_line(
	frame: numpy.ndarray,
	pred_local_cx: int,
	pred_local_cy: int,
	cand_local_cx: int,
	cand_local_cy: int,
	accepted: bool,
	thickness: int,
) -> None:
	"""Draw a line from prediction to candidate, colored green (accept) or red (reject).

	Args:
		frame: BGR image to draw on (modified in-place).
		pred_local_cx, pred_local_cy: Prediction center in tile coordinates.
		cand_local_cx, cand_local_cy: Candidate center in tile coordinates.
		accepted: True if gate accepted, False if rejected.
		thickness: Line thickness.
	"""
	# Green for accept, red for reject.
	if accepted:
		color = _hex_to_bgr(_WALK_PALETTE['residual_accept'])
	else:
		color = _hex_to_bgr(_WALK_PALETTE['residual_reject'])
	cv2.line(
		frame,
		(pred_local_cx, pred_local_cy),
		(cand_local_cx, cand_local_cy),
		color=color,
		thickness=thickness,
		lineType=cv2.LINE_AA,
	)


#============================================
def render_walk_tile(
	frame_index: int,
	debug_row: walk_debug_log.DebugLogRow,
	trace: blob_trace.BlobObserverTrace,
	reader,
	scene_transform: scene_coords.SceneTransform,
	fps: float,
	out_png_path: pathlib.Path,
	vx_px: float = None,
	vy_px: float = None,
	max_displacement_px: float = None,
) -> None:
	"""Render a single walker tile PNG.

	Reads source frame, composites heat-map overlay, crops to ROI, draws blob
	ellipses and prior marker, bakes vector overlays, and saves to PNG.

	Args:
		frame_index: Frame index (must be readable from reader).
		debug_row: DebugLogRow with pred_cx, pred_cy, torso_w_px, torso_h_px,
			cand_cx, cand_cy, production_winner_cx/cy, audit_winner_cx/cy,
			winner_strength_score, winner_total_score, status.
		trace: BlobObserverTrace with raw_blobs, corridor_blobs, winner_blob,
			roi_origin_xy, acceptance_box.
		reader: FrameReader with read_frame(index) -> BGR ndarray.
		scene_transform: SceneTransform for residual computation.
		fps: Frame rate (required for residual stride resolution).
		out_png_path: Output PNG path.
		vx_px: Velocity x-component in px/frame (M1 schema; optional).
		vy_px: Velocity y-component in px/frame (M1 schema; optional).
		max_displacement_px: Allowed-jump radius in px (M1 schema; optional).

	Raises:
		RuntimeError: If frame is unreadable, heat-map overlay is None,
			or required fields are missing.
	"""
	# Read source frame
	source_bgr = reader.read_frame(frame_index)
	if source_bgr is None:
		raise RuntimeError(f"render_walk_tile: frame {frame_index} not readable")

	# Extract prediction geometry from debug_row
	pred_cx = debug_row.pred_cx
	pred_cy = debug_row.pred_cy
	torso_w_px = debug_row.torso_w_px
	torso_h_px = debug_row.torso_h_px

	if pred_cx is None or pred_cy is None or torso_w_px is None or torso_h_px is None:
		raise RuntimeError(
			f"render_walk_tile: frame {frame_index} missing required prediction fields "
			f"(pred_cx={pred_cx}, pred_cy={pred_cy}, torso_w_px={torso_w_px}, torso_h_px={torso_h_px})"
		)

	# Compute heat-map overlay
	pred_center = (pred_cx, pred_cy)
	pred_box = (torso_w_px, torso_h_px)
	overlay_result = residual_heat_map.compute_heat_map_overlay_roi(
		reader=reader,
		frame_index=frame_index,
		scene_transform=scene_transform,
		pred_center=pred_center,
		pred_box=pred_box,
		fps=fps,
		threshold=10.0,
		fixed_max=30.0,
		blend_alpha=0.40,
	)
	if overlay_result is None:
		raise RuntimeError(
			f"render_walk_tile: compute_heat_map_overlay_roi returned None for frame {frame_index}"
		)

	overlay_bgra, (roi_x_origin, roi_y_origin) = overlay_result

	# Extract ROI bounds (overlay_bgra dimensions define the ROI size)
	roi_h, roi_w = overlay_bgra.shape[:2]
	roi_x2 = roi_x_origin + roi_w
	roi_y2 = roi_y_origin + roi_h

	# Crop source frame to ROI
	source_crop = source_bgr[roi_y_origin:roi_y2, roi_x_origin:roi_x2].copy()

	# Alpha-composite the BGRA overlay onto the cropped BGR source frame
	# Formula: out = src * (1 - alpha) + overlay_rgb * alpha
	overlay_rgb = overlay_bgra[:, :, :3]
	overlay_alpha = overlay_bgra[:, :, 3].astype(numpy.float32) / 255.0

	# Reshape alpha for broadcasting (HxW1 instead of HxW)
	alpha_3ch = numpy.stack(
		(overlay_alpha, overlay_alpha, overlay_alpha), axis=-1
	)

	# Convert source to float for compositing
	source_float = source_crop.astype(numpy.float32)
	overlay_float = overlay_rgb.astype(numpy.float32)

	# Composite: src * (1 - alpha) + overlay_rgb * alpha
	composited_float = (
		source_float * (1.0 - alpha_3ch) +
		overlay_float * alpha_3ch
	)
	composited_bgr = numpy.clip(composited_float, 0, 255).astype(numpy.uint8)

	# Draw line thickness based on torso height
	thickness = _compute_thickness_from_torso_h(torso_h_px)

	# Draw blob ellipses in the tile frame (local ROI coordinates)
	# Convert trace blob coordinates to tile-local (subtract ROI origin)

	# YELLOW ellipse: walker-accepted winner (if status == "accepted")
	if debug_row.status == "accepted":
		# Determine which winner to draw
		if debug_row.winner_mode == "production_winner":
			winner_cx = debug_row.production_winner_cx
			winner_cy = debug_row.production_winner_cy
		else:
			# audit_winner mode
			winner_cx = debug_row.audit_winner_cx
			winner_cy = debug_row.audit_winner_cy

		if winner_cx is not None and winner_cy is not None and trace.winner_blob is not None:
			# Local coordinates (relative to tile origin)
			local_cx = winner_cx - roi_x_origin
			local_cy = winner_cy - roi_y_origin

			# Draw yellow ellipse for the winner
			yellow_bgr = _get_walker_overlay_color_bgr("winner")

			_draw_blob_ellipse_cv2(
				composited_bgr,
				local_cx, local_cy,
				_ellipse_area_from_blob(trace.winner_blob),
				torso_h_px, torso_w_px,
				color=yellow_bgr,
				thickness=thickness,
			)

	# LIGHT-RED ellipse: v12 motion-gate-rejected status.
	# rejected_motion_gate is a legacy v12 status; v13 walker never emits it.
	# Branch retained for backward CSV read compat when rendering v12 debug logs.
	if debug_row.status == "rejected_motion_gate":
		if debug_row.cand_cx is not None and debug_row.cand_cy is not None:
			local_cx = debug_row.cand_cx - roi_x_origin
			local_cy = debug_row.cand_cy - roi_y_origin

			light_red_bgr = _get_walker_overlay_color_bgr("rejected_winner")

			_draw_blob_ellipse_cv2(
				composited_bgr,
				local_cx, local_cy,
				_ellipse_area_from_blob(trace.winner_blob) if trace.winner_blob else 1.0,
				torso_h_px, torso_w_px,
				color=light_red_bgr,
				thickness=thickness,
			)

	# CYAN ellipse: non-winner corridor blobs
	if trace.corridor_blobs:
		cyan_bgr = _get_walker_overlay_color_bgr("corridor_non_winner")

		for blob in trace.corridor_blobs:
			# Skip the winner blob (already drawn in yellow above)
			if trace.winner_blob is not None and blob.get("centroid_x") == trace.winner_blob.get("centroid_x") and blob.get("centroid_y") == trace.winner_blob.get("centroid_y"):
				continue

			blob_cx = float(blob.get("centroid_x", 0.0))
			blob_cy = float(blob.get("centroid_y", 0.0))
			local_cx = blob_cx - roi_x_origin
			local_cy = blob_cy - roi_y_origin

			_draw_blob_ellipse_cv2(
				composited_bgr,
				local_cx, local_cy,
				_ellipse_area_from_blob(blob),
				torso_h_px, torso_w_px,
				color=cyan_bgr,
				thickness=thickness,
			)

	# FADED GRAY ellipse: raw blobs outside corridor
	if trace.raw_blobs:
		gray_bgr = _get_walker_overlay_color_bgr("raw_outside_corridor")

		for blob in trace.raw_blobs:
			# Only draw if not in corridor
			if blob.get("in_corridor", False):
				continue

			blob_cx = float(blob.get("centroid_x", 0.0))
			blob_cy = float(blob.get("centroid_y", 0.0))
			local_cx = blob_cx - roi_x_origin
			local_cy = blob_cy - roi_y_origin

			_draw_blob_ellipse_cv2(
				composited_bgr,
				local_cx, local_cy,
				_ellipse_area_from_blob(blob),
				torso_h_px, torso_w_px,
				color=gray_bgr,
				thickness=int(thickness * 0.5),  # fainter
			)

	# MAGENTA +: walker prior at (pred_cx, pred_cy)
	magenta_bgr = _get_walker_overlay_color_bgr("prior_cross")

	prior_local_cx = int(pred_cx - roi_x_origin)
	prior_local_cy = int(pred_cy - roi_y_origin)
	_draw_plus_marker(
		composited_bgr,
		prior_local_cx, prior_local_cy,
		color=magenta_bgr,
		size_px=12,
		thickness=thickness,
	)

	# DASHED acceptance-box rectangle on step==0 bootstrap tile
	if debug_row.step == 0 and trace.acceptance_box is not None:
		box_bgr = _get_walker_overlay_color_bgr("acceptance_box")

		# acceptance_box is (x_min, y_min, x_max, y_max) in source frame coords
		box_x_min, box_y_min, box_x_max, box_y_max = trace.acceptance_box

		# Convert to tile-local coordinates
		local_x1 = int(box_x_min - roi_x_origin)
		local_y1 = int(box_y_min - roi_y_origin)
		local_x2 = int(box_x_max - roi_x_origin)
		local_y2 = int(box_y_max - roi_y_origin)

		# Clamp to tile bounds
		local_x1 = max(0, min(local_x1, roi_w - 1))
		local_y1 = max(0, min(local_y1, roi_h - 1))
		local_x2 = max(0, min(local_x2, roi_w))
		local_y2 = max(0, min(local_y2, roi_h))

		# Draw dashed rectangle
		draw_utils.draw_dashed_rect(
			composited_bgr,
			local_x1, local_y1, local_x2, local_y2,
			color=box_bgr,
			thickness=thickness,
			dash_len=10,
		)

	# AUDIT-MODE DISAGREEMENT MARKER: small open square when production_winner != audit_winner
	if (debug_row.winner_mode == "audit_winner" and
		debug_row.production_winner_cx is not None and
		debug_row.audit_winner_cx is not None):
		# Check if production and audit winners differ
		prod_pt = (debug_row.production_winner_cx, debug_row.production_winner_cy)
		audit_pt = (debug_row.audit_winner_cx, debug_row.audit_winner_cy)

		# Small threshold for "different" (not exact equality due to floating point)
		dist = math.sqrt(
			(prod_pt[0] - audit_pt[0]) ** 2 +
			(prod_pt[1] - audit_pt[1]) ** 2
		)

		if dist > 0.5:
			# Draw small open square at production winner location
			local_prod_cx = int(debug_row.production_winner_cx - roi_x_origin)
			local_prod_cy = int(debug_row.production_winner_cy - roi_y_origin)

			disagreement_bgr = _get_walker_overlay_color_bgr("audit_disagreement")

			# Draw small open square (no fill, outline only)
			square_half = 8  # small marker
			cv2.rectangle(
				composited_bgr,
				(local_prod_cx - square_half, local_prod_cy - square_half),
				(local_prod_cx + square_half, local_prod_cy + square_half),
				color=disagreement_bgr,
				thickness=1,
				lineType=cv2.LINE_AA,
			)

	# RESIDUAL LINE: pred -> candidate, colored by gate decision.
	# Drawn for accepted (green) and soft_miss_no_path (red: candidate present
	# but no plausible path survived the displacement cap).
	# Also drawn for legacy v12 rejected_motion_gate (backward compat).
	if debug_row.cand_cx is not None and debug_row.cand_cy is not None:
		if debug_row.status in ("accepted", "soft_miss_no_path", "rejected_motion_gate"):
			cand_local_cx = int(debug_row.cand_cx - roi_x_origin)
			cand_local_cy = int(debug_row.cand_cy - roi_y_origin)
			_draw_residual_line(
				composited_bgr,
				prior_local_cx, prior_local_cy,
				cand_local_cx, cand_local_cy,
				accepted=(debug_row.status == "accepted"),
				thickness=thickness,
			)

	# ALLOWED-JUMP CIRCLE: dashed circle centered on pred with radius max_displacement_px.
	# Uses M1 schema field; silently skipped when absent.
	if max_displacement_px is not None and max_displacement_px > 0:
		circle_color = _hex_to_bgr(_WALK_PALETTE['allowed_jump_circle'])
		_draw_allowed_jump_circle(
			composited_bgr,
			prior_local_cx, prior_local_cy,
			max_displacement_px,
			circle_color,
			thickness=1,
		)

	# VELOCITY VECTOR: arrowed line from pred in the (vx*dt, vy*dt) direction.
	# Uses M1 schema fields; silently skipped when absent.
	if vx_px is not None and vy_px is not None and debug_row.dt is not None:
		vec_color = _hex_to_bgr(_WALK_PALETTE['velocity_vector'])
		_draw_velocity_vector(
			composited_bgr,
			prior_local_cx, prior_local_cy,
			vx_px, vy_px,
			float(debug_row.dt),
			vec_color,
			thickness=1,
		)

	# Save PNG
	success = cv2.imwrite(str(out_png_path), composited_bgr)
	if not success:
		raise RuntimeError(
			f"render_walk_tile: failed to write PNG to {out_png_path}"
		)


#============================================
def _draw_blob_ellipse_cv2(
	frame: numpy.ndarray,
	cx: float,
	cy: float,
	area: float,
	torso_h_px: float,
	torso_w_px: float,
	color: tuple,
	thickness: int,
) -> None:
	"""Draw a blob as an ellipse outline using cv2.

	Args:
		frame: BGR image to draw on (modified in-place).
		cx, cy: Blob centroid in frame coordinates.
		area: Blob area in pixels.
		torso_h_px: Torso height (used for aspect ratio).
		torso_w_px: Torso width (used for aspect ratio).
		color: BGR color tuple.
		thickness: Line thickness in pixels.
	"""
	if area < 1.0:
		return

	# Aspect ratio: height / width (typical runner is taller than wide)
	aspect_ratio = torso_h_px / torso_w_px if torso_w_px > 0 else 2.0

	# Semi-axes from area and aspect ratio
	# For an ellipse: area = pi * a * b where a and b are semi-axes
	# a is horizontal (width), b is vertical (height)
	# If aspect_ratio = b / a, then b = aspect_ratio * a
	# area = pi * a * (aspect_ratio * a) = pi * aspect_ratio * a^2
	# a = sqrt(area / (pi * aspect_ratio))
	semi_a = math.sqrt(area / (math.pi * aspect_ratio)) if aspect_ratio > 0 else math.sqrt(area / math.pi)
	semi_b = aspect_ratio * semi_a

	# Convert to integer center and axes
	center = (int(round(cx)), int(round(cy)))
	axes = (int(round(semi_a)), int(round(semi_b)))

	# Draw ellipse (angle=0, startAngle=0, endAngle=360)
	cv2.ellipse(
		frame,
		center=center,
		axes=axes,
		angle=0,
		startAngle=0,
		endAngle=360,
		color=color,
		thickness=thickness,
		lineType=cv2.LINE_AA,
	)


#============================================
def _draw_plus_marker(
	frame: numpy.ndarray,
	cx: int,
	cy: int,
	color: tuple,
	size_px: int,
	thickness: int,
) -> None:
	"""Draw a plus (+) marker at the given center.

	Args:
		frame: BGR image to draw on (modified in-place).
		cx, cy: Center position in pixels.
		color: BGR color tuple.
		size_px: Size of the cross arms in pixels.
		thickness: Line thickness in pixels.
	"""
	# Horizontal line
	cv2.line(
		frame,
		(cx - size_px, cy),
		(cx + size_px, cy),
		color=color,
		thickness=thickness,
		lineType=cv2.LINE_AA,
	)
	# Vertical line
	cv2.line(
		frame,
		(cx, cy - size_px),
		(cx, cy + size_px),
		color=color,
		thickness=thickness,
		lineType=cv2.LINE_AA,
	)
