"""Unit tests for state_io diagnostics schema migrations.

Tests the v3 diagnostics writer and shape-based reader migration:
- write_solver_diagnostics always emits v3 nested interval_score
- load_diagnostics accepts both v2 flat and v3 nested shapes
- legacy flat-shape entries are migrated in-place on read
- round-trip preservation of v3 nested shape
- explicit error when entries lack both shapes

Per docs/PYTHON_STYLE.md PYTEST guidance: behavioral tests only, no
hardcoded-constant asserts, no brittle checks on derived schema versions.
"""

# Standard Library
import json
import os
import tempfile

# PIP3 modules
import numpy
import pytest

# local repo modules
import camera_motion
import interval_solver
import scene_coords
import state_io
import tr_paths


#============================================
def test_round_trip_v3_nested(tmp_path):
	"""Round-trip a v3 nested interval_score through write + load.

	Verifies that write_solver_diagnostics + load_diagnostics preserve
	the nested shape, field values, and confidence_tier when starting
	with v3 input.
	"""
	# build a diagnostics dict with v3 nested interval_score
	diagnostics = {
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				"interval_score": {
					"agreement": 0.85,
					"velocity_consistency": 0.80,
					"size_consistency": 0.75,
					"motion_quality": 0.70,
					"occlusion_fraction": 0.05,
					"confidence_tier": "high",
					"failure_reasons": [],
					"warning_flags": [],
				},
			}
		]
	}

	# write to temp file
	diag_path = tmp_path / "diagnostics.json"
	state_io.write_solver_diagnostics(diagnostics, str(diag_path), fps=30.0)

	# load and verify
	loaded = state_io.load_diagnostics(str(diag_path))
	loaded_iv = loaded["intervals"][0]
	assert "interval_score" in loaded_iv
	score = loaded_iv["interval_score"]
	assert abs(score["agreement"] - 0.85) < 0.01
	assert score["confidence_tier"] == "high"


#============================================
def test_writer_preserves_legacy_numeric(tmp_path):
	"""Writer given flat v2 input preserves numeric fields and adds marker.

	When a score dict has flat v2 shape (agreement_score, identity_score, etc.)
	but lacks confidence_tier, the writer synthesizes a v3 entry that:
	- maps agreement_score to agreement (1:1 mapping)
	- preserves any other present numeric fields via zero-fill
	- sets confidence_tier to "unsolved" (runtime unsolved, not legacy)
	- appends "legacy_schema" to failure_reasons
	- preserves any pre-existing failure_reasons
	"""
	# build a diagnostics dict with flat v2 score shape
	diagnostics = {
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				"interval_score": {
					"agreement_score": 0.7,
					"identity_score": 0.5,
					"competitor_margin": 0.3,
					"confidence": "low",
					"failure_reasons": ["stale_prior"],
				},
			}
		]
	}

	# write and load
	diag_path = tmp_path / "diagnostics.json"
	state_io.write_solver_diagnostics(diagnostics, str(diag_path), fps=30.0)
	loaded = state_io.load_diagnostics(str(diag_path))

	# verify v3 nested shape and preserved values
	loaded_iv = loaded["intervals"][0]
	score = loaded_iv["interval_score"]
	assert abs(score["agreement"] - 0.7) < 0.01
	assert score["confidence_tier"] == "unsolved"
	assert "legacy_schema" in score["failure_reasons"]
	# pre-existing reason should also be preserved
	assert "stale_prior" in score["failure_reasons"]


#============================================
def test_reader_migrates_flat_regardless_of_header(tmp_path):
	"""Reader migrates flat-shape entries on load regardless of header version.

	Verifies that migration is shape-gated (not header-gated): a v3-header
	file can still contain flat v2 entries and they get migrated in-place.
	"""
	# hand-craft a JSON file with v3 header but flat v2 entry shape
	diag_dict = {
		state_io.DIAGNOSTICS_HEADER_KEY: 3,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				"agreement_score": 0.42,
				"identity_score": 0.0,
				"competitor_margin": 0.0,
				"confidence": "low",
				"failure_reasons": [],
			}
		]
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# load and verify migration occurred
	loaded = state_io.load_diagnostics(str(diag_path))
	loaded_iv = loaded["intervals"][0]
	assert "interval_score" in loaded_iv
	score = loaded_iv["interval_score"]
	assert abs(score["agreement"] - 0.42) < 0.01
	assert score["confidence_tier"] == "legacy_migrated"
	assert "legacy_schema" in score["failure_reasons"]


