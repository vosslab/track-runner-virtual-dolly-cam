"""Video encoding module for track_runner.

Handles video cropping via numpy and encoding via ffmpeg subprocess pipe.
"""

# Standard Library
import os
import math
import shutil
import subprocess
import concurrent.futures

# PIP3 modules
import cv2
import numpy

# local repo modules (BlockBarColumn for wide block-character progress bars)
import interval_solver
import tr_crop
import overlay_config
import video_io
import key_input
import draw_utils
import common_tools.frame_filters as frame_filters
import common_tools.frame_reader
import common_tools.probe_video


#============================================
def _input_has_audio(input_path: str) -> bool:
	"""Check whether a video file contains an audio stream.

	Args:
		input_path: Path to the video file.

	Returns:
		True if at least one audio stream is detected.
	"""
	ffprobe_path = shutil.which("ffprobe")
	if ffprobe_path is None:
		raise RuntimeError("ffprobe not found in PATH")
	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "a",
		"-show_entries", "stream=index",
		"-of", "csv=p=0",
		input_path,
	]
	result = subprocess.run(cmd, capture_output=True, text=True)
	# if any audio stream index was printed, audio exists
	has_audio = len(result.stdout.strip()) > 0
	return has_audio


#============================================
def copy_audio(
	input_path: str,
	video_path: str,
	output_path: str,
) -> None:
	"""Mux audio from input video with video from cropped output.

	If the input file has no audio stream, the video file is
	copied directly to the output path.

	Args:
		input_path: Path to original video with audio.
		video_path: Path to cropped video (video only).
		output_path: Path for the final muxed output.
	"""
	ffmpeg_path = shutil.which("ffmpeg")
	if ffmpeg_path is None:
		raise RuntimeError("ffmpeg not found in PATH")
	# when the input has no audio, still remux through ffmpeg so the
	# output container matches the destination extension (mkvmerge writes
	# Matroska bytes regardless of the temp file's extension; a plain
	# byte-copy from a .tmp.mp4 to a .mp4 destination would mislabel the
	# container).
	if not _input_has_audio(input_path):
		print(f"No audio stream in {input_path}, remuxing video only")
		cmd = [
			ffmpeg_path,
			"-y",
			"-i", video_path,
			"-c:v", "copy",
			output_path,
		]
		result = subprocess.run(cmd, capture_output=True, text=True)
		if result.returncode != 0:
			raise RuntimeError(
				f"ffmpeg remux (no audio) failed with code "
				f"{result.returncode}: {result.stderr}"
			)
		return
	cmd = [
		ffmpeg_path,
		"-y",
		"-i", video_path,
		"-i", input_path,
		"-c:v", "copy",
		"-c:a", "aac",
		"-map", "0:v:0",
		"-map", "1:a:0",
		"-shortest",
		output_path,
	]
	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(
			f"ffmpeg mux failed with code {result.returncode}: "
			f"{result.stderr}"
		)


