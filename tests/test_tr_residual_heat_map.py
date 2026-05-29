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

Tests for compute_heat_map_roi and compute_heat_map_overlay_roi include
WP-1C coverage: byte-identity hash for the main function, and shape+alpha
validation for the sibling overlay function.

Library behavior (OpenCV JET, numpy threshold) is deliberately not tested
here -- those are not our code.
"""

# Standard Library
import hashlib

# PIP3 modules
import numpy
import pytest

# local repo modules (bare imports resolved by conftest.py)
import camera_motion
import overlay_config
import residual_heat_map
import residual_motion
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
	)
	transform = scene_coords.SceneTransform(motion)

	result = residual_heat_map.compute_heat_map_roi(
		reader, frame_index=5, scene_transform=transform,
		pred_center=(960.0, 540.0), pred_box=(40.0, pred_h),
		fps=reader.fps,
		threshold=style["threshold"],
		fixed_max=style["fixed_max"],
	)

	assert result is not None
	bgr, _origin = result
	# ROI crop must be strictly smaller than the full frame on both axes
	assert bgr.shape[0] < height and bgr.shape[1] < width


#============================================
def test_compute_roi_off_frame_prediction_never_inverts_bounds():
	"""_compute_roi must never return x1 > x2 or y1 > y2 even when the
	prediction is off-frame. Zero-area ROIs (x1 == x2 on a clamped
	edge) are acceptable and handled downstream; inverted bounds
	(x1 > x2) produced the broadcast error in the prior behavior."""
	# prediction far to the right of the 1280-wide frame
	x1, y1, x2, y2 = residual_motion._compute_roi(
		pred_cx=5000.0, pred_cy=360.0, pred_h=100.0,
		frame_w=1280, frame_h=720,
	)
	assert x2 >= x1
	assert y2 >= y1


#============================================
def test_compute_residual_bails_on_degenerate_roi():
	"""compute_residual_for_frame raises ValueError for a zero-area
	ROI (off-frame prediction / caller bug) instead of returning None
	or mis-broadcasting the full-frame median against an empty slice."""
	width, height = 1280, 720
	frame = numpy.full((height, width, 3), 120, dtype=numpy.uint8)
	reader = _FakeReader([frame.copy() for _ in range(5)])
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(5, dtype=numpy.float32),
		dy=numpy.zeros(5, dtype=numpy.float32),
		scale=numpy.ones(5, dtype=numpy.float32),
		quality=numpy.ones(5, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)
	# degenerate ROI: x1 == x2 produces zero width
	degenerate_roi = (500, 100, 500, 500)
	with pytest.raises(ValueError, match="degenerate ROI"):
		residual_motion.compute_residual_for_frame(
			reader, frame_index=2, scene_transform=transform,
			roi=degenerate_roi,
		)


#============================================
def test_compute_heat_map_overlay_roi_returns_bgra_with_proper_alpha():
	"""New overlay function returns BGRA with correct alpha channel.

	Alpha = 0 below threshold (fully transparent).
	Alpha = int(blend_alpha * 255) above threshold.
	Output shape is (H, W, 4) instead of (H, W, 3) like the original.
	"""
	style = overlay_config.get_heat_map_style()
	n_frames = 11
	width, height = 1920, 1080
	pred_h = 60.0
	blend_alpha = 0.40
	frame = numpy.full((height, width, 3), 120, dtype=numpy.uint8)
	reader = _FakeReader([frame.copy() for _ in range(n_frames)])
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	result = residual_heat_map.compute_heat_map_overlay_roi(
		reader, frame_index=5, scene_transform=transform,
		pred_center=(960.0, 540.0), pred_box=(40.0, pred_h),
		fps=reader.fps,
		threshold=style["threshold"],
		fixed_max=style["fixed_max"],
		blend_alpha=blend_alpha,
	)

	assert result is not None
	bgra, origin = result
	# Output must be BGRA (4 channels), not BGR (3 channels)
	assert len(bgra.shape) == 3
	assert bgra.shape[2] == 4
	assert bgra.dtype == numpy.uint8
	# Alpha channel values must be either 0 or int(blend_alpha * 255)
	expected_alpha = int(blend_alpha * 255)
	unique_alphas = numpy.unique(bgra[:, :, 3])
	for alpha_val in unique_alphas:
		assert alpha_val == 0 or alpha_val == expected_alpha, \
			f"Unexpected alpha value {alpha_val} (expected 0 or {expected_alpha})"


#============================================
def test_compute_heat_map_roi_byte_identity():
	"""WP-1C: lock compute_heat_map_roi output byte-identity via SHA256 hash.

	This test captures the exact behavior of compute_heat_map_roi on a
	synthetic test case. The hash must remain stable as long as the
	underlying computation (residual, DoG, JET colorization, composition)
	remains unchanged. A hash mismatch indicates an unintended change
	to the pipeline (WP-1C refactor or other modification).

	The hash was computed from a known good run and is encoded here.
	If the hash changes due to a legitimate code improvement, recompute
	it by running this test, capturing the failure output, extracting
	the new hash, and updating the expected value.
	"""
	style = overlay_config.get_heat_map_style()
	n_frames = 11
	width, height = 320, 240
	pred_h = 60.0
	frame = numpy.full((height, width, 3), 120, dtype=numpy.uint8)
	reader = _FakeReader([frame.copy() for _ in range(n_frames)])
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	transform = scene_coords.SceneTransform(motion)

	result = residual_heat_map.compute_heat_map_roi(
		reader, frame_index=5, scene_transform=transform,
		pred_center=(160.0, 120.0), pred_box=(40.0, pred_h),
		fps=reader.fps,
		threshold=style["threshold"],
		fixed_max=style["fixed_max"],
		blend_alpha=0.40,
	)

	assert result is not None
	bgr, origin = result
	# Compute SHA256 hash of the output bytes
	bgr_bytes = bgr.tobytes()
	hash_obj = hashlib.sha256(bgr_bytes)
	actual_hash = hash_obj.hexdigest()
	# Expected hash from a known good run (WP-1C baseline)
	expected_hash = "26167e8f76e8e0347a0dde2b6156ee0ae696bd167a56f1ba13d678de30fd424f"
	assert actual_hash == expected_hash, (
		f"compute_heat_map_roi output hash changed. "
		f"Expected: {expected_hash}, got: {actual_hash}. "
		f"If this is intentional, update the expected hash in the test."
	)
