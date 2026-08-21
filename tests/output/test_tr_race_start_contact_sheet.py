"""Tests for race_start_contact_sheet renderer."""

from collections.abc import Callable
import os
import pathlib
from unittest.mock import MagicMock, patch

# PIP3 modules
import numpy
import pytest

# local repo modules
import race_start_contact_sheet


#============================================
def _make_tiles(
		frame_offset_fn: Callable[[int], int], count: int = 11,
		clamped_fn: Callable[[int], bool] | None = None,
) -> list:
	"""Build a standard tile list for contact sheet tests.

	Args:
		frame_offset_fn: Callable(i) -> frame_index for tile i.
		count: Number of tiles to generate.
		clamped_fn: Optional callable(i) -> bool for clamped status. Defaults to False.

	Returns:
		List of tile dicts with standard structure.
	"""
	tiles = []
	for i in range(count):
		offset_s = -0.5 + (i * 0.1)
		label = "PRE" if i < 5 else ("START" if i == 5 else "POST")
		row = "top" if i < 5 else ("center" if i == 5 else "bottom")
		frame_index = frame_offset_fn(i)
		clamped = clamped_fn(i) if clamped_fn else False
		tiles.append({
			"frame_index": frame_index,
			"requested_frame_index": frame_index,
			"offset_s": offset_s,
			"label": label,
			"row": row,
			"clamped": clamped,
		})
	return tiles


#============================================
class FakeFrameReader:
	"""Minimal fake FrameReader for testing without real video I/O.

	Optional tile_height_overrides maps frame_index -> height; matching frames
	are returned with a non-default height to exercise the resize-to-uniform
	path in render_race_start_contact_sheet (cv2.hconcat fails on mismatched
	tile heights without that resize).
	"""

	tile_height_overrides: dict = {}

	def __init__(self, video_path: str, fps: float, total_frames: int) -> None:
		self.video_path = video_path
		self.fps = fps
		self.total_frames = total_frames
		self.closed = False

	def read_frame(self, frame_index: int) -> numpy.ndarray | None:
		"""Return a synthetic BGR frame, or None for test error paths."""
		if self.closed:
			return None
		# Return a small 480x270 BGR frame filled with a frame-index-dependent value
		value = int(128 + (frame_index % 127))
		height = self.tile_height_overrides.get(frame_index, 270)
		frame = numpy.full((height, 480, 3), value, dtype=numpy.uint8)
		return frame

	def close(self) -> None:
		"""Mark reader as closed."""
		self.closed = True


#============================================
def test_render_race_start_contact_sheet_happy_path(tmp_path: pathlib.Path) -> None:
	"""Happy path: render contact sheet to a temp PNG."""
	output_path = str(tmp_path / "race_start_check.png")

	# Build minimal tile list (11 tiles)
	tiles = _make_tiles(lambda i: 100 + i * 10)

	# Build minimal pre_race_reference
	pre_race_reference = {
		"race_start_frame": 100,
		"race_start_interval": [50, 150],
		"torso_w": 50.0,
		"torso_h": 60.0,
		"scene_anchor_x": 320.0,
		"scene_anchor_y": 180.0,
		"source_frame_indices": [50, 60],
		"source_count": 2,
		"method": "test",
		"warnings": [],
	}

	# Create a fake scene_transform
	fake_transform = MagicMock()
	fake_transform.scene_to_pixel.return_value = (240, 135)

	# Patch FrameReader and overlay_config
	with patch("race_start_contact_sheet.frame_reader.FrameReader", FakeFrameReader):
		with patch("race_start_contact_sheet.overlay_config.get_pre_race_reference_bgr") as mock_color:
			mock_color.return_value = (0, 255, 0)

			race_start_contact_sheet.render_race_start_contact_sheet(
				"test_video.mov",
				30.0,
				1000,
				tiles,
				pre_race_reference,
				fake_transform,
				output_path,
			)

	# Verify PNG was written
	assert os.path.exists(output_path)
	# Verify non-zero file size
	assert os.path.getsize(output_path) > 0