#============================================
def encode_cropped_video(
	reader: common_tools.frame_reader.FrameReader,
	crop_rects: list,
	output_path: str,
	crop_width: int,
	crop_height: int,
	codec: str = "libx264",
	crf: int = 18,
	frame_states: list | None = None,
	encode_filters: list | None = None,
	run_control: object = None,
	key_reader_obj: object = None,
	draw_tracking: bool = False,
	draw_debug: bool = False,
	draw_velocity: bool = False,
) -> None:
	"""Read all frames, apply crops, and write encoded output.

	When any of draw_tracking / draw_debug / draw_velocity is True and
	frame_states is provided, the corresponding overlay tier is burned
	into the cropped frames. See draw_debug_overlay_cropped() for the
	tier semantics.

	Args:
		reader: An open FrameReader instance.
		crop_rects: List of (x, y, w, h) tuples, one per frame.
		output_path: Path for the output video file.
		crop_width: Output frame width after resize.
		crop_height: Output frame height after resize.
		codec: Video codec (default libx264).
		crf: Constant Rate Factor (default 18).
		frame_states: List of per-frame state dicts from tracker.
		encode_filters: Ordered list of filter names to apply during encode.
		draw_tracking: Draw the review overlay (torso box + crosshair).
		draw_debug: Draw the developer overlay (implies tracking).
		draw_velocity: Draw the per-frame motion arrow.
	"""
	fps = reader.fps
	# build ffmpeg vf string from encode filters
	vf_string = ""
	if encode_filters:
		vf_string = frame_filters.get_ffmpeg_vf_string(encode_filters)
	writer = video_io.VideoWriter(
		output_path, crop_width, crop_height, fps,
		codec=codec, crf=crf, vf_string=vf_string,
	)
	frame_count = len(crop_rects)
	# Shared progress bar: identical column layout to solve Stages 1/3/4.
	with interval_solver.make_solve_progress() as progress:
		task = progress.add_task("  encoding", total=frame_count)
		for frame_index, frame in reader:
			# stop once we have processed all provided crop rects
			if frame_index >= frame_count:
				break
			# crop the frame using the crop module
			crop_rect = crop_rects[frame_index]
			cropped = tr_crop.apply_crop(frame, crop_rect)
			# adaptive interpolation: INTER_AREA for downscaling (avoids aliasing),
			# INTER_LANCZOS4 for upscaling (preserves detail)
			crop_h = crop_rect[3]
			interp = cv2.INTER_AREA if crop_h >= crop_height else cv2.INTER_LANCZOS4
			resized = cv2.resize(cropped, (crop_width, crop_height), interpolation=interp)
			# apply opencv encode filters after resize
			if encode_filters:
				resized = frame_filters.apply_filter_pipeline(resized, encode_filters)
			# draw overlays on the cropped frame when any tier requested.
			# prev_center is precomputed on the driver side and stashed
			# on each per-frame state dict so the parallel encoder path
			# sees correct lookback across chunk boundaries (a chunk-local
			# walk would miss valid priors from preceding chunks).
			any_overlay = draw_tracking or draw_debug or draw_velocity
			if any_overlay and frame_states is not None:
				state = frame_states[frame_index] if frame_index < len(frame_states) else None
				prev_center = state.get("prev_center") if state is not None else None
				draw_debug_overlay_cropped(
					resized, state, crop_rect, crop_width, crop_height,
					draw_tracking=draw_tracking,
					draw_debug=draw_debug,
					draw_velocity=draw_velocity,
					prev_center=prev_center,
				)
			writer.write_frame(resized)
			progress.update(task, advance=1)
			# poll for quit key every 30 frames (pause not supported during encode)
			if run_control is not None and frame_index % 30 == 0:
				if key_reader_obj is not None:
					ch = key_reader_obj.poll()
					if ch is not None and ch.lower() == "q":
						run_control.request_quit()
						key_input._quit_trace("KEY_HANDLE", quit_requested=True)
						progress.console.print("  Q pressed, finishing current interval...")
				if run_control.quit_requested:
					key_input._quit_trace(
						"MAIN_LOOP", context="encode_sequential",
						quit_requested=True, frame=frame_index,
					)
					progress.console.print(
						f"  encoding interrupted at frame {frame_index}/{frame_count}"
					)
					break
	# ffmpeg may still be encoding frames through its filter pipeline
	# after all input has been written (especially with heavy filters)
	print("  finalizing ffmpeg encode...", flush=True)
	writer.close()
	# trace early return on quit
	if run_control is not None and run_control.quit_requested:
		key_input._quit_trace(
			"FUNCTION_RETURN", context="encode_sequential",
			quit_requested=True,
		)


#============================================
# color lookups for debug overlay drawing (v2) - from overlay_styles.yaml
def _source_color(source: str, seed_status: str = "") -> tuple:
	"""Return a BGR color for a v2 tracking source label.

	Seed frames are further distinguished by seed_status (visible,
	partial, approximate). Non-seed sources ignore seed_status.
	Colors are loaded from overlay_styles.yaml via overlay_config.

	Args:
		source: Frame source string from tracker pipeline.
		seed_status: Seed annotation status (visible, partial, approximate).

	Returns:
		Tuple of (B, G, R) color values.
	"""
	color = overlay_config.get_source_bgr(source, seed_status)
	return color


#============================================
def _box_to_crop_coords(
	box: list,
	crop_rect: tuple,
	out_w: int,
	out_h: int,
) -> tuple:
	"""Convert a [cx, cy, w, h] full-frame box to crop-space pixel coords.

	Args:
		box: [cx, cy, w, h] in full-frame coordinates.
		crop_rect: (x, y, w, h) crop region in full-frame coordinates.
		out_w: Output frame width in pixels.
		out_h: Output frame height in pixels.

	Returns:
		Tuple of (x1, y1, x2, y2) pixel coordinates in crop-space.
	"""
	crop_x, crop_y, crop_w, crop_h = crop_rect
	scale_x = out_w / crop_w if crop_w > 0 else 1.0
	scale_y = out_h / crop_h if crop_h > 0 else 1.0
	bcx, bcy, bw, bh = box
	# transform center from full-frame to crop-space
	cx_in_crop = (bcx - crop_x) * scale_x
	cy_in_crop = (bcy - crop_y) * scale_y
	bw_scaled = bw * scale_x
	bh_scaled = bh * scale_y
	x1 = int(cx_in_crop - bw_scaled / 2.0)
	y1 = int(cy_in_crop - bh_scaled / 2.0)
	x2 = int(cx_in_crop + bw_scaled / 2.0)
	y2 = int(cy_in_crop + bh_scaled / 2.0)
	return (x1, y1, x2, y2)


