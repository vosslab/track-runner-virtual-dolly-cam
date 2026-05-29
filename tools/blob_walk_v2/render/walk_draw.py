"""Low-level draw primitives for blob-walk tile rendering.

Each function takes a BGR numpy frame, draws on it in-place, and returns
nothing (or None). No orchestration logic lives here. Call sites are in
walk_render.render_walk_tile.
"""

# Standard Library
import math

# PIP3 modules
import cv2
import numpy

# shared sys.path bootstrap (track_runner, tests, repo root, blob_walk_v2)
import walk_paths
_REPO_ROOT = walk_paths.setup()

# local repo modules
import overlay_config
import walk_palette

# Load walk overlay palette (colors for velocity vector, circle, residual line).
_WALK_PALETTE = walk_palette.load_walk_overlays()


#============================================
def _compute_thickness_from_torso_h(torso_h_px: float) -> int:
	"""Compute line thickness as 4% of torso height, minimum 2 pixels."""
	thickness = max(2, int(round(0.04 * torso_h_px)))
	return thickness


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
		color = overlay_config.hex_to_bgr(_WALK_PALETTE['residual_accept'])
	else:
		color = overlay_config.hex_to_bgr(_WALK_PALETTE['residual_reject'])
	cv2.line(
		frame,
		(pred_local_cx, pred_local_cy),
		(cand_local_cx, cand_local_cy),
		color=color,
		thickness=thickness,
		lineType=cv2.LINE_AA,
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
