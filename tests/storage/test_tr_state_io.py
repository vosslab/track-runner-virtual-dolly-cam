"""Unit tests for current state I/O contracts."""

# Standard Library
import json
import os
import pathlib
import tempfile

# PIP3 modules
import numpy
import pytest

# local repo modules
import state_io
import torso_box_coords_io
import tr_schema
import trajectory_confidence


#============================================
def _current_video_identity(frame_count: int = 300) -> dict:
	"""Build the current source identity required by an NPZ artifact."""
	identity = {
		"width": 640,
		"height": 480,
		"frame_count": frame_count,
	}
	return identity


#============================================
def test_round_trip_v3_nested(tmp_path: pathlib.Path) -> None:
	"""Round-trip a v3 nested interval_score through write + load.

	Verifies that write_solver_interval_scores + load_interval_scores preserve
	the nested shape, field values, and confidence_tier when starting
	with v3 input.
	"""
	# build a diagnostics dict with v3 nested interval_score
	diagnostics = {
		"video_identity": _current_video_identity(),
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
	state_io.write_solver_interval_scores(diagnostics, str(diag_path), fps=30.0)

	# load and verify
	loaded = state_io.load_interval_scores(str(diag_path))
	loaded_iv = loaded["intervals"][0]
	assert "interval_score" in loaded_iv
	score = loaded_iv["interval_score"]
	assert abs(score["agreement"] - 0.85) < 0.01
	assert score["confidence_tier"] == "high"


#============================================
def test_agreement_debug_owner_output_writes_sidecar(tmp_path: pathlib.Path) -> None:
	"""The debug writer accepts the confidence owner's current field names."""
	forward = [{"cx": 10.0, "cy": 10.0, "w": 10.0, "h": 20.0}]
	backward = [{"cx": 12.0, "cy": 10.0, "w": 10.0, "h": 20.0}]
	debug = trajectory_confidence.compute_agreement_debug(forward, backward)
	path = tmp_path / "agreement_debug.json"
	diagnostics = {
		"intervals": [{
			"start_frame": 0, "end_frame": 0, "agreement_debug": debug,
		}],
	}

	assert state_io.write_agreement_debug_sidecar(diagnostics, str(path)) == 1
	persisted = json.loads(path.read_text())["intervals"][0]
	assert persisted["confidence_p50"] == round(debug["confidence_p50"], 4)


#============================================
def test_reader_rejects_missing_interval_score(tmp_path: pathlib.Path) -> None:
	"""Reader rejects a current-schema interval without interval_score."""
	# Hand-craft a current-schema file with one malformed interval.
	diag_dict = {
		state_io.INTERVAL_SCORES_HEADER_KEY: state_io.INTERVAL_SCORES_HEADER_VALUE,
		"fps": 30.0,
		"video_identity": _current_video_identity(),
		"intervals": [
			{
				"start_frame": 10,
				"end_frame": 100,
				# missing interval_score
			}
		]
	}
	diag_path = tmp_path / "diagnostics.json"
	with open(diag_path, "w") as fh:
		json.dump(diag_dict, fh)

	# loading should raise RuntimeError
	with pytest.raises(RuntimeError):
		state_io.load_interval_scores(str(diag_path))


#============================================
def test_reader_rejects_retired_diagnostics_schema(tmp_path: pathlib.Path) -> None:
	"""A noncurrent diagnostics header directs the caller to regenerate it."""
	diag_path = tmp_path / "diagnostics.json"
	diag_path.write_text(json.dumps({
		state_io.INTERVAL_SCORES_HEADER_KEY: state_io.INTERVAL_SCORES_HEADER_VALUE - 1,
		"intervals": [],
	}))

	with pytest.raises(RuntimeError, match="header mismatch"):
		state_io.load_interval_scores(str(diag_path))


#============================================
def test_interval_score_reader_rejects_missing_current_header(
		tmp_path: pathlib.Path,
) -> None:
	"""A score file without the current header cannot be consumed."""
	diag_path = tmp_path / "interval_scores.json"
	diag_path.write_text(json.dumps({
		"intervals": [],
	}))

	with pytest.raises(RuntimeError, match="track_runner_diagnostics"):
		state_io.load_interval_scores(str(diag_path))


#============================================
def test_reader_rejects_incomplete_current_interval_score(
		tmp_path: pathlib.Path,
) -> None:
	"""A partial score record cannot silently skip required review data."""
	diag_path = tmp_path / "diagnostics.json"
	diag_path.write_text(json.dumps({
		state_io.INTERVAL_SCORES_HEADER_KEY: state_io.INTERVAL_SCORES_HEADER_VALUE,
		"fps": 30.0,
		"video_identity": _current_video_identity(),
		"intervals": [{
			"start_frame": 10,
			"end_frame": 20,
			"interval_score": {"confidence_tier": "high"},
		}],
	}))

	with pytest.raises(RuntimeError, match="stale interval scores"):
		state_io.load_interval_scores(str(diag_path))


#============================================
def test_writer_rejects_incomplete_interval_score(tmp_path: pathlib.Path) -> None:
	"""The writer rejects records the current score reader would reject."""
	with pytest.raises(RuntimeError, match="interval_score is incomplete"):
		state_io.write_interval_scores(str(tmp_path / "interval_scores.json"), {
			"fps": 30.0,
			"video_identity": _current_video_identity(),
			"intervals": [{
				"start_frame": 10,
				"end_frame": 20,
				"interval_score": {"confidence_tier": "high"},
			}],
		})


#============================================
def test_torso_writer_rejects_path_length_mismatch(tmp_path: pathlib.Path) -> None:
	"""The NPZ writer refuses a path its own reader would reject."""
	with pytest.raises(ValueError, match="blended path length"):
		torso_box_coords_io.write_torso_box_coords(str(tmp_path / "coords.npz"), {
			"video_identity": _current_video_identity(),
			"solve_complete": True,
			"solved_intervals": {
				"interval": {
					"start_frame": 10,
					"end_frame": 11,
					"forward_path": None,
					"backward_path": None,
					"blended_path": [{"cx": 1, "cy": 1, "w": 1, "h": 1}],
				},
			},
		})


#============================================

def test_current_file_without_race_start_diagnostics_loads(tmp_path: pathlib.Path) -> None:
	"""Current diagnostics contain interval scores, not race-start state."""
	# Hand-craft a current JSON file with nested interval score data.
	diag_dict = {
		state_io.INTERVAL_SCORES_HEADER_KEY: state_io.INTERVAL_SCORES_HEADER_VALUE,
		"fps": 30.0,
		"video_identity": _current_video_identity(),
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
	loaded = state_io.load_interval_scores(str(diag_path))
	assert "pre_race_reference" not in loaded
	iv = loaded["intervals"][0]
	assert "interval_score" in iv
	assert abs(iv["interval_score"]["agreement"] - 0.85) < 0.01


#============================================


def test_writer_does_not_serialize_pre_race_reference(
		tmp_path: pathlib.Path,
) -> None:
	"""Race-start state is not retained in interval-scores diagnostics."""
	diagnostics = {
		"fps": 30.0,
		"video_identity": _current_video_identity(),
		"intervals": [],
		"pre_race_reference": {"race_start_frame": 120},
	}

	diag_path = tmp_path / "diagnostics.json"
	state_io.write_interval_scores(str(diag_path), diagnostics)

	# Race-start data belongs only in torso_box_coords.npz.
	loaded = state_io.load_interval_scores(str(diag_path))
	assert "pre_race_reference" not in loaded


#============================================

def test_writer_omits_pre_race_reference_when_none(tmp_path: pathlib.Path) -> None:
	"""A None race-start value is not a diagnostics field either."""
	diagnostics = {
		"fps": 30.0,
		"video_identity": _current_video_identity(),
		"intervals": [],
		"pre_race_reference": None,
	}

	diag_path = tmp_path / "diagnostics.json"
	state_io.write_interval_scores(str(diag_path), diagnostics)

	# load and verify
	loaded = state_io.load_interval_scores(str(diag_path))
	assert "pre_race_reference" not in loaded


#============================================
def test_torso_box_coords_rejects_one_absent_direction(tmp_path: pathlib.Path) -> None:
	"""A partial FWD/BWD pair cannot masquerade as a pre-race interval."""
	cache_data = {
		"video_identity": _current_video_identity(),
		"solve_complete": False,
		"solved_intervals": {
			"fp_test": {
				"start_frame": 0,
				"end_frame": 1,
				"forward_path": None,
				"backward_path": [{"cx": 1, "cy": 2, "w": 3, "h": 4}],
				"blended_path": [{"cx": 1, "cy": 2, "w": 3, "h": 4}],
			}
		}
	}
	coords_path = tmp_path / "torso_box_coords.npz"
	with pytest.raises(ValueError, match="both FWD/BWD paths or neither"):
		torso_box_coords_io.write_torso_box_coords(str(coords_path), cache_data)


#============================================
def test_torso_box_coords_loader_rejects_one_directional_array_group(
		tmp_path: pathlib.Path,
) -> None:
	"""A corrupt persisted FWD-only group cannot become a pre-race interval."""
	manifest = [{
		"fingerprint": "fp_test",
		"start_frame": 0,
		"end_frame": 0,
		"array_index": 0,
	}]
	arrays = {
		"schema_version": numpy.asarray(tr_schema.SCHEMA_VERSION),
		"manifest": numpy.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=numpy.uint8),
		"video_identity": numpy.frombuffer(
			json.dumps(_current_video_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		"solve_complete": numpy.asarray(False),
	}
	for direction_tag in ("blended", "fwd"):
		for coord_key, value in (("cx", 10), ("cy", 20), ("w", 30), ("h", 40)):
			arrays[f"i0_{direction_tag}_{coord_key}"] = numpy.asarray([value], dtype=numpy.uint16)
	arrays["i0_conf"] = numpy.asarray([255], dtype=numpy.uint8)
	coords_path = tmp_path / "torso_box_coords.npz"
	numpy.savez(str(coords_path), **arrays)
	with pytest.raises(RuntimeError, match="only one FWD/BWD path"):
		torso_box_coords_io.load_torso_box_coords(str(coords_path))


#============================================

def test_torso_box_coords_rejects_old_schema(tmp_path: pathlib.Path) -> None:
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
		torso_box_coords_io.load_torso_box_coords(str(coords_path))

	# verify error message mentions re-solve
	assert "re-solve" in str(exc_info.value).lower() or "upgrade" in str(exc_info.value).lower()


#============================================
def test_torso_box_coords_v10_requires_resolve(tmp_path: pathlib.Path) -> None:
	"""Schema 15 is a hard format cutover from the former v10 layout."""
	coords_path = tmp_path / "torso_box_coords_v10.npz"
	numpy.savez(
		coords_path,
		schema_version=numpy.asarray(10, dtype=numpy.int32),
		manifest=numpy.frombuffer(b"[]", dtype=numpy.uint8),
	)
	with pytest.raises(RuntimeError, match="re-solve"):
		torso_box_coords_io.load_torso_box_coords(str(coords_path))


#============================================
def test_load_torso_box_coords_rejects_frame_count_mismatch(
		tmp_path: pathlib.Path,
) -> None:
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
		"i0_conf": numpy.ones(frame_count_arrays, dtype=numpy.uint8) * 255,
		# Corrupt: claim frame_count=100, but manifest has end_frame=200
		"video_identity": numpy.frombuffer(
			json.dumps({
				"width": 1920,
				"height": 1080,
				"frame_count": 100,
			}).encode("utf-8"),
			dtype=numpy.uint8,
		),
		"solve_complete": numpy.asarray(False),
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
		torso_box_coords_io.load_torso_box_coords(str(coords_path))

	error_msg = str(exc_info.value).lower()
	assert "frame_count" in error_msg
	assert "corrupt" in error_msg or "trimmed" in error_msg


#============================================
def test_load_torso_box_coords_rejects_incomplete_blended_path(
		tmp_path: pathlib.Path,
) -> None:
	"""A manifest interval must carry a complete blended trajectory."""
	coords_path = tmp_path / "torso_box_coords.npz"
	manifest = [{
		"fingerprint": "fp",
		"array_index": 0,
		"start_frame": 10,
		"end_frame": 10,
	}]
	numpy.savez(
		coords_path,
		schema_version=numpy.asarray(state_io.SCHEMA_VERSION, dtype=numpy.int32),
		manifest=numpy.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=numpy.uint8),
		video_identity=numpy.frombuffer(
			json.dumps(_current_video_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		solve_complete=numpy.asarray(False),
		i0_blended_cx=numpy.asarray([10], dtype=numpy.uint16),
	)

	with pytest.raises(RuntimeError, match="incomplete blended path"):
		torso_box_coords_io.load_torso_box_coords(str(coords_path))


#============================================
def test_load_torso_box_coords_rejects_non_uint16_coordinates(
		tmp_path: pathlib.Path,
) -> None:
	"""The shared path loader rejects corrupt coordinate transport dtypes."""
	coords_path = tmp_path / "torso_box_coords.npz"
	manifest = [{
		"fingerprint": "fp",
		"array_index": 0,
		"start_frame": 0,
		"end_frame": 0,
	}]
	numpy.savez(
		coords_path,
		schema_version=numpy.asarray(tr_schema.SCHEMA_VERSION, dtype=numpy.int32),
		manifest=numpy.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=numpy.uint8),
		video_identity=numpy.frombuffer(
			json.dumps(_current_video_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		solve_complete=numpy.asarray(False),
		i0_blended_cx=numpy.asarray([10.0], dtype=numpy.float32),
		i0_blended_cy=numpy.asarray([20], dtype=numpy.uint16),
		i0_blended_w=numpy.asarray([30], dtype=numpy.uint16),
		i0_blended_h=numpy.asarray([40], dtype=numpy.uint16),
		i0_conf=numpy.asarray([255], dtype=numpy.uint8),
	)
	with pytest.raises(RuntimeError, match="coordinates must be uint16"):
		torso_box_coords_io.load_torso_box_coords(str(coords_path))


#============================================
def test_load_torso_box_coords_rejects_missing_confidence(
		tmp_path: pathlib.Path,
) -> None:
	"""Every schema-15 interval must transport raw-pass agreement."""
	coords_path = tmp_path / "coords.npz"
	manifest = [{
		"fingerprint": "fp", "array_index": 0,
		"start_frame": 0, "end_frame": 0,
	}]
	numpy.savez(
		coords_path,
		schema_version=numpy.asarray(tr_schema.SCHEMA_VERSION, dtype=numpy.int32),
		manifest=numpy.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=numpy.uint8),
		video_identity=numpy.frombuffer(
			json.dumps(_current_video_identity()).encode("utf-8"), dtype=numpy.uint8,
		),
		solve_complete=numpy.asarray(False),
		i0_blended_cx=numpy.asarray([10], dtype=numpy.uint16),
		i0_blended_cy=numpy.asarray([20], dtype=numpy.uint16),
		i0_blended_w=numpy.asarray([30], dtype=numpy.uint16),
		i0_blended_h=numpy.asarray([40], dtype=numpy.uint16),
	)
	with pytest.raises(RuntimeError, match="missing stored confidence"):
		torso_box_coords_io.load_torso_box_coords(str(coords_path))


#============================================
def _artifact_path(cx: float, conf: float) -> list[dict]:
	"""Build one frame of SOURCE-space raw path data."""
	return [{"cx": cx, "cy": 20.0, "w": 30.0, "h": 60.0, "conf": conf}]


#============================================
def test_torso_box_coords_v15_conditional_raw_paths_and_confidence(
		tmp_path: pathlib.Path,
) -> None:
	"""V15 retains raw paths only when their uint16 difference survives."""
	coords_path = tmp_path / "coords.npz"
	torso_box_coords_io.write_torso_box_coords(str(coords_path), {
		"video_identity": _current_video_identity(),
		"solve_complete": True,
		"solved_intervals": {
			"diverging": {
				"start_frame": 0, "end_frame": 0,
				"forward_path": _artifact_path(10.0, 0.2),
				"backward_path": _artifact_path(12.0, 0.2),
				"blended_path": _artifact_path(11.0, 0.2),
				"conf": [0.2],
			},
			"quantization_equal": {
				"start_frame": 1, "end_frame": 1,
				"forward_path": _artifact_path(20.1, 0.2),
				"backward_path": _artifact_path(20.4, 0.2),
				"blended_path": _artifact_path(20.25, 0.2),
				"conf": [0.2],
			},
		},
	})
	with numpy.load(coords_path, allow_pickle=False) as npz:
		assert npz["i0_conf"].dtype == numpy.uint8
		assert npz["i1_conf"].dtype == numpy.uint8
		assert "i0_fwd_cx" in npz.files
		assert "i0_bwd_cx" in npz.files
		assert "i1_fwd_cx" not in npz.files
		assert "i1_bwd_cx" not in npz.files
	loaded = torso_box_coords_io.load_torso_box_coords(str(coords_path))
	diverging = loaded["solved_intervals"]["diverging"]
	equal = loaded["solved_intervals"]["quantization_equal"]
	assert diverging["forward_path"] is not None
	assert diverging["backward_path"] is not None
	assert equal["forward_path"] is None
	assert equal["backward_path"] is None
	assert abs(equal["conf"][0] - 0.2) <= 1.0 / 255.0


#============================================
def test_torso_box_coords_round_trips_optional_race_start(
		tmp_path: pathlib.Path,
) -> None:
	"""A detected race start round-trips; no detected phase may omit the block."""
	coords_path = tmp_path / "coords.npz"
	race_start = {
		"race_start_frame": 5,
		"race_start_interval": [0, 10],
		"torso_w": 30.0,
		"torso_h": 60.0,
		"scene_anchor_x": 1.0,
		"scene_anchor_y": 2.0,
		"method": "test",
		"warnings": [],
	}
	torso_box_coords_io.write_torso_box_coords(str(coords_path), {
		"video_identity": _current_video_identity(),
		"race_start": race_start,
		"solve_complete": True,
		"solved_intervals": {},
	})
	assert torso_box_coords_io.load_torso_box_coords(str(coords_path))["race_start"] == race_start
	with pytest.raises(RuntimeError, match="race_start is incomplete"):
		torso_box_coords_io.write_torso_box_coords(str(tmp_path / "bad.npz"), {
			"video_identity": _current_video_identity(),
			"race_start": {"race_start_frame": 5},
			"solve_complete": True,
			"solved_intervals": {},
		})


#============================================
def test_torso_box_coords_round_trips_pre_race_without_directional_paths(
	tmp_path: pathlib.Path,
) -> None:
	"""Pre-race synthesis persists its blended path without invented passes."""
	coords_path = tmp_path / "coords.npz"
	path = _artifact_path(10.0, 1.0)
	torso_box_coords_io.write_torso_box_coords(str(coords_path), {
		"video_identity": _current_video_identity(),
		"solve_complete": False,
		"solved_intervals": {
			"pre_race": {
				"start_frame": 0,
				"end_frame": 0,
				"blended_path": path,
				"source": "pre_race_reference",
			},
		},
	})
	loaded = torso_box_coords_io.load_torso_box_coords(str(coords_path))
	interval = loaded["solved_intervals"]["pre_race"]
	assert interval["forward_path"] is None
	assert interval["backward_path"] is None
	assert interval["blended_path"][0]["cx"] == 10
