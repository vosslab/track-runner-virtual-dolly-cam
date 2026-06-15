"""Fixed-ROI heat-movie frame compositor and raw-BGR spill.

This module builds the per-frame imagery for the OPTIONAL `--heat-movie`
diagnostic. The `--heat-movie` CLI flag, the driver wiring, and the ffmpeg
encode are handled in heat_movie_encode.py; nothing in here adds a flag,
spawns a subprocess, or calls ffmpeg.

Two responsibilities:

  1. compute_fixed_heat_roi_size(seed1_box, seed2_box) -> (roiW, roiH):
     a fixed window size for the whole interval, computed ONCE from the larger
     of the two bracketing seed boxes (by torso height). It reuses the solver's
     own ROI rule (residual_motion.ROI_MULTIPLIER and the same min-half-side
     clamp as residual_motion._compute_roi) so the heat window matches the
     window the observer actually scores. A single constant size lets the ffmpeg
     call pass one `-s WxH` for the whole sequence.

  2. build_heat_movie_frame(...): for one frame, build a BGR image whose shape
     is ALWAYS exactly (roiH, roiW, 3): a fixed (roiW, roiH) window centered on
     the solved box, with the JET motion-cue heat (from
     residual_heat_map.compute_heat_map_roi) pasted in by absolute PROCESSED
     pixel coordinates, the solved torso box drawn with walk_draw styling, and
     the in-box hot-mean drawn as text. Where the fixed window extends past
     a frame edge (or where the heat composite does not cover), the pixels stay
     BLACK-FILLED. The window is NEVER shifted (that would move the box
     off-center) and NEVER scaled (that would distort overlay coordinates). The
     finished frame is written to disk as raw headerless BGR bytes
     (`frame_%06d.bgr`, exactly roiW*roiH*3 bytes) and the path is returned; the
     array is not held long-term.

Memory: this compositor builds one frame, writes it, and returns the path. It
never accumulates frames. Decode is already capped by the bounded gray cache
(residual_motion.MAX_GRAY_CACHE_FRAMES) inside compute_heat_map_roi, so no new
frame-limit constant is introduced here.

Disk: the raw ROI frames are uncompressed but ROI-sized (not full-frame), so the
on-disk footprint is far smaller than full frames; the per-interval frame count
is bounded and the encode step deletes the scratch dir at run end.

Coordinate spaces: everything here is PROCESSED-space. The solved box is a typed
PROCESSED ProcessedBox; the reader reports PROCESSED width/height and decodes
PROCESSED frames; compute_heat_map_roi's ROI origin is in PROCESSED pixels. This
OPTIONAL compositor MAY read frame imagery: compute_heat_map_roi decodes the
center frame plus its warp neighbors through the bounded gray cache.
"""

# Standard Library
import os

# PIP3 modules
import numpy

# shared sys.path bootstrap (track_runner, tests, repo root, blob_walk_v2)
import walk_paths
_REPO_ROOT = walk_paths.setup()

# PIP3 modules (after bootstrap so cv2 resolves consistently with siblings)
import cv2

# local repo modules
import residual_motion
import residual_heat_map
import overlay_config
import walk_palette
import walk_draw
import common_tools.coord_space
import common_tools.in_box_heat

# Load the same overlay palette the walker tiles use, so the solved box on the
# heat movie matches the solved box drawn on the walk tiles.
_HEAT_PALETTE = walk_palette.load_walk_overlays()


#============================================
def compute_fixed_heat_roi_size(
	seed1_box: common_tools.coord_space.ProcessedBox,
	seed2_box: common_tools.coord_space.ProcessedBox,
) -> tuple:
	"""Compute the fixed (roiW, roiH) heat window for one interval.

	The window size is fixed ONCE per interval from the larger of the two
	bracketing seed boxes (by torso height) using the solver's ROI rule. This
	reuses residual_motion.ROI_MULTIPLIER and the identical min-half-side clamp
	found in residual_motion._compute_roi, so the heat window matches the crop
	the observer scores rather than inventing new ROI math. The result is a
	square window (matching _compute_roi's square crop), constant across every
	frame in the interval so ffmpeg gets one `-s`.

	Args:
		seed1_box: one bracketing seed torso box (PROCESSED space).
		seed2_box: the other bracketing seed torso box (PROCESSED space).

	Returns:
		(roiW, roiH): the fixed window size in PROCESSED pixels. Always square
		and even (2 * half_side), so a centered window has integer extents.
	"""
	# Guard both seeds: a space mismatch must fail loud, not silently scale.
	common_tools.coord_space.require_processed_box(seed1_box)
	common_tools.coord_space.require_processed_box(seed2_box)
	# Pick the larger seed by torso height (drives the ROI rule, matching the
	# observer's pred_h-based crop).
	larger_h = max(float(seed1_box.h), float(seed2_box.h))
	# Mirror residual_motion._compute_roi: half_side = ROI_MULTIPLIER*h/2, with
	# the same minimum-half-side floor of 50 px. We intentionally do NOT apply
	# _compute_roi's frame clamp here: the window size must stay constant for
	# the whole interval, and edge clamping is handled per-frame by black-fill.
	half_side = int(residual_motion.ROI_MULTIPLIER * larger_h / 2)
	half_side = max(half_side, 50)
	side = 2 * half_side
	roi_size = (side, side)
	return roi_size