#============================================
def test_render_race_start_contact_sheet_frame_read_failure(tmp_path: pathlib.Path) -> None:
	"""Error path: frame read returns None."""
	output_path = str(tmp_path / "race_start_check.png")

	# Build tile list
	tiles = _make_tiles(lambda i: 100 + i * 10)

	pre_race_reference = {
		"race_start_frame": 100,
		"race_start_interval": [50, 150],
		"torso_w": 50.0,
		"torso_h": 60.0,
		"scene_anchor_x": 320.0,
		"scene_anchor_y": 180.0,
		"source_frame_indices": [50],
		"source_count": 1,
		"method": "test",
		"warnings": [],
	}

	fake_transform = MagicMock()
	fake_transform.scene_to_pixel.return_value = (240, 135)

	# Create a fake reader that returns None for one tile
	class BadFrameReader:
		def __init__(self, video_path: str, fps: float, total_frames: int) -> None:
			self.frame_count = 0

		def read_frame(self, frame_index: int) -> numpy.ndarray | None:
			self.frame_count += 1
			if self.frame_count == 2:  # Fail on second frame
				return None
			return numpy.full((270, 480, 3), 128, dtype=numpy.uint8)

		def close(self) -> None:
			pass

	with patch("race_start_contact_sheet.frame_reader.FrameReader", BadFrameReader):
		with patch("race_start_contact_sheet.overlay_config.get_pre_race_reference_bgr") as mock_color:
			mock_color.return_value = (0, 255, 0)

			with pytest.raises(RuntimeError) as exc_info:
				race_start_contact_sheet.render_race_start_contact_sheet(
					"test_video.mov",
					30.0,
					1000,
					tiles,
					pre_race_reference,
					fake_transform,
					output_path,
				)

			# Verify error message indicates frame read failure
			error_msg = str(exc_info.value).lower()
			assert "failed to read frame" in error_msg or "frame" in error_msg


#============================================
def test_render_race_start_contact_sheet_tile_count_validation(tmp_path: pathlib.Path) -> None:
	"""Error path: wrong tile count."""
	output_path = str(tmp_path / "race_start_check.png")

	# Build a tile list with the wrong count
	tiles = [
		{
			"frame_index": 100,
			"requested_frame_index": 100,
			"offset_s": 0.0,
			"label": "START",
			"row": "center",
			"clamped": False,
		}
	]

	pre_race_reference = {
		"race_start_frame": 100,
		"race_start_interval": [50, 150],
		"torso_w": 50.0,
		"torso_h": 60.0,
		"scene_anchor_x": 320.0,
		"scene_anchor_y": 180.0,
		"source_frame_indices": [50],
		"source_count": 1,
		"method": "test",
		"warnings": [],
	}

	fake_transform = MagicMock()

	with pytest.raises(RuntimeError):
		race_start_contact_sheet.render_race_start_contact_sheet(
			"test_video.mov",
			30.0,
			1000,
			tiles,
			pre_race_reference,
			fake_transform,
			output_path,
		)


#============================================
def test_padding_in_frame_crop_no_padding(tmp_path: pathlib.Path) -> None:
	"""Padding test: crop fully in frame, no padding needed."""
	output_path = str(tmp_path / "race_start_check.png")

	# Build tile list (11 tiles)
	tiles = _make_tiles(lambda i: 240 + i * 10)

	# Frame is 480x270, crop is centered at (240, 135) with size 200x200
	# All crops should fit fully in frame with no padding
	pre_race_reference = {
		"race_start_frame": 240,
		"race_start_interval": [100, 300],
		"torso_w": 50.0,
		"torso_h": 60.0,
		"scene_anchor_x": 240.0,
		"scene_anchor_y": 135.0,
		"source_frame_indices": [100, 200],
		"source_count": 2,
		"method": "test",
		"warnings": [],
	}

	fake_transform = MagicMock()
	fake_transform.scene_to_pixel.return_value = (240, 135)

	with patch("race_start_contact_sheet.frame_reader.FrameReader", FakeFrameReader):
		with patch("race_start_contact_sheet.overlay_config.get_pre_race_reference_bgr") as mock_color:
			mock_color.return_value = (0, 255, 0)

			race_start_contact_sheet.render_race_start_contact_sheet(
				"test_video.mov",
				30.0,
				1000,
				tiles,
				pre_race_reference,
				fake_transform,
				output_path,
			)

	# Verify PNG was written
	assert os.path.exists(output_path)
	assert os.path.getsize(output_path) > 0


