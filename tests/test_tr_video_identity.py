"""Tests for track_runner.tr_video_identity module.

Tests the video_identity comparison logic, including classification of
mismatches into blocking vs. informational categories per C12.3.
"""

# PIP3 modules
import pytest

# local repo modules
import tr_video_identity


#============================================
def test_compare_returns_blocking_and_informational_keys():
	"""Verify compare_video_identity returns dict with correct keys."""
	stored = {
		"basename": "video.MOV",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.MOV",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert isinstance(result, dict)
	assert "blocking" in result
	assert "informational" in result
	assert isinstance(result["blocking"], list)
	assert isinstance(result["informational"], list)
	assert result["blocking"] == []
	assert result["informational"] == []


#============================================
def test_rename_only_is_informational():
	"""Verify file rename alone produces only informational mismatch."""
	stored = {
		"basename": "foo.MOV",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "foo.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == []
	assert len(result["informational"]) == 1
	assert "basename" in result["informational"][0]


#============================================
def test_size_bytes_change_only_is_informational():
	"""Verify file size change alone produces only informational mismatch."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 999999,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == []
	assert len(result["informational"]) == 1
	assert "size_bytes" in result["informational"][0]


#============================================
def test_frame_count_mismatch_is_blocking():
	"""Verify frame_count mismatch is blocking."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 999,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["informational"] == []
	assert len(result["blocking"]) == 1
	assert "frame_count" in result["blocking"][0]


#============================================
def test_width_mismatch_is_blocking():
	"""Verify width mismatch is blocking."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 3840,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["informational"] == []
	assert len(result["blocking"]) == 1
	assert "width" in result["blocking"][0]


#============================================
def test_height_mismatch_is_blocking():
	"""Verify height mismatch is blocking."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 2160,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["informational"] == []
	assert len(result["blocking"]) == 1
	assert "height" in result["blocking"][0]


#============================================
def test_fps_within_tolerance_is_clean():
	"""Verify fps within 0.01 tolerance produces no mismatch."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 29.97,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 29.971,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == []
	assert result["informational"] == []


#============================================
def test_fps_beyond_tolerance_is_blocking():
	"""Verify fps beyond 0.01 tolerance is blocking."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 60.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["informational"] == []
	assert len(result["blocking"]) == 1
	assert "fps" in result["blocking"][0]


#============================================
def test_duration_within_tolerance_is_clean():
	"""Verify duration within 0.5s tolerance produces no mismatch."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.3,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == []
	assert result["informational"] == []


#============================================
def test_duration_beyond_tolerance_is_informational():
	"""Verify duration beyond 0.5s tolerance is informational."""
	stored = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 33.0,
	}
	current = {
		"basename": "video.mkv",
		"size_bytes": 1000000,
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 34.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == []
	assert len(result["informational"]) == 1
	assert "duration_s" in result["informational"][0]


#============================================
def test_summarize_mismatches_lists_blocking_first():
	"""Verify summarize_mismatches lists blocking entries before informational."""
	result = {
		"blocking": ["frame_count: stored=1000, current=999"],
		"informational": ["basename: stored=foo.MOV, current=foo.mkv"],
	}
	summary = tr_video_identity.summarize_mismatches(result)
	blocking_idx = summary.find("blocking:")
	informational_idx = summary.find("informational")
	assert blocking_idx >= 0
	assert informational_idx >= 0
	assert blocking_idx < informational_idx


#============================================
def test_summarize_mismatches_empty_result():
	"""Verify summarize_mismatches returns empty string for no mismatches."""
	result = {
		"blocking": [],
		"informational": [],
	}
	summary = tr_video_identity.summarize_mismatches(result)
	assert summary == ""


#============================================
def test_summarize_mismatches_blocking_only():
	"""Verify summarize_mismatches handles blocking entries only."""
	result = {
		"blocking": ["frame_count: stored=1000, current=999"],
		"informational": [],
	}
	summary = tr_video_identity.summarize_mismatches(result)
	assert "blocking:" in summary
	assert "informational" not in summary
	assert "frame_count" in summary


#============================================
def test_summarize_mismatches_informational_only():
	"""Verify summarize_mismatches handles informational entries only."""
	result = {
		"blocking": [],
		"informational": ["basename: stored=foo.MOV, current=foo.mkv"],
	}
	summary = tr_video_identity.summarize_mismatches(result)
	assert "informational" in summary
	assert "blocking:" not in summary
	assert "basename" in summary


#============================================
def test_missing_fields_raise_key_error():
	"""Verify missing required fields fail loudly per repo style.

	Per docs/PYTHON_STYLE.md "DO NOT HIDE BUGS WITH DEFAULTS",
	video_identity dicts always carry every field declared by the
	rule tables; a missing key indicates a tampered or buggy producer
	and must surface as KeyError, not be silently skipped.
	"""
	stored = {"width": 1920, "height": 1080}
	current = {"width": 1920, "height": 1080}
	with pytest.raises(KeyError):
		tr_video_identity.compare_video_identity(stored, current)
