"""Render per-frame walker tiles with heat-map overlay and blob geometry.

Produces one PNG per visited frame showing:
- Source frame at the ROI crop (implicit base layer, not in the layer list)
- Transparent JET heat-map overlay alpha-composited at the ROI
- Blob ellipses from the real BlobObserverTrace (WS2-A): yellow=winner,
  cyan=corridor non-winner, faded gray=raw blobs outside corridor,
  light-red=legacy rejected winner (v12 backward-compat only)
- Magenta + at the walker prior (pred_cx, pred_cy)
- Dashed amber acceptance-corridor rectangle (step==0 bootstrap tile only):
  this is the walker search corridor, NOT a torso box
- Audit-mode disagreement marker (small open square) when production and
  audit winners differ
- Velocity vector: arrowed line from pred to pred + (vx*dt, vy*dt)
- Allowed-jump circle: dashed circle at pred with radius max_displacement_px
- Residual line: pred to candidate, green if accepted, red if rejected

Draw order is data-driven: walk_palette.resolve_layer_order() reads the
walk_tile_layer_order list from track_runner/overlay_styles.yaml; each layer
is a callable dispatched in that order.  heat is one named layer (the
alpha-composite step); all others are vector-draw steps.  Reordering the YAML
list changes z-order with no code edits.

Coordinate contract (WS2-B2 / WS2-F): blob centroid_x/y in the
BlobObserverTrace are in PROCESSED-pixel space (roi_x1/roi_y1 was added to
them at extract time, see residual_motion.py:1249).  Each ellipse center is
converted to tile-local by subtracting roi_origin exactly once; NO bin
upscale applied.  trace_coord_space_before_draw = "processed".

Seed and solved torso boxes are typed coord_space.ProcessedBox at the render
entry and pass through the single typed processed->tile-local conversion in
walk_draw.processed_box_to_tile_local.  TILE-LOCAL (processed minus the
per-tile ROI origin) is a render-only coordinate flavor, NOT a third global
pipeline space: docs/COORDINATE_SPACES.md fixes the pipeline at exactly two
spaces (SOURCE, PROCESSED), so the conversion is kept render-local rather
than added to common_tools/coord_space.py.

Renderer raises loudly on missing input with frame index; the driver
is responsible for per-interval error containment.
"""

# Standard Library
import math
import pathlib

# PIP3 modules
import cv2
import numpy

# shared sys.path bootstrap (track_runner, tests, repo root, blob_walk_v2)
import walk_paths
_REPO_ROOT = walk_paths.setup()

# local repo modules
import residual_heat_map
import overlay_config
import draw_utils
import blob_trace
import scene_coords
import walk_palette
import walk_debug_log
import walk_draw
import common_tools.coord_space

# Load walk overlay palette (colors for velocity vector, circle, residual line).
_WALK_PALETTE = walk_palette.load_walk_overlays()


