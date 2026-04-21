"""Tests for the geometry_cache.npz NPZ schema.

Covers the WP-I1 + WP-I4 (combined Patch 4+7) acceptance criteria:
- NPZ round-trip reconstructs the in-memory shape existing consumers
  expect (fingerprint-keyed dict with fused_track list-of-dicts).
- Writer emits schema_version = GEOMETRY_CACHE_SCHEMA_VERSION, a JSON
  manifest, and per-interval float32 arrays.
- interval_score, forward_track, backward_track, and per-frame extras
  are NOT persisted (scoring-owner gate enforcement).
- Loader rejects unknown schema versions with a clear error pointing
  at the migration tool.
- Empty cache (no solved_intervals) round-trips cleanly.
- video_identity and solve_complete survive a round-trip.
- Size regression: NPZ float32 is smaller than the equivalent JSON
  list-of-dicts on a realistic interval.
"""

# Standard Library
import json

# PIP3 modules
import numpy
import pytest

# local repo modules
import state_io


#============================================


def _make_interval_entry(start: int, end: int) -> dict:
	"""Build an in-memory interval entry with a short fused_track."""
	n = end - start + 1
	fused_track = [
		{
			"cx": 100.0 + i,
			"cy": 200.0 + i * 0.5,
			"w": 40.0,
			"h": 60.0,
		}
		for i in range(n)
	]
	return {"start_frame": start, "end_frame": end, "fused_track": fused_track}


#============================================


def test_empty_cache_round_trip(tmp_path):
	"""An empty cache writes and loads cleanly with no intervals."""
	path = str(tmp_path / "cache.npz")
	state_io.write_geometry_cache(path, {"solved_intervals": {}})
	loaded = state_io.load_geometry_cache(path)
	assert loaded[state_io.INTERVALS_HEADER_KEY] == state_io.INTERVALS_HEADER_VALUE
	assert loaded["solved_intervals"] == {}


#============================================


def test_single_interval_round_trip(tmp_path):
	"""A single-interval cache round-trips with the fused_track shape
	existing consumers expect."""
	path = str(tmp_path / "cache.npz")
	data = {
		"solved_intervals": {
			"fp_a": _make_interval_entry(10, 14),
		},
	}
	state_io.write_geometry_cache(path, data)
	loaded = state_io.load_geometry_cache(path)
	iv = loaded["solved_intervals"]["fp_a"]
	assert iv["start_frame"] == 10
	assert iv["end_frame"] == 14
	assert len(iv["fused_track"]) == 5
	assert iv["fused_track"][0]["cx"] == 100.0
	assert iv["fused_track"][4]["cy"] == 202.0


#============================================


def test_interval_score_not_persisted(tmp_path):
	"""interval_score in the input dict must not appear in the NPZ
	file. Scoring-owner gate: geometry_cache.npz is geometry only."""
	path = str(tmp_path / "cache.npz")
	entry = _make_interval_entry(0, 3)
	# inject fields that MUST be stripped by the writer
	entry["interval_score"] = {"agreement": 0.9, "confidence_tier": "high"}
	entry["forward_track"] = [{"cx": 1.0, "cy": 1.0, "w": 1.0, "h": 1.0}]
	entry["backward_track"] = [{"cx": 2.0, "cy": 2.0, "w": 1.0, "h": 1.0}]
	data = {"solved_intervals": {"fp_a": entry}}
	state_io.write_geometry_cache(path, data)
	# on-disk NPZ: only expected keys
	with numpy.load(path, allow_pickle=False) as npz:
		keys = set(npz.files)
		# no score/forward/backward surface anywhere in the NPZ
		for forbidden in (
			"interval_score", "forward_track", "backward_track",
			"i0_interval_score", "i0_forward_track", "i0_backward_track",
		):
			assert forbidden not in keys, forbidden
		# manifest itself carries no score or fwd/bwd fields
		manifest = json.loads(bytes(npz["manifest"]).decode("utf-8"))
		for entry_m in manifest:
			manifest_keys = set(entry_m.keys())
			assert "interval_score" not in manifest_keys
			assert "forward_track" not in manifest_keys
			assert "backward_track" not in manifest_keys
	# loaded back: interval does not carry stripped fields
	loaded = state_io.load_geometry_cache(path)
	iv = loaded["solved_intervals"]["fp_a"]
	assert "interval_score" not in iv
	assert "forward_track" not in iv
	assert "backward_track" not in iv


#============================================


def test_video_identity_and_solve_complete_round_trip(tmp_path):
	"""Top-level metadata survives a round-trip."""
	path = str(tmp_path / "cache.npz")
	data = {
		"solved_intervals": {"fp_a": _make_interval_entry(0, 2)},
		"video_identity": {
			"basename": "clip.mkv",
			"fps": 30.0,
			"frame_count": 1000,
		},
		"solve_complete": True,
	}
	state_io.write_geometry_cache(path, data)
	loaded = state_io.load_geometry_cache(path)
	assert loaded["video_identity"]["basename"] == "clip.mkv"
	assert loaded["solve_complete"] is True


#============================================


def test_arrays_are_float32(tmp_path):
	"""Per-frame arrays are persisted as float32, not float64."""
	path = str(tmp_path / "cache.npz")
	state_io.write_geometry_cache(path, {
		"solved_intervals": {"fp_a": _make_interval_entry(0, 4)}
	})
	with numpy.load(path, allow_pickle=False) as npz:
		assert npz["i0_cx"].dtype == numpy.float32
		assert npz["i0_cy"].dtype == numpy.float32
		assert npz["i0_w"].dtype == numpy.float32
		assert npz["i0_h"].dtype == numpy.float32


