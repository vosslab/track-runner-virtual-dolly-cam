"""Tests for the durable source-video geometry identity boundary."""

# PIP3 modules
import numpy
import pytest

# local repo modules
import tr_video_identity


#============================================
def _identity(**changes: object) -> dict:
	"""Build one valid identity with optional field changes."""
	identity = {
		"width": 1920,
		"height": 1080,
		"frame_count": 1000,
	}
	identity.update(changes)
	return identity


#============================================
def test_make_video_identity_emits_only_geometry_fields() -> None:
	"""A probe produces the three durable geometry fields and nothing else."""
	identity = tr_video_identity.make_video_identity("runner.mkv", {
		"width": 1920,
		"height": 1080,
		"fps": 30.0,
		"frame_count": 1000,
		"duration_s": 1000 / 30.0,
	})
	assert identity == _identity()


#============================================
def test_make_video_identity_canonicalizes_integer_like_metadata() -> None:
	"""Integer-like probe metadata persists as built-in JSON-compatible ints."""
	identity = tr_video_identity.make_video_identity("runner.mkv", {
		"width": numpy.int32(1920),
		"height": numpy.int64(1080),
		"frame_count": numpy.int64(1000),
	})
	assert identity == _identity()
	assert all(type(value) is int for value in identity.values())


#============================================
@pytest.mark.parametrize("field, changed_value", [
	("width", 3840),
	("height", 2160),
	("frame_count", 999),
])
def test_current_geometry_mismatch_blocks(
	field: str, changed_value: int,
) -> None:
	"""Every persisted geometry dimension blocks a mismatched current video."""
	stored = _identity()
	current = _identity(**{field: changed_value})
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result["blocking"] == [
		f"{field}: stored={stored[field]}, current={current[field]}"
	]


#============================================
def test_legacy_extras_and_rename_pass_quietly() -> None:
	"""Retired metadata fields do not affect a valid geometry comparison."""
	stored = _identity(
		basename="runner.MOV",
		size_bytes=3_180_000_000,
		fps=119.94,
		duration_s=150.0,
	)
	current = _identity(
		basename="runner-fastread.mkv",
		size_bytes=2_950_000_000,
		fps=119.916,
		duration_s=150.1,
	)
	result = tr_video_identity.compare_video_identity(stored, current)
	assert result == {"blocking": []}
	assert tr_video_identity.summarize_mismatches(result) == ""


#============================================
def test_summary_lists_only_blocking_geometry() -> None:
	"""The single comparison bucket yields a concise blocking summary."""
	result = {"blocking": ["frame_count: stored=1000, current=999"]}
	summary = tr_video_identity.summarize_mismatches(result)
	assert summary == "blocking:\n  frame_count: stored=1000, current=999"


#============================================
@pytest.mark.parametrize("identity", [
	None,
	[],
	_identity(width=True),
	_identity(height=1080.0),
	_identity(frame_count=0),
])
def test_malformed_identity_fails_loud(identity: object) -> None:
	"""Compatibility rejects malformed identity data before comparison."""
	with pytest.raises((TypeError, ValueError)):
		tr_video_identity.compare_video_identity(identity, _identity())


#============================================
def test_missing_required_identity_field_fails_loud() -> None:
	"""Compatibility requires the full geometry identity structure."""
	with pytest.raises(KeyError):
		tr_video_identity.compare_video_identity({"width": 1920}, _identity())
