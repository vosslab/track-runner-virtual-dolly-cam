"""Base annotation controller for track runner UI.

Shared plumbing for all annotation controllers: event filter,
mouse drawing, overlay management, zoom, draw mode toggles,
scale bar, and activation lifecycle.
"""

# Standard Library
import time

# PIP3 modules
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QWidget, QPushButton
import numpy

# local repo modules
import camera_motion
import overlay_config
import scene_coords
import ui.heat_map_overlay as heat_map_overlay_module
import ui.overlay_items as overlay_items_module

PreviewBoxItem = overlay_items_module.PreviewBoxItem
RectItem = overlay_items_module.RectItem
ScaleBarItem = overlay_items_module.ScaleBarItem
PredictionLegendItem = overlay_items_module.PredictionLegendItem
HeatMapOverlay = heat_map_overlay_module.HeatMapOverlay

#============================================


class BaseAnnotationController(QObject):
	"""Shared base for annotation controllers.

	Provides window plumbing, event filter, mouse drawing, overlay
	management, zoom cycling, draw mode toggles, and scale bar.
	Subclasses implement abstract methods for mode-specific behavior.
	"""

	@staticmethod
	def add_argparse_args(parser: object) -> None:
		"""Register shared interactive-mode CLI arguments on a parser.

		Call this from cli.py for each interactive subparser (seed,
		edit, target, run) to avoid duplicating argument definitions.

		Args:
			parser: An argparse subparser to add arguments to.
		"""
		parser.add_argument(
			"-S", "--start", dest="start_time", type=float, default=None,
			help="Start time in seconds (seek UI to this position on launch).",
		)

	#============================================

	def __init__(
		self,
		reader: object,
		fps: float,
		config: dict,
		save_callback: object,
		predictions: dict | None = None,
	) -> None:
		"""Initialize common controller state.

		Args:
			reader: FrameReader instance with read_frame(idx) method.
			fps: Frames per second of the video.
			config: Configuration dict.
			save_callback: Callable for saving state.
			predictions: Optional dict mapping frame_index to prediction dicts.
		"""
		super().__init__()

		self._reader = reader
		self._fps = fps
		self._config = config
		self._save_callback = save_callback
		self._predictions = predictions

		# Window and UI state
		self._window: object = None
		self._current_frame: int = 0
		self._current_bgr: numpy.ndarray | None = None
		self._done: bool = False

		# Drawing state
		self._drawing: bool = False
		self._drag_start: tuple | None = None
		self._drag_current: tuple | None = None
		self._preview_item: object = None
		self._partial_mode: bool = False
		self._approx_mode: bool = False

		# Overlay items tracked for cleanup
		self._overlay_items: list = []
		self._fwd_item: object = None
		self._bwd_item: object = None
		self._blended_item: object = None
		self._consensus_item: object = None
		self._scale_bar_item: object = None
		self._legend_item: object = None

		# Peek suppression state
		self._preds_suppressed: bool = False

		# Quit double-press confirmation state
		self._quit_pending_time: float = 0.0

		# Per-overlay persistent visibility toggles. "heat" defaults to
		# False because the motion heat-map overlay is an expensive
		# compute-on-show diagnostic, not a zero-cost visual; see
		# track_runner/ui/heat_map_overlay.py. Mode-switch reset in
		# AnnotationWindow must respect this default explicitly.
		self._overlay_visibility: dict = {
			"fwd": True, "bwd": True, "blended": True,
			"consensus": True, "legend": True, "heat": False,
		}
		# Heat-map overlay (created in activate() when the scene exists)
		self._heat_map_overlay: HeatMapOverlay | None = None

		# Toolbar widgets
		self._toolbar_widget: QWidget | None = None
		self._btn_partial: QPushButton | None = None
		self._btn_approx: QPushButton | None = None

	#============================================

	@property
	def toolbar_widget(self) -> QWidget | None:
		"""Toolbar widget for the annotation toolbar.

		Returns:
			QWidget with navigation and draw mode buttons, or None.
		"""
		return self._toolbar_widget

	#============================================

	def activate(self, window: object) -> None:
		"""Activate the controller and connect to window events.

		Args:
			window: AnnotationWindow instance.
		"""
		self._window = window

		# Build toolbar widget
		self._toolbar_widget = self._build_toolbar()

		# Install event filter for keyboard and mouse events
		self._window.installEventFilter(self)
		self._window.get_frame_view().installEventFilter(self)
		viewport = self._window.get_frame_view().viewport()
		viewport.installEventFilter(self)

		# Add scale bar item to scene
		scene = self._window.get_frame_view().scene()
		self._scale_bar_item = ScaleBarItem()
		scene.addItem(self._scale_bar_item)
		self._overlay_items.append(self._scale_bar_item)

		# Add prediction legend if predictions are available
		if self._predictions is not None:
			scene_rect = scene.sceneRect()
			self._legend_item = PredictionLegendItem(
				scene_rect.width(), scene_rect.height(),
			)
			scene.addItem(self._legend_item)
			self._overlay_items.append(self._legend_item)

		# Build motion heat-map overlay (hidden; enabled via toolbar/H).
		# Loads the solver's cached motion_track if it exists on disk so
		# the residual compensates for camera pan correctly. If no cache
		# matches (fresh video, never solved), falls back to identity and
		# the "camera motion not compensated" badge appears under the
		# ROI so the user knows the residual may ghost on pan.
		heat_style = overlay_config.get_heat_map_style()
		scene_transform, transform_ok = self._load_scene_transform_for_gui()
		self._heat_map_overlay = HeatMapOverlay(
			reader=self._reader,
			scene_transform=scene_transform,
			scene=scene,
			style=heat_style,
			get_pred_fn=self._get_heat_prediction,
			scene_transform_available=transform_ok,
			is_drawing_fn=self._is_drawing,
		)
		self._heat_map_overlay.statusChanged.connect(self._on_heat_status)

		# Populate the persistent hint bar below the frame view.
		# Previously this was an in-scene QGraphicsItem whose font scaled
		# with the frame and became unreadable on high-res video.
		mode_color = overlay_config.get_workspace_mode_color(
			self._get_mode_name()
		)
		if hasattr(self._window, "set_hints"):
			self._window.set_hints(
				self._get_mode_name().upper(),
				self._get_keybinding_hints(),
				mode_color,
			)

		# Subclass hook
		self._on_activated()

	#============================================

	def deactivate(self) -> None:
		"""Deactivate the controller and disconnect from window events."""
		if self._window is not None:
			self._window.removeEventFilter(self)
			self._window.get_frame_view().removeEventFilter(self)
			viewport = self._window.get_frame_view().viewport()
			viewport.removeEventFilter(self)

		# Remove all tracked overlay items from scene
		if self._window is not None:
			scene = self._window.get_frame_view().scene()
			for item in self._overlay_items:
				if item is not None:
					scene.removeItem(item)
		self._overlay_items.clear()
		self._fwd_item = None
		self._bwd_item = None
		self._blended_item = None
		self._consensus_item = None
		self._scale_bar_item = None
		self._legend_item = None
		# tear down heat-map overlay and reset visibility so the next
		# controller starts with heat OFF (matches the default).
		if self._heat_map_overlay is not None:
			self._heat_map_overlay.clear()
			self._heat_map_overlay = None
		self._overlay_visibility["heat"] = False
		# clear the persistent hint bar too
		if self._window is not None and hasattr(self._window, "clear_hints"):
			self._window.clear_hints()
		# Remove preview item if present
		if self._preview_item is not None and self._window is not None:
			scene = self._window.get_frame_view().scene()
			scene.removeItem(self._preview_item)
			self._preview_item = None

		# Subclass hook
		self._on_deactivated()

	#============================================

	def _add_overlay(self, item: object) -> None:
		"""Add an overlay item to the scene and tracking list.

		Args:
			item: QGraphicsItem to add.
		"""
		scene = self._window.get_frame_view().scene()
		scene.addItem(item)
		self._overlay_items.append(item)

	#============================================

	def _remove_overlay(self, item: object) -> None:
		"""Remove an overlay item from the scene and tracking list.

		Args:
			item: QGraphicsItem to remove.
		"""
		if item is None:
			return
		scene = self._window.get_frame_view().scene()
		scene.removeItem(item)
		if item in self._overlay_items:
			self._overlay_items.remove(item)

	#============================================

	def eventFilter(self, obj: object, event: object) -> bool:
		"""Handle window and viewport events.

		Args:
			obj: Object that received the event.
			event: Event instance.

		Returns:
			True if event was handled, False otherwise.
		"""
		from PySide6.QtCore import QEvent as QEventType
		from PySide6.QtGui import QMouseEvent

		if event.type() == QEventType.Type.KeyPress:
			key = event.key()
			modifiers = event.modifiers()
			if self.handle_key_press(key, modifiers):
				return True
		elif event.type() == QEventType.Type.MouseButtonPress:
			if isinstance(event, QMouseEvent):
				pos = event.position()
				sx, sy = self._window.get_frame_view().map_to_scene(
					int(pos.x()), int(pos.y())
				)
				self.handle_mouse_press(sx, sy)
				return True
		elif event.type() == QEventType.Type.MouseMove:
			if isinstance(event, QMouseEvent):
				pos = event.position()
				sx, sy = self._window.get_frame_view().map_to_scene(
					int(pos.x()), int(pos.y())
				)
				self.handle_mouse_move(sx, sy)
				return True
		elif event.type() == QEventType.Type.MouseButtonRelease:
			if isinstance(event, QMouseEvent):
				pos = event.position()
				sx, sy = self._window.get_frame_view().map_to_scene(
					int(pos.x()), int(pos.y())
				)
				self.handle_mouse_release(sx, sy)
				return True
		elif event.type() == QEventType.Type.Wheel:
			# Delegate wheel to the FrameView for zoom or trackpad pan
			frame_view = self._window.get_frame_view()
			frame_view.wheelEvent(event)
			# Only update scale bar on mouse wheel zoom, not trackpad pan
			is_trackpad = frame_view._is_trackpad_event(event)
			if not is_trackpad:
				QTimer.singleShot(0, self._update_scale_bar)
			return True

		return super().eventFilter(obj, event)

	#============================================

	def handle_mouse_press(self, scene_x: float, scene_y: float) -> None:
		"""Handle mouse button press.

		Args:
			scene_x: Scene x coordinate.
			scene_y: Scene y coordinate.
		"""
		if self._current_bgr is None:
			return

		self._drawing = True
		self._drag_start = (scene_x, scene_y)
		self._drag_current = (scene_x, scene_y)

		# Remove any old preview item
		if self._preview_item is not None:
			scene = self._window.get_frame_view().scene()
			scene.removeItem(self._preview_item)
			self._preview_item = None

		# User interaction has priority over diagnostic rendering: if
		# heat is ON, cancel any pending compute and hide the overlay
		# so the residual computation cannot block the Qt event loop
		# mid-drag. The controller re-arms heat in handle_mouse_release
		# once drawing is done. The "Heatmap: paused while drawing"
		# status keeps the toggle's ON state visible.
		if self._overlay_visibility.get("heat", False):
			if self._heat_map_overlay is not None:
				self._heat_map_overlay.pause_for_drawing()

	#============================================

	def handle_mouse_move(self, scene_x: float, scene_y: float) -> None:
		"""Handle mouse move.

		Args:
			scene_x: Scene x coordinate.
			scene_y: Scene y coordinate.
		"""
		if not self._drawing or self._drag_start is None:
			return

		self._drag_current = (scene_x, scene_y)

		# Update preview box
		scene = self._window.get_frame_view().scene()
		if self._preview_item is not None:
			scene.removeItem(self._preview_item)

		x1, y1 = self._drag_start
		x2, y2 = self._drag_current
		x = min(x1, x2)
		y = min(y1, y2)
		w = abs(x2 - x1)
		h = abs(y2 - y1)

		self._preview_item = PreviewBoxItem(x, y, w, h)
		scene.addItem(self._preview_item)

	#============================================

	def handle_mouse_release(self, scene_x: float, scene_y: float) -> None:
		"""Handle mouse button release.

		Args:
			scene_x: Scene x coordinate.
			scene_y: Scene y coordinate.
		"""
		if not self._drawing:
			return

		self._drawing = False

		# Re-arm the heat-map overlay only if the user did not toggle
		# it off during the drag. Going through request_show() hits
		# the existing 150 ms debounce, so any follow-up frame refresh
		# that may fire from the box-commit path collapses into one
		# compute rather than stacking. Runs before the drag_start
		# sanity check so heat resumes even if the drag produced no
		# valid box (min-area reject, ESC, etc.).
		if self._overlay_visibility.get("heat", False):
			if self._heat_map_overlay is not None:
				self._heat_map_overlay.request_show(int(self._current_frame))

		if self._drag_start is None:
			return

		x1, y1 = self._drag_start
		x2, y2 = scene_x, scene_y

		# Normalize the box
		x = min(x1, x2)
		y = min(y1, y2)
		w = abs(x2 - x1)
		h = abs(y2 - y1)

		# Remove preview item
		scene = self._window.get_frame_view().scene()
		if self._preview_item is not None:
			scene.removeItem(self._preview_item)
			self._preview_item = None

		# Validate box size
		box_area = w * h
		frame_h, frame_w = self._current_bgr.shape[:2]
		min_area = 10
		max_area = frame_w * frame_h * 0.5

		if box_area < min_area:
			if self._window is not None:
				self._window.statusBar().showMessage(
					"Box too small -- draw a larger rectangle", 3000
				)
			return
		if box_area > max_area:
			if self._window is not None:
				self._window.statusBar().showMessage(
					"Box too large -- draw a smaller rectangle", 3000
				)
			return

		box = [int(x), int(y), int(w), int(h)]
		self._on_box_drawn(box)

	#============================================

	def _handle_common_key(self, key: int, modifiers: object) -> bool | None:
		"""Handle keys common to all controllers.

		Handles ESC/Q, P (partial), A (approx), Z (zoom).

		Args:
			key: Qt key code.
			modifiers: Qt keyboard modifiers.

		Returns:
			True if handled, None if not.
		"""
		if key == Qt.Key.Key_Escape or key == Qt.Key.Key_Q:
			now = time.monotonic()
			# require double-press within 2 seconds to quit
			if now - self._quit_pending_time < 2.0:
				self._on_quit()
			else:
				self._quit_pending_time = now
				if self._window is not None:
					self._window.statusBar().showMessage(
						"Press ESC/Q again to quit", 2000
					)
			return True
		elif key == Qt.Key.Key_P:
			self._on_partial_toggle()
			return True
		elif key == Qt.Key.Key_A:
			self._on_approx_toggle()
			return True
		elif key == Qt.Key.Key_Z:
			self._on_zoom_toggle()
			return True
		elif key == Qt.Key.Key_V:
			self._suppress_predictions()
			return True
		return None

	#============================================

	def _get_prediction_center(self) -> tuple | None:
		"""Get center of best prediction for the current frame.

		Prefers the REFINED (blended) box when available, falling back
		to averaging FWD/BWD centers.

		Returns:
			Tuple of (cx, cy) or None if no predictions available.
		"""
		if self._predictions is None:
			return None
		preds = self._predictions.get(self._current_frame)
		if preds is None:
			return None

		# Prefer REFINED (blended second-pass) center
		blended = preds.get("blended")
		if blended is not None:
			return (float(blended["cx"]), float(blended["cy"]))

		# Fall back to averaging FWD/BWD centers
		centers = []
		fwd = preds.get("forward")
		if fwd is not None:
			centers.append((float(fwd["cx"]), float(fwd["cy"])))
		bwd = preds.get("backward")
		if bwd is not None:
			centers.append((float(bwd["cx"]), float(bwd["cy"])))

		if not centers:
			return None

		avg_cx = sum(c[0] for c in centers) / len(centers)
		avg_cy = sum(c[1] for c in centers) / len(centers)
		return (avg_cx, avg_cy)

	#============================================

	def _update_fwd_bwd_overlays(self) -> None:
		"""Update FWD/BWD/blended/consensus prediction overlays on the scene."""
		# Reset peek suppression on frame advance
		self._preds_suppressed = False

		# Remove old overlays
		if self._fwd_item is not None:
			self._remove_overlay(self._fwd_item)
			self._fwd_item = None
		if self._bwd_item is not None:
			self._remove_overlay(self._bwd_item)
			self._bwd_item = None
		if self._blended_item is not None:
			self._remove_overlay(self._blended_item)
			self._blended_item = None
		if self._consensus_item is not None:
			self._remove_overlay(self._consensus_item)
			self._consensus_item = None

		# Return early if no predictions
		if self._predictions is None:
			return
		preds = self._predictions.get(self._current_frame)
		if preds is None:
			return

		# Consensus overlay (AVG of FWD/BWD) -- Z=3, below others
		cons = preds.get("consensus")
		if cons is not None:
			cons_style = overlay_config.get_prediction_style("consensus")
			cx = float(cons["cx"])
			cy = float(cons["cy"])
			w = float(cons["w"])
			h = float(cons["h"])
			x = int(cx - w / 2.0)
			y = int(cy - h / 2.0)
			self._consensus_item = RectItem(
				x, y, int(w), int(h),
				color_str=cons_style["color"],
				label="AVG",
				fill_alpha=int(cons_style["fill_opacity"] * 255),
				dashed=(cons_style["line_style"] == "dotted"),
				label_slot=4,
			)
			self._consensus_item.setZValue(3)
			self._add_overlay(self._consensus_item)

		# Fused (refined second-pass) overlay -- Z=4
		blended = preds.get("blended")
		if blended is not None:
			blended_style = overlay_config.get_prediction_style("blended")
			cx = float(blended["cx"])
			cy = float(blended["cy"])
			w = float(blended["w"])
			h = float(blended["h"])
			x = int(cx - w / 2.0)
			y = int(cy - h / 2.0)
			self._blended_item = RectItem(
				x, y, int(w), int(h),
				color_str=blended_style["color"],
				label="REFINED",
				fill_alpha=int(blended_style["fill_opacity"] * 255),
				dashed=(blended_style["line_style"] == "dashed"),
				label_slot=1,
			)
			self._blended_item.setZValue(4)
			self._add_overlay(self._blended_item)

		# FWD prediction -- Z=5
		fwd = preds.get("forward")
		if fwd is not None:
			fwd_style = overlay_config.get_prediction_style("forward")
			cx = float(fwd["cx"])
			cy = float(fwd["cy"])
			w = float(fwd["w"])
			h = float(fwd["h"])
			x = int(cx - w / 2.0)
			y = int(cy - h / 2.0)
			self._fwd_item = RectItem(
				x, y, int(w), int(h),
				color_str=fwd_style["color"],
				label="FWD",
				fill_alpha=int(fwd_style["fill_opacity"] * 255),
				dashed=(fwd_style["line_style"] == "dashed"),
				label_slot=2,
			)
			self._fwd_item.setZValue(5)
			self._add_overlay(self._fwd_item)

		# BWD prediction -- Z=5
		bwd = preds.get("backward")
		if bwd is not None:
			bwd_style = overlay_config.get_prediction_style("backward")
			cx = float(bwd["cx"])
			cy = float(bwd["cy"])
			w = float(bwd["w"])
			h = float(bwd["h"])
			x = int(cx - w / 2.0)
			y = int(cy - h / 2.0)
			self._bwd_item = RectItem(
				x, y, int(w), int(h),
				color_str=bwd_style["color"],
				label="BWD",
				fill_alpha=int(bwd_style["fill_opacity"] * 255),
				dashed=(bwd_style["line_style"] == "dashed"),
				label_slot=3,
			)
			self._bwd_item.setZValue(5)
			self._add_overlay(self._bwd_item)

		# reposition legend to the corner farthest from the tracked box
		if self._legend_item is not None:
			# use consensus center as the bbox reference point
			cons = preds.get("consensus")
			if cons is not None:
				bbox_cx = float(cons["cx"])
				bbox_cy = float(cons["cy"])
			else:
				bbox_cx = -1
				bbox_cy = -1
			scene_rect = self._window.get_frame_view().scene().sceneRect()
			self._legend_item.reposition(
				scene_rect.width(), scene_rect.height(),
				bbox_cx, bbox_cy,
			)

		# apply per-overlay visibility (respects user toggles and peek suppression)
		self._apply_overlay_visibility()

		# drive the heat-map overlay from the same refresh hook: hide
		# previous frame's heat immediately, and if the toggle is ON,
		# arm a debounced compute for the current frame.
		self._apply_heat_overlay()

	#============================================

	def _update_scale_bar(self) -> None:
		"""Update the zoom scale bar display."""
		if self._scale_bar_item is None:
			return
		zoom = self._window.get_frame_view().get_zoom_factor()
		self._scale_bar_item.update_zoom(zoom)

	#============================================

	def _on_zoom_toggle(self) -> None:
		"""Cycle zoom: fit -> 1x -> 1.5x -> 2.25x -> 3.375x -> 5x -> 8x -> 12x -> fit.

		Centers zoom on predictions or seed position when available,
		otherwise centers on the frame center.
		"""
		zoom_levels = [1.0, 1.5, 2.25, 3.375, 5.0, 8.0, 12.0]
		frame_view = self._window.get_frame_view()

		# If currently in fit mode, advance to the first fixed level
		if frame_view.is_fit_zoom():
			next_zoom = zoom_levels[0]
		else:
			current = frame_view.get_zoom_factor()
			# find the next zoom level above current
			next_zoom = None
			for zf in zoom_levels:
				if zf > current + 0.01:
					next_zoom = zf
					break
			# wrap around to fit if we passed the last level
			if next_zoom is None:
				frame_view.fit_to_view()
				self._update_scale_bar()
				return

		# determine zoom center for non-fit levels
		center_x = -1.0
		center_y = -1.0
		center = self._get_zoom_center()
		if center is not None:
			center_x, center_y = center
		# fallback to frame center
		if center_x < 0 and self._current_bgr is not None:
			h, w = self._current_bgr.shape[:2]
			center_x = w / 2.0
			center_y = h / 2.0

		frame_view.set_zoom(next_zoom, center_x, center_y)
		self._update_scale_bar()

	#============================================

	def _get_zoom_center(self) -> tuple | None:
		"""Get zoom center point. Subclasses may override.

		Default uses prediction center.

		Returns:
			Tuple of (cx, cy) or None.
		"""
		return self._get_prediction_center()

	#============================================

	def _on_partial_toggle(self) -> None:
		"""Toggle partial draw mode."""
		if self._partial_mode:
			self._partial_mode = False
			self._update_mode_badge()
			print("  partial mode cancelled")
		else:
			self._partial_mode = True
			self._approx_mode = False
			self._update_mode_badge()
			print("  partial mode: draw the runner's torso box (press p again to cancel)")

	#============================================

	def _on_approx_toggle(self) -> None:
		"""Toggle approximate/obstruction draw mode."""
		if self._approx_mode:
			self._approx_mode = False
			self._update_mode_badge()
			print("  approx mode cancelled")
		else:
			self._approx_mode = True
			self._partial_mode = False
			self._update_mode_badge()
			print("  approx mode: draw approximate box for obstructed position")

	#============================================

	def _suppress_predictions(self) -> None:
		"""Toggle temporary prediction overlay suppression for current frame.

		Suppression resets on frame advance (in _update_fwd_bwd_overlays).
		"""
		self._preds_suppressed = not self._preds_suppressed
		self._apply_overlay_visibility()

	#============================================

	def _apply_overlay_visibility(self) -> None:
		"""Apply three-layer visibility model to prediction overlays.

		visible = available AND user_enabled AND NOT temporary_suppressed
		"""
		item_map = {
			"fwd": self._fwd_item,
			"bwd": self._bwd_item,
			"blended": self._blended_item,
			"consensus": self._consensus_item,
			"legend": self._legend_item,
		}
		for key, item in item_map.items():
			if item is not None:
				user_enabled = self._overlay_visibility.get(key, True)
				show = user_enabled and not self._preds_suppressed
				item.setVisible(show)

	#============================================

	def set_overlay_enabled(self, key: str, enabled: bool) -> None:
		"""Set persistent visibility for a specific overlay type.

		Args:
			key: Overlay key ("fwd", "bwd", "blended", "consensus",
				"legend", "heat").
			enabled: Whether the overlay should be visible.
		"""
		if key in self._overlay_visibility:
			self._overlay_visibility[key] = enabled
			self._apply_overlay_visibility()
			# the heat overlay is not governed by prediction-box
			# visibility plumbing; drive it separately so a toolbar
			# click takes effect immediately for the current frame.
			if key == "heat":
				self._apply_heat_overlay()

	#============================================

	def _apply_heat_overlay(self) -> None:
		"""Route the current heat visibility flag to the heat overlay.

		Called from _update_fwd_bwd_overlays on every frame refresh and
		from set_overlay_enabled on toolbar toggle. When the flag is ON
		this hides the previous frame's heat and arms a debounced
		compute for the current frame via HeatMapOverlay.request_show.
		When the flag is OFF this hides the overlay and cancels any
		pending compute.

		When the user is actively drawing a torso box this method is a
		no-op: annotation input has priority over diagnostic rendering,
		and the heat overlay will be re-armed from handle_mouse_release
		once the drag ends. The paused state is communicated via the
		STATUS_DRAWING_PAUSE status emitted by pause_for_drawing().
		"""
		if self._heat_map_overlay is None:
			return
		if self._drawing:
			return
		if self._overlay_visibility.get("heat", False):
			self._heat_map_overlay.request_show(int(self._current_frame))
		else:
			self._heat_map_overlay.hide()

	#============================================

	def _is_drawing(self) -> bool:
		"""Callable injected into HeatMapOverlay for its stale-result guard.

		The overlay invokes this in _compute_and_display right before
		applying the pixmap. A True return discards the compute output
		so a late-arriving residual cannot overwrite the frozen view
		mid-drag.

		Returns:
			True while a torso-box drag is in progress, else False.
		"""
		return bool(self._drawing)

	#============================================

	def _get_heat_prediction(self, frame_index: int) -> tuple | None:
		"""Return ((cx, cy), (w, h)) for the heat ROI, or None.

		Chooses the most trustworthy available prediction: blended over
		consensus over forward. Returns None when no prediction is
		available (pre-race, unsolved intervals, edge frames).

		Args:
			frame_index: Frame to look up a prediction for.

		Returns:
			Tuple ((cx, cy), (w, h)) in full-frame pixels, or None.
		"""
		if self._predictions is None:
			return None
		preds = self._predictions.get(frame_index)
		if preds is None:
			return None
		# priority order: blended > consensus > forward. Use explicit
		# None checks so a legitimately falsy dict (e.g. an empty one)
		# does not silently fall through to a less-preferred source.
		pick = None
		if preds.get("blended") is not None:
			pick = preds["blended"]
		elif preds.get("consensus") is not None:
			pick = preds["consensus"]
		elif preds.get("forward") is not None:
			pick = preds["forward"]
		if pick is None:
			return None
		cx = float(pick["cx"])
		cy = float(pick["cy"])
		w = float(pick["w"])
		h = float(pick["h"])
		pred = ((cx, cy), (w, h))
		return pred

	#============================================

	def _on_heat_status(self, text: str) -> None:
		"""Relay heat-map status strings to the window's status label.

		Args:
			text: Status string emitted by HeatMapOverlay.statusChanged.
		"""
		if self._window is not None and hasattr(self._window, "set_heat_status"):
			self._window.set_heat_status(text)

	#============================================

	def _load_scene_transform_for_gui(self) -> tuple:
		"""Find and load the solver's cached motion track for the heat map.

		Loads the active per-hash camera-motion cache (the file the
		current solved state binds to via the active.json marker),
		with a legacy single-file fallback for pre-marker runs. If
		nothing loads, returns an identity transform so the GUI still
		opens on fresh videos and the "camera motion not compensated"
		disclosure badge fires.

		Returns:
			Tuple (scene_transform, available: bool). available is True
			only when a real cache was loaded; False means identity and
			triggers the "camera motion not compensated" disclosure badge.
		"""
		n_frames = max(int(self._reader.frame_count), 1)
		motion_track = None
		video_path = getattr(self._reader, "video_path", None)
		if video_path is not None:
			try:
				motion_track = camera_motion.load_active_camera_motion_or_fail(
					video_path
				)
			except RuntimeError:
				motion_track = None
		if motion_track is not None:
			transform = scene_coords.SceneTransform(motion_track)
			return (transform, True)
		# identity fallback
		identity_motion = camera_motion.MotionTrack(
			dx=numpy.zeros(n_frames, dtype=numpy.float32),
			dy=numpy.zeros(n_frames, dtype=numpy.float32),
			scale=numpy.ones(n_frames, dtype=numpy.float32),
			quality=numpy.ones(n_frames, dtype=numpy.float32),
		)
		transform = scene_coords.SceneTransform(identity_motion)
		return (transform, False)

	#============================================

	def _sync_toolbar_buttons(self) -> None:
		"""Sync toolbar button checked state with internal mode flags."""
		if self._btn_partial is not None:
			self._btn_partial.setChecked(self._partial_mode)
		if self._btn_approx is not None:
			self._btn_approx.setChecked(self._approx_mode)

	#============================================

	def _update_mode_badge(self) -> None:
		"""Update the status bar to show active draw mode (partial/approx).

		Calls _sync_toolbar_buttons, applies badge styling, and falls
		back to _get_default_status_text() for normal state.
		"""
		self._sync_toolbar_buttons()
		if self._window is None:
			return
		if self._approx_mode:
			approx_color = overlay_config.get_draw_mode_badge_color("approximate")
			self._window.statusBar().setStyleSheet(
				f"background-color: {approx_color}; color: #000000; font-weight: bold;"
			)
			self._set_status_text(
				"** APPROX MODE ** draw approximate box (press 'a' to cancel)"
			)
		elif self._partial_mode:
			partial_color = overlay_config.get_draw_mode_badge_color("partial")
			self._window.statusBar().setStyleSheet(
				f"background-color: {partial_color}; color: #000000; font-weight: bold;"
			)
			self._set_status_text(
				"** PARTIAL MODE ** draw visible torso (press 'p' to cancel)"
			)
		else:
			self._window.statusBar().setStyleSheet("")
			self._restore_default_status()

	#============================================

	def _set_status_text(self, text: str) -> None:
		"""Set the status bar message text.

		Subclasses may override if they use a custom status widget.

		Args:
			text: Message to display.
		"""
		self._window.statusBar().showMessage(text)

	#============================================

	def _restore_default_status(self) -> None:
		"""Restore the default status text.

		Subclasses may override to update their own status widget.
		"""
		text = self._get_default_status_text()
		self._window.statusBar().showMessage(text)

	#============================================
	# Abstract methods -- subclasses must implement

	def _on_box_drawn(self, box: list) -> None:
		"""Process a completed drawn box. Subclass must implement.

		Args:
			box: Box as [x, y, w, h].
		"""
		raise NotImplementedError

	#============================================

	def _on_quit(self) -> None:
		"""Handle quit/done request. Subclass must implement."""
		raise NotImplementedError

	#============================================

	def _build_toolbar(self) -> QWidget:
		"""Build the controller toolbar. Subclass must implement.

		Returns:
			QWidget for the annotation toolbar.
		"""
		raise NotImplementedError

	#============================================

	def _on_activated(self) -> None:
		"""Called after base activate finishes. Subclass must implement."""
		raise NotImplementedError

	#============================================

	def _on_deactivated(self) -> None:
		"""Called after base deactivate finishes. Subclass must implement."""
		raise NotImplementedError

	#============================================

	def _get_default_status_text(self) -> str:
		"""Short mode/state summary for the status bar. Subclass must implement.

		Returns:
			String with mode summary.
		"""
		raise NotImplementedError

	#============================================

	def _get_keybinding_hints(self) -> str:
		"""Keybinding hint string for the key hint overlay. Subclass must implement.

		Returns:
			String with keybinding hints (without mode label prefix).
		"""
		raise NotImplementedError

	#============================================

	def _get_mode_name(self) -> str:
		"""Short mode name for display. Subclass must implement.

		Returns:
			String like "seed", "edit", or "target".
		"""
		raise NotImplementedError

	#============================================

	def handle_key_press(self, key: int, modifiers: object = None) -> bool:
		"""Handle keyboard events. Subclass must implement.

		Args:
			key: Qt key code.
			modifiers: Qt keyboard modifiers.

		Returns:
			True if event was handled.
		"""
		raise NotImplementedError