#============================================
def test_reader_raises_on_missing_both(tmp_path):
	"""Reader raises RuntimeError when entry lacks both nested and flat shapes.

	Stale or corrupt files missing both interval_score and flat agreement_score
	should not be silently loaded. The error message must mention re-solve.
	"""
	# hand-craft a JSON file with one interval missing both shapes
	diag_dict = {
		state_io.DIAGNOSTICS_HEADER_KEY: 3,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				# missing both interval_score and agreement_score
			}
		]
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# loading should raise RuntimeError
	with pytest.raises(RuntimeError):
		state_io.load_diagnostics(str(diag_path))


#============================================

def test_v3_file_migrates_on_load(tmp_path):
	"""Header 3 file without pre_race_reference loads with pre_race_reference=None.

	v3 files lack the pre_race_reference key. On load, it should be
	synthesized as None. Intervals round-trip unchanged.
	"""
	# hand-craft a v3 JSON with nested interval_score shape
	diag_dict = {
		state_io.DIAGNOSTICS_HEADER_KEY: 3,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				"interval_score": {
					"agreement": 0.85,
					"velocity_consistency": 0.80,
					"size_consistency": 0.75,
					"motion_quality": 0.70,
					"occlusion_fraction": 0.05,
					"confidence_tier": "high",
					"failure_reasons": [],
					"warning_flags": [],
				},
			}
		]
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# load and verify
	loaded = state_io.load_diagnostics(str(diag_path))
	assert loaded["pre_race_reference"] is None
	iv = loaded["intervals"][0]
	assert "interval_score" in iv
	assert abs(iv["interval_score"]["agreement"] - 0.85) < 0.01


#============================================

def test_v2_file_migrates_on_load(tmp_path):
	"""Header 2 file with flat shape migrates to nested on load.

	v2 files have flat interval_score shape (agreement_score, etc.).
	On load, should migrate to nested shape and pre_race_reference=None.
	"""
	# hand-craft a v2 JSON with flat interval_score shape
	diag_dict = {
		state_io.DIAGNOSTICS_HEADER_KEY: 2,
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				"agreement_score": 0.7,
				"identity_score": 0.5,
				"competitor_margin": 0.3,
				"confidence": "low",
				"failure_reasons": [],
			}
		]
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# load and verify
	loaded = state_io.load_diagnostics(str(diag_path))
	assert loaded["pre_race_reference"] is None
	assert len(loaded.get("intervals", [])) == 1
	iv = loaded["intervals"][0]
	score = iv["interval_score"]
	assert "agreement" in score
	assert abs(score["agreement"] - 0.7) < 0.01
	assert "legacy_schema" in score["failure_reasons"]


#============================================

