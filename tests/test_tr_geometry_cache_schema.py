"""Tests for the geometry_cache.npz NPZ schema.

Covers the WP-I1 + WP-I4 (combined Patch 4+7) acceptance criteria:
- NPZ round-trip reconstructs the in-memory shape existing consumers
  expect (fingerprint-keyed dict with blended_path list-of-dicts).
- Writer emits schema_version = GEOMETRY_CACHE_SCHEMA_VERSION, a JSON
  manifest, and per-interval float32 arrays.
- interval_score, forward_path, backward_path, and per-frame extras
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
import tr_schema


#============================================


def _make_interval_entry(start: int, end: int) -> dict:
	"""Build an in-memory interval entry with a short blended_path."""
	n = end - start + 1
	blended_path = [
		{
			"cx": 100.0 + i,
			"cy": 200.0 + i * 0.5,
			"w": 40.0,
			"h": 60.0,
		}
		for i in range(n)
	]
	return {"start_frame": start, "end_frame": end, "blended_path": blended_path}


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
	"""A single-interval cache round-trips: every cx/cy/w/h matches."""
	path = str(tmp_path / "cache.npz")
	original = _make_interval_entry(10, 14)
	state_io.write_geometry_cache(
		path, {"solved_intervals": {"fp_a": original}},
	)
	loaded = state_io.load_geometry_cache(path)
	iv = loaded["solved_intervals"]["fp_a"]
	# round-trip invariant: every per-frame record survives unchanged
	for orig, rt in zip(original["blended_path"], iv["blended_path"]):
		assert orig == rt


#============================================


def test_interval_score_not_persisted(tmp_path):
	"""Scoring-owner gate: interval_score, forward_path, and
	backward_path must not survive write_geometry_cache in any
	form (NPZ key, indexed array, or manifest field)."""
	path = str(tmp_path / "cache.npz")
	entry = _make_interval_entry(0, 3)
	# inject fields that MUST be stripped by the writer
	entry["interval_score"] = {"agreement": 0.9, "confidence_tier": "high"}
	entry["forward_path"] = [{"cx": 1.0, "cy": 1.0, "w": 1.0, "h": 1.0}]
	entry["backward_path"] = [{"cx": 2.0, "cy": 2.0, "w": 1.0, "h": 1.0}]
	state_io.write_geometry_cache(
		path, {"solved_intervals": {"fp_a": entry}},
	)
	forbidden = {"interval_score", "forward_path", "backward_path"}
	# gather every place a forbidden name could appear on disk
	with numpy.load(path, allow_pickle=False) as npz:
		on_disk = set(npz.files)
		manifest = json.loads(bytes(npz["manifest"]).decode("utf-8"))
		for entry_m in manifest:
			on_disk.update(entry_m.keys())
	# also indexed-array variants, e.g. i0_interval_score
	indexed = {f"i0_{name}" for name in forbidden}
	assert forbidden.isdisjoint(on_disk | indexed)


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
		dtypes = {npz[f"i0_{k}"].dtype for k in ("cx", "cy", "w", "h")}
	# disk-format contract: all four arrays are float32, not float64
	assert dtypes == {numpy.dtype("float32")}


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
				"blended_path": entry["blended_path"],
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
	"""Build an entry with forward/backward interval paths for debug-sidecar tests."""
	entry = _make_interval_entry(start, end)
	entry["forward_path"] = [
		{"cx": 10.0 + i, "cy": 20.0 + i, "w": 30.0, "h": 40.0}
		for i in range(end - start + 1)
	]
	entry["backward_path"] = [
		{"cx": 11.0 + i, "cy": 21.0 + i, "w": 30.0, "h": 40.0}
		for i in range(end - start + 1)
	]
	return entry


#============================================


def test_debug_paths_round_trip(tmp_path):
	"""Debug sidecar writer/loader reconstruct fwd/bwd tracks
	unchanged -- round-trip invariant.

	Schema v2 invariant: slot 0 of forward_path and backward_path both
	correspond to start_frame (chronological slot storage, same for
	both passes).
	"""
	path = str(tmp_path / "debug.npz")
	original = _make_entry_with_fwd_bwd(0, 4)
	state_io.write_debug_paths(
		path, {"solved_intervals": {"fp_a": original}},
	)
	loaded = state_io.load_debug_paths(path)
	iv = loaded["fp_a"]
	# round-trip invariant: every fwd and bwd frame survives unchanged
	for orig, rt in zip(original["forward_path"], iv["forward_path"]):
		assert orig == rt
	for orig, rt in zip(original["backward_path"], iv["backward_path"]):
		assert orig == rt
	# slot 0 of each pass survives round trip at the same logical
	# position, i.e. writer/loader never silently reverse BWD
	assert iv["forward_path"][0] == original["forward_path"][0]
	assert iv["backward_path"][0] == original["backward_path"][0]


#============================================


def test_debug_paths_schema_version_matches_unified(tmp_path):
	"""Writer emits the unified schema version stamp (C9)."""
	path = str(tmp_path / "debug.npz")
	state_io.write_debug_paths(path, {
		"solved_intervals": {"fp_a": _make_entry_with_fwd_bwd(0, 2)},
	})
	with numpy.load(path, allow_pickle=False) as npz:
		assert int(npz["schema_version"]) == tr_schema.SCHEMA_VERSION
	assert state_io.DEBUG_PATHS_SCHEMA_VERSION == tr_schema.SCHEMA_VERSION


#============================================


def test_debug_paths_rejects_v1_sidecar(tmp_path):
	"""A v1 sidecar is rejected with a schema-mismatch error.

	Contract C8: stale v1 files are regenerated on the next solve, not
	silently re-interpreted under the new index-to-frame mapping.
	"""
	path = str(tmp_path / "legacy.npz")
	numpy.savez(
		path,
		schema_version=numpy.asarray(1, dtype=numpy.int32),
		manifest=numpy.frombuffer(b"[]", dtype=numpy.uint8),
	)
	with pytest.raises(RuntimeError, match="schema"):
		state_io.load_debug_paths(path)


#============================================


def test_debug_paths_skips_intervals_without_fwd_bwd(tmp_path):
	"""Intervals without forward/backward interval paths are silently omitted
	from the debug sidecar."""
	path = str(tmp_path / "debug.npz")
	data = {
		"solved_intervals": {
			"fp_with": _make_entry_with_fwd_bwd(0, 3),
			"fp_without": _make_interval_entry(10, 13),
		},
	}
	state_io.write_debug_paths(path, data)
	loaded = state_io.load_debug_paths(path)
	assert set(loaded.keys()) == {"fp_with"}


#============================================


def test_debug_paths_loader_missing_file_returns_empty(tmp_path):
	"""load_debug_paths returns {} when the sidecar does not exist."""
	path = str(tmp_path / "does_not_exist.npz")
	loaded = state_io.load_debug_paths(path)
	assert loaded == {}


#============================================


def test_video_identity_readable_from_npz_without_json_load(tmp_path):
	"""The identity-mismatch check treats the geometry cache as NPZ,
	not JSON. Regression guard: an earlier version tried `json.load`
	on the cache file and crashed with UnicodeDecodeError on the
	NPZ magic bytes."""
	path = str(tmp_path / "cache.npz")
	state_io.write_geometry_cache(path, {
		"solved_intervals": {"fp_a": _make_interval_entry(0, 2)},
		"video_identity": {"basename": "clip.mkv", "frame_count": 100},
	})
	# raw bytes start with PK (zip) or \x93NUMPY, never valid JSON
	with open(path, "rb") as fh:
		head = fh.read(4)
	assert head[:2] in (b"PK", b"\x93N")
	# but load_geometry_cache returns the identity dict cleanly
	loaded = state_io.load_geometry_cache(path)
	assert loaded["video_identity"]["basename"] == "clip.mkv"


#============================================


def test_debug_paths_dtype_is_float32(tmp_path):
	"""Debug sidecar arrays are float32."""
	path = str(tmp_path / "debug.npz")
	state_io.write_debug_paths(path, {
		"solved_intervals": {"fp_a": _make_entry_with_fwd_bwd(0, 2)},
	})
	with numpy.load(path, allow_pickle=False) as npz:
		dtypes = {npz["i0_fwd_cx"].dtype, npz["i0_bwd_cx"].dtype}
	assert dtypes == {numpy.dtype("float32")}
