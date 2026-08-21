"""Heat-map helpers shared by annotation controllers.

This module owns the residual heat-map support while controllers retain their
public lifecycle and callback methods.
"""

import numpy

# local repo modules
import camera_motion
import common_tools.coord_space
import overlay_config
import scene_coords
import ui.heat_map_overlay as heat_map_overlay_module


#============================================


def create_heat_map_overlay(
	reader: object,
	config: dict,
	scene: object,
	get_pred_fn: object,
	is_drawing_fn: object,
	status_callback: object,
) -> object:
	"""Build the controller's heat-map overlay and connect its status signal.

	Args:
		reader: Frame source used by the overlay worker.
		config: Loaded track-runner configuration.
		scene: Graphics scene receiving the overlay item.
		get_pred_fn: Callback returning the selected heat ROI prediction.
		is_drawing_fn: Callback reporting an active torso-box drag.
		status_callback: Slot receiving overlay status text.

	Returns:
		Configured HeatMapOverlay instance.
	"""
	heat_style = overlay_config.get_heat_map_style()
	scene_transform, transform_ok = load_scene_transform_for_gui(reader, config)
	overlay = heat_map_overlay_module.HeatMapOverlay(
		reader=reader,
		scene_transform=scene_transform,
		scene=scene,
		style=heat_style,
		get_pred_fn=get_pred_fn,
		scene_transform_available=transform_ok,
		is_drawing_fn=is_drawing_fn,
	)
	overlay.statusChanged.connect(status_callback)
	return overlay


#============================================


def apply_heat_overlay(
	heat_map_overlay: object | None,
	drawing: bool,
	overlay_visibility: dict,
	current_frame: int,
) -> None:
	"""Apply the current heat visibility state to an existing overlay.

	Args:
		heat_map_overlay: Current HeatMapOverlay, if activation created one.
		drawing: Whether a torso-box drag currently owns the interaction.
		overlay_visibility: Persistent per-overlay visibility flags.
		current_frame: Frame for the overlay request.
	"""
	if heat_map_overlay is None or drawing:
		return
	if overlay_visibility.get("heat", False):
		heat_map_overlay.request_show(int(current_frame))
	else:
		heat_map_overlay.hide()


#============================================


def get_heat_prediction(predictions: dict | None, frame_index: int) -> tuple | None:
	"""Return the preferred prediction's center and dimensions for heat ROI.

	Args:
		predictions: Optional mapping of frame index to prediction records.
		frame_index: Frame to look up.

	Returns:
		Tuple ((cx, cy), (w, h)) in source pixels, or None.
	"""
	if predictions is None:
		return None
	preds = predictions.get(frame_index)
	if preds is None:
		return None
	pick = None
	if preds.get("blended") is not None:
		pick = preds["blended"]
	elif preds.get("consensus") is not None:
		pick = preds["consensus"]
	elif preds.get("forward") is not None:
		pick = preds["forward"]
	if pick is None:
		return None
	box = common_tools.coord_space.require_source_box(pick)
	prediction = ((box.cx, box.cy), (box.w, box.h))
	return prediction


#============================================


def load_scene_transform_for_gui(reader: object, config: dict) -> tuple:
	"""Load persisted camera motion, or return an identity scene transform.

	Args:
		reader: Frame source exposing frame count and optional video path.
		config: Loaded track-runner configuration.

	Returns:
		Tuple (scene_transform, available), where available identifies a real
		persisted motion artifact.
	"""
	n_frames = max(int(reader.frame_count), 1)
	motion_track = None
	video_path = getattr(reader, "video_path", None)
	if video_path is not None:
		try:
			motion_track = camera_motion.load_active_camera_motion_or_fail(
				video_path, config
			)
		except RuntimeError:
			motion_track = None
	if motion_track is not None:
		transform = scene_coords.SceneTransform(motion_track)
		return (transform, True)
	identity_motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(identity_motion)
	return (transform, False)
