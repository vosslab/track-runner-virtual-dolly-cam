"""Tests for seed controller navigation behavior."""

# PIP3 modules
import PySide6.QtCore
import pytest

# local repo modules (bare imports resolved by conftest.py)
import common_tools.coord_space
import seed_editor
import ui.seed_controller

SeedController = ui.seed_controller.SeedController


#============================================
class _DummyReader:
	"""Minimal asynchronous frame source stub for controller tests."""

	def __init__(self) -> None:
		"""Expose the queued-result connection surface used by controllers."""
		self.frame_ready = _DummySignal()

	def request_frame(self, frame_index: int) -> int:
		"""Record a non-blocking request without decoding on the test thread."""
		_ = frame_index
		return 1


#============================================
class _DummySignal:
	"""Small signal double retaining the one connected slot."""

	def connect(self, slot: object) -> None:
		"""Accept the controller's queued-result subscription."""
		self.slot = slot

	#============================================

	def disconnect(self, slot: object) -> None:
		"""Remove the deactivated controller subscription."""
		if self.slot == slot:
			self.slot = None


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

	controller.handle_key_press(PySide6.QtCore.Qt.Key.Key_Left)
	assert controller._current_frame == 196

	controller.handle_key_press(PySide6.QtCore.Qt.Key.Key_Right)
	assert controller._current_frame == 200


#============================================
def test_deactivated_seed_controller_disconnects_late_frame_results() -> None:
	"""A late decode signal cannot reach a controller after deactivation."""
	controller = _make_controller()
	controller._on_deactivated()

	assert controller._reader.frame_ready.slot is None


#============================================
def test_duplicate_seed_rejection_uses_annotation_feedback() -> None:
	"""A rejected duplicate reports its reason through the GUI feedback path."""
	controller = _make_controller()
	controller._all_seeds = [{"frame_index": controller._current_frame}]
	feedback = []
	controller._show_seed_feedback = feedback.append

	controller._on_box_drawn([10, 20, 30, 40])

	assert "already exists" in feedback[0]


#============================================
def test_fwd_bwd_auto_seed_rejects_processed_prediction_box() -> None:
	"""F-key cannot commit a prediction from the wrong coordinate space."""
	controller = _make_controller()
	processed_box = common_tools.coord_space.ProcessedBox(10.0, 20.0, 30.0, 40.0)
	controller._predictions = {
		controller._current_frame: {
			"forward": processed_box,
			"backward": processed_box,
		}
	}

	with pytest.raises(ValueError, match="expected SourceBox"):
		controller._on_fwd_bwd_avg()


#============================================
def test_edit_consensus_rejects_processed_prediction_box() -> None:
	"""Edit refinement cannot translate a processed box into a source seed."""
	processed_box = common_tools.coord_space.ProcessedBox(10.0, 20.0, 30.0, 40.0)
	predictions = {100: {"forward": processed_box}}
	seed = {"cx": 20.0, "cy": 30.0, "w": 40.0, "h": 50.0}

	with pytest.raises(ValueError, match="expected SourceBox"):
		seed_editor._refine_box_consensus(seed, predictions, 100)


#============================================
def test_quit_routes_seed_coverage_summary_to_annotation_feedback() -> None:
	"""Quit keeps actionable coverage results visible in the annotation UI."""
	controller = _make_controller()
	controller._return_callback = lambda seeds: None
	feedback = []
	controller._show_seed_feedback = feedback.append

	controller._on_quit()

	assert "Seed stats:" in feedback[0]
	assert "need at least 2 usable seeds" in feedback[0]
