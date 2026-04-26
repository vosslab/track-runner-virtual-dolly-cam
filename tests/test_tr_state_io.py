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