#============================================
def _point_to_crop_coords(
	cx: float,
	cy: float,
	crop_rect: tuple,
	out_w: int,
	out_h: int,
) -> tuple:
	"""Convert a single full-frame (cx, cy) to crop-space float coords.

	Returns floats so downstream callers (e.g. velocity-arrow angle
	via atan2) keep subpixel precision; truncating here was the root
	cause of the ~10-15 degree angle quantization in the velocity
	arrow. Round to int only at the cv2 draw call.

	Args:
		cx: Full-frame x coordinate.
		cy: Full-frame y coordinate.
		crop_rect: (x, y, w, h) crop region in full-frame coordinates.
		out_w: Output frame width in pixels.
		out_h: Output frame height in pixels.

	Returns:
		Tuple of (x, y) float pixel coordinates in crop-space.
	"""
	crop_x, crop_y, crop_w, crop_h = crop_rect
	scale_x = out_w / crop_w if crop_w > 0 else 1.0
	scale_y = out_h / crop_h if crop_h > 0 else 1.0
	x_in_crop = (cx - crop_x) * scale_x
	y_in_crop = (cy - crop_y) * scale_y
	return (x_in_crop, y_in_crop)


#============================================
# Velocity-arrow tunables. The gain amplifies frame-to-frame motion
# so the arrow is legible at typical fps; gain is the only length
# knob (no cap), so doubling it doubles the visible arrow length.
_VELOCITY_GAIN = 9.0
# bounded look-back so a single dropped frame still produces a vector
# but a long not-in-frame gap does not
_VELOCITY_LOOKBACK_FRAMES = 5


#============================================
def _find_prev_valid_center(frame_states: list, index: int) -> tuple | None:
	"""Walk back from index-1 up to _VELOCITY_LOOKBACK_FRAMES for a valid prior.

	A valid prior state has finite cx and cy. Used to anchor the
	velocity-arrow's direction when the immediately-prior frame is
	missing or invalid.

	Args:
		frame_states: List of per-frame state dicts (or None entries).
		index: Current frame index; the search starts at index-1.

	Returns:
		(cx, cy) tuple in full-frame coords, or None if no valid prior
		state is found within the lookback window.
	"""
	if frame_states is None:
		return None
	start = index - 1
	stop = max(-1, index - 1 - _VELOCITY_LOOKBACK_FRAMES)
	for k in range(start, stop, -1):
		if k < 0 or k >= len(frame_states):
			continue
		st = frame_states[k]
		if st is None:
			continue
		cx = st.get("cx")
		cy = st.get("cy")
		if cx is None or cy is None:
			continue
		if not (math.isfinite(cx) and math.isfinite(cy)):
			continue
		return (float(cx), float(cy))
	return None


#============================================
def _compute_overlay_scale(
	out_h: int,
	box_h_px: float = 0.0,
) -> float:
	"""Compute a draw-scale factor for debug overlay elements.

	Scales relative to 1080p as the reference resolution, then adjusts
	down when the tracked box is small relative to the frame (zoomed out).

	Args:
		out_h: Output frame height in pixels.
		box_h_px: Tracked box height in crop-space pixels (0 if unknown).

	Returns:
		Float scale factor (1.0 at 1080p with box filling ~30% of frame).
	"""
	# base scale: normalize to 1080p
	res_scale = out_h / 1080.0
	# box scale: how much of the frame the runner fills
	if box_h_px > 0 and out_h > 0:
		fill_ratio = box_h_px / out_h
		# clamp: don't go below 0.3x or above 1.0x from box size
		box_factor = max(0.3, min(1.0, fill_ratio / 0.3))
	else:
		box_factor = 0.5
	scale = res_scale * box_factor
	# floor at 0.25 so things stay visible on very small outputs
	scale = max(0.25, scale)
	return scale


#============================================
# overlay transparency loaded from overlay_styles.yaml
_OVERLAY_ALPHA_BOXES = overlay_config.get_encoder_overlay_opacity("box")
_OVERLAY_ALPHA_TEXT = overlay_config.get_encoder_overlay_opacity("text")