#============================================
def _draw_hot_mean_text(
	frame: numpy.ndarray,
	hot_mean,
	hot_count: int,
) -> None:
	"""Draw the in-box hot-mean (and count) as overlay text, in-place.

	Args:
		frame: BGR canvas to draw on (modified in-place).
		hot_mean: mean DoG over in-box hot pixels, or None when no pixel
			qualified (drawn as "n/a").
		hot_count: number of in-box hot pixels.
	"""
	# Format the metric; None means no qualifying pixel this frame.
	if hot_mean is None:
		label = f"heat: n/a (n={hot_count})"
	else:
		label = f"heat: {hot_mean:.1f} (n={hot_count})"
	# Reuse the walk overlay text color so the movie matches the tiles.
	color = overlay_config.hex_to_bgr(_HEAT_PALETTE['residual_accept'])
	# Top-left corner, small inset; outline-free simplex matches the tiles.
	cv2.putText(
		frame,
		label,
		(6, 22),
		cv2.FONT_HERSHEY_SIMPLEX,
		0.6,
		color,
		1,
		cv2.LINE_AA,
	)


#============================================
def _paste_composite_into_window(
	canvas: numpy.ndarray,
	composite: numpy.ndarray,
	composite_origin: tuple,
	window_x1: int,
	window_y1: int,
) -> None:
	"""Paste a heat composite into the fixed window canvas by PROCESSED coords.

	The canvas spans PROCESSED pixels [window_x1, window_x1 + roiW) by
	[window_y1, window_y1 + roiH). The composite spans PROCESSED pixels
	[composite_origin_x, ...). Pixels are aligned by their shared PROCESSED
	coordinate; any canvas pixel not covered by the composite stays BLACK
	(off-frame or outside the heat ROI). No shifting and no scaling occurs:
	composite pixels land at their true PROCESSED location minus the window
	origin.

	Args:
		canvas: (roiH, roiW, 3) uint8 BGR canvas (modified in-place).
		composite: (ch, cw, 3) uint8 BGR heat composite.
		composite_origin: (ox, oy) top-left of the composite in PROCESSED px.
		window_x1: PROCESSED x of the canvas top-left.
		window_y1: PROCESSED y of the canvas top-left.
	"""
	canvas_h, canvas_w = canvas.shape[0], canvas.shape[1]
	comp_h, comp_w = composite.shape[0], composite.shape[1]
	ox, oy = int(composite_origin[0]), int(composite_origin[1])
	# Composite -> canvas offset (PROCESSED origin difference).
	dx = ox - window_x1
	dy = oy - window_y1
	# Overlap rectangle in canvas coordinates.
	cx1 = max(0, dx)
	cy1 = max(0, dy)
	cx2 = min(canvas_w, dx + comp_w)
	cy2 = min(canvas_h, dy + comp_h)
	# No overlap: leave the canvas fully black.
	if cx2 <= cx1 or cy2 <= cy1:
		return
	# Corresponding source rectangle in composite coordinates.
	sx1 = cx1 - dx
	sy1 = cy1 - dy
	sx2 = sx1 + (cx2 - cx1)
	sy2 = sy1 + (cy2 - cy1)
	canvas[cy1:cy2, cx1:cx2] = composite[sy1:sy2, sx1:sx2]