def test_v3_file_race_phase_block_dropped(tmp_path):
	"""Header 3 file with race_phase block: block is dropped on load.

	v3 and earlier files may have a race_phase top-level block.
	v4 does not use it. Loader should drop it unconditionally.
	"""
	# hand-craft a v3 JSON with a race_phase block
	diag_dict = {
		state_io.DIAGNOSTICS_HEADER_KEY: 3,
		"intervals": [],
		"race_phase": {
			"race_start_frame": 120,
			"method": "velocity_onset",
		}
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# load and verify race_phase is gone
	loaded = state_io.load_diagnostics(str(diag_path))
	assert "race_phase" not in loaded


#============================================

def test_writer_serializes_pre_race_reference_when_present(tmp_path):
	"""Writer serializes pre_race_reference when present.

	Diagnostics dict with populated pre_race_reference should survive
	write+load round-trip.
	"""
	diagnostics = {
		"intervals": [],
		"pre_race_reference": {
			"race_start_frame": 120,
			"torso_w": 35.0,
			"torso_h": 70.0,
			"scene_anchor_x": 100.0,
			"scene_anchor_y": 50.0,
			"source_count": 3,
			"warnings": [],
		}
	}

	diag_path = tmp_path / "diagnostics.json"
	state_io.write_diagnostics(str(diag_path), diagnostics)

	# load and verify round-trip
	loaded = state_io.load_diagnostics(str(diag_path))
	assert loaded["pre_race_reference"] is not None
	ref = loaded["pre_race_reference"]
	assert ref["race_start_frame"] == 120
	assert abs(ref["torso_w"] - 35.0) < 0.01
	assert abs(ref["torso_h"] - 70.0) < 0.01


#============================================

def test_writer_omits_pre_race_reference_when_none(tmp_path):
	"""Writer with pre_race_reference=None loads back as None.

	Diagnostics dict with pre_race_reference=None should load as None
	(or omitted key, which load_diagnostics synthesizes as None).
	"""
	diagnostics = {
		"intervals": [],
		"pre_race_reference": None,
	}

	diag_path = tmp_path / "diagnostics.json"
	state_io.write_diagnostics(str(diag_path), diagnostics)

	# load and verify
	loaded = state_io.load_diagnostics(str(diag_path))
	assert loaded["pre_race_reference"] is None


#============================================
def test_torso_box_coords_round_trip_hermite_only(tmp_path):
	"""C10 round-trip: write_torso_box_coords + load_torso_box_coords preserve
	per-interval forward / backward / blended paths after a hermite-only solve.
	"""
	n_frames = 300
	motion = camera_motion.MotionTrack(
		dx=numpy.zeros(n_frames, dtype=numpy.float32),
		dy=numpy.zeros(n_frames, dtype=numpy.float32),
		scale=numpy.ones(n_frames, dtype=numpy.float32),
		quality=numpy.ones(n_frames, dtype=numpy.float32),
	)
	scene_transform = scene_coords.SceneTransform(motion)
	seeds = [
		{"frame_index": 10 + i * 100, "cx": 100.0 + i * 50.0, "cy": 200.0,
			"w": 30.0, "h": 60.0, "status": "visible"}
		for i in range(3)
	]

	class _StubReader:
		def get_info(self):
			return {"fps": 30.0}

		def read_frame(self, frame_index):
			return numpy.zeros((480, 640, 3), dtype=numpy.uint8)

	diagnostics = interval_solver.solve_all_intervals(
		reader=_StubReader(),
		seeds=seeds,
		detector=None,
		config={},
		num_workers=1,
		debug=False,
		scene_transform=scene_transform,
		motion_track=motion,
		video_frame_count=n_frames,
		hermite_only=True,
		full_solve=False,
		race_start_interval=None,
	)
	prior_ivs = {f"fp_{i}": iv for i, iv in enumerate(diagnostics["intervals"])}

	coords_path = tr_paths.default_intervals_path(str(tmp_path / "test_video.mp4"))
	state_io.write_torso_box_coords(coords_path, {"solved_intervals": prior_ivs})
	loaded = state_io.load_torso_box_coords(coords_path)

	# Round-trip invariant: same fingerprint key set, and every loaded path
	# carries finite per-frame coordinates on each direction.
	assert set(loaded["solved_intervals"]) == set(prior_ivs)
	for interval_data in loaded["solved_intervals"].values():
		for path_key in ("forward_path", "backward_path", "blended_path"):
			path = interval_data[path_key]
			assert all(numpy.isfinite(s["cx"]) and numpy.isfinite(s["cy"]) for s in path)


#============================================

def test_torso_box_coords_uint16_round_trip(tmp_path):
	"""V10 round-trip: coords are rounded to int, stored as uint16, and loaded as int.

	Per C12.4, per-frame coordinates are rounded to nearest integer and
	stored as uint16 (pixel-snapped, no subpixel precision). On load,
	they are reconstructed as Python ints, not numpy types.

	This test verifies:
	- Float coords are rounded to nearest int before storage
	- On-disk arrays have dtype == uint16
	- Loaded values are Python ints within +-1 of rounded originals
	- Round-trip preserves integer-level accuracy
	"""
	# create a single interval with float coords
	blended_path = [
		{"cx": 100.3, "cy": 200.7, "w": 30.1, "h": 60.9},
		{"cx": 101.5, "cy": 201.5, "w": 30.5, "h": 61.5},
		{"cx": 102.9, "cy": 202.1, "w": 31.9, "h": 62.1},
	]
	cache_data = {
		"solved_intervals": {
			"fp_test": {
				"start_frame": 10,
				"end_frame": 20,
				"forward_path": None,
				"backward_path": None,
				"blended_path": blended_path,
			}
		}
	}

	coords_path = tmp_path / "torso_box_coords.npz"
	state_io.write_torso_box_coords(str(coords_path), cache_data)

	# verify on-disk arrays are uint16
	with numpy.load(str(coords_path), allow_pickle=False) as npz:
		assert npz["i0_blended_cx"].dtype == numpy.uint16
		assert npz["i0_blended_cy"].dtype == numpy.uint16
		assert npz["i0_blended_w"].dtype == numpy.uint16
		assert npz["i0_blended_h"].dtype == numpy.uint16

	# load and verify reconstructed coords are Python ints within tolerance
	loaded = state_io.load_torso_box_coords(str(coords_path))
	loaded_interval = loaded["solved_intervals"]["fp_test"]
	loaded_blended = loaded_interval["blended_path"]

	# verify each coordinate matches the rounded original (within +-1)
	expected_rounded = [
		{"cx": 100, "cy": 201, "w": 30, "h": 61},
		{"cx": 102, "cy": 202, "w": 31, "h": 62},
		{"cx": 103, "cy": 202, "w": 32, "h": 62},
	]
	for loaded_frame, expected_frame in zip(loaded_blended, expected_rounded):
		for key in ("cx", "cy", "w", "h"):
			loaded_val = loaded_frame[key]
			expected_val = expected_frame[key]
			assert isinstance(loaded_val, int), f"loaded {key} should be int, got {type(loaded_val)}"
			assert abs(loaded_val - expected_val) <= 1, \
				f"coord {key} mismatch: expected ~{expected_val}, got {loaded_val}"


#============================================

def test_torso_box_coords_rejects_old_schema(tmp_path):
	"""V10 loader rejects v9 schema with clear error message.

	When loading a v9 torso_box_coords.npz, the loader should raise
	RuntimeError with a message directing the user to re-solve.
	"""
	# hand-craft a v9 NPZ file with minimal content
	arrays = {
		"schema_version": numpy.asarray(9, dtype=numpy.int32),
		"manifest": numpy.frombuffer(json.dumps([]).encode("utf-8"), dtype=numpy.uint8),
	}
	coords_path = tmp_path / "torso_box_coords_v9.npz"
	dir_path = str(coords_path.parent)
	fd, tmp_file = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_file, **arrays)
		real_tmp = tmp_file if tmp_file.endswith(".npz") else tmp_file + ".npz"
		os.replace(real_tmp, str(coords_path))
	except Exception:
		for candidate in (tmp_file, tmp_file + ".npz"):
			if os.path.exists(candidate):
				os.unlink(candidate)
		raise

	# loading should raise RuntimeError
	with pytest.raises(RuntimeError) as exc_info:
		state_io.load_torso_box_coords(str(coords_path))

	# verify error message mentions re-solve
	assert "re-solve" in str(exc_info.value).lower() or "upgrade" in str(exc_info.value).lower()