#============================================
def draw_debug_overlay_cropped(
	frame: numpy.ndarray,
	state: dict | None,
	crop_rect: tuple,
	out_w: int,
	out_h: int,
	debug_state: dict | None = None,
	draw_tracking: bool = False,
	draw_debug: bool = False,
	draw_velocity: bool = False,
	prev_center: tuple | None = None,
) -> None:
	"""Draw the encode-overlay set on a cropped/resized frame in-place.

	Three independent tiers gate which elements get drawn:

	- draw_tracking: torso box + crosshair only. Optimized for legibility
	  during playback ("is the tracker on the runner?").
	- draw_debug: tracking elements plus FWD/BWD prediction boxes,
	  competitor box, raw box, source/confidence labels, interval ID,
	  per-frame diagnostic text. Optimized for offline frame-by-frame
	  review.
	- draw_velocity: per-frame motion arrow anchored at the current
	  crosshair, pointing in the direction of motion since the most
	  recent valid prior center.

	draw_debug implies draw_tracking by construction (the caller derives
	draw_tracking = draw_tracking_overlay or draw_debug_overlay).

	All drawing elements scale with output resolution and tracked box
	size, so overlays remain proportional whether zoomed in or out.
	Boxes and text are drawn with transparency via alpha blending.

	Args:
		frame: BGR image (already cropped and resized to out_w x out_h).
		state: v2 per-frame state dict with keys: cx, cy, w, h, conf,
			source. None if the frame had no tracking data.
		crop_rect: Crop rectangle as (x, y, w, h) in full-frame coords.
		out_w: Output frame width in pixels.
		out_h: Output frame height in pixels.
		debug_state: Optional dict with additional debug boxes and labels.
		draw_tracking: Draw the torso box and center crosshair.
		draw_debug: Draw the full developer overlay (implies tracking).
		draw_velocity: Draw the per-frame motion arrow.
		prev_center: (cx, cy) of the most recent valid prior frame in
			full-frame coords, or None if no valid prior is available.
	"""
	# nothing requested: short-circuit
	if not (draw_tracking or draw_debug or draw_velocity):
		return

	if state is None:
		# only the developer tier shows the NO DATA marker; the review
		# tier stays clean on missing frames
		if draw_debug:
			s = _compute_overlay_scale(out_h)
			font_scale = max(0.3, 0.5 * s)
			thickness = max(1, int(1.5 * s))
			cv2.putText(
				frame, "NO DATA", (10, int(25 * s)),
				cv2.FONT_HERSHEY_SIMPLEX, font_scale,
				overlay_config.get_source_bgr("lost"), thickness,
			)
		return

	# extract v2 state fields
	source = state.get("source", "")
	seed_status = state.get("seed_status", "")
	conf = state["conf"]
	cx = state.get("cx")
	cy = state.get("cy")
	w = state.get("w")
	h = state.get("h")

	# compute box height in crop-space pixels for scale computation
	box_h_px = 0.0
	if h is not None:
		crop_x, crop_y, crop_w, crop_h = crop_rect
		scale_y = out_h / crop_h if crop_h > 0 else 1.0
		box_h_px = h * scale_y

	# compute drawing scale from resolution and box size
	s = _compute_overlay_scale(out_h, box_h_px)

	# scaled drawing parameters
	# line thickness: 1px at small scale, up to 2-3px at full scale
	thin_line = max(1, int(1.0 * s))
	med_line = max(1, int(1.5 * s))
	outline_line = max(1, int(2.5 * s))
	# crosshair length scales with box size
	cross_len = max(4, int(8 * s))
	# text sizes
	font_scale_large = max(0.25, 0.45 * s)
	font_scale_med = max(0.2, 0.38 * s)
	font_scale_small = max(0.2, 0.35 * s)
	text_thick = max(1, int(1.2 * s))
	# dash length for dashed rects
	dash_len = max(4, int(8 * s))
	# confidence bar dimensions
	bar_w = max(30, int(70 * s))
	bar_h = max(4, int(8 * s))
	# text vertical positions
	text_y1 = max(12, int(22 * s))
	text_y2 = max(24, int(42 * s))

	# resolve debug_state values safely; fall back to state for FWD/BWD boxes
	if debug_state is None:
		debug_state = {}
	forward_box = debug_state.get("forward_box") or state.get("forward_box")
	backward_box = debug_state.get("backward_box") or state.get("backward_box")
	competitor_box = debug_state.get("competitor_box")
	confidence_label = debug_state.get("confidence_label", "")
	interval_id = debug_state.get("interval_id", "")

	# draw all boxes and crosshair on an overlay for alpha blending
	overlay = frame.copy()

	# track whether the boxes-overlay layer was modified so we can skip
	# the alpha-blend when only velocity/text layers had work to do
	overlay_dirty = False
	# crosshair position is needed for the velocity arrow even when
	# draw_tracking is false (e.g. velocity arrow on top of clean
	# debug-overlay state); compute it once if we have geometry
	cross_cx: int | None = None
	cross_cy: int | None = None

	# developer-tier geometry: FWD/BWD/competitor/raw boxes
	if draw_debug:
		if forward_box is not None:
			fx1, fy1, fx2, fy2 = _box_to_crop_coords(forward_box, crop_rect, out_w, out_h)
			draw_utils.draw_dashed_rect(overlay, fx1, fy1, fx2, fy2,
				overlay_config.get_prediction_bgr("forward"),
				thickness=thin_line, dash_len=dash_len)
			overlay_dirty = True
		if backward_box is not None:
			bx1, by1, bx2, by2 = _box_to_crop_coords(backward_box, crop_rect, out_w, out_h)
			draw_utils.draw_dashed_rect(overlay, bx1, by1, bx2, by2,
				overlay_config.get_prediction_bgr("backward"),
				thickness=thin_line, dash_len=dash_len)
			overlay_dirty = True
		if competitor_box is not None:
			rx1, ry1, rx2, ry2 = _box_to_crop_coords(competitor_box, crop_rect, out_w, out_h)
			cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), (0, 0, 0), outline_line)
			cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2),
				overlay_config.get_source_bgr("competitor"), med_line)
			overlay_dirty = True

	# accepted torso track as solid box + crosshair (review tier and up)
	ax1 = ay1 = ax2 = ay2 = 0
	if cx is not None and cy is not None and w is not None and h is not None:
		# color the box by tracking source (seed, propagated, merged, etc.)
		box_color = _source_color(source, seed_status)
		accepted_box = [cx, cy, w, h]
		ax1, ay1, ax2, ay2 = _box_to_crop_coords(accepted_box, crop_rect, out_w, out_h)
		# crosshair coords are also used for the velocity arrow tail
		cross_cx = int((ax1 + ax2) / 2)
		cross_cy = int((ay1 + ay2) / 2)
		if draw_tracking:
			# black outline for contrast against any background
			cv2.rectangle(overlay, (ax1, ay1), (ax2, ay2), (0, 0, 0), outline_line)
			# solid source-colored accepted track box
			cv2.rectangle(overlay, (ax1, ay1), (ax2, ay2), box_color, med_line)
			# crosshair at box center
			cv2.line(overlay, (cross_cx - cross_len, cross_cy),
				(cross_cx + cross_len, cross_cy), (0, 0, 0), med_line)
			cv2.line(overlay, (cross_cx, cross_cy - cross_len),
				(cross_cx, cross_cy + cross_len), (0, 0, 0), med_line)
			cv2.line(overlay, (cross_cx - cross_len, cross_cy),
				(cross_cx + cross_len, cross_cy), box_color, thin_line)
			cv2.line(overlay, (cross_cx, cross_cy - cross_len),
				(cross_cx, cross_cy + cross_len), box_color, thin_line)
			# small filled dot at exact computed center for drift diagnosis
			cv2.circle(overlay, (cross_cx, cross_cy), max(2, int(3 * s)), box_color, -1)
			overlay_dirty = True

	# raw (pre-anchor) box as dashed gray outline (developer tier only)
	if draw_debug:
		raw_box = debug_state.get("raw_box")
		if raw_box is not None:
			rx1, ry1, rx2, ry2 = _box_to_crop_coords(raw_box, crop_rect, out_w, out_h)
			draw_utils.draw_dashed_rect(overlay, rx1, ry1, rx2, ry2, (200, 200, 200),
				thickness=thin_line, dash_len=dash_len)
			overlay_dirty = True

	# velocity arrow: anchored at the current crosshair, pointing in the
	# direction of motion since the most recent valid prior center.
	# Computed in floats end-to-end so atan2(dy, dx) keeps subpixel
	# precision; only the final cv2.arrowedLine endpoints are rounded.
	# No length cap: _VELOCITY_GAIN is the only length knob, so
	# doubling it visibly doubles the arrow length.
	if (
		draw_velocity
		and prev_center is not None
		and cx is not None
		and cy is not None
	):
		prev_cx, prev_cy = prev_center
		# float crop-space coords for both endpoints; the prior point
		# only contributes its displacement direction, not the tail
		prev_x_f, prev_y_f = _point_to_crop_coords(
			prev_cx, prev_cy, crop_rect, out_w, out_h,
		)
		cur_x_f, cur_y_f = _point_to_crop_coords(
			cx, cy, crop_rect, out_w, out_h,
		)
		dx = cur_x_f - prev_x_f
		dy = cur_y_f - prev_y_f
		mag = math.hypot(dx, dy)
		if mag > 0.5:
			# scale-amplify so frame-to-frame motion is legible
			amp_dx = dx * _VELOCITY_GAIN
			amp_dy = dy * _VELOCITY_GAIN
			tail_pt = (int(round(cur_x_f)), int(round(cur_y_f)))
			tip_pt = (
				int(round(cur_x_f + amp_dx)),
				int(round(cur_y_f + amp_dy)),
			)
			arrow_color = _source_color(source, seed_status)
			# black outline first for contrast, then source-color arrow
			cv2.arrowedLine(
				overlay, tail_pt, tip_pt,
				(0, 0, 0), max(2, outline_line), tipLength=0.3,
			)
			cv2.arrowedLine(
				overlay, tail_pt, tip_pt,
				arrow_color, max(1, med_line), tipLength=0.3,
			)
			overlay_dirty = True

	# blend box overlay onto frame with transparency only if anything drew
	if overlay_dirty:
		cv2.addWeighted(overlay, _OVERLAY_ALPHA_BOXES, frame, 1.0 - _OVERLAY_ALPHA_BOXES, 0, frame)

	# text overlay (labels, conf bar, diagnostic text) is developer-tier only
	if not draw_debug:
		return

	text_overlay = frame.copy()

	# build top-left info text: source and confidence value
	source_color = _source_color(source, seed_status)
	# include seed_status in the label when source is a seed type
	if seed_status and source in ("human", "seed"):
		source_label = f"src:{source}({seed_status})"
	elif source:
		source_label = f"src:{source}"
	else:
		source_label = "src:unknown"
	text_x = max(4, int(6 * s))

	# diagnostic: show crop vs box coords for first 10 frames
	frame_index = state.get("frame_index", -1)
	if frame_index >= 0 and frame_index < 10 and cx is not None:
		diag_lines = [
			f"f{frame_index} box:({cx:.0f},{cy:.0f})"
			+ f" crop:({crop_rect[0]},{crop_rect[1]},{crop_rect[2]},{crop_rect[3]})",
			f"  out:({out_w},{out_h})"
			+ f" ax1,ay1:({ax1},{ay1})",
		]
		diag_y = out_h - max(30, int(40 * s))
		for line in diag_lines:
			cv2.putText(
				text_overlay, line, (text_x, diag_y),
				cv2.FONT_HERSHEY_SIMPLEX, font_scale_small,
				(255, 255, 255), thin_line,
			)
			diag_y += max(12, int(16 * s))
	cv2.putText(text_overlay, source_label, (text_x, text_y1),
		cv2.FONT_HERSHEY_SIMPLEX, font_scale_large, source_color, text_thick)

	# confidence label: HIGH / MED / LOW from debug_state, or compute fallback
	if confidence_label:
		conf_text = f"conf:{confidence_label} ({conf:.2f})"
	else:
		conf_text = f"conf:{conf:.2f}"
	cv2.putText(text_overlay, conf_text, (text_x, text_y2),
		cv2.FONT_HERSHEY_SIMPLEX, font_scale_med, source_color, text_thick)

	# interval ID in bottom-left corner when provided
	if interval_id:
		interval_text = f"[{interval_id}]"
		cv2.putText(
			text_overlay, interval_text, (text_x, out_h - max(6, int(8 * s))),
			cv2.FONT_HERSHEY_SIMPLEX, font_scale_small, (200, 200, 200), thin_line,
		)

	# draw confidence bar in top-right corner
	bar_x = out_w - bar_w - max(4, int(6 * s))
	bar_y = max(4, int(6 * s))
	# background bar (dark gray)
	cv2.rectangle(text_overlay, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
	# filled portion scales with conf value
	fill_w = int(bar_w * max(0.0, min(1.0, conf)))
	# color: green at 1.0, red at 0.0
	bar_g = int(255 * conf)
	bar_r = int(255 * (1.0 - conf))
	bar_color = (0, bar_g, bar_r)
	cv2.rectangle(text_overlay, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), bar_color, -1)

	# blend text overlay onto frame
	cv2.addWeighted(text_overlay, _OVERLAY_ALPHA_TEXT, frame, 1.0 - _OVERLAY_ALPHA_TEXT, 0, frame)


