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
import draw_utils
import overlay_config
import walk_palette
import common_tools.coord_space

# Load walk overlay palette (colors for velocity vector, circle, residual line).
_WALK_PALETTE = walk_palette.load_walk_overlays()


#============================================
def processed_box_to_tile_local(
	box: common_tools.coord_space.ProcessedBox,
	roi_origin: tuple,
) -> tuple:
	"""Convert a PROCESSED box to a tile-local edge rectangle (single subtraction).

	TILE-LOCAL is the render-only third coordinate flavor: it is PROCESSED
	pixels minus the per-tile ROI origin (the tile is a crop of the processed
	frame).  It is deliberately NOT a global pipeline space and NOT a new type
	in common_tools/coord_space.py: docs/COORDINATE_SPACES.md fixes the pipeline
	at exactly two spaces (SOURCE, PROCESSED), and tile-local is an ephemeral
	draw-time coordinate parameterized by the ROI origin, valid only inside one
	tile.  Keeping it a render-local typed helper honors that contract while
	still making the processed->tile-local conversion explicit, single-direction,
	and impossible to double-apply by accident.

	The ROI-origin subtraction happens HERE and only here.  This is the single
	conversion site that the render manifest's conversion_count == 1 refers to.
	Edges derive from the float center BEFORE any rounding, per the render
	rounding policy (matching ProcessedBox.edges() semantics).

	Args:
		box: a typed PROCESSED-space center-size box (validated by the caller
			with require_processed_box at the render entry).
		roi_origin: (roi_x_origin, roi_y_origin) in PROCESSED pixels.

	Returns:
		(x1, y1, x2, y2) tile-local edge tuple as floats.
	"""
	# Guard: refuse any non-ProcessedBox so a space mismatch fails loud.
	common_tools.coord_space.require_processed_box(box)
	roi_x_origin, roi_y_origin = roi_origin
	# Single ROI-origin subtraction: processed center -> tile-local center.
	tile_cx = box.cx - roi_x_origin
	tile_cy = box.cy - roi_y_origin
	# Width/height are deltas: the ROI origin does not shift them.
	x1 = tile_cx - box.w / 2.0
	y1 = tile_cy - box.h / 2.0
	x2 = tile_cx + box.w / 2.0
	y2 = tile_cy + box.h / 2.0
	edge_tuple = (x1, y1, x2, y2)
	return edge_tuple


# Named constants for torso-relative marker geometry.
# k_plus_arm: fraction of torso height used as plus-marker arm length.
# k_sq_half: fraction of torso height used as audit-square half-side.
# Clamp bounds are intentional pixel-space limits (allowed exception to the
# no-raw-pixel rule: these are UI element minimum/maximum legibility clamps,
# not scene-geometry thresholds).
_K_PLUS_ARM: float = 0.10          # arm length = 10% of torso height
_PLUS_ARM_MIN_PX: int = 5          # minimum arm length in pixels (legibility clamp)
_PLUS_ARM_MAX_PX: int = 24         # maximum arm length in pixels (legibility clamp)
_K_SQ_HALF: float = 0.07           # square half-side = 7% of torso height
_SQ_HALF_MIN_PX: int = 4           # minimum square half-side in pixels (legibility clamp)
_SQ_HALF_MAX_PX: int = 16          # maximum square half-side in pixels (legibility clamp)


#============================================
def _compute_thickness_from_torso_h(torso_h_px: float) -> int:
	"""Compute line thickness as 2% of torso height, minimum 1 pixel."""
	thickness = max(1, int(round(0.02 * torso_h_px)))
	return thickness


#============================================
def compute_plus_arm_px(torso_h_px: float) -> int:
	"""Compute plus-marker arm length from torso height, clamped for legibility.

	Args:
		torso_h_px: Torso height in pixels.

	Returns:
		Arm length in pixels, clamped to [_PLUS_ARM_MIN_PX, _PLUS_ARM_MAX_PX].
	"""
	arm = int(round(_K_PLUS_ARM * torso_h_px))
	arm = max(_PLUS_ARM_MIN_PX, min(_PLUS_ARM_MAX_PX, arm))
	return arm


#============================================
def compute_sq_half_px(torso_h_px: float) -> int:
	"""Compute audit-square half-side from torso height, clamped for legibility.

	Args:
		torso_h_px: Torso height in pixels.

	Returns:
		Half-side in pixels, clamped to [_SQ_HALF_MIN_PX, _SQ_HALF_MAX_PX].
	"""
	half = int(round(_K_SQ_HALF * torso_h_px))
	half = max(_SQ_HALF_MIN_PX, min(_SQ_HALF_MAX_PX, half))
	return half


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
def _draw_torso_box_solid_heavy_edges(
	frame: numpy.ndarray,
	edges: tuple,
	color: tuple,
	base_thickness: int,
) -> None:
	"""Draw a solid heavy torso box from a precomputed tile-local edge rectangle.

	Solid heavy seed-box draw from precomputed edges: the (x1, y1, x2, y2)
	tuple is already derived from the float center by the single processed->tile-local
	conversion (processed_box_to_tile_local), so no center math or second
	roi subtraction happens here.  Visual output is identical: the seed box uses
	a 1.5x emphasis tier (heavy), solid rectangle, rounded to int only at the
	final draw call.

	Args:
		frame: BGR image to draw on (modified in-place).
		edges: (x1, y1, x2, y2) tile-local float edges.
		color: BGR color tuple.
		base_thickness: Base line thickness; heavy tier multiplies by 1.5.
	"""
	x1, y1, x2, y2 = edges
	# Heavy tier is 1.5x base (seed box emphasis tier).
	heavy_thickness = max(1, int(round(base_thickness * 1.5)))
	# Round to int only at the final draw call.
	cv2.rectangle(
		frame,
		(int(round(x1)), int(round(y1))),
		(int(round(x2)), int(round(y2))),
		color=color,
		thickness=heavy_thickness,
		lineType=cv2.LINE_AA,
	)


#============================================
def _draw_torso_box_dashed_normal_edges(
	frame: numpy.ndarray,
	edges: tuple,
	color: tuple,
	base_thickness: int,
) -> None:
	"""Draw a dashed normal torso box from a precomputed tile-local edge rectangle.

	Dashed normal solved-box draw from precomputed edges: the (x1, y1, x2, y2)
	tuple is already derived from the float center by the single processed->tile-local
	conversion (processed_box_to_tile_local), so no center math or second
	roi subtraction happens here.  Visual output is identical: normal tier is
	1x base, dashed rectangle, rounded to int only at the final draw call.

	Args:
		frame: BGR image to draw on (modified in-place).
		edges: (x1, y1, x2, y2) tile-local float edges.
		color: BGR color tuple.
		base_thickness: Base line thickness; normal tier is 1x.
	"""
	x1, y1, x2, y2 = edges
	# Normal tier is 1x base.
	normal_thickness = max(1, base_thickness)
	# Round to int only at the final draw call (via draw_utils).
	draw_utils.draw_dashed_rect(
		frame,
		int(round(x1)), int(round(y1)),
		int(round(x2)), int(round(y2)),
		color=color,
		thickness=normal_thickness,
		dash_len=10,
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