#============================================
def _ellipse_area_from_blob(blob: dict) -> float:
	"""Extract area from a blob dict, defaulting to zero."""
	return float(blob.get("area", 0.0))


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
	seed_box: dict = None,
	solved_box: dict = None,
) -> dict:
	"""Render a single walker tile PNG.

	Reads source frame, composites heat-map overlay, crops to ROI, draws blob
	ellipses and prior marker, bakes vector overlays, and saves to PNG.

	Coordinate contract (WS2-B1 / WS2-F): seed_box and solved_box are in
	PROCESSED pixels (same space as roi_origin_xy).  At this render entry each
	is built into a typed coord_space.ProcessedBox and validated with
	require_processed_box, then passed through exactly one typed tile-local
	conversion (walk_draw.processed_box_to_tile_local): tile_coord =
	processed_coord - roi_origin.  TILE-LOCAL is a render-only flavor, not a
	new pipeline space (docs/COORDINATE_SPACES.md fixes the pipeline at two
	spaces); the helper is the single roi-subtraction site, so a double
	subtract is impossible.  Edges derive from the float center before any
	rounding inside the conversion.

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
		seed_box: Optional dict {cx, cy, w, h} in PROCESSED pixels for this
			frame's human seed annotation.  When present, drawn solid+heavy
			(user-authored style, C1/C3 truth).  When None, no seed box drawn.
		solved_box: Optional dict {cx, cy, w, h} in PROCESSED pixels for this
			frame's walker-solved torso box.  When present, drawn dashed+normal
			(predicted style).  When None, no solved box drawn.

	Raises:
		RuntimeError: If frame is unreadable, heat-map overlay is None,
			or required fields are missing.

	Returns:
		Render manifest dict with per-tile metadata for WS2-C:
		  frame_index, seed_box_present, solved_box_present,
		  solved_box_source, trace_present, raw_blob_count,
		  corridor_blob_count, winner_blob_count,
		  box_coord_space_before_draw, trace_coord_space_before_draw,
		  conversion_count.
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

	# Crop source frame to ROI -- this is the implicit base canvas.
	# source_crop is the starting canvas; overlays (including heat) draw on top
	# in the order driven by walk_palette.resolve_layer_order().
	source_crop = source_bgr[roi_y_origin:roi_y2, roi_x_origin:roi_x2].copy()

	# Draw line thickness based on torso height
	thickness = walk_draw._compute_thickness_from_torso_h(torso_h_px)

	# Build per-tile render manifest for WS2-C.
	# box_coord_space_before_draw='processed': both seed_box and solved_box are
	# typed ProcessedBox (PROCESSED pixels) before the single typed
	# processed->tile-local conversion below (walk_draw.processed_box_to_tile_local).
	# trace_coord_space_before_draw='processed': blob centroid_x/y are in
	# PROCESSED-pixel space (roi offsets added at extract time); subtracted
	# by roi_origin once to produce tile-local coords; no bin upscale.
	# conversion_count=1: exactly one roi_origin subtraction converts each box
	# from processed to tile-local, performed only inside
	# walk_draw.processed_box_to_tile_local (the single conversion site).
	solved_box_source = "in-memory" if solved_box is not None else "none"
	trace_present = (
		trace.raw_blobs or trace.corridor_blobs or trace.winner_blob is not None
	)
	raw_blob_count = len(trace.raw_blobs) if trace.raw_blobs else 0
	corridor_blob_count = len(trace.corridor_blobs) if trace.corridor_blobs else 0
	winner_blob_count = 1 if trace.winner_blob is not None else 0
	render_manifest = {
		"frame_index": frame_index,
		"seed_box_present": seed_box is not None,
		"solved_box_present": solved_box is not None,
		"solved_box_source": solved_box_source,
		"trace_present": trace_present,
		"raw_blob_count": raw_blob_count,
		"corridor_blob_count": corridor_blob_count,
		"winner_blob_count": winner_blob_count,
		"box_coord_space_before_draw": "processed",
		"trace_coord_space_before_draw": "processed",
		"conversion_count": 1,
	}

	# The single processed->tile-local conversion site (conversion_count == 1):
	# the per-tile ROI origin in PROCESSED pixels, subtracted exactly once per
	# box by walk_draw.processed_box_to_tile_local.
	roi_origin = (roi_x_origin, roi_y_origin)

	# Precompute prior tile-local coords (used by plus_marker, residual_line,
	# allowed_jump_circle, velocity_vector layers).
	prior_local_cx = int(pred_cx - roi_x_origin)
	prior_local_cy = int(pred_cy - roi_y_origin)

	# --- Layer dispatch table ---
	# Each entry is a zero-argument callable that draws onto `canvas` in-place.
	# canvas is a one-element list so closures share the same mutable reference;
	# each callable reads canvas[0] and draws on it.
	canvas = [source_crop]

	def _layer_heat():
		"""Alpha-composite JET heat onto the canvas (alpha=0.40)."""
		# Formula: out = src * (1 - alpha) + overlay_rgb * alpha
		overlay_rgb = overlay_bgra[:, :, :3]
		overlay_alpha = overlay_bgra[:, :, 3].astype(numpy.float32) / 255.0
		# Reshape alpha for broadcasting (HxW1 -> HxWx3)
		alpha_3ch = numpy.stack(
			(overlay_alpha, overlay_alpha, overlay_alpha), axis=-1
		)
		source_float = canvas[0].astype(numpy.float32)
		overlay_float = overlay_rgb.astype(numpy.float32)
		composited_float = (
			source_float * (1.0 - alpha_3ch) +
			overlay_float * alpha_3ch
		)
		canvas[0] = numpy.clip(composited_float, 0, 255).astype(numpy.uint8)

	def _layer_seed_box():
		"""SEED BOX: solid + heavy, user-authored style (C1/C3 truth).

		seed_box arrives as a PROCESSED-pixel dict; build the typed ProcessedBox
		at this render boundary and validate with require_processed_box, then run
		the single typed processed->tile-local conversion.  Edges derive from the
		float center before any rounding (inside the conversion helper).
		"""
		if seed_box is None:
			return
		seed_proc_box = common_tools.coord_space.ProcessedBox(
			cx=float(seed_box["cx"]),
			cy=float(seed_box["cy"]),
			w=float(seed_box["w"]),
			h=float(seed_box["h"]),
		)
		common_tools.coord_space.require_processed_box(seed_proc_box)
		seed_edges = walk_draw.processed_box_to_tile_local(seed_proc_box, roi_origin)
		# Color from overlay_config seed_status "visible" (green, solid, heavy).
		seed_color_bgr = overlay_config.get_seed_status_bgr("visible")
		walk_draw._draw_torso_box_solid_heavy_edges(
			canvas[0],
			seed_edges,
			color=seed_color_bgr,
			base_thickness=thickness,
		)

	def _layer_solved_box():
		"""SOLVED BOX: dashed + normal, predicted style.

		solved_box arrives as a PROCESSED-pixel dict; build the typed ProcessedBox
		at this render boundary and validate, then run the single typed
		processed->tile-local conversion (the same single roi subtraction).
		"""
		if solved_box is None:
			return
		solved_proc_box = common_tools.coord_space.ProcessedBox(
			cx=float(solved_box["cx"]),
			cy=float(solved_box["cy"]),
			w=float(solved_box["w"]),
			h=float(solved_box["h"]),
		)
		common_tools.coord_space.require_processed_box(solved_proc_box)
		solved_edges = walk_draw.processed_box_to_tile_local(solved_proc_box, roi_origin)
		# Color from overlay_config predictions "blended" (cyan, dashed, normal).
		solved_color_bgr = overlay_config.get_prediction_bgr("blended")
		walk_draw._draw_torso_box_dashed_normal_edges(
			canvas[0],
			solved_edges,
			color=solved_color_bgr,
			base_thickness=thickness,
		)

	def _layer_blob_ellipses():
		"""Blob ellipses: yellow winner, cyan corridor, gray raw, light-red legacy.

		All blob centroid_x/y are in PROCESSED space.  Subtract roi_origin once
		to convert to tile-local; no bin upscale.
		"""
		# YELLOW ellipse: walker-accepted winner blob from the real trace.
		if debug_row.status == "accepted" and trace.winner_blob is not None:
			blob_cx = float(trace.winner_blob["centroid_x"])
			blob_cy = float(trace.winner_blob["centroid_y"])
			local_cx = blob_cx - roi_x_origin
			local_cy = blob_cy - roi_y_origin
			yellow_bgr = walk_palette.get_walker_overlay_color_bgr("winner")
			walk_draw._draw_blob_ellipse_cv2(
				canvas[0],
				local_cx, local_cy,
				_ellipse_area_from_blob(trace.winner_blob),
				torso_h_px, torso_w_px,
				color=yellow_bgr,
				thickness=thickness,
			)
			# In audit_winner mode: additionally draw the audit-winner position
			# (may differ from production winner) using debug_row coords.
			if (debug_row.winner_mode == "audit_winner" and
				debug_row.audit_winner_cx is not None and
				debug_row.audit_winner_cy is not None):
				audit_local_cx = float(debug_row.audit_winner_cx) - roi_x_origin
				audit_local_cy = float(debug_row.audit_winner_cy) - roi_y_origin
				walk_draw._draw_blob_ellipse_cv2(
					canvas[0],
					audit_local_cx, audit_local_cy,
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
				light_red_bgr = walk_palette.get_walker_overlay_color_bgr("rejected_winner")
				walk_draw._draw_blob_ellipse_cv2(
					canvas[0],
					local_cx, local_cy,
					_ellipse_area_from_blob(trace.winner_blob) if trace.winner_blob else 1.0,
					torso_h_px, torso_w_px,
					color=light_red_bgr,
					thickness=thickness,
				)

		# CYAN ellipse: non-winner corridor blobs
		if trace.corridor_blobs:
			cyan_bgr = walk_palette.get_walker_overlay_color_bgr("corridor_non_winner")
			for blob in trace.corridor_blobs:
				# Skip the winner blob (already drawn in yellow above)
				if (trace.winner_blob is not None and
					blob.get("centroid_x") == trace.winner_blob.get("centroid_x") and
					blob.get("centroid_y") == trace.winner_blob.get("centroid_y")):
					continue
				blob_cx = float(blob.get("centroid_x", 0.0))
				blob_cy = float(blob.get("centroid_y", 0.0))
				local_cx = blob_cx - roi_x_origin
				local_cy = blob_cy - roi_y_origin
				walk_draw._draw_blob_ellipse_cv2(
					canvas[0],
					local_cx, local_cy,
					_ellipse_area_from_blob(blob),
					torso_h_px, torso_w_px,
					color=cyan_bgr,
					thickness=thickness,
				)

		# FADED GRAY ellipse: raw blobs outside corridor
		if trace.raw_blobs:
			gray_bgr = walk_palette.get_walker_overlay_color_bgr("raw_outside_corridor")
			for blob in trace.raw_blobs:
				# Only draw if not in corridor
				if blob.get("in_corridor", False):
					continue
				blob_cx = float(blob.get("centroid_x", 0.0))
				blob_cy = float(blob.get("centroid_y", 0.0))
				local_cx = blob_cx - roi_x_origin
				local_cy = blob_cy - roi_y_origin
				walk_draw._draw_blob_ellipse_cv2(
					canvas[0],
					local_cx, local_cy,
					_ellipse_area_from_blob(blob),
					torso_h_px, torso_w_px,
					color=gray_bgr,
					thickness=int(thickness * 0.5),  # fainter
				)

	def _layer_plus_marker():
		"""MAGENTA +: walker prior at (pred_cx, pred_cy)."""
		magenta_bgr = walk_palette.get_walker_overlay_color_bgr("prior_cross")
		walk_draw._draw_plus_marker(
			canvas[0],
			prior_local_cx, prior_local_cy,
			color=magenta_bgr,
			size_px=walk_draw.compute_plus_arm_px(torso_h_px),
			thickness=thickness,
		)

	def _layer_acceptance_box():
		"""DASHED AMBER acceptance-corridor rectangle on step==0 bootstrap frame only.

		This is the walker search corridor, NOT a torso box.  Distinct from
		seed/solved boxes by color (amber #FBBF24 vs green/cyan) and role.
		acceptance_box = (x_min, y_min, x_max, y_max) in PROCESSED frame
		coords; subtract roi_origin once to convert to tile-local.
		"""
		if not (debug_row.step == 0 and trace.acceptance_box is not None):
			return
		box_bgr = walk_palette.get_walker_overlay_color_bgr("acceptance_box")
		box_x_min, box_y_min, box_x_max, box_y_max = trace.acceptance_box
		# Convert to tile-local (single subtraction, no bin upscale).
		local_x1 = int(box_x_min - roi_x_origin)
		local_y1 = int(box_y_min - roi_y_origin)
		local_x2 = int(box_x_max - roi_x_origin)
		local_y2 = int(box_y_max - roi_y_origin)
		# Clamp to tile bounds.
		local_x1 = max(0, min(local_x1, roi_w - 1))
		local_y1 = max(0, min(local_y1, roi_h - 1))
		local_x2 = max(0, min(local_x2, roi_w))
		local_y2 = max(0, min(local_y2, roi_h))
		# Draw dashed rectangle (not solid, not heavy: clearly not a torso box).
		draw_utils.draw_dashed_rect(
			canvas[0],
			local_x1, local_y1, local_x2, local_y2,
			color=box_bgr,
			thickness=thickness,
			dash_len=10,
		)

	def _layer_audit_square():
		"""AUDIT-MODE DISAGREEMENT MARKER: small open square when production_winner != audit_winner."""
		if not (debug_row.winner_mode == "audit_winner" and
			debug_row.production_winner_cx is not None and
			debug_row.audit_winner_cx is not None):
			return
		prod_pt = (debug_row.production_winner_cx, debug_row.production_winner_cy)
		audit_pt = (debug_row.audit_winner_cx, debug_row.audit_winner_cy)
		# Small threshold for "different" (not exact equality due to floating point)
		dist = math.sqrt(
			(prod_pt[0] - audit_pt[0]) ** 2 +
			(prod_pt[1] - audit_pt[1]) ** 2
		)
		if dist <= 0.5:
			return
		# Draw small open square at production winner location
		local_prod_cx = int(debug_row.production_winner_cx - roi_x_origin)
		local_prod_cy = int(debug_row.production_winner_cy - roi_y_origin)
		disagreement_bgr = walk_palette.get_walker_overlay_color_bgr("audit_disagreement")
		square_half = walk_draw.compute_sq_half_px(torso_h_px)
		cv2.rectangle(
			canvas[0],
			(local_prod_cx - square_half, local_prod_cy - square_half),
			(local_prod_cx + square_half, local_prod_cy + square_half),
			color=disagreement_bgr,
			thickness=thickness,
			lineType=cv2.LINE_AA,
		)

	def _layer_residual_line():
		"""RESIDUAL LINE: pred -> candidate, colored by gate decision.

		Drawn for accepted (green) and soft_miss_no_path (red: candidate present
		but no plausible path survived the displacement cap).
		Also drawn for legacy v12 rejected_motion_gate (backward compat).
		"""
		if debug_row.cand_cx is None or debug_row.cand_cy is None:
			return
		if debug_row.status not in ("accepted", "soft_miss_no_path", "rejected_motion_gate"):
			return
		cand_local_cx = int(debug_row.cand_cx - roi_x_origin)
		cand_local_cy = int(debug_row.cand_cy - roi_y_origin)
		walk_draw._draw_residual_line(
			canvas[0],
			prior_local_cx, prior_local_cy,
			cand_local_cx, cand_local_cy,
			accepted=(debug_row.status == "accepted"),
			thickness=thickness,
		)

	def _layer_allowed_jump_circle():
		"""ALLOWED-JUMP CIRCLE: dashed circle centered on pred with radius max_displacement_px.

		Uses schema field max_displacement_px; silently skipped when absent.
		"""
		if max_displacement_px is None or max_displacement_px <= 0:
			return
		circle_color = overlay_config.hex_to_bgr(_WALK_PALETTE['allowed_jump_circle'])
		walk_draw._draw_allowed_jump_circle(
			canvas[0],
			prior_local_cx, prior_local_cy,
			max_displacement_px,
			circle_color,
			thickness=thickness,
		)

	def _layer_velocity_vector():
		"""VELOCITY VECTOR: arrowed line from pred in the (vx*dt, vy*dt) direction.

		Uses schema fields vx_px/vy_px/dt; silently skipped when absent.
		"""
		if vx_px is None or vy_px is None or debug_row.dt is None:
			return
		vec_color = overlay_config.hex_to_bgr(_WALK_PALETTE['velocity_vector'])
		walk_draw._draw_velocity_vector(
			canvas[0],
			prior_local_cx, prior_local_cy,
			vx_px, vy_px,
			float(debug_row.dt),
			vec_color,
			thickness=thickness,
		)

	# Map layer names to callables (closed set; must match _KNOWN_LAYERS in walk_palette).
	layer_dispatch = {
		"heat": _layer_heat,
		"seed_box": _layer_seed_box,
		"solved_box": _layer_solved_box,
		"blob_ellipses": _layer_blob_ellipses,
		"plus_marker": _layer_plus_marker,
		"acceptance_box": _layer_acceptance_box,
		"audit_square": _layer_audit_square,
		"residual_line": _layer_residual_line,
		"allowed_jump_circle": _layer_allowed_jump_circle,
		"velocity_vector": _layer_velocity_vector,
	}

	# Resolve draw order from config (missing key falls back to built-in default;
	# unknown/duplicate/omitted names raise loudly in resolve_layer_order).
	layer_order = walk_palette.resolve_layer_order()

	# Draw each layer in the resolved order onto canvas[0].
	for layer_name in layer_order:
		layer_dispatch[layer_name]()

	# Save PNG
	success = cv2.imwrite(str(out_png_path), canvas[0])
	if not success:
		raise RuntimeError(
			f"render_walk_tile: failed to write PNG to {out_png_path}"
		)

	return render_manifest
