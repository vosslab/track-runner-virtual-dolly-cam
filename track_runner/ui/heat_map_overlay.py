"""Sticky ROI motion heat-map overlay for the annotation GUI.

Wraps the headless facade in track_runner.residual_heat_map and owns the
Qt-side pixmap / outline / legend / disclosure-badge lifecycle inside a
QGraphicsScene. Designed to be held by the BaseAnnotationController and
driven from _refresh_frame.

Sticky mode semantics:
  - request_show(frame_index) hides any prior overlay immediately, emits
    "computing...", arms a 150 ms debounce timer, then runs the compute
    on the latest pending frame index when the timer fires.
  - Rapid navigation collapses N calls into one compute for the final
    landing frame.
  - hide() cancels any pending compute and clears all overlay items.
  - clear() removes all items from the scene (mode-switch teardown).

Status strings are a small closed set emitted via statusChanged(str) so
callers can render feedback without guessing what the overlay is doing.
"""

# Standard Library
# (none)

# PIP3 modules
import cv2
import numpy
from PySide6.QtCore import QObject, Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPen, QPixmap
from PySide6.QtWidgets import (
	QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsSimpleTextItem,
)

# local repo modules
import residual_heat_map


# Z-values placed between the frame pixmap (conventionally at 0) and
# prediction overlays (drawn later with higher z). Kept as module
# constants so future overlays can pick adjacent slots without collision.
Z_HEAT_PIXMAP = 50.0
Z_HEAT_OUTLINE = 51.0
Z_HEAT_LEGEND = 52.0
# badge has its own background rect one slot below the text so the
# warning stays legible against any frame content.
Z_HEAT_BADGE_BG = 52.5
Z_HEAT_BADGE = 53.0

# debounce window for rapid frame-scrubbing
DEBOUNCE_MS = 150

# closed set of status strings; kept as module constants so a future
# patch can i18n them without hunting through string literals. Every
# active string leads with "Heatmap: " so the feature's mode stays
# visible at a glance in the toolbar status label, not just when a
# transient event (computing, no-prediction) is emitted.
STATUS_EMPTY = ""
STATUS_COMPUTING = "Heatmap: computing..."
STATUS_NO_PREDICTION = "Heatmap: no prediction for this frame"
STATUS_EDGE_FRAME = "Heatmap: residual unavailable (edge frame)"
STATUS_DRAWING_PAUSE = "Heatmap: paused while drawing"


#============================================
def _status_for_success(frame_index: int) -> str:
	"""Build the success status string.

	Args:
		frame_index: Frame index that was just rendered.

	Returns:
		Human-readable status string, prefixed with "Heatmap: ".
	"""
	text = f"Heatmap: ROI shown at frame {frame_index}"
	return text


#============================================
def _bgr_to_pixmap(bgr: numpy.ndarray) -> QPixmap:
	"""Convert a BGR uint8 image to a detached QPixmap.

	The QImage constructor shares memory with the numpy buffer, so we
	build a contiguous RGB copy first and .copy() the QImage to detach
	before handing it off to QPixmap.

	Args:
		bgr: BGR uint8 array shape (H, W, 3).

	Returns:
		QPixmap ready for scene placement.
	"""
	rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
	rgb_contig = numpy.ascontiguousarray(rgb)
	h, w = rgb_contig.shape[:2]
	qimg = QImage(
		rgb_contig.data, w, h, 3 * w, QImage.Format_RGB888,
	)
	pixmap = QPixmap.fromImage(qimg.copy())
	return pixmap


