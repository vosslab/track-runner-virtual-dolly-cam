"""Polish workflow helpers for the seed editor.

The EditController owns navigation, session state, and Qt signal targets; this
module keeps the detector and preview implementation independently testable.
"""

# PIP3 modules
from PySide6.QtCore import QThread

# local repo modules
import ui.overlay_items as overlay_items_module

PreviewBoxItem = overlay_items_module.PreviewBoxItem


#============================================


class YoloLoaderThread(QThread):
	"""Background thread that initializes a YOLO detector."""

	def __init__(self, detector_list: list) -> None:
		"""Initialize the loader.

		Args:
			detector_list: Mutable singleton list receiving the detector.
		"""
		super().__init__()
		self._detector_list = detector_list

	#============================================

	def run(self) -> None:
		"""Load detector weights outside the Qt event loop."""
		import tr_detection as detection_module
		detector = detection_module.create_detector()
		self._detector_list[0] = detector


#============================================


def refine_box_yolo(
	current_bgr: object,
	current_seed: dict,
	config: dict,
	detector: object,
) -> dict | None:
	"""Request a YOLO refinement for the current seed.

	Args:
		current_bgr: Current decoded BGR image.
		current_seed: Seed being reviewed.
		config: Loaded track-runner configuration.
		detector: Loaded YOLO detector.

	Returns:
		Refined box dict, or None if detector has no suitable candidate.
	"""
	import seed_editor as seed_editor_module
	refined = seed_editor_module._refine_box_yolo(
		current_bgr, current_seed, config, detector,
	)
	return refined


#============================================


def refine_box_consensus(
	current_seed: dict,
	predictions: dict | None,
) -> dict | None:
	"""Request a forward/backward consensus refinement.

	Args:
		current_seed: Seed being reviewed.
		predictions: Optional solved prediction store.

	Returns:
		Refined box dict, or None if no consensus is available.
	"""
	import seed_editor as seed_editor_module
	frame_index = int(current_seed["frame_index"])
	refined = seed_editor_module._refine_box_consensus(
		current_seed, predictions, frame_index,
	)
	return refined


#============================================


def create_polish_preview(scene: object, refined: dict) -> object:
	"""Create and add the preview rectangle for a refined box.

	Args:
		scene: Graphics scene receiving the preview.
		refined: Refined box dict with cx, cy, w, and h values.

	Returns:
		Added PreviewBoxItem.
	"""
	cx = float(refined["cx"])
	cy = float(refined["cy"])
	width = float(refined["w"])
	height = float(refined["h"])
	x = int(cx - width / 2.0)
	y = int(cy - height / 2.0)
	preview_item = PreviewBoxItem(x, y, int(width), int(height))
	scene.addItem(preview_item)
	return preview_item


#============================================


def remove_polish_preview(scene: object, preview_item: object | None) -> None:
	"""Remove a prior polish preview when one is present.

	Args:
		scene: Graphics scene that owns the preview item.
		preview_item: Existing preview item, if any.
	"""
	if preview_item is not None:
		scene.removeItem(preview_item)


#============================================


def build_polished_seed(
	current_seed: dict,
	refined: dict,
	config: dict,
) -> dict:
	"""Convert an accepted polish box into the canonical visible seed.

	Args:
		current_seed: Existing seed whose frame and pass remain authoritative.
		refined: Accepted refined box.
		config: Loaded track-runner configuration.

	Returns:
		Canonical replacement seed.
	"""
	import seed_color
	frame_index = int(current_seed["frame_index"])
	x = int(refined["cx"] - refined["w"] / 2.0)
	y = int(refined["cy"] - refined["h"] / 2.0)
	polish_box = [x, y, int(refined["w"]), int(refined["h"])]
	norm_box = seed_color.normalize_seed_box(polish_box, config)
	new_seed = seed_color.build_seed_dict(
		frame_index, norm_box, current_seed["pass"], status="visible",
	)
	return new_seed


#============================================