#============================================
def _encode_segment(
	video_path: str,
	crop_rects_chunk: list,
	output_path: str,
	crop_width: int,
	crop_height: int,
	start_frame: int,
	codec: str,
	crf: int,
	frame_states_chunk: list | None,
	worker_id: int,
	total_workers: int,
	encode_filters: list | None = None,
	draw_tracking: bool = False,
	draw_debug: bool = False,
	draw_velocity: bool = False,
) -> str:
	"""Encode one segment of the video in a worker process.

	Each worker opens its own VideoReader and ffmpeg pipe. Module-level
	function so it is picklable for ProcessPoolExecutor.

	Args:
		video_path: Path to input video file.
		crop_rects_chunk: Crop rects for this segment's frames.
		output_path: Output path for the segment file.
		crop_width: Output frame width.
		crop_height: Output frame height.
		start_frame: First frame index for this segment.
		codec: Video codec string.
		crf: Constant Rate Factor.
		frame_states_chunk: Per-frame state dicts for overlays, or None.
		worker_id: Worker index for progress bar positioning.
		total_workers: Total number of workers for display.
		encode_filters: Ordered list of filter names to apply during encode.
		draw_tracking: Draw the review overlay.
		draw_debug: Draw the developer overlay (implies tracking).
		draw_velocity: Draw the per-frame motion arrow.

	Returns:
		Path to the encoded segment file.
	"""
	probe_info = common_tools.probe_video.probe_video(video_path)
	fps = probe_info["fps"]
	reader = common_tools.frame_reader.FrameReader(
		video_path, fps, probe_info["frame_count"],
	)
	# build ffmpeg vf string from encode filters
	vf_string = ""
	if encode_filters:
		vf_string = frame_filters.get_ffmpeg_vf_string(encode_filters)
	writer = video_io.VideoWriter(
		output_path, crop_width, crop_height, fps,
		codec=codec, crf=crf, vf_string=vf_string,
	)
	chunk_size = len(crop_rects_chunk)
	# Shared progress bar: identical column layout to solve Stages 1/3/4.
	with interval_solver.make_solve_progress() as progress:
		task = progress.add_task(
			f"  worker {worker_id + 1}/{total_workers}",
			total=chunk_size,
		)
		# seek to start_frame and read sequentially via fast-path
		reader.seek_for_encode(start_frame)
		for local_idx in range(chunk_size):
			frame = reader.read_frame(start_frame + local_idx)
			if frame is None:
				break
			# crop and resize
			crop_rect = crop_rects_chunk[local_idx]
			cropped = tr_crop.apply_crop(frame, crop_rect)
			# adaptive interpolation: INTER_AREA for downscaling (avoids aliasing),
			# INTER_LANCZOS4 for upscaling (preserves detail)
			crop_h = crop_rect[3]
			interp = cv2.INTER_AREA if crop_h >= crop_height else cv2.INTER_LANCZOS4
			resized = cv2.resize(cropped, (crop_width, crop_height), interpolation=interp)
			# apply opencv encode filters after resize
			if encode_filters:
				resized = frame_filters.apply_filter_pipeline(resized, encode_filters)
			# draw overlays when any tier requested. prev_center is
			# precomputed on the driver side (see _mode_encode) and
			# carried inside each frame_state dict; this is what makes
			# velocity arrows correct at chunk seams.
			any_overlay = draw_tracking or draw_debug or draw_velocity
			if any_overlay and frame_states_chunk is not None:
				state = frame_states_chunk[local_idx] if local_idx < len(frame_states_chunk) else None
				prev_center = state.get("prev_center") if state is not None else None
				draw_debug_overlay_cropped(
					resized, state, crop_rect, crop_width, crop_height,
					draw_tracking=draw_tracking,
					draw_debug=draw_debug,
					draw_velocity=draw_velocity,
					prev_center=prev_center,
				)
			writer.write_frame(resized)
			progress.update(task, advance=1)
	# ffmpeg may still be encoding through its filter pipeline
	print(f"  worker {worker_id + 1}/{total_workers} finalizing...", flush=True)
	writer.close()
	reader.close()
	return output_path


