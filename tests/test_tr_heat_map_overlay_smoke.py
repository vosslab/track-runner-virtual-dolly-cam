"""Smoke tests for the sticky motion heat-map overlay.

Two invariants are locked here, one per test:

  1. Sticky-mode recompute on advance: showing heat for frame N, then
     requesting heat for frame N+1, must end with the overlay visible
     for N+1 (not stuck on N, not blank). Protects the core UX claim
     that the toggle is a persistent mode rather than a one-shot.
  2. Debounce: 10 rapid request_show calls collapse to exactly one
     compute dispatch. Protects the rapid-scrubbing path from queueing
     N sequential computes.

Other behaviors (null-case status, hide() flipping visibility, the
success-status message shape) are thin wrappers around the primitive
and do not warrant their own tests per docs/PYTHON_STYLE.md PYTEST
guidance.

Skipped entirely if Qt cannot initialize in the CI env.
"""

# Standard Library
# (none)

# PIP3 modules
import numpy
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QGraphicsScene  # noqa: E402

# local repo modules (bare imports resolved by conftest.py)
import camera_motion  # noqa: E402
import overlay_config  # noqa: E402
import scene_coords  # noqa: E402
import ui.heat_map_overlay as heat_map_overlay_module  # noqa: E402

HeatMapOverlay = heat_map_overlay_module.HeatMapOverlay
DEBOUNCE_MS = heat_map_overlay_module.DEBOUNCE_MS


#============================================
class _FakeReader:
	"""Minimal BGR frame reader used to exercise the residual compute."""

	def __init__(self, width: int, height: int, n_frames: int, gray: int):
		"""Create a fixed grayscale-flat video in memory."""
		self.width = int(width)
		self.height = int(height)
		self.frame_count = int(n_frames)
		self.fps = 30.0
		self._frame = numpy.full(
			(self.height, self.width, 3), int(gray), dtype=numpy.uint8,
		)

	#============================================
	def read_frame(self, frame_index: int):
		"""Return a BGR copy of the flat frame, or None if out of range."""
		if frame_index < 0 or frame_index >= self.frame_count:
			return None
		return self._frame.copy()


def _make_overlay(qtbot, predictions: dict) -> object:
	"""Build a real HeatMapOverlay wired to a QGraphicsScene.

	Args:
		qtbot: pytest-qt bot fixture (guarantees QApplication exists).
		predictions: Dict mapping frame_index -> prediction tuple.

	Returns:
		HeatMapOverlay instance attached to a fresh scene.
	"""
	_ = qtbot  # fixture presence is the important side effect
	n_frames = 40
	reader = _FakeReader(width=400, height=300, n_frames=n_frames, gray=60)
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	scene = QGraphicsScene()
	style = overlay_config.get_heat_map_style()
	overlay = HeatMapOverlay(
		reader=reader,
		scene_transform=transform,
		scene=scene,
		style=style,
		get_pred_fn=predictions.get,
		scene_transform_available=False,
	)
	return overlay


# debounce + small slack for the QTimer.singleShot to fire
_WAIT_MS = DEBOUNCE_MS + 100


#============================================
def test_sticky_mode_recomputes_on_frame_advance(qtbot):
	"""After showing heat for frame N and requesting frame N+1, heat is visible for N+1.

	Locks the sticky-mode contract: a second request_show does not leave
	the overlay blank; the debounce fires for the new frame and shows a
	fresh ROI. A regression that reverts to one-shot (auto-clear) would
	leave the pixmap hidden after the second request.
	"""
	predictions = {
		10: ((200.0, 150.0), (40.0, 60.0)),
		11: ((205.0, 150.0), (40.0, 60.0)),
	}
	overlay = _make_overlay(qtbot, predictions)

	overlay.request_show(10)
	qtbot.wait(_WAIT_MS)
	overlay.request_show(11)
	qtbot.wait(_WAIT_MS)

	assert overlay._pixmap_item.isVisible()


#============================================
def test_rapid_request_show_collapses_to_one_compute(qtbot):
	"""10 rapid request_show calls produce exactly one compute invocation."""
	predictions = {
		i: ((200.0, 150.0), (40.0, 60.0))
		for i in range(10, 21)
	}
	overlay = _make_overlay(qtbot, predictions)

	for i in range(10, 20):
		overlay.request_show(i)
	qtbot.wait(_WAIT_MS)

	assert overlay._compute_count == 1
