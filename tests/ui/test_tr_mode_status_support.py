"""Tests for controller-independent mode-status presentation helpers."""

# local repo modules
import ui.mode_status_support as mode_status_support_module


#============================================


class _Button:
	"""Checked toolbar-button double."""

	def __init__(self) -> None:
		"""Create an unchecked button."""
		self.checked = False

	#============================================

	def setChecked(self, checked: bool) -> None:
		"""Record the checked state provided by the helper."""
		self.checked = checked


#============================================


class _StatusBar:
	"""Status-bar double that records styles and messages."""

	def __init__(self) -> None:
		"""Create an empty status display."""
		self.style_sheet = "unset"
		self.message = ""

	#============================================

	def setStyleSheet(self, style_sheet: str) -> None:
		"""Record the assigned style sheet."""
		self.style_sheet = style_sheet

	#============================================

	def showMessage(self, text: str) -> None:
		"""Record the visible status text."""
		self.message = text


#============================================


class _Window:
	"""Window double exposing a persistent status bar."""

	def __init__(self) -> None:
		"""Create a window with an observable status bar."""
		self.status_bar = _StatusBar()

	#============================================

	def statusBar(self) -> _StatusBar:
		"""Return the status bar used by the helper."""
		return self.status_bar


#============================================


def test_sync_toolbar_buttons_tracks_explicit_mode_state() -> None:
	"""The leaf helper assigns each toolbar button independently."""
	partial_button = _Button()
	approximate_button = _Button()

	mode_status_support_module.sync_toolbar_buttons(
		partial_button, approximate_button, True, False,
	)

	assert partial_button.checked is True
	assert approximate_button.checked is False


#============================================


def test_update_mode_badge_preserves_approximate_presentation() -> None:
	"""Approximate mode keeps its exact status text and badge appearance."""
	window = _Window()
	status_texts: list[str] = []
	restored: list[bool] = []

	mode_status_support_module.update_mode_badge(
		window, True, True, status_texts.append,
		lambda: restored.append(True),
	)

	assert window.status_bar.style_sheet == (
		"background-color: #F97316; color: #000000; font-weight: bold;"
	)
	assert status_texts == ["** APPROX MODE ** draw approximate box (press 'a' to cancel)"]
	assert restored == []


#============================================


def test_update_mode_badge_preserves_partial_and_normal_transitions() -> None:
	"""Partial mode and normal mode retain their established callbacks."""
	window = _Window()
	status_texts: list[str] = []
	restored: list[bool] = []

	mode_status_support_module.update_mode_badge(
		window, True, False, status_texts.append,
		lambda: restored.append(True),
	)

	assert window.status_bar.style_sheet == (
		"background-color: #F59E0B; color: #000000; font-weight: bold;"
	)
	assert status_texts == ["** PARTIAL MODE ** draw visible torso (press 'p' to cancel)"]
	assert restored == []

	mode_status_support_module.update_mode_badge(
		window, False, False, status_texts.append,
		lambda: restored.append(True),
	)

	assert window.status_bar.style_sheet == ""
	assert restored == [True]