def on_yolo_polish(controller: object) -> None:
	"""Run YOLO polish through the controller-owned state and UI.

	Args:
		controller: Edit controller owning navigation and Qt widgets.
	"""
	if controller._current_seed is None:
		return
	status = controller._current_seed.get("status", "visible")
	if status == "not_in_frame":
		return
	if controller._yolo_loading:
		return
	detector = controller._yolo_detector_list[0] if controller._yolo_detector_list else None
	if detector is None and not controller._yolo_tried:
		controller._start_yolo_load()
		return
	if detector is None:
		controller._status_presenter.get_widget().setText("YOLO: load failed")
		return
	refined = refine_box_yolo(
		controller._current_bgr, controller._current_seed, controller._config, detector,
	)
	if refined is None:
		controller._status_presenter.get_widget().setText("YOLO: no refinement available")
		return
	controller._show_polish_preview(refined, "YOLO polish: SPACE=accept, other=reject")


#============================================


def start_yolo_load(controller: object) -> None:
	"""Create the controller-owned YOLO thread and connect its completion slot.

	Args:
		controller: Edit controller retaining the worker lifetime and signal target.
	"""
	controller._yolo_loading = True
	controller._yolo_tried = True
	controller._status_presenter.get_widget().setText("Loading YOLO...")
	controller._yolo_thread = YoloLoaderThread(controller._yolo_detector_list)
	controller._yolo_thread.finished.connect(controller._on_yolo_loaded)
	controller._yolo_thread.start()


#============================================


def on_yolo_loaded(controller: object) -> None:
	"""Present the completed YOLO load state.

	Args:
		controller: Edit controller receiving the thread completion signal.
	"""
	controller._yolo_loading = False
	detector = controller._yolo_detector_list[0] if controller._yolo_detector_list else None
	if detector is None:
		controller._status_presenter.get_widget().setText("YOLO: load failed")
	else:
		controller._status_presenter.get_widget().setText("YOLO: ready - press y again")


#============================================


def on_consensus_polish(controller: object) -> None:
	"""Run consensus polish through the controller-owned prediction store.

	Args:
		controller: Edit controller owning the current seed and UI.
	"""
	if controller._current_seed is None:
		return
	status = controller._current_seed.get("status", "visible")
	if status == "not_in_frame":
		return
	refined = refine_box_consensus(controller._current_seed, controller._predictions)
	if refined is None:
		controller._status_presenter.get_widget().setText(
			"FWD/BWD: no predictions available"
		)
		return
	controller._show_polish_preview(refined, "FWD/BWD polish: SPACE=accept, other=reject")


#============================================


def show_polish_preview(controller: object, refined: dict, message: str) -> None:
	"""Replace the controller's polish preview and status message.

	Args:
		controller: Edit controller owning the scene and polish state.
		refined: Refined box dict.
		message: User-facing preview instruction.
	"""
	controller._clear_polish_preview()
	scene = controller._window.get_frame_view().scene()
	controller._polish_preview_item = create_polish_preview(scene, refined)
	controller._pending_refined = refined
	controller._polish_mode = "pending"
	controller._status_presenter.get_widget().setText(message)


#============================================


def clear_polish_preview(controller: object) -> None:
	"""Remove controller-owned polish preview and reset pending state.

	Args:
		controller: Edit controller owning the scene and polish state.
	"""
	if controller._polish_preview_item is not None:
		scene = controller._window.get_frame_view().scene()
		remove_polish_preview(scene, controller._polish_preview_item)
		controller._polish_preview_item = None
	controller._pending_refined = None
	controller._polish_mode = None


#============================================


def accept_polish(controller: object) -> None:
	"""Commit the pending polished seed through the controller transaction.

	Args:
		controller: Edit controller owning edit counters and persistence.
	"""
	if controller._pending_refined is None:
		return
	seed = controller._current_seed
	frame_index = int(seed["frame_index"])
	new_seed = build_polished_seed(seed, controller._pending_refined, controller._config)
	seed_list_idx = controller._filtered_indices[controller._nav_idx]
	controller._work_seeds[seed_list_idx] = new_seed
	controller._redrawn += 1
	controller._reviewed += 1
	controller._changed_frames.add(frame_index)
	controller._save_callback(controller._work_seeds)
	controller._clear_polish_preview()
	controller._advance()