#============================================
def test_load_torso_box_coords_rejects_frame_count_mismatch(tmp_path):
	"""Verify load_torso_box_coords rejects frame_count vs manifest mismatch.

	Tests the internal consistency check: if video_identity frame_count
	is less than max(end_frame) in the manifest, raise RuntimeError with
	"frame_count" in the message.
	"""
	coords_path = tmp_path / "torso_box_coords.npz"
	dir_path = tmp_path

	# Build a valid torso_box_coords file
	manifest = [
		{
			"fingerprint": "test_fp",
			"array_index": 0,
			"start_frame": 10,
			"end_frame": 200,
		}
	]

	# Create torso box arrays for 191 frames (frames 10-200)
	frame_count_arrays = 191
	arrays = {
		"schema_version": numpy.asarray(state_io.SCHEMA_VERSION, dtype=numpy.int64),
		"manifest": numpy.frombuffer(
			json.dumps(manifest).encode("utf-8"), dtype=numpy.uint8
		),
		"i0_blended_cx": numpy.arange(frame_count_arrays, dtype=numpy.uint16),
		"i0_blended_cy": numpy.arange(frame_count_arrays, dtype=numpy.uint16),
		"i0_blended_w": numpy.ones(frame_count_arrays, dtype=numpy.uint16) * 100,
		"i0_blended_h": numpy.ones(frame_count_arrays, dtype=numpy.uint16) * 100,
		# Corrupt: claim frame_count=100, but manifest has end_frame=200
		"video_identity": numpy.frombuffer(
			json.dumps({
				"basename": "test.mp4",
				"size_bytes": 1000000,
				"width": 1920,
				"height": 1080,
				"fps": 30.0,
				"frame_count": 100,
				"duration_s": 33.0,
			}).encode("utf-8"),
			dtype=numpy.uint8,
		),
	}

	# Write to temp file then move to final path
	fd, tmp_file = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_file, **arrays)
		real_tmp = tmp_file if tmp_file.endswith(".npz") else tmp_file + ".npz"
		os.replace(real_tmp, str(coords_path))
	except Exception:
		for candidate in (tmp_file, tmp_file + ".npz"):
			if os.path.exists(candidate):
				os.unlink(candidate)
		raise

	# Load should raise RuntimeError about frame_count
	with pytest.raises(RuntimeError) as exc_info:
		state_io.load_torso_box_coords(str(coords_path))

	error_msg = str(exc_info.value).lower()
	assert "frame_count" in error_msg
	assert "corrupt" in error_msg or "trimmed" in error_msg