#============================================
def encode_cropped_video_parallel(
	video_path: str,
	crop_rects: list,
	output_path: str,
	crop_width: int,
	crop_height: int,
	codec: str = "libx264",
	crf: int = 18,
	frame_states: list | None = None,
	workers: int = 4,
	encode_filters: list | None = None,
	run_control: object = None,
	key_reader_obj: object = None,
	draw_tracking: bool = False,
	draw_debug: bool = False,
	draw_velocity: bool = False,
) -> None:
	"""Encode cropped video using parallel worker processes.

	Splits the frame range into chunks, encodes each chunk in a separate
	process with its own VideoReader and ffmpeg pipe, then concatenates
	the segment files with mkvmerge.

	Falls back to single-threaded encoding if workers <= 1.

	Args:
		video_path: Path to input video file.
		crop_rects: List of (x, y, w, h) tuples, one per frame.
		output_path: Path for the output video file.
		crop_width: Output frame width after resize.
		crop_height: Output frame height after resize.
		codec: Video codec (default libx264).
		crf: Constant Rate Factor (default 18).
		frame_states: List of per-frame state dicts from tracker.
		workers: Number of parallel encoding workers.
		encode_filters: Ordered list of filter names to apply during encode.
		run_control: Optional RunControl for quit detection.
		key_reader_obj: Optional KeyInputReader for keyboard polling.
		draw_tracking: Draw the review overlay (torso box + crosshair).
		draw_debug: Draw the developer overlay (implies tracking).
		draw_velocity: Draw the per-frame motion arrow.
	"""
	# fall back to sequential if only 1 worker
	if workers <= 1:
		probe_info = common_tools.probe_video.probe_video(video_path)
		with common_tools.frame_reader.FrameReader(
			video_path, probe_info["fps"], probe_info["frame_count"],
		) as reader:
			encode_cropped_video(
				reader, crop_rects, output_path,
				crop_width, crop_height,
				codec=codec, crf=crf,
				frame_states=frame_states,
				encode_filters=encode_filters,
				draw_tracking=draw_tracking,
				draw_debug=draw_debug,
				draw_velocity=draw_velocity,
			)
		return

	frame_count = len(crop_rects)
	# cap workers to frame count
	actual_workers = min(workers, frame_count)
	# compute chunk boundaries
	chunk_size = frame_count // actual_workers
	remainder = frame_count % actual_workers

	segments = []
	offset = 0
	for w_idx in range(actual_workers):
		# distribute remainder frames across first workers
		this_chunk = chunk_size + (1 if w_idx < remainder else 0)
		end_offset = offset + this_chunk
		# slice crop_rects and frame_states for this chunk
		rects_chunk = crop_rects[offset:end_offset]
		states_chunk = None
		if frame_states is not None:
			states_chunk = frame_states[offset:end_offset]
		# segment output file path
		seg_path = f"{output_path}.seg{w_idx:03d}.mp4"
		segments.append({
			"path": seg_path,
			"rects": rects_chunk,
			"states": states_chunk,
			"start_frame": offset,
			"worker_id": w_idx,
		})
		offset = end_offset

	# launch workers with ProcessPoolExecutor
	seg_paths = []
	quit_interrupted = False
	with concurrent.futures.ProcessPoolExecutor(max_workers=actual_workers) as pool:
		future_to_seg = {}
		for seg in segments:
			future = pool.submit(
				_encode_segment,
				video_path,
				seg["rects"],
				seg["path"],
				crop_width, crop_height,
				seg["start_frame"],
				codec, crf,
				seg["states"],
				seg["worker_id"],
				actual_workers,
				encode_filters,
				draw_tracking,
				draw_debug,
				draw_velocity,
			)
			future_to_seg[future] = seg["path"]
		# polling loop: use concurrent.futures.wait() with short timeout
		# to properly detect completion via internal condition variables
		# (manual f.done() polling does not work reliably)
		all_futures = set(future_to_seg.keys())
		key_input._quit_trace(
			"WAIT_ENTER", context="encode_parallel",
			futures=len(all_futures),
		)
		done_futures = set()
		# heartbeat counter: only trace every 30 iterations (~6s)
		heartbeat_counter = 0
		while len(done_futures) < len(all_futures):
			# poll for quit key (pause not supported during encode)
			if key_reader_obj is not None and run_control is not None:
				ch = key_reader_obj.poll()
				if ch is not None and ch.lower() == "q":
					run_control.request_quit()
					key_input._quit_trace("KEY_HANDLE", quit_requested=True)
					print("  Q pressed, finishing current interval...", flush=True)
			# check quit flag
			if run_control is not None and run_control.quit_requested:
				key_input._quit_trace(
					"MAIN_LOOP", context="encode_parallel",
					quit_requested=True,
				)
				# cancel pending futures
				for f in all_futures:
					if not f.done():
						f.cancel()
				# kill worker processes
				from interval_solver import _force_kill_pool
				_force_kill_pool(pool)
				quit_interrupted = True
				break
			# wait with 0.2s timeout for any future to complete
			# this uses the internal condition variable, unlike f.done()
			remaining = all_futures - done_futures
			completed_batch, _ = concurrent.futures.wait(
				remaining, timeout=0.2,
				return_when=concurrent.futures.FIRST_COMPLETED,
			)
			for f in completed_batch:
				done_futures.add(f)
				if not f.cancelled():
					# propagate any exception from the worker
					f.result()
			# heartbeat trace every ~6s (30 iterations at 0.2s)
			heartbeat_counter += 1
			if heartbeat_counter % 30 == 0:
				key_input._quit_trace(
					"ENCODE_WAIT", context="encode_parallel",
					pending=len(all_futures) - len(done_futures),
					completed=len(done_futures),
					quit=run_control.quit_requested if run_control else False,
				)
		key_input._quit_trace(
			"WAIT_EXIT", context="encode_parallel",
			completed=len(done_futures),
		)
		# collect segment paths in original order
		if not quit_interrupted:
			for seg in segments:
				seg_paths.append(seg["path"])
	# early return if quit interrupted encoding
	if quit_interrupted:
		key_input._quit_trace(
			"FUNCTION_RETURN", context="encode_parallel",
			quit_requested=True,
		)
		# clean up any partial segment files
		for seg in segments:
			if os.path.isfile(seg["path"]):
				os.remove(seg["path"])
		return

	# concatenate segments with mkvmerge
	mkvmerge_path = shutil.which("mkvmerge")
	if mkvmerge_path is None:
		raise RuntimeError("mkvmerge not found in PATH for segment concatenation")
	# build mkvmerge command: first file + subsequent with '+'
	concat_cmd = [mkvmerge_path, "-o", output_path]
	for idx, seg_path in enumerate(seg_paths):
		if idx > 0:
			concat_cmd.append("+")
		concat_cmd.append(seg_path)
	result = subprocess.run(concat_cmd, capture_output=True, text=True)
	if result.returncode not in (0, 1):
		# mkvmerge returns 1 for warnings, 2 for errors
		raise RuntimeError(
			f"mkvmerge concat failed (code {result.returncode}): {result.stderr}"
		)

	# clean up segment temp files
	for seg_path in seg_paths:
		if os.path.isfile(seg_path):
			os.remove(seg_path)
