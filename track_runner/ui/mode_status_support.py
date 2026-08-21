"""Mode-toolbar and draw-status presentation helpers.

This leaf module updates controls and status-bar presentation from explicit
window and mode state. Controllers retain their public compatibility methods
and subclass override seams.
"""

import collections.abc

# local repo modules
import overlay_config


#============================================


def sync_toolbar_buttons(
	partial_button: object | None,
	approximate_button: object | None,
	partial_mode: bool,
	approximate_mode: bool,
) -> None:
	"""Sync draw-mode toolbar buttons with their owning state.

	Args:
		partial_button: Optional toolbar button for partial-torso drawing.
		approximate_button: Optional toolbar button for approximate drawing.
		partial_mode: Whether partial-torso drawing is active.
		approximate_mode: Whether approximate drawing is active.
	"""
	if partial_button is not None:
		partial_button.setChecked(partial_mode)
	if approximate_button is not None:
		approximate_button.setChecked(approximate_mode)


#============================================


def update_mode_badge(
	window: object | None,
	partial_mode: bool,
	approximate_mode: bool,
	set_status_text: collections.abc.Callable[[str], None],
	restore_default_status: collections.abc.Callable[[], None],
) -> None:
	"""Apply draw-mode status-bar styling and text.

	Args:
		window: Active annotation window, or None before activation.
		partial_mode: Whether partial-torso drawing is active.
		approximate_mode: Whether approximate drawing is active.
		set_status_text: Controller-owned status text callback.
		restore_default_status: Controller-owned normal-status callback.
	"""
	if window is None:
		return
	if approximate_mode:
		approx_color = overlay_config.get_draw_mode_badge_color("approximate")
		window.statusBar().setStyleSheet(
			f"background-color: {approx_color}; color: #000000; font-weight: bold;"
		)
		set_status_text(
			"** APPROX MODE ** draw approximate box (press 'a' to cancel)"
		)
	elif partial_mode:
		partial_color = overlay_config.get_draw_mode_badge_color("partial")
		window.statusBar().setStyleSheet(
			f"background-color: {partial_color}; color: #000000; font-weight: bold;"
		)
		set_status_text(
			"** PARTIAL MODE ** draw visible torso (press 'p' to cancel)"
		)
	else:
		window.statusBar().setStyleSheet("")
		restore_default_status()


#============================================


def set_status_text(window: object, text: str) -> None:
	"""Display controller status text in the annotation window.

	Args:
		window: Active annotation window.
		text: Message to display.
	"""
	window.statusBar().showMessage(text)
