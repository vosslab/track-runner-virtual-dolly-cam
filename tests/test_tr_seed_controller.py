"""Tests for seed controller navigation behavior."""

# PIP3 modules
from PySide6.QtCore import Qt

# local repo modules (bare imports resolved by conftest.py)
import ui.seed_controller as seed_controller_module

SeedController = seed_controller_module.SeedController


#============================================
class _DummyReader:
	"""Minimal frame reader stub for controller tests."""

	def read_frame(self, frame_index: int):
		_ = frame_index
		return None


#============================================
class _DummyFrameView:
	"""Minimal frame view stub exposing fit-zoom state."""

	def __init__(self, fit_zoom: bool = True) -> None:
		self._fit_zoom = fit_zoom

	def is_fit_zoom(self) -> bool:
		return self._fit_zoom


#============================================
class _DummyWindow:
	"""Minimal window stub for key handling."""

	def __init__(self, fit_zoom: bool = True) -> None:
		self._frame_view = _DummyFrameView(fit_zoom)

	def get_frame_view(self) -> _DummyFrameView:
		return self._frame_view


#============================================
def _make_controller() -> SeedController:
	"""Create a seed controller with minimal test dependencies."""
	controller = SeedController(
		seed_frame_indices=[100, 200, 300],
		reader=_DummyReader(),
		fps=60.0,
		config={},
		all_seeds=[],
		save_callback=None,
	)
	controller._window = _DummyWindow(fit_zoom=True)
	controller._refresh_frame = lambda: None
	return controller


#============================================
def test_prev_next_buttons_advance_one_step() -> None:
	"""Toolbar nav buttons should move by one scrub step."""
	controller = _make_controller()
	controller._current_frame = 200
	controller._scrub_step_frames = 3

	controller._on_prev_button(False)
	assert controller._current_frame == 197

	controller._on_next_button(False)
	assert controller._current_frame == 200


#============================================
def test_plain_arrow_keys_scrub_at_fit_zoom() -> None:
	"""Plain left/right arrows scrub when pan is unavailable."""
	controller = _make_controller()
	controller._current_frame = 200
	controller._scrub_step_frames = 4

	controller.handle_key_press(Qt.Key.Key_Left)
	assert controller._current_frame == 196

	controller.handle_key_press(Qt.Key.Key_Right)
	assert controller._current_frame == 200