#============================================


def test_loader_rejects_unknown_schema_version(tmp_path):
	"""Loader raises with a clear error when schema_version is wrong."""
	path = str(tmp_path / "cache.npz")
	numpy.savez(
		path,
		schema_version=numpy.asarray(99, dtype=numpy.int32),
		manifest=numpy.frombuffer(b"[]", dtype=numpy.uint8),
	)
	# numpy.savez appended .npz if path didn't already end in .npz; our
	# path does, so it should be saved in place.
	with pytest.raises(RuntimeError, match="schema"):
		state_io.load_geometry_cache(path)


#============================================


def test_missing_file_returns_empty_skeleton(tmp_path):
	"""Loading a non-existent cache returns the empty-skeleton shape."""
	path = str(tmp_path / "does_not_exist.npz")
	loaded = state_io.load_geometry_cache(path)
	assert loaded[state_io.INTERVALS_HEADER_KEY] == state_io.INTERVALS_HEADER_VALUE
	assert loaded["solved_intervals"] == {}


#============================================


def test_size_beats_equivalent_json(tmp_path):
	"""For a realistic interval, the NPZ is smaller than the
	equivalent JSON list-of-dicts. Size-regression gate."""
	path = str(tmp_path / "cache.npz")
	# 500 frames of cx/cy/w/h dicts
	entry = _make_interval_entry(0, 499)
	state_io.write_geometry_cache(path, {
		"solved_intervals": {"fp_a" * 12: entry},
	})
	npz_bytes = (tmp_path / "cache.npz").stat().st_size
	# equivalent JSON (list of per-frame dicts plus start/end)
	equivalent_json = {
		"solved_intervals": {
			"fp_a" * 12: {
				"start_frame": 0,
				"end_frame": 499,
				"fused_track": entry["fused_track"],
			}
		}
	}
	json_bytes = len(
		json.dumps(equivalent_json, indent=2).encode("utf-8")
	)
	assert npz_bytes < json_bytes, (npz_bytes, json_bytes)


#============================================


def test_unknown_top_level_fields_ignored(tmp_path):
	"""Unknown top-level fields in the input dict are silently dropped
	by the writer (forward-compat: tolerant read, strict write)."""
	path = str(tmp_path / "cache.npz")
	data = {
		"solved_intervals": {"fp_a": _make_interval_entry(0, 2)},
		"some_future_field": {"experiment": True},
	}
	state_io.write_geometry_cache(path, data)
	with numpy.load(path, allow_pickle=False) as npz:
		assert "some_future_field" not in npz.files


#============================================


def _make_entry_with_fwd_bwd(start: int, end: int) -> dict:
	"""Build an entry with forward/backward tracks for debug-sidecar tests."""
	entry = _make_interval_entry(start, end)
	entry["forward_track"] = [
		{"cx": 10.0 + i, "cy": 20.0 + i, "w": 30.0, "h": 40.0}
		for i in range(end - start + 1)
	]
	entry["backward_track"] = [
		{"cx": 11.0 + i, "cy": 21.0 + i, "w": 30.0, "h": 40.0}
		for i in range(end - start + 1)
	]
	return entry


#============================================


def test_debug_tracks_round_trip(tmp_path):
	"""Debug sidecar writer/loader reconstruct fwd/bwd tracks per
	interval."""
	path = str(tmp_path / "debug.npz")
	data = {
		"solved_intervals": {
			"fp_a": _make_entry_with_fwd_bwd(0, 4),
			"fp_b": _make_entry_with_fwd_bwd(20, 24),
		},
	}
	state_io.write_debug_tracks(path, data)
	loaded = state_io.load_debug_tracks(path)
	assert set(loaded.keys()) == {"fp_a", "fp_b"}
	assert loaded["fp_a"]["start_frame"] == 0
	assert len(loaded["fp_a"]["forward_track"]) == 5
	assert loaded["fp_a"]["forward_track"][0]["cx"] == 10.0
	assert loaded["fp_a"]["backward_track"][0]["cx"] == 11.0


#============================================


def test_debug_tracks_skips_intervals_without_fwd_bwd(tmp_path):
	"""Intervals without forward/backward tracks are silently omitted
	from the debug sidecar."""
	path = str(tmp_path / "debug.npz")
	data = {
		"solved_intervals": {
			"fp_with": _make_entry_with_fwd_bwd(0, 3),
			"fp_without": _make_interval_entry(10, 13),
		},
	}
	state_io.write_debug_tracks(path, data)
	loaded = state_io.load_debug_tracks(path)
	assert set(loaded.keys()) == {"fp_with"}


#============================================


def test_debug_tracks_loader_missing_file_returns_empty(tmp_path):
	"""load_debug_tracks returns {} when the sidecar does not exist."""
	path = str(tmp_path / "does_not_exist.npz")
	loaded = state_io.load_debug_tracks(path)
	assert loaded == {}


#============================================


def test_debug_tracks_dtype_is_float32(tmp_path):
	"""Debug sidecar arrays are float32."""
	path = str(tmp_path / "debug.npz")
	state_io.write_debug_tracks(path, {
		"solved_intervals": {"fp_a": _make_entry_with_fwd_bwd(0, 2)},
	})
	with numpy.load(path, allow_pickle=False) as npz:
		assert npz["i0_fwd_cx"].dtype == numpy.float32
		assert npz["i0_bwd_cx"].dtype == numpy.float32