#============================================
class HeatMapOverlay(QObject):
	"""Sticky-mode ROI motion heat-map overlay in a QGraphicsScene.

	Owns four hidden graphics items (pixmap, outline, legend, badge) that
	are revealed together when a compute succeeds and hidden together on
	hide() / request_show(). Emits statusChanged for every state
	transition so a status label elsewhere in the window can reflect the
	current overlay state without polling.

	Args:
		reader: VideoReader with .width, .height, .frame_count, .read_frame.
		scene_transform: SceneTransform passed through to the facade. An
			identity transform is acceptable; the overlay sets
			scene_transform_available=False to show the disclosure badge.
		scene: QGraphicsScene to attach the overlay items to.
		style: Dict from overlay_config.get_heat_map_style() with keys
			fixed_max, blend_alpha, half_window, threshold, outline_rgb,
			legend_text, missing_transform_note.
		get_pred_fn: Callable(frame_index) -> ((cx, cy), (w, h)) or None.
		scene_transform_available: True when the scene_transform argument
			is a real loaded transform; False when it is an identity
			fallback (causes the disclosure badge to render).
	"""

	statusChanged = Signal(str)

	#============================================
	def __init__(
		self,
		reader: object,
		scene_transform: object,
		scene: object,
		style: dict,
		get_pred_fn: object,
		scene_transform_available: bool = False,
		is_drawing_fn: object = None,
	):
		"""Create hidden overlay items and attach them to the scene.

		Args:
			is_drawing_fn: Optional callable returning True while the
				user is actively drawing. If provided and True when a
				compute completes, the pixmap update is discarded so a
				late-arriving result cannot overwrite the frozen view
				mid-drag. None disables the guard (compute always
				applies).
		"""
		super().__init__()
		self._reader = reader
		self._scene_transform = scene_transform
		self._scene = scene
		self._style = style
		self._get_pred_fn = get_pred_fn
		self._transform_available = bool(scene_transform_available)
		self._is_drawing_fn = is_drawing_fn

		# Build hidden graphics items. Pixmap, outline, legend are
		# positioned at compute time; they start offscreen (at 0,0)
		# hidden, which is fine.
		self._pixmap_item = QGraphicsPixmapItem()
		self._pixmap_item.setZValue(Z_HEAT_PIXMAP)
		self._pixmap_item.setVisible(False)
		self._scene.addItem(self._pixmap_item)

		# ROI outline: 1 px neutral-gray rect, no fill. Helps the user
		# read the boundary between "heat-mapped region" and raw frame.
		outline_pen = QPen(QColor(*self._style["outline_rgb"]))
		outline_pen.setWidth(1)
		outline_pen.setCosmetic(True)
		self._outline_item = QGraphicsRectItem()
		self._outline_item.setPen(outline_pen)
		self._outline_item.setBrush(QBrush(Qt.NoBrush))
		self._outline_item.setZValue(Z_HEAT_OUTLINE)
		self._outline_item.setVisible(False)
		self._scene.addItem(self._outline_item)

		# Tiny JET legend anchored to the ROI top-left corner.
		legend_brush = QBrush(QColor(*self._style["outline_rgb"]))
		self._legend_item = QGraphicsSimpleTextItem(self._style["legend_text"])
		self._legend_item.setBrush(legend_brush)
		self._legend_item.setZValue(Z_HEAT_LEGEND)
		self._legend_item.setVisible(False)
		self._scene.addItem(self._legend_item)

		# Disclosure badge: built unconditionally so show/hide is a
		# visibility flip, not a conditional addItem. Rendered with a
		# bold red font over a dark translucent background rect so the
		# warning is impossible to miss even when it sits over the busy
		# content of the video frame. A silent identity-fallback would
		# otherwise let the user blame the residual algorithm for pan
		# ghosting that is actually a missing-transform diagnostic.
		self._badge_bg_item = QGraphicsRectItem()
		badge_bg_color = QColor(0, 0, 0, 180)
		self._badge_bg_item.setBrush(QBrush(badge_bg_color))
		self._badge_bg_item.setPen(QPen(Qt.NoPen))
		self._badge_bg_item.setZValue(Z_HEAT_BADGE_BG)
		self._badge_bg_item.setVisible(False)
		self._scene.addItem(self._badge_bg_item)
		self._badge_item = QGraphicsSimpleTextItem(
			self._style["missing_transform_note"],
		)
		badge_font = QFont()
		badge_font.setPointSize(14)
		badge_font.setBold(True)
		self._badge_item.setFont(badge_font)
		self._badge_item.setBrush(QBrush(QColor("#EF4444")))
		self._badge_item.setZValue(Z_HEAT_BADGE)
		self._badge_item.setVisible(False)
		self._scene.addItem(self._badge_item)

		# debounce machinery: one single-shot timer; pending_frame holds
		# the most recent request, so if the user scrubs past 10 frames
		# we still only compute for the frame they landed on.
		self._pending_frame: int | None = None
		# monotonic counter of request_show calls; each compute remembers
		# the generation that scheduled it, and a compute whose generation
		# is no longer the latest discards its result in the stale guard.
		# Main-thread-blocking compute makes a race rare today; the guard
		# defends against future threaded compute without behavior change.
		self._request_generation: int = 0
		self._timer = QTimer(self)
		self._timer.setSingleShot(True)
		self._timer.setInterval(DEBOUNCE_MS)
		self._timer.timeout.connect(self._on_timer_fire)

		# test hook: number of times compute has been dispatched. Tests
		# use this to confirm rapid requests collapsed correctly. Not
		# part of the public API.
		self._compute_count = 0

	#============================================
	def request_show(self, frame_index: int) -> None:
		"""Schedule the heat map to appear for `frame_index`.

		Always hides any currently-visible overlay immediately so the
		previous frame's heat never lingers over the new frame. Emits
		STATUS_COMPUTING, starts the debounce timer, and returns. The
		actual compute runs in _compute_and_display when the timer
		fires.

		Args:
			frame_index: Frame to compute heat for.
		"""
		self._pending_frame = int(frame_index)
		self._request_generation += 1
		self._hide_items()
		# restart the debounce window from scratch
		self._timer.stop()
		self._timer.start()
		self.statusChanged.emit(STATUS_COMPUTING)

	#============================================
	def hide(self) -> None:
		"""Hide the overlay and cancel any pending compute (toggle OFF path)."""
		self._pending_frame = None
		self._timer.stop()
		self._hide_items()
		self.statusChanged.emit(STATUS_EMPTY)

	#============================================
	def pause_for_drawing(self) -> None:
		"""Cancel pending compute and FREEZE the current overlay image.

		Distinct from `hide()` in two ways:
		  - preserves the toggle's ON intent (controller re-arms on
		    mouse release);
		  - leaves whatever image was last composited visible rather
		    than hiding items. Disappear-on-press would flicker the
		    ROI every time the user starts a drag; freeze-on-press
		    keeps the view stable and matches what the user was
		    looking at when they clicked.
		Emits a status string so the user can see the mode is still
		ON and merely paused by their own action.
		"""
		self._pending_frame = None
		self._timer.stop()
		self.statusChanged.emit(STATUS_DRAWING_PAUSE)

	#============================================
	def clear(self) -> None:
		"""Remove overlay items from the scene. Safe to call twice."""
		self._pending_frame = None
		self._timer.stop()
		for item in (
			self._pixmap_item, self._outline_item,
			self._legend_item, self._badge_bg_item, self._badge_item,
		):
			if item is not None and item.scene() is self._scene:
				self._scene.removeItem(item)

	#============================================
	def _on_timer_fire(self) -> None:
		"""Debounce timer fired; run compute on the latest pending frame."""
		if self._pending_frame is None:
			return
		frame_index = self._pending_frame
		self._pending_frame = None
		# snapshot the generation so _compute_and_display can detect a
		# newer request_show that lands while this compute runs.
		generation_at_start = self._request_generation
		self._compute_and_display(frame_index, generation_at_start)

	#============================================
	def _compute_and_display(
		self, frame_index: int, generation_at_start: int = 0,
	) -> None:
		"""Run the compute and render the overlay (or emit a status).

		Three branches in order:
			1. get_pred_fn returns None -> STATUS_NO_PREDICTION, hidden.
			2. facade returns None -> STATUS_EDGE_FRAME, hidden.
			3. success -> render pixmap + outline + legend (+ badge if
			   scene transform is missing), emit success status.

		Two stale-result guards run after compute and before apply:
			- is_drawing_fn -> True: discard (user started drawing after
			  compute began).
			- _request_generation advanced past generation_at_start:
			  discard (user navigated to a different frame since this
			  compute was scheduled; primarily defensive for future
			  threaded compute).

		Args:
			frame_index: Frame to render heat for.
			generation_at_start: request_show generation counter at the
				moment this compute was scheduled.
		"""
		self._compute_count += 1

		pred = self._get_pred_fn(frame_index)
		if pred is None:
			self.statusChanged.emit(STATUS_NO_PREDICTION)
			return
		pred_center, pred_box = pred

		result = residual_heat_map.compute_heat_map_roi(
			self._reader, frame_index, self._scene_transform,
			pred_center, pred_box,
			half_window=self._style["half_window"],
			threshold=self._style["threshold"],
			fixed_max=self._style["fixed_max"],
			blend_alpha=self._style["blend_alpha"],
		)
		if result is None:
			self.statusChanged.emit(STATUS_EDGE_FRAME)
			return

		# stale-result guard 1: if the user started drawing while this
		# compute was in flight, drop the result. Applying it would
		# overwrite the frozen view mid-drag and disrupt the box-draw
		# interaction. Emit the paused-while-drawing status so the
		# user can still see the mode is ON.
		if self._is_drawing_fn is not None and self._is_drawing_fn():
			self.statusChanged.emit(STATUS_DRAWING_PAUSE)
			return
		# stale-result guard 2: if a newer request_show was issued while
		# this compute ran, the user has moved to a different frame and
		# this result is for the wrong one. Drop it silently; the newer
		# compute will emit its own status when it completes.
		if self._request_generation != generation_at_start:
			return
		bgr, origin = result

		# BGR -> QPixmap (detached from numpy buffer).
		pixmap = _bgr_to_pixmap(bgr)
		h, w = bgr.shape[:2]
		origin_x, origin_y = origin

		# place pixmap at full opacity. the facade already composited
		# the underlying frame into the BGR result (grayscale for
		# below-threshold pixels, JET-over-color for above-threshold),
		# so any Qt-level alpha would double-blend and lighten the
		# whole ROI.
		self._pixmap_item.setPixmap(pixmap)
		self._pixmap_item.setPos(float(origin_x), float(origin_y))
		self._pixmap_item.setOpacity(1.0)
		self._pixmap_item.setVisible(True)

		# place outline rect around the ROI (full opacity)
		rect = QRectF(
			float(origin_x), float(origin_y), float(w), float(h),
		)
		self._outline_item.setRect(rect)
		self._outline_item.setOpacity(1.0)
		self._outline_item.setVisible(True)

		# legend anchored to ROI top-left with a small inset
		legend_x = float(origin_x) + 6.0
		legend_y = float(origin_y) + 6.0
		self._legend_item.setPos(legend_x, legend_y)
		self._legend_item.setVisible(True)

		# disclosure badge below the ROI when scene transform is absent.
		# size the background rect to the text's bounding rect + padding
		# so it fully backs the warning label against busy frame content.
		if not self._transform_available:
			badge_x = float(origin_x)
			badge_y = float(origin_y) + float(h) + 4.0
			self._badge_item.setPos(badge_x + 6.0, badge_y + 3.0)
			text_rect = self._badge_item.boundingRect()
			self._badge_bg_item.setRect(
				badge_x, badge_y,
				float(text_rect.width()) + 12.0,
				float(text_rect.height()) + 6.0,
			)
			self._badge_bg_item.setVisible(True)
			self._badge_item.setVisible(True)

		success_status = _status_for_success(frame_index)
		self.statusChanged.emit(success_status)

	#============================================
	def _hide_items(self) -> None:
		"""Flip visibility False on all overlay items without removing them."""
		for item in (
			self._pixmap_item, self._outline_item,
			self._legend_item, self._badge_bg_item, self._badge_item,
		):
			if item is not None:
				item.setVisible(False)
