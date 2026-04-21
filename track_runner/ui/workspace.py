"""Annotation workspace for track runner.

Provides the AnnotationWindow with mode toolbar and annotation controls.
"""

# Standard Library
# (none needed)

# PIP3 modules
from PySide6.QtWidgets import (
	QApplication, QLabel, QPushButton, QProgressBar,
	QWidget, QVBoxLayout, QDialog, QTextBrowser,
)
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import (
	QAction, QActionGroup, QFontDatabase, QIcon, QPixmap, QColor,
	QKeySequence, QShortcut,
)

# local repo modules
import overlay_config
import common_tools.frame_filters as frame_filters_module
import ui.frame_view as frame_view_module
import ui.app_shell as app_shell_module
import ui.zoom_controls as zoom_controls_module
import ui.heat_map_overlay as heat_map_overlay_module

FrameView = frame_view_module.FrameView
AppShell = app_shell_module.AppShell
ZoomControls = zoom_controls_module.ZoomControls

#============================================

class AnnotationWindow(AppShell):
	"""Main annotation workspace with mode selection and frame display.

	Provides a window with mode toolbar (Seed, Target, Edit), frame view,
	and annotation controls. Manages controller activation/deactivation
	and persists window geometry via QSettings.
	"""

	def __init__(self, title: str = "Track Runner", initial_mode: str = "seed") -> None:
		"""Initialize the AnnotationWindow.

		Args:
			title: Window title to display.
			initial_mode: Starting mode ("seed", "target", or "edit").
		"""
		super().__init__()

		self.setWindowTitle(title)

		# Create frame view and wrap it with a persistent hint bar below
		self._frame_view = FrameView()
		# hint bar: monospace QLabel that shows current-mode shortcuts.
		# Font family comes from QFontDatabase so the OS picks its own
		# fixed-width face (Menlo on macOS, Consolas on Windows, DejaVu
		# Sans Mono on most Linux) without a QSS alias-resolution pass.
		self._hint_bar = QLabel("")
		self._hint_bar.setTextFormat(Qt.TextFormat.RichText)
		hint_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
		hint_font.setPointSize(11)
		self._hint_bar.setFont(hint_font)
		self._hint_bar.setStyleSheet(
			"QLabel { background: #111111; color: #C0C0C0; "
			"padding: 4px 8px; }"
		)
		self._hint_bar.setMinimumHeight(22)
		# central widget wraps frame view + hint bar
		central = QWidget()
		layout = QVBoxLayout(central)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)
		layout.addWidget(self._frame_view, 1)
		layout.addWidget(self._hint_bar, 0)
		self.setCentralWidget(central)
		# cache last hints so the help dialog can render the full list
		self._last_hint_mode = ""
		self._last_hint_text = ""
		self._last_hint_color = "#FFFFFF"
		# F1 / ? opens a help dialog listing the current-mode shortcuts
		help_shortcut_f1 = QShortcut(QKeySequence("F1"), self)
		help_shortcut_f1.activated.connect(self._show_help_dialog)
		help_shortcut_q = QShortcut(QKeySequence("?"), self)
		help_shortcut_q.activated.connect(self._show_help_dialog)

		# Mode colors loaded from overlay_styles.yaml
		self._mode_colors = {
			"seed": overlay_config.get_workspace_mode_color("seed"),
			"target": overlay_config.get_workspace_mode_color("target"),
			"edit": overlay_config.get_workspace_mode_color("edit"),
		}

		# Create annotation toolbar
		self._annotation_toolbar = self.addToolBar("Annotation")
		self._annotation_toolbar.setMovable(False)

		# Guard: set_controller() is called during init via setChecked signal;
		# skip teardown logic until all widgets exist
		self._init_complete = False

		# Initialize state before mode toolbar (setChecked fires _on_mode_changed)
		self._active_controller = None
		self._controller_widget_action = None
		self._current_mode = "seed"
		self._current_filter = "bilateral+clahe"
		self._raw_bgr = None

		# Create mode label before mode toolbar so _on_mode_changed can update it
		self._mode_label = QLabel("MODE: SEED")
		self._annotation_toolbar.addWidget(self._mode_label)

		# Create mode toolbar
		self._mode_toolbar = self.addToolBar("Modes")
		self._mode_toolbar.setMovable(False)

		# Create mode action group (mutually exclusive)
		self._mode_group = QActionGroup(self)
		self._mode_group.setExclusive(True)

		self._mode_actions = {}
		for mode in ["seed", "target", "edit"]:
			action = QAction(mode.capitalize(), self)
			action.setCheckable(True)
			action.setData(mode)
			action.toggled.connect(self._on_mode_changed)
			self._mode_group.addAction(action)
			self._mode_toolbar.addAction(action)
			self._mode_actions[mode] = action

		# Set initial mode (defaults to seed)
		self._mode_actions[initial_mode].setChecked(True)

		# Apply initial mode color
		self._apply_mode_color(initial_mode)

		# Add display filter button to annotation toolbar
		self._filter_button = QPushButton("Filter: bilateral+clahe")
		self._filter_button.clicked.connect(self._cycle_filter)
		self._annotation_toolbar.addWidget(self._filter_button)

		# Create overlay visibility toolbar with checkable toggle actions
		self._overlay_toolbar = self.addToolBar("Overlays")
		self._overlay_toolbar.setMovable(False)
		# map of overlay key -> (label, color hex from predictions section)
		pred_colors = {
			"fwd": ("FWD", overlay_config.get_prediction_color("forward")),
			"bwd": ("BWD", overlay_config.get_prediction_color("backward")),
			"fused": ("REFINED", overlay_config.get_prediction_color("fused")),
			"consensus": ("AVG", overlay_config.get_prediction_color("consensus")),
			"legend": ("Legend", "#FFFFFF"),
		}
		self._overlay_actions: dict = {}
		for key, (label, color) in pred_colors.items():
			action = QAction(label, self)
			action.setCheckable(True)
			action.setChecked(True)
			# color-code the action icon with a small swatch
			action.setIcon(self._make_swatch_icon(color))
			action.setData(key)
			action.toggled.connect(self._on_overlay_toggled)
			self._overlay_toolbar.addAction(action)
			self._overlay_actions[key] = action

		# Motion heat-map overlay. Default OFF (expensive compute).
		# Toggled via this action or the H keyboard shortcut. Owned by
		# the active controller; see BaseAnnotationController.
		heat_action = QAction("Heat", self)
		heat_action.setCheckable(True)
		heat_action.setChecked(False)
		heat_action.setToolTip("Toggle motion heat-map overlay (H)")
		# swatch picks a deep magenta not used by any prediction,
		# severity, or tracking-source palette entry so the toolbar
		# icon cannot be misread as another overlay class.
		heat_action.setIcon(self._make_swatch_icon("#A21CAF"))
		heat_action.setData("heat")
		heat_action.toggled.connect(self._on_overlay_toggled)
		self._overlay_toolbar.addAction(heat_action)
		self._overlay_actions["heat"] = heat_action

		# Make the heat toolbar button visibly latched when checked:
		# saturated magenta background with bold white text, distinct
		# from all prediction/severity/tracking-source palette colors.
		# Magenta is used (not red) so the active state is not read as
		# an error condition. The stylesheet applies to the specific
		# QToolButton rather than the whole toolbar so other overlay
		# toggles keep their default Qt checked style.
		heat_tool_button = self._overlay_toolbar.widgetForAction(heat_action)
		if heat_tool_button is not None:
			heat_tool_button.setStyleSheet(
				"QToolButton:checked { "
				"background-color: #A21CAF; color: white; "
				"font-weight: bold; border: 2px solid #D946EF; "
				"padding: 2px 6px; border-radius: 3px; }"
			)

		# Status label for heat-map overlay feedback ("computing...",
		# "ROI shown at frame N", "no prediction for this frame", etc.)
		self._heat_status_label = QLabel("")
		self._heat_status_label.setStyleSheet(
			"QLabel { color: #94A3B8; padding: 0 8px; font-size: 11px; }"
		)
		self._overlay_toolbar.addWidget(self._heat_status_label)

		# H keyboard shortcut toggles the heat action. Keeping a
		# reference prevents garbage collection of the QShortcut.
		self._heat_shortcut = QShortcut(QKeySequence("H"), self)
		self._heat_shortcut.activated.connect(heat_action.toggle)

		# Lazy-built "Loading heatmap..." dialog. Shown while the heat
		# compute is running (STATUS_COMPUTING) and hidden the moment any
		# other status arrives. The frame view is disabled in parallel
		# so the user cannot click into a half-ready scene to start
		# drawing a torso box. Constructed on first use in _set_heat_busy.
		self._heat_busy_dialog: QDialog | None = None

		# Add zoom controls to the status bar
		self._zoom_controls = ZoomControls()
		self.statusBar().addPermanentWidget(self._zoom_controls)
		# Connect zoom controls -> frame view
		self._zoom_controls.zoom_in_clicked.connect(self._on_zoom_in)
		self._zoom_controls.zoom_out_clicked.connect(self._on_zoom_out)
		self._zoom_controls.zoom_to_fit_clicked.connect(self._frame_view.fit_to_view)
		self._zoom_controls.zoom_slider_changed.connect(self._on_zoom_slider)
		# Connect frame view -> zoom controls (bidirectional sync)
		self._frame_view.zoom_changed.connect(
			self._zoom_controls.update_zoom_display
		)

		# Add progress bar to the status bar
		self._progress_bar = QProgressBar()
		self._progress_bar.setMaximumWidth(200)
		self._progress_bar.setTextVisible(True)
		self._progress_bar.setFormat("%v / %m")
		self._progress_bar.setValue(0)
		self._progress_bar.setMaximum(0)
		# style progress bar for the dark theme
		mode_color = self._mode_colors.get(initial_mode, "#0D9488")
		self._progress_bar.setStyleSheet(
			"QProgressBar { max-height: 14px; border: none; "
			"background: #1A1A2E; border-radius: 2px; text-align: center; "
			"font-size: 10px; color: #F8FAFC; }"
			f"QProgressBar::chunk {{ border-radius: 2px; background: {mode_color}; }}"
		)
		self.statusBar().addPermanentWidget(self._progress_bar)

		# Restore window geometry from QSettings
		settings = QSettings("emwy", "AnnotationWindow")
		geometry = settings.value("geometry")
		if geometry is not None:
			self.restoreGeometry(geometry)

		# all widgets created; set_controller() can now do full teardown
		self._init_complete = True

	#============================================

	def _on_mode_changed(self, checked: bool) -> None:
		"""Handle mode button toggled signal.

		Determines which mode is now active, updates UI, and deactivates
		current controller.

		Args:
			checked: True if action is now checked.
		"""
		# Find which mode action is now checked
		current_mode = None
		for mode, action in self._mode_actions.items():
			if action.isChecked():
				current_mode = mode
				break

		if current_mode is None:
			return

		# Update mode label
		mode_text = current_mode.upper()
		self._mode_label.setText(f"MODE: {mode_text}")

		# Apply mode color to frame view
		self._apply_mode_color(current_mode)

		# Deactivate current controller
		self.set_controller(None)

		# Update internal state
		self._current_mode = current_mode

	#============================================

	def _apply_mode_color(self, mode: str) -> None:
		"""Apply mode-specific accent color to frame view.

		Args:
			mode: Mode name ("seed", "target", or "edit").
		"""
		color = self._mode_colors.get(mode, "#0D9488")
		# Apply subtle color accent as top border to the frame view
		stylesheet = f"border-top: 2px solid {color};"
		self._frame_view.setStyleSheet(stylesheet)

	#============================================

	def set_controller(self, controller) -> None:
		"""Set or clear the active controller.

		Deactivates the previous controller, activates the new one,
		and swaps annotation toolbar widgets.

		Args:
			controller: Controller instance with optional activate/deactivate
				methods and toolbar_widget attribute, or None.
		"""
		# Deactivate previous controller
		if self._active_controller is not None:
			if hasattr(self._active_controller, "deactivate"):
				self._active_controller.deactivate()

		# Store new controller
		self._active_controller = controller

		# skip widget teardown during __init__ (setChecked fires before widgets exist)
		if not self._init_complete:
			return

		# Reset progress bar between controller swaps
		self._progress_bar.setValue(0)
		self._progress_bar.setMaximum(0)

		# Reset overlay toggles on mode switch. Prediction overlays
		# return to their default-visible state; the heat overlay
		# defaults to OFF so it does not fire compute unprompted after
		# a mode swap.
		for key, action in self._overlay_actions.items():
			default_checked = (key != "heat")
			action.setChecked(default_checked)
		# clear any stale heat status from the previous controller and
		# force-hide the busy popup in case a mode switch interrupts an
		# in-flight compute (the frame view must not stay disabled).
		if hasattr(self, "_heat_status_label") and self._heat_status_label is not None:
			self._heat_status_label.setText("")
		if hasattr(self, "_heat_busy_dialog"):
			self._set_heat_busy(False)

		# Remove previous controller widget from toolbar (keep mode_label and filter_button)
		if self._controller_widget_action is not None:
			self._annotation_toolbar.removeAction(self._controller_widget_action)
			self._controller_widget_action = None

		# Update persistent widget labels
		self._mode_label.setText(f"MODE: {self._current_mode.upper()}")
		self._filter_button.setText(f"Filter: {self._current_filter}")

		# Activate new controller if provided
		if self._active_controller is not None:
			if hasattr(self._active_controller, "activate"):
				self._active_controller.activate(self)
			# Add controller toolbar widget if available
			if hasattr(self._active_controller, "toolbar_widget"):
				widget = self._active_controller.toolbar_widget
				if widget is not None:
					self._controller_widget_action = self._annotation_toolbar.addWidget(widget)

	#============================================

	def _make_swatch_icon(self, hex_color: str) -> QIcon:
		"""Create a small colored swatch icon for toolbar actions.

		Args:
			hex_color: Hex color string like "#EF4444".

		Returns:
			QIcon with a filled colored square.
		"""
		size = 12
		pixmap = QPixmap(size, size)
		pixmap.fill(QColor(hex_color))
		icon = QIcon(pixmap)
		return icon

	#============================================

	def _on_overlay_toggled(self, checked: bool) -> None:
		"""Handle overlay toggle action.

		Args:
			checked: Whether the overlay is now enabled.
		"""
		action = self.sender()
		if action is None:
			return
		key = action.data()
		if self._active_controller is not None:
			if hasattr(self._active_controller, "set_overlay_enabled"):
				self._active_controller.set_overlay_enabled(key, checked)

	#============================================

	def set_heat_status(self, text: str) -> None:
		"""Set the motion heat-map overlay status label text.

		Called by BaseAnnotationController when the HeatMapOverlay emits
		statusChanged. Empty string clears the label. Also drives the
		modal "Loading heatmap..." popup: the popup appears on exactly
		the STATUS_COMPUTING status and closes on every other status so
		the user cannot click into the scene while a compute is in
		flight.

		Args:
			text: Status string to display next to the heat toolbar
				action (e.g. "computing...", "ROI shown at frame 1247",
				"no prediction for this frame").
		"""
		if hasattr(self, "_heat_status_label") and self._heat_status_label is not None:
			self._heat_status_label.setText(text)
		# drive busy popup: exactly one status opens it; every other
		# status closes it. The drawing-pause path emits a non-computing
		# status so the popup is never shown over an active drag.
		is_busy = text == heat_map_overlay_module.STATUS_COMPUTING
		self._set_heat_busy(is_busy)

	#============================================

	def _set_heat_busy(self, busy: bool) -> None:
		"""Show or hide the heat-map loading popup and gate scene clicks.

		When `busy` is True, builds the popup on first use, shows it
		centered over the frame view, and disables the frame view so
		mouse events cannot reach the QGraphicsScene. When False,
		hides the popup and re-enables the view. Idempotent: safe to
		call repeatedly in the same state.

		Args:
			busy: True while a heat-map compute is in flight.
		"""
		if busy:
			# build on first use; keep one persistent instance so
			# repeated shows do not churn widget construction
			if self._heat_busy_dialog is None:
				self._heat_busy_dialog = self._build_heat_busy_dialog()
			# center the popup over the frame view before showing
			self._center_heat_busy_dialog()
			self._heat_busy_dialog.show()
			self._heat_busy_dialog.raise_()
			# disabling the view blocks mouse press / move / release into
			# the scene; keyboard shortcuts (H, arrows) still fire on the
			# window so the user can toggle off or navigate
			self._frame_view.setEnabled(False)
		else:
			if self._heat_busy_dialog is not None:
				self._heat_busy_dialog.hide()
			self._frame_view.setEnabled(True)

	#============================================

	def _build_heat_busy_dialog(self) -> QDialog:
		"""Build the "Loading heatmap..." popup dialog.

		Frameless, non-modal, styled to match the dark toolbar. Kept as
		a persistent child of the window so show/hide is cheap. Not
		modal in the Qt sense -- the view-disable path does the
		click-blocking work, while the dialog stays non-modal so the
		H shortcut and arrow-key navigation continue to fire on the
		parent window.

		Returns:
			A ready-to-show QDialog.
		"""
		dlg = QDialog(self)
		dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
		dlg.setAttribute(Qt.WA_StyledBackground, True)
		dlg.setStyleSheet(
			"QDialog { background-color: #1A1A2E; "
			"border: 2px solid #A21CAF; border-radius: 6px; }"
			"QLabel { color: #F8FAFC; }"
		)
		layout = QVBoxLayout(dlg)
		layout.setContentsMargins(24, 18, 24, 18)
		layout.setSpacing(4)
		title_label = QLabel("Loading heatmap...")
		title_font = title_label.font()
		title_font.setPointSize(14)
		title_font.setBold(True)
		title_label.setFont(title_font)
		title_label.setAlignment(Qt.AlignCenter)
		sub_label = QLabel("Please wait")
		sub_font = sub_label.font()
		sub_font.setPointSize(10)
		sub_label.setFont(sub_font)
		sub_label.setAlignment(Qt.AlignCenter)
		sub_label.setStyleSheet("QLabel { color: #94A3B8; }")
		layout.addWidget(title_label)
		layout.addWidget(sub_label)
		dlg.setLayout(layout)
		dlg.adjustSize()
		return dlg

	#============================================

	def _center_heat_busy_dialog(self) -> None:
		"""Center the busy popup over the frame view before show()."""
		if self._heat_busy_dialog is None:
			return
		view_rect = self._frame_view.geometry()
		# map the frame view's top-left to global coords so the dialog
		# can be placed in the correct screen region regardless of
		# window / toolbar layout
		top_left_global = self._frame_view.mapToGlobal(view_rect.topLeft())
		view_cx = top_left_global.x() + view_rect.width() // 2
		view_cy = top_left_global.y() + view_rect.height() // 2
		dlg_size = self._heat_busy_dialog.sizeHint()
		dlg_x = view_cx - dlg_size.width() // 2
		dlg_y = view_cy - dlg_size.height() // 2
		self._heat_busy_dialog.move(dlg_x, dlg_y)

	#============================================

	def set_progress(self, current: int, total: int) -> None:
		"""Update the progress bar with current/total values.

		Args:
			current: Current item number (1-based).
			total: Total number of items.
		"""
		self._progress_bar.setMaximum(total)
		self._progress_bar.setValue(current)
		# update chunk color to match current mode
		mode_color = self._mode_colors.get(self._current_mode, "#0D9488")
		self._progress_bar.setStyleSheet(
			"QProgressBar { max-height: 14px; border: none; "
			"background: #1A1A2E; border-radius: 2px; text-align: center; "
			"font-size: 10px; color: #F8FAFC; }"
			f"QProgressBar::chunk {{ border-radius: 2px; background: {mode_color}; }}"
		)

	#============================================

	def _cycle_filter(self) -> None:
		"""Advance to the next display filter preset and refresh the frame."""
		self._current_filter = frame_filters_module.get_next_preset(
			self._current_filter
		)
		self._filter_button.setText(f"Filter: {self._current_filter}")
		# re-apply filter to the current raw frame
		if self._raw_bgr is not None:
			filtered = frame_filters_module.apply_filter(
				self._raw_bgr, self._current_filter
			)
			self._frame_view.set_frame(filtered)

	#============================================

	def set_frame(self, bgr_array) -> None:
		"""Set the displayed frame.

		Stores the raw BGR array and applies the active display filter
		before forwarding to the frame view.

		Args:
			bgr_array: BGR numpy array for display.
		"""
		# keep raw reference for filter cycling
		self._raw_bgr = bgr_array
		filtered = frame_filters_module.apply_filter(
			bgr_array, self._current_filter
		)
		self._frame_view.set_frame(filtered)

	#============================================

	def set_hints(self, mode_label: str, hints: str, mode_color: str = "#FFFFFF") -> None:
		"""Update the persistent hint bar with current-mode shortcuts.

		Args:
			mode_label: Short mode name (e.g. "SEED", "EDIT").
			hints: Space-separated keybinding hints string.
			mode_color: Hex color for the mode label.
		"""
		self._last_hint_mode = mode_label
		self._last_hint_text = hints
		self._last_hint_color = mode_color
		html = (
			f"<span style='color: {mode_color}; font-weight: bold;'>"
			f"{mode_label}</span>"
			f"  <span style='color: #C0C0C0;'>{hints}</span>"
			f"  <span style='color: #707070;'>(F1 for help)</span>"
		)
		self._hint_bar.setText(html)

	#============================================

	def clear_hints(self) -> None:
		"""Clear the hint bar text (called on controller deactivation)."""
		self._last_hint_mode = ""
		self._last_hint_text = ""
		self._hint_bar.setText("")

	#============================================

	def _show_help_dialog(self) -> None:
		"""Pop up a dialog listing the current-mode shortcuts.

		Reads the last hints string set via set_hints() and renders
		each space-run as a separate row for readability.
		"""
		mode = self._last_hint_mode or "(no mode)"
		hints = self._last_hint_text or "(no shortcuts available)"
		# split hints into "KEY=action" tokens on whitespace runs
		tokens = [t for t in hints.split("  ") if t.strip()]
		rows = ""
		for token in tokens:
			if "=" in token:
				key_part, action_part = token.split("=", 1)
				rows += (
					"<tr>"
					f"<td style='color: #7DD3FC; padding-right: 14px; "
					f"font-weight: bold;'>{key_part.strip()}</td>"
					f"<td style='color: #E5E7EB;'>{action_part.strip()}</td>"
					"</tr>"
				)
			else:
				rows += (
					f"<tr><td colspan='2' style='color: #E5E7EB;'>"
					f"{token}</td></tr>"
				)
		html = (
			f"<h3 style='color: {self._last_hint_color};'>"
			f"{mode} mode shortcuts</h3>"
			f"<table cellspacing='2'>{rows}</table>"
			"<p style='color: #707070; font-size: 10px;'>"
			"See docs/TRACK_RUNNER_KEYBINDINGS.md for the full reference."
			"</p>"
		)
		dialog = QDialog(self)
		dialog.setWindowTitle("Keyboard shortcuts")
		dialog.resize(520, 420)
		browser = QTextBrowser(dialog)
		browser.setHtml(html)
		browser.setStyleSheet("QTextBrowser { background: #1A1A2E; }")
		layout = QVBoxLayout(dialog)
		layout.setContentsMargins(8, 8, 8, 8)
		layout.addWidget(browser)
		dialog.exec()

	#============================================

	def get_frame_view(self) -> FrameView:
		"""Get the frame view widget.

		Returns:
			The FrameView instance.
		"""
		return self._frame_view

	#============================================

	def closeEvent(self, event) -> None:
		"""Save window state on close.

		Args:
			event: Close event.
		"""
		settings = QSettings("emwy", "AnnotationWindow")
		settings.setValue("geometry", self.saveGeometry())
		super().closeEvent(event)

	#============================================

	def _on_zoom_in(self) -> None:
		"""Zoom in by 1.25x from the current zoom factor."""
		current = self._frame_view.get_zoom_factor()
		self._frame_view.set_zoom(current * 1.25)

	#============================================

	def _on_zoom_out(self) -> None:
		"""Zoom out by 1.25x from the current zoom factor."""
		current = self._frame_view.get_zoom_factor()
		self._frame_view.set_zoom(current / 1.25)

	#============================================

	def _on_zoom_slider(self, percent: int) -> None:
		"""Set zoom from slider value (percent).

		Args:
			percent: Zoom percentage from the slider (e.g. 150 for 1.5x).
		"""
		factor = percent / 100.0
		self._frame_view.set_zoom(factor)

	#============================================

	def run(self) -> None:
		"""Show window and start event loop.

		Convenience method for starting the application.
		"""
		self.show()
		app = QApplication.instance()
		if app is not None:
			app.exec()