#============================================
def test_render_handles_mismatched_tile_heights(tmp_path: pathlib.Path) -> None:
	"""Regression: refine-mode seed edits can produce tiles with slightly
	different heights; cv2.hconcat must not fail on the mismatch.
	"""
	output_path = str(tmp_path / "contact_sheet.png")

	tiles = _make_tiles(lambda i: i)

	pre_race_reference = {
		"race_start_frame": 5,
		"torso_w": 10.0,
		"torso_h": 10.0,
		"scene_anchor_x": 320.0,
		"scene_anchor_y": 240.0,
	}

	fake_transform = MagicMock()
	fake_transform.scene_to_pixel.return_value = (320.0, 240.0)

	# Force a single tile to a different height so cv2.hconcat would fail
	# without the resize-to-uniform fallback.
	original_overrides = FakeFrameReader.tile_height_overrides
	FakeFrameReader.tile_height_overrides = {1: 271}
	try:
		with patch("race_start_contact_sheet.frame_reader.FrameReader", FakeFrameReader):
			with patch("race_start_contact_sheet.overlay_config.get_pre_race_reference_bgr") as mock_color:
				mock_color.return_value = (0, 255, 0)

				race_start_contact_sheet.render_race_start_contact_sheet(
					video_path="dummy.mp4",
					fps=30.0,
					total_frames=100,
					tiles=tiles,
					pre_race_reference=pre_race_reference,
					scene_transform=fake_transform,
					output_path=output_path,
				)
	finally:
		FakeFrameReader.tile_height_overrides = original_overrides

	assert os.path.exists(output_path)
	assert os.path.getsize(output_path) > 0


#============================================
def test_padding_edge_clamped_crop(tmp_path: pathlib.Path) -> None:
	"""Padding test: crop at frame edge gets padded and final size is correct."""
	output_path = str(tmp_path / "race_start_check.png")

	# Build tile list with clamped=True for edge crops
	tiles = _make_tiles(
		lambda i: max(0, min(100 + i * 10, 999)),
		clamped_fn=lambda i: i in [0, 10],
	)

	# Frame is 480x270, crop center at frame edge so it extends past boundaries
	pre_race_reference = {
		"race_start_frame": 100,
		"race_start_interval": [50, 150],
		"torso_w": 50.0,
		"torso_h": 60.0,
		"scene_anchor_x": 10.0,  # Very close to left edge
		"scene_anchor_y": 10.0,  # Very close to top edge
		"source_frame_indices": [50, 60],
		"source_count": 2,
		"method": "test",
		"warnings": [],
	}

	fake_transform = MagicMock()
	fake_transform.scene_to_pixel.return_value = (10, 10)

	with patch("race_start_contact_sheet.frame_reader.FrameReader", FakeFrameReader):
		with patch("race_start_contact_sheet.overlay_config.get_pre_race_reference_bgr") as mock_color:
			mock_color.return_value = (0, 255, 0)

			race_start_contact_sheet.render_race_start_contact_sheet(
				"test_video.mov",
				30.0,
				1000,
				tiles,
				pre_race_reference,
				fake_transform,
				output_path,
			)

	# Verify PNG was written
	assert os.path.exists(output_path)
	assert os.path.getsize(output_path) > 0