#============================================
def build_heat_movie_frame(
	reader: object,
	frame_index: int,
	scene_transform: object,
	solved_box: common_tools.coord_space.ProcessedBox,
	roi_size: tuple,
	scratch_dir: str,
	fps: float,
	threshold: float = residual_motion.DEFAULT_THRESHOLD,
) -> str:
	"""Build one fixed-shape heat-movie frame and spill it to disk as raw BGR.

	The output image is ALWAYS exactly (roiH, roiW, 3): a fixed (roiW, roiH)
	window centered on the solved box center. The JET motion-cue heat composite
	from residual_heat_map.compute_heat_map_roi is pasted in by absolute
	PROCESSED pixel coordinates; the solved torso box is drawn with walk_draw
	styling (cyan dashed normal, matching the walk tiles); the in-box
	hot-mean is drawn as text. Any region the window covers that lies past a
	frame edge, or that the heat composite does not cover, stays BLACK-FILLED.
	The window is NEVER shifted and NEVER scaled.

	When compute_heat_map_roi returns None for this frame (no residual
	available, e.g. an interval-edge frame with no usable neighbor), the output
	is still the fixed-shape window: black base, solved box, and an "n/a" hot
	label. The shape and byte count are identical to a normal frame.

	The finished frame is written to `<scratch_dir>/frame_%06d.bgr` as
	contiguous raw BGR bytes (exactly roiW*roiH*3 bytes) and the path is
	returned. The array is not retained after the write.

	Args:
		reader: VideoReader-compatible reader (PROCESSED .width/.height and
			.read_frame). Passed straight to compute_heat_map_roi.
		frame_index: index of the frame to render.
		scene_transform: SceneTransform for the residual primitive's warp.
		solved_box: the solved torso box for this frame (PROCESSED space).
		roi_size: the fixed (roiW, roiH) from compute_fixed_heat_roi_size.
		scratch_dir: run-scoped scratch directory (owned by the encode step).
		fps: source frame rate, forwarded to compute_heat_map_roi for stride.
		threshold: motion-cue intensity threshold for the in-box hot-mean.
			Defaults to the single-source residual_motion.DEFAULT_THRESHOLD so
			the movie's hot-mean uses the same threshold as the manifest metric.

	Returns:
		str: absolute path to the written `frame_%06d.bgr` file.
	"""
	# Guard the solved box: a space mismatch must fail loud.
	common_tools.coord_space.require_processed_box(solved_box)
	roi_w, roi_h = int(roi_size[0]), int(roi_size[1])
	# Fixed-shape black canvas: any uncovered pixel (off-frame or outside the
	# heat ROI) stays black, so the output is ALWAYS exactly (roi_h, roi_w, 3).
	canvas = numpy.zeros((roi_h, roi_w, 3), dtype=numpy.uint8)

	# Window top-left in PROCESSED pixels, centered on the solved box. Integer
	# extents: roi_w/roi_h are even, and we floor the center once. The window is
	# placed exactly here regardless of frame edges (black-fill, never shift).
	window_x1 = int(numpy.floor(solved_box.cx - roi_w / 2.0))
	window_y1 = int(numpy.floor(solved_box.cy - roi_h / 2.0))

	# Compute the JET heat composite at the observer's own ROI (a separate rect
	# from our fixed window). out_arrays gives us the residual DoG + validity +
	# ROI origin needed for the in-box hot-mean. This is the only frame
	# imagery read; compute_heat_map_roi decodes through the bounded gray cache.
	out_arrays = {}
	pred_center = (float(solved_box.cx), float(solved_box.cy))
	pred_box = (float(solved_box.w), float(solved_box.h))
	heat_result = residual_heat_map.compute_heat_map_roi(
		reader, frame_index, scene_transform,
		pred_center=pred_center, pred_box=pred_box,
		fps=fps, threshold=float(threshold),
		out_arrays=out_arrays,
	)

	hot_mean = None
	hot_count = 0
	if heat_result is not None:
		composite, composite_origin = heat_result
		# Paste the heat composite into the fixed window by PROCESSED coords.
		_paste_composite_into_window(
			canvas, composite, composite_origin, window_x1, window_y1,
		)
		# in-box hot-mean from the SAME residual arrays the composite used
		# (single-source threshold). roi_bounds is (x1, y1, x2, y2); origin is
		# its top-left.
		residual_dog = out_arrays['residual_dog']
		validity_mask = out_arrays['validity_mask']
		roi_bounds = out_arrays['roi_bounds']
		roi_origin = (roi_bounds[0], roi_bounds[1])
		hot_mean, hot_count = common_tools.in_box_heat.measure_in_box_heat(
			residual_dog, validity_mask, roi_origin, solved_box, float(threshold),
		)

	# Draw the solved box with walk_draw styling (cyan dashed normal), using the
	# single processed->tile-local conversion with the window origin as the ROI
	# origin so the box lands at its true position inside the fixed window.
	solved_edges = walk_draw.processed_box_to_tile_local(
		solved_box, (window_x1, window_y1),
	)
	# Match walk_render.render_walk_tile: solved box uses the "blended"
	# prediction color, drawn dashed + normal.
	solved_color = overlay_config.get_prediction_bgr("blended")
	base_thickness = walk_draw._compute_thickness_from_torso_h(float(solved_box.h))
	walk_draw._draw_torso_box_dashed_normal_edges(
		canvas, solved_edges, solved_color, base_thickness,
	)

	# Draw the in-box hot-mean text last so it sits on top.
	_draw_hot_mean_text(canvas, hot_mean, hot_count)

	# Spill to disk as raw headerless BGR. tobytes() on a C-contiguous uint8
	# canvas yields exactly roi_w*roi_h*3 bytes; ffmpeg reads it as bgr24.
	frame_name = f"frame_{frame_index:06d}.bgr"
	frame_path = os.path.join(scratch_dir, frame_name)
	contiguous = numpy.ascontiguousarray(canvas)
	with open(frame_path, 'wb') as handle:
		handle.write(contiguous.tobytes())
	return frame_path
