"""Edit mode controller for seed editor annotation.

Manages the workflow for reviewing, deleting, redrawn, and changing status
of existing seeds. Handles keyboard shortcuts and mouse drawing.
"""

import collections.abc

# PIP3 modules
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout, QPushButton

# local repo modules
import overlay_config
import ui.overlay_items as overlay_items_module
import ui.status_presenter as status_presenter_module
import ui.base_controller as base_controller_module
import ui.edit_polish as edit_polish_module
import ui.frame_source as frame_source_module
import ui.seed_controller as seed_controller_module

RectItem = overlay_items_module.RectItem
StatusPresenter = status_presenter_module.StatusPresenter
BaseAnnotationController = base_controller_module.BaseAnnotationController

#============================================


class EditController(BaseAnnotationController):
	"""Manages the Edit mode annotation workflow.

	Allows reviewing, filtering, deleting, and redrawing existing seeds.
	Handles keyboard shortcuts and mouse drawing for box refinement.
	"""

	def __init__(
		self,
		work_seeds: list,
		filtered_indices: list,
		reader: frame_source_module.FrameSource,
		fps: float,
		config: dict,
		save_callback: collections.abc.Callable[[list], None],
		predictions: dict | None = None,
		seed_confidences: dict | None = None,
		yolo_detector_list: list | None = None,
		frame_filter: set | None = None,
		start_frame: int | None = None,
	) -> None:
		"""Initialize the EditController.

		Args:
			work_seeds: Mutable list of all seeds (modified in-place for edits).
			filtered_indices: List of indices into work_seeds to iterate over.
			reader: FrameSource that asynchronously provides requested frames.
			fps: Frames per second of the video.
			config: Configuration dict.
			save_callback: Callable(work_seeds) to save incremental changes.
			predictions: Optional dict mapping frame_index to prediction dicts.
			seed_confidences: Optional dict mapping frame_index to confidence dicts.
			yolo_detector_list: Optional [None] list for lazy YOLO loading.
			frame_filter: Optional set of frame indices for filtering seeds.
			start_frame: Optional frame index to seek to on first activate.
		"""
		super().__init__(
			reader=reader,
			fps=fps,
			config=config,
			save_callback=save_callback,
			predictions=predictions,
		)
		self._frame_source_connected = False
		self._connect_frame_source()

		self._work_seeds = work_seeds
		self._filtered_indices = filtered_indices
		self._seed_confidences = seed_confidences
		self._yolo_detector_list = yolo_detector_list or [None]
		self._frame_filter = frame_filter

		# Navigation state
		self._nav_idx = 0

		# Tracking counters
		self._reviewed = 0
		self._kept = 0
		self._redrawn = 0
		self._deleted = 0
		self._added = 0
		self._status_changed = 0
		self._changed_frames: set = set()
		self._delete_indices: set = set()

		# Status presenter
		self._status_presenter = StatusPresenter()
		self._session_feedback: str | None = None

		# Seed box display
		self._seed_rect_item: object = None

		# Polish mode state
		self._polish_preview_item: object = None
		self._polish_mode: str | None = None
		self._pending_refined: dict | None = None
		self._yolo_loading: bool = False
		self._yolo_tried: bool = False
		self._yolo_thread: object = None

		# Current seed being reviewed
		self._current_seed: dict | None = None

		# Start frame for initial seek
		self._start_frame = start_frame

		# Keybindings label
		self._keybindings_label: QLabel | None = None

	#============================================

	def _build_toolbar(self) -> QWidget:
		"""Build the toolbar widget with nav and draw mode buttons.

		Returns:
			QWidget containing prev/next and draw mode buttons.
		"""
		widget = QWidget()
		layout = QHBoxLayout(widget)
		layout.setContentsMargins(4, 0, 4, 0)
		layout.setSpacing(4)

		# Navigation buttons
		btn_prev = QPushButton("<  Prev")
		btn_prev.setToolTip("Previous seed (Shift+LEFT)")
		btn_prev.clicked.connect(self._on_prev)
		layout.addWidget(btn_prev)

		btn_keep = QPushButton("Keep  >")
		btn_keep.setToolTip("Keep seed and advance (SPACE or RIGHT)")
		btn_keep.clicked.connect(self._on_keep)
		layout.addWidget(btn_keep)

		# Separator space
		layout.addSpacing(12)

		# Draw mode toggle buttons (checkable for visual state)
		self._btn_partial = QPushButton("Partial")
		self._btn_partial.setCheckable(True)
		self._btn_partial.setToolTip("Toggle partial draw mode (P)")
		self._btn_partial.clicked.connect(self._on_partial_toggle)
		layout.addWidget(self._btn_partial)

		self._btn_approx = QPushButton("Approx")
		self._btn_approx.setCheckable(True)
		self._btn_approx.setToolTip("Toggle approx/obstruction draw mode (A)")
		self._btn_approx.clicked.connect(self._on_approx_toggle)
		layout.addWidget(self._btn_approx)

		return widget

	#============================================

	def _on_activated(self) -> None:
		"""Set up status presenter and load the first seed."""
		self._connect_frame_source()
		# Add status presenter to toolbar
		toolbar_widget = self._status_presenter.get_widget()
		self._window.statusBar().addWidget(toolbar_widget)

		# Add keybinding hints as a permanent label in the status bar.
		# Font family comes from QFontDatabase so the OS picks its own
		# fixed-width face; the stylesheet only controls color and padding.
		self._keybindings_label = QLabel(self._get_default_status_text())
		kb_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
		kb_font.setPointSize(10)
		self._keybindings_label.setFont(kb_font)
		self._keybindings_label.setStyleSheet(
			"color: #888888; padding: 2px 8px;"
		)
		self._window.statusBar().addPermanentWidget(self._keybindings_label)

		# Seek to nearest seed at or after start_frame if provided
		if self._start_frame is not None and self._filtered_indices:
			for i, idx in enumerate(self._filtered_indices):
				frame_index = int(self._work_seeds[idx]["frame_index"])
				if frame_index >= self._start_frame:
					self._nav_idx = i
					break
			else:
				# all seeds before start_frame, go to last
				self._nav_idx = len(self._filtered_indices) - 1

		# Load and display the current seed
		self._load_current_seed()

	#============================================

	def _on_deactivated(self) -> None:
		"""Clean up edit-specific state."""
		self._disconnect_frame_source()
		# Remove edit-specific overlays (seed rect, polish preview)
		if self._window is not None:
			scene = self._window.get_frame_view().scene()
			if self._seed_rect_item is not None:
				scene.removeItem(self._seed_rect_item)
				self._seed_rect_item = None
			if self._polish_preview_item is not None:
				scene.removeItem(self._polish_preview_item)
				self._polish_preview_item = None

		# Remove status bar widgets to prevent accumulation
		if self._window is not None:
			self._window.statusBar().removeWidget(
				self._status_presenter.get_widget()
			)
			if self._keybindings_label is not None:
				self._window.statusBar().removeWidget(self._keybindings_label)

		# Clear status bar
		self._status_presenter.clear()

	#============================================

	def _connect_frame_source(self) -> None:
		"""Subscribe only while this controller can own window updates."""
		if not self._frame_source_connected:
			self._reader.frame_ready.connect(self._on_frame_ready)
			self._frame_source_connected = True

	#============================================

	def _disconnect_frame_source(self) -> None:
		"""Prevent late worker results reaching a deactivated controller."""
		if self._frame_source_connected:
			self._reader.frame_ready.disconnect(self._on_frame_ready)
			self._frame_source_connected = False

	#============================================

	def _get_default_status_text(self) -> str:
		"""Short mode summary for the status bar.

		Returns:
			String with mode summary.
		"""
		return "Edit mode - review seeds"

	#============================================

	def _get_mode_name(self) -> str:
		"""Mode name for display.

		Returns:
			String "edit".
		"""
		return "edit"

	#============================================

	def _get_zoom_center(self) -> tuple | None:
		"""Get zoom center from current seed or predictions.

		Returns:
			Tuple of (cx, cy) or None.
		"""
		# try to center on current seed position
		if self._current_seed is not None:
			cx = self._current_seed.get("cx")
			cy = self._current_seed.get("cy")
			if cx is not None and cy is not None:
				return (float(cx), float(cy))
		# fallback to prediction center
		return self._get_prediction_center()

	#============================================

	def _set_status_text(self, text: str) -> None:
		"""Set status via the StatusPresenter widget.

		Args:
			text: Message to display.
		"""
		self._status_presenter.show_feedback(text)

	#============================================

	def show_session_feedback(self, text: str) -> None:
		"""Display feedback retained by AnnotationSession across a mode swap."""
		self._session_feedback = text
		self._set_status_text(text)

	#============================================

	def _restore_default_status(self) -> None:
		"""Restore the status presenter with current seed info."""
		self._update_status_presenter()

	#============================================

	def _load_current_seed(self) -> None:
		"""Load and display the current seed frame."""
		# Navigation is the next user action after a returned add-mode summary.
		# It may replace that summary with the selected seed's normal metadata.
		self._session_feedback = None
		if self._nav_idx >= len(self._filtered_indices):
			self._on_quit()
			return

		seed_list_idx = self._filtered_indices[self._nav_idx]
		seed = self._work_seeds[seed_list_idx]
		self._current_seed = seed
		frame_index = int(seed["frame_index"])

		# Decode requests never block the annotation event loop.
		self._reader.request_frame(frame_index)

	#============================================

	def _on_frame_ready(self, frame_index: int, frame: object) -> None:
		"""Display the current seed only when its queued decode completes."""
		if self._current_seed is None:
			return
		if frame_index != int(self._current_seed["frame_index"]):
			return
		seed = self._current_seed
		if frame is None:
			self._nav_idx += 1
			self._load_current_seed()
			return

		self._current_frame = frame_index
		self._current_bgr = frame
		self._window.set_frame(frame)

		# Show seed box underneath (thick, solid)
		self._update_seed_rect_overlay()

		# Show FWD/BWD overlays on top (thin, dashed)
		self._update_fwd_bwd_overlays()

		# Update progress bar
		self._window.set_progress(
			self._nav_idx + 1, len(self._filtered_indices)
		)

		# Update status presenter
		seed_confidence = None
		if self._seed_confidences is not None:
			seed_confidence = self._seed_confidences.get(frame_index)
		# look up interval_info for severity display
		interval_info = None
		if self._predictions is not None:
			preds = self._predictions.get(frame_index)
			if preds is not None:
				interval_info = preds.get("interval_info")
		self._status_presenter.update(
			seed, self._nav_idx, len(self._filtered_indices),
			self._fps, seed_confidence, interval_info,
		)
		if self._session_feedback is not None:
			self._set_status_text(self._session_feedback)

		# Update scale bar
		self._update_scale_bar()

		# Recenter view on bbox when zoomed in
		self._recenter_on_bbox()

	#============================================

	def _recenter_on_bbox(self) -> None:
		"""Recenter the view on the current seed bbox when zoomed in."""
		frame_view = self._window.get_frame_view()
		zoom = frame_view.get_zoom_factor()
		# Skip if not zoomed in
		if zoom <= 1.05:
			return

		seed = self._current_seed
		status = seed.get("status", "unknown")

		# Use seed cx/cy if the seed has a real position
		if status not in ("approximate", "not_in_frame") and seed.get("cx") is not None:
			center_x = float(seed["cx"])
			center_y = float(seed["cy"])
		else:
			# Fall back to FWD/BWD prediction average center
			center = self._get_prediction_center()
			if center is None:
				return
			center_x, center_y = center

		frame_view.set_zoom(zoom, center_x, center_y)

	#============================================

	def _update_seed_rect_overlay(self) -> None:
		"""Show the existing seed box on the frame."""
		scene = self._window.get_frame_view().scene()

		# Remove old seed rect item
		if self._seed_rect_item is not None:
			scene.removeItem(self._seed_rect_item)
			self._seed_rect_item = None

		# Get current seed
		seed_list_idx = self._filtered_indices[self._nav_idx]
		seed = self._work_seeds[seed_list_idx]

		# Show box for any seed that has coordinates
		cx = seed.get("cx")
		cy = seed.get("cy")
		if cx is None or cy is None:
			return

		# Extract seed box
		cx = float(cx)
		cy = float(cy)
		w = float(seed.get("w", 0))
		h = float(seed.get("h", 0))

		x = int(cx - w / 2.0)
		y = int(cy - h / 2.0)

		# Color seed box by status type from overlay_styles.yaml
		status = seed.get("status", "visible")
		style = overlay_config.get_seed_status_style(status)
		color = style["color"]
		fill_alpha = int(style["fill_opacity"] * 255)
		thickness = overlay_config.get_thickness_scale(style["thickness_tier"])

		# Create seed box overlay: solid line, heavy thickness, drawn underneath
		self._seed_rect_item = RectItem(
			x, y, int(w), int(h),
			color_str=color,
			label=f"SEED ({status})",
			fill_alpha=fill_alpha,
			thickness_scale=thickness,
			label_slot=0,
		)
		# low z-value so seed box renders below FWD/BWD
		self._seed_rect_item.setZValue(1)
		scene.addItem(self._seed_rect_item)

	#============================================

	def handle_key_press(self, key: int, modifiers: object = None) -> bool:
		"""Dispatch a keyboard event through the declarative key map."""
		# Reject polish preview on any non-SPACE key before its next action.
		if self._polish_mode == "pending" and key != Qt.Key.Key_Space:
			self._clear_polish_preview()
			self._update_status_presenter()
		handled = self._dispatch_keybinding(key, modifiers)
		return handled

	#============================================

	def _key_action_keep_or_accept_polish(self, binding: object, key: int, modifiers: object) -> bool:
		"""Keep the seed, or accept a pending polish preview."""
		_ = binding, key, modifiers
		if self._polish_mode == "pending":
			self._on_accept_polish()
			return True
		self._on_keep()
		return True

	#============================================

	def _key_action_pan_left(self, binding: object, key: int, modifiers: object) -> bool:
		"""Leave plain LEFT to QGraphicsView pan handling."""
		_ = binding, key, modifiers
		return False

	#============================================

	def _key_action_pan_right(self, binding: object, key: int, modifiers: object) -> bool:
		"""Leave plain RIGHT to QGraphicsView pan handling."""
		_ = binding, key, modifiers
		return False

	#============================================

	def _key_action_previous_seed(self, binding: object, key: int, modifiers: object) -> bool:
		"""Move to the previous reviewed seed."""
		_ = binding, key, modifiers
		self._on_prev()
		return True

	#============================================

	def _key_action_delete_seed(self, binding: object, key: int, modifiers: object) -> bool:
		"""Delete the current seed."""
		_ = binding, key, modifiers
		self._on_delete()
		return True

	#============================================

	def _key_action_not_in_frame(self, binding: object, key: int, modifiers: object) -> bool:
		"""Mark the current seed as not in frame."""
		_ = binding, key, modifiers
		self._on_status_change("not_in_frame")
		return True

	#============================================

	def _key_action_yolo_polish(self, binding: object, key: int, modifiers: object) -> bool:
		"""Generate a YOLO polish preview."""
		_ = binding, key, modifiers
		self._on_yolo_polish()
		return True

	#============================================

	def _key_action_consensus_polish(self, binding: object, key: int, modifiers: object) -> bool:
		"""Generate an FWD/BWD consensus polish preview."""
		_ = binding, key, modifiers
		self._on_consensus_polish()
		return True

	#============================================

	def _key_action_jump_forward(self, binding: object, key: int, modifiers: object) -> bool:
		"""Jump forward through the filtered seeds."""
		_ = binding, key, modifiers
		self._on_jump_forward()
		return True

	#============================================

	def _key_action_jump_backward(self, binding: object, key: int, modifiers: object) -> bool:
		"""Jump backward through the filtered seeds."""
		_ = binding, key, modifiers
		self._on_jump_backward()
		return True

	#============================================

	def _key_action_jump_low_confidence(self, binding: object, key: int, modifiers: object) -> bool:
		"""Jump to the next low-confidence seed."""
		_ = binding, key, modifiers
		self._on_jump_low_conf()
		return True

	#============================================

	def _key_action_enter_add_mode(self, binding: object, key: int, modifiers: object) -> bool:
		"""Enter seed mode to add annotations."""
		_ = binding, key, modifiers
		self._on_enter_add_mode()
		return True

	#============================================

	def _on_box_drawn(self, box: list) -> None:
		"""Process a drawn box.

		Args:
			box: Box as [x, y, w, h].
		"""
		import seed_color

		seed_list_idx = self._filtered_indices[self._nav_idx]
		seed = self._work_seeds[seed_list_idx]
		frame_index = int(seed["frame_index"])

		if self._approx_mode:
			self._approx_mode = False
			self._update_mode_badge()
			norm_box = seed_color.normalize_seed_box(box, self._config)
			new_seed = seed_color.build_seed_dict(
				frame_index,
				norm_box,
				seed["pass"],
				status="approximate",
			)
			self._work_seeds[seed_list_idx] = new_seed
			self._redrawn += 1
			self._reviewed += 1
			self._status_changed += 1
			self._changed_frames.add(frame_index)
			self._save_callback(self._work_seeds)
			self._advance()
			return

		if self._partial_mode:
			self._partial_mode = False
			self._update_mode_badge()
			norm_box = seed_color.normalize_seed_box(box, self._config)
			new_seed = seed_color.build_seed_dict(
				frame_index,
				norm_box,
				seed["pass"],
				status="partial",
			)
			self._reviewed += 1
			self._redrawn += 1
			self._status_changed += 1
			self._changed_frames.add(frame_index)
			self._work_seeds[seed_list_idx] = new_seed
			self._save_callback(self._work_seeds)
			self._advance()
		else:
			norm_box = seed_color.normalize_seed_box(box, self._config)
			new_seed = seed_color.build_seed_dict(
				frame_index,
				norm_box,
				seed["pass"],
				status="visible",
			)
			self._reviewed += 1
			self._redrawn += 1
			self._changed_frames.add(frame_index)
			self._work_seeds[seed_list_idx] = new_seed
			self._save_callback(self._work_seeds)
			self._advance()

	#============================================

	def _on_keep(self) -> None:
		"""Keep seed as-is and advance."""
		self._reviewed += 1
		self._kept += 1
		self._advance()

	#============================================

	def _on_prev(self) -> None:
		"""Go back to previous seed."""
		self._nav_idx = max(0, self._nav_idx - 1)
		self._load_current_seed()

	#============================================

	def _on_delete(self) -> None:
		"""Delete the current seed."""
		seed_list_idx = self._filtered_indices[self._nav_idx]
		frame_index = int(self._work_seeds[seed_list_idx]["frame_index"])

		self._reviewed += 1
		self._deleted += 1
		self._delete_indices.add(seed_list_idx)
		self._changed_frames.add(frame_index)
		self._save_callback(self._work_seeds)
		self._advance()

	#============================================

	def _on_status_change(self, new_status: str) -> None:
		"""Change seed status (only not_in_frame supported).

		Args:
			new_status: New status string.
		"""
		seed_list_idx = self._filtered_indices[self._nav_idx]
		seed = self._work_seeds[seed_list_idx]
		frame_index = int(seed["frame_index"])

		self._reviewed += 1
		self._status_changed += 1
		self._changed_frames.add(frame_index)

		# Build canonical seed with no torso_box (e.g., not_in_frame).
		# No derived geometry: _derive_seed_geometry skips seeds without
		# a torso_box, and downstream code filters by status before
		# reading cx/cy.
		new_seed = {
			"frame_index": seed["frame_index"],
			"status": new_status,
			"pass": seed["pass"],
		}
		self._work_seeds[seed_list_idx] = new_seed
		self._save_callback(self._work_seeds)
		self._advance()

	#============================================

	def _on_yolo_polish(self) -> None:
		"""Run YOLO polish on current seed and show preview."""
		edit_polish_module.on_yolo_polish(self)

	#============================================

	def _start_yolo_load(self) -> None:
		"""Start background YOLO loading in QThread."""
		edit_polish_module.start_yolo_load(self)

	#============================================

	def _on_yolo_loaded(self) -> None:
		"""Handle YOLO loading completion."""
		edit_polish_module.on_yolo_loaded(self)

	#============================================

	def _on_consensus_polish(self) -> None:
		"""Run FWD/BWD consensus polish and show preview."""
		edit_polish_module.on_consensus_polish(self)

	#============================================

	def _show_polish_preview(self, refined: dict, message: str) -> None:
		"""Show a polish preview box as a QGraphicsItem.

		Args:
			refined: Refined box dict with cx, cy, w, h keys.
			message: Status message to display.
		"""
		edit_polish_module.show_polish_preview(self, refined, message)

	#============================================

	def _clear_polish_preview(self) -> None:
		"""Clear the polish preview item from the scene."""
		edit_polish_module.clear_polish_preview(self)

	#============================================

	def _on_accept_polish(self) -> None:
		"""Accept the polish preview and update seed."""
		edit_polish_module.accept_polish(self)

	#============================================

	def _update_status_presenter(self) -> None:
		"""Update status presenter with current seed info."""
		if self._current_seed is None:
			return
		seed_list_idx = (
			self._filtered_indices[self._nav_idx]
			if self._nav_idx < len(self._filtered_indices) else -1
		)
		if seed_list_idx < 0:
			return
		frame_index = int(self._current_seed["frame_index"])
		conf = None
		if self._seed_confidences is not None:
			conf = self._seed_confidences.get(frame_index)
		# look up interval_info for severity display
		interval_info = None
		if self._predictions is not None:
			preds = self._predictions.get(frame_index)
			if preds is not None:
				interval_info = preds.get("interval_info")
		self._status_presenter.update(
			self._current_seed, self._nav_idx, len(self._filtered_indices),
			self._fps, conf, interval_info,
		)
		if self._session_feedback is not None:
			self._set_status_text(self._session_feedback)

	#============================================

	def _advance(self) -> None:
		"""Advance to next seed."""
		self._nav_idx += 1
		if self._nav_idx >= len(self._filtered_indices):
			self._on_quit()
			return
		self._load_current_seed()

	#============================================

	def _on_jump_forward(self) -> None:
		"""Jump forward 10% of the filtered seed list."""
		total = len(self._filtered_indices)
		jump = max(1, total // 10)
		self._nav_idx = min(self._nav_idx + jump, total - 1)
		self._load_current_seed()

	#============================================

	def _on_jump_backward(self) -> None:
		"""Jump backward 10% of the filtered seed list."""
		total = len(self._filtered_indices)
		jump = max(1, total // 10)
		self._nav_idx = max(self._nav_idx - jump, 0)
		self._load_current_seed()

	#============================================

	def _on_jump_low_conf(self) -> None:
		"""Jump to the next low-confidence seed after the current position.

		Searches forward through filtered seeds for one with confidence
		score below 0.5. Wraps around to the beginning if needed.
		"""
		if self._seed_confidences is None:
			self._set_status_text("No confidence data available")
			return

		total = len(self._filtered_indices)
		# search forward from current position, wrapping around
		for offset in range(1, total):
			idx = (self._nav_idx + offset) % total
			seed_list_idx = self._filtered_indices[idx]
			seed = self._work_seeds[seed_list_idx]
			frame_index = int(seed["frame_index"])
			conf = self._seed_confidences.get(frame_index)
			if conf is not None and float(conf.get("score", 1.0)) < 0.5:
				self._nav_idx = idx
				self._load_current_seed()
				return

		self._set_status_text("No low-confidence seeds found")

	#============================================

	def _on_quit(self) -> None:
		"""Quit the editor."""
		self._done = True
		if self._window is not None:
			self._window.close()

	#============================================

	def _on_enter_add_mode(self) -> None:
		"""Enter seed-add mode via SeedController.

		Saves the current frame position and asks the persistent session to
		construct the add-seed controller.
		"""
		# Save current frame for position restoration on return
		self._saved_frame_index = self._current_frame
		session = self._window.get_session()
		if session is None:
			raise RuntimeError("Edit add-seed mode requires an AnnotationSession")
		session.begin_add_seed(self)

	#============================================

	def create_add_seed_controller(
		self,
		total_frames: int,
		seed_store: list,
		prediction_store: dict | None,
	) -> object:
		"""Build the add-seed controller for this pending Edit transaction.

		Args:
			total_frames: Number of frames available for annotation.
			seed_store: Session-owned committed seed list.
			prediction_store: Session-owned predictions, if available.
		"""
		controller = seed_controller_module.SeedController(
			seed_frame_indices=list(range(total_frames)),
			reader=self._reader,
			fps=self._fps,
			config=self._config,
			all_seeds=seed_store,
			save_callback=self._save_callback,
			mode_str="edit_add",
			predictions=prediction_store,
			return_callback=self.resume_from_add_mode,
			start_frame=self._current_frame,
		)
		return controller

	#============================================

	def resume_from_add_mode(self, new_seeds: list) -> None:
		"""Resume edit mode after returning from add-seed mode.

		Args:
			new_seeds: List of newly collected seeds from seed mode.
		"""
		session = self._window.get_session()
		if session is None:
			raise RuntimeError("Edit add-seed return requires an AnnotationSession")
		self._complete_add_mode(new_seeds)
		self._save_callback(self._work_seeds)
		session.resume_edit_after_add(self, self._work_seeds)

	#============================================

	def _complete_add_mode(self, new_seeds: list) -> None:
		"""Merge add-mode commits while retaining pending edit state.

		Args:
			new_seeds: Newly committed seed dicts returned by Seed mode.
		"""
		# 1. Purge deleted seeds
		if self._delete_indices:
			self._work_seeds[:] = [
				s for i, s in enumerate(self._work_seeds)
				if i not in self._delete_indices
			]
			self._delete_indices.clear()

		# 2. Append new seeds
		self._added += len(new_seeds)
		self._work_seeds.extend(new_seeds)

		# 3. Rebuild filtered indices
		self._rebuild_filtered_indices()

		# 4. Restore position: first seed with frame_index >= saved
		self._restore_nav_position()

	def _rebuild_filtered_indices(self) -> None:
		"""Rebuild filtered indices after seed list changes.

		Sorts work_seeds in place by frame_index and rebuilds
		_filtered_indices based on _frame_filter.
		"""
		# Sort work_seeds in place by frame_index
		self._work_seeds.sort(key=lambda s: int(s["frame_index"]))

		# Rebuild filtered indices
		if self._frame_filter is not None:
			self._filtered_indices = [
				i for i, s in enumerate(self._work_seeds)
				if int(s["frame_index"]) in self._frame_filter
			]
		else:
			self._filtered_indices = list(range(len(self._work_seeds)))

	#============================================

	def _restore_nav_position(self) -> None:
		"""Restore nav position to first seed at or after saved frame."""
		if not self._filtered_indices:
			# No seeds left, quit gracefully
			self._nav_idx = 0
			return

		saved = getattr(self, "_saved_frame_index", 0)
		for i, idx in enumerate(self._filtered_indices):
			frame_index = int(self._work_seeds[idx]["frame_index"])
			if frame_index >= saved:
				self._nav_idx = i
				return
		# All seeds before saved frame, go to last
		self._nav_idx = len(self._filtered_indices) - 1

	#============================================

	def get_summary(self) -> tuple:
		"""Get the editing summary and final seeds list.

		Returns:
			Tuple of (final_seeds, summary_dict) where final_seeds is the
			work_seeds list with deleted indices removed, and summary_dict
			contains counts and metadata.
		"""
		# Remove deleted seeds
		final_seeds = [
			s for i, s in enumerate(self._work_seeds)
			if i not in self._delete_indices
		]

		summary = {
			"reviewed": self._reviewed,
			"kept": self._kept,
			"redrawn": self._redrawn,
			"deleted": self._deleted,
			"added": self._added,
			"status_changed": self._status_changed,
			"changed_frames": self._changed_frames,
		}

		return (final_seeds, summary)
