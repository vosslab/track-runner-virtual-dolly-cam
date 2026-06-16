"""Tests for track_runner.tr_video_identity module.

Tests the video_identity comparison logic, including classification of
mismatches into blocking vs. informational categories per C12.3.
"""

# PIP3 modules
import pytest

# local repo modules
import tr_video_identity


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
def test_fps_remux_precision_within_1fps_tolerance_is_clean():
	"""Verify remux display-precision fps shift is absorbed by the 1.0 tolerance.

	A .MOV -> .mkv container remux shifts the displayed fps value by a small
	amount (for example 119.94 -> 119.916, diff=0.024). This must land in
	neither the blocking nor the informational bucket: the 1.0 absolute
	tolerance absorbs it entirely so users see no fps noise in the warning.
	"""
	stored = {
		"basename": "video.MOV",
		"size_bytes": 1000000,
		"width": 3840,
		"height": 2160,
		"fps": 119.94,
		"frame_count": 18000,
		"duration_s": 150.0,
	}
	current = {
		"basename": "video.MOV",
		"size_bytes": 1000000,
		"width": 3840,
		"height": 2160,
		"fps": 119.916,
		"frame_count": 18000,
		"duration_s": 150.0,
	}
	result = tr_video_identity.compare_video_identity(stored, current)
	# diff is 0.024, well within the 1.0 tolerance: no mismatch in either bucket
	assert result["blocking"] == []
	informational_fields = " ".join(result["informational"])
	assert "fps" not in informational_fields


#============================================
def test_fps_large_change_is_informational():
	"""Verify a large fps change (30 vs 60) is informational, not blocking.

	fps is advisory metadata noise: even a large fps difference (30 vs 60,
	diff=30.0) must land in the informational bucket, never the blocking
	bucket. The mismatch is still visibly reported so users can see it in the
	warning, but it never prevents a load.
	"""
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
	# blocking must be empty: fps never blocks, width/height/frame_count still do
	assert result["blocking"] == []
	# fps is reported as an informational mismatch (diff=30.0 > 1.0 tolerance)
	assert any("fps" in msg for msg in result["informational"])


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


#============================================
def test_fastread_remux_bucket_assignments():
	"""Pin bucket assignments for the prepare/fastread remux scenario.

	A valid fastread/remux path changes basename, size_bytes, and shows a
	small fps display-precision difference (for example 119.916 vs 119.94
	from the .MOV -> .mkv remux). With matching width, height, and
	frame_count, all those fields are advisory metadata noise -- they must
	land in the informational bucket, never the blocking bucket.

	A true geometry mismatch (width, height, or frame_count change) still
	lands in the blocking bucket so a broken prepare output is caught.

	This test pins the bucket split. If fps, basename, or size_bytes ever
	move to blocking, this test fails and routes the author to review the
	fastread contract in docs/TRACK_RUNNER_DESIGN.md.
	"""
	# simulate stored seeds written against original .MOV
	original = {
		"basename": "Lyra-Wheeling-IMG_3912.MOV",
		"size_bytes": 3_180_000_000,
		"width": 3840,
		"height": 2160,
		"fps": 119.94,
		"frame_count": 18000,
		"duration_s": 150.0,
	}
	# simulate current video as the remuxed .mkv fastread with display-
	# precision fps shift and smaller file size
	remuxed = {
		"basename": "Lyra-Wheeling-IMG_3912-fastread.mkv",
		"size_bytes": 2_950_000_000,
		"width": 3840,
		"height": 2160,
		"fps": 119.916,
		"frame_count": 18000,
		"duration_s": 150.1,
	}
	result = tr_video_identity.compare_video_identity(original, remuxed)
	# geometry matches: no blocking mismatches
	assert result["blocking"] == []
	# advisory metadata mismatches: basename and size_bytes differ
	informational_fields = " ".join(result["informational"])
	assert "basename" in informational_fields
	assert "size_bytes" in informational_fields
	# fps 119.94 vs 119.916 diff=0.024 is within 1.0 tolerance: no fps
	# mismatch at all -- the remux precision noise is fully absorbed
	assert "fps" not in informational_fields

	# true geometry mismatch must still land in blocking
	different_resolution = dict(remuxed)
	different_resolution["width"] = 1920
	geo_result = tr_video_identity.compare_video_identity(original, different_resolution)
	assert any("width" in msg for msg in geo_result["blocking"])

	# true frame_count mismatch must still land in blocking
	different_frame_count = dict(remuxed)
	different_frame_count["frame_count"] = 17999
	fc_result = tr_video_identity.compare_video_identity(original, different_frame_count)
	assert any("frame_count" in msg for msg in fc_result["blocking"])
