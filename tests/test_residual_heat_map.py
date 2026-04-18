"""Behavioral test for the residual-motion heat-map display facade.

The facade in track_runner/residual_heat_map.py wraps the solver's
residual-motion primitive and the 8x-torso ROI rule to produce an
ROI-scoped JET-colorized BGR image for the annotation GUI overlay.

One invariant is locked here: the facade must return an ROI-sized crop,
not a full-frame image. This is the ROI-scoped-compute design commitment
from docs/TRACK_RUNNER_DESIGN.md; if a future patch forgets to pass roi=
to compute_residual_for_frame, GUI compute will silently balloon to
full-frame and lag on 2.8k footage. Asserting output shape bounds catches
that regression.

Library behavior (OpenCV JET, numpy threshold) is deliberately not tested
here -- those are not our code.
"""

# Standard Library
# (none)

# PIP3 modules
import numpy

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import overlay_config
import residual_heat_map
import scene_coords


#============================================
class _FakeReader:
	"""In-memory video reader stub returning pre-built BGR frames."""

	def __init__(self, frames: list):
		"""Store a list of BGR frames and expose VideoReader-compatible attrs."""
		h, w = frames[0].shape[:2]
		self.width = int(w)
		self.height = int(h)
		self.frame_count = len(frames)
		self.fps = 30.0
		self._frames = frames

	#============================================
	def read_frame(self, frame_index: int):
		"""Return the BGR frame at the given index, or None if out of range."""
		if frame_index < 0 or frame_index >= self.frame_count:
			return None
		return self._frames[frame_index].copy()


#============================================
def test_facade_output_is_roi_scoped_not_full_frame():
	"""Facade output must be an ROI crop, not a full-frame image.

	This locks the ROI-scoped-compute commitment from
	docs/TRACK_RUNNER_DESIGN.md. On a 640x360 frame with a 60 px torso,
	the 8x-torso ROI side is about 480 px, so the facade's BGR shape
	must be strictly smaller than the frame in BOTH dimensions. If a
	future change drops roi= from the primitive call, output shape
	matches frame shape and this test fails loudly.
	"""
	style = overlay_config.get_heat_map_style()
	n_frames = 11
	# frame sized so an 8x-torso ROI stays strictly inside it; a
	# regression that drops roi= and returns the whole frame would
	# produce bgr.shape matching (height, width).
	width, height = 1920, 1080
	pred_h = 60.0
	frame = numpy.full((height, width, 3), 120, dtype=numpy.uint8)
	reader = _FakeReader([frame.copy() for _ in range(n_frames)])
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
		event_flags=numpy.zeros(n_frames, dtype=numpy.int32),
	)
	transform = scene_coords.SceneTransform(motion)

	result = residual_heat_map.compute_heat_map_roi(
		reader, frame_index=5, scene_transform=transform,
		pred_center=(960.0, 540.0), pred_box=(40.0, pred_h),
		half_window=style["half_window"],
		threshold=style["threshold"],
		fixed_max=style["fixed_max"],
	)

	assert result is not None
	bgr, _origin = result
	# ROI crop must be strictly smaller than the full frame on both axes
	assert bgr.shape[0] < height and bgr.shape[1] < width
