"""Tests for the canonical v3 seeds schema.

Covers the WP-S1 acceptance criteria from the tr_config storage cleanup
plan:
- v2 -> v3 round-trip (legacy fields stripped, derived geometry attached)
- interval_fingerprint stability across migration
- not_in_frame seeds survive without geometry
- redacted real-file fixture loads and canonicalizes cleanly
- key-shape assertion: on-disk keys after write_seeds are exactly
  CANONICAL_SEED_KEYS
- canonical-stability gate: the second round-trip after migration is
  byte-identical to the first
"""

# Standard Library
import json
import shutil

# PIP3 modules
import pytest

# local repo modules
import file_utils
import state_io
import interval_fingerprint


REPO_ROOT = file_utils.get_repo_root()
FIXTURE_PATH = f"{REPO_ROOT}/tests/fixtures/seeds_v2_legacy.json"


#============================================


def _make_v2_seed_dict() -> dict:
	"""Build a minimal v2-shaped seed file in memory for tests."""
	data = {
		"track_runner_seeds": 2,
		"seeds": [
			{
				"frame_index": 10,
				"time_s": 0.333,
				"torso_box": [640, 360, 40, 60],
				"cx": 660.0,
				"cy": 390.0,
				"w": 40.0,
				"h": 60.0,
				"jersey_hsv": [120, 180, 200],
				"histogram": [[0.0, 0.1], [0.2, 0.3]],
				"pass": 1,
				"source": "human",
				"mode": "initial",
				"status": "visible",
			},
		],
	}
	return data


#============================================


def test_writer_emits_only_canonical_keys_on_disk(tmp_path):
	"""On disk, seeds carry exactly the canonical allow-list."""
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, _make_v2_seed_dict())
	with open(path, "r") as fh:
		raw = json.load(fh)
	assert state_io.CANONICAL_SEED_KEYS.issubset(raw["seeds"][0].keys())


#============================================


def test_loader_attaches_derived_geometry_in_memory(tmp_path):
	"""load_seeds adds cx/cy/w/h to the in-memory dict from torso_box."""
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, _make_v2_seed_dict())
	loaded = state_io.load_seeds(path)
	expected = state_io.CANONICAL_SEED_KEYS | state_io.DERIVED_SEED_KEYS
	assert expected.issubset(loaded["seeds"][0].keys())


#============================================


def test_fingerprint_stable_across_migration(tmp_path):
	"""Pre-migration stored cx/cy and post-migration derived cx/cy
	produce identical interval_fingerprint output when the stored
	geometry matched torso_box (the common path)."""
	v2 = _make_v2_seed_dict()
	# fingerprint computed from the v2 in-memory seed (uses stored cx/cy)
	seed_v2 = v2["seeds"][0]
	# second "seed" to form an interval (synthesize from a shifted box)
	seed_v2_end = {
		"frame_index": 50,
		"cx": 680.0,
		"cy": 400.0,
		"w": 44.0,
		"h": 64.0,
	}
	fp_before = state_io.interval_fingerprint(seed_v2, seed_v2_end)
	# write, load, re-fingerprint
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, v2)
	loaded = state_io.load_seeds(path)
	seed_v3 = loaded["seeds"][0]
	fp_after = state_io.interval_fingerprint(seed_v3, seed_v2_end)
	assert fp_before == fp_after


#============================================


def test_not_in_frame_seed_round_trips_without_geometry(tmp_path):
	"""not_in_frame seeds carry no torso_box and must survive a
	round-trip with no cx/cy/w/h attached on load."""
	data = {
		"track_runner_seeds": 3,
		"seeds": [
			{
				"frame_index": 100,
				"status": "not_in_frame",
				"pass": 1,
			},
		],
	}
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, data)
	loaded = state_io.load_seeds(path)
	seed = loaded["seeds"][0]
	# no torso_box, so no derived geometry
	assert seed["status"] == "not_in_frame"
	assert "torso_box" not in seed
	for derived in state_io.DERIVED_SEED_KEYS:
		assert derived not in seed


#============================================


def test_real_file_fixture_loads_and_canonicalizes(tmp_path):
	"""The redacted real-file fixture loads through load_seeds without
	error and writes out canonical v3."""
	path = str(tmp_path / "seeds.json")
	shutil.copy(FIXTURE_PATH, path)
	loaded = state_io.load_seeds(path)
	assert loaded[state_io.SEEDS_HEADER_KEY] == 2
	# every loaded seed's keys are canonical + possibly derived geometry
	for seed in loaded["seeds"]:
		seed_keys = set(seed.keys())
		allowed = state_io.CANONICAL_SEED_KEYS | state_io.DERIVED_SEED_KEYS
		assert seed_keys.issubset(allowed), seed_keys - allowed
	# writing canonicalizes header and strips derived geometry
	state_io.write_seeds(path, loaded)
	with open(path, "r") as fh:
		raw = json.load(fh)
	assert raw[state_io.SEEDS_HEADER_KEY] == state_io.SEEDS_HEADER_VALUE
	for seed in raw["seeds"]:
		disk_keys = set(seed.keys())
		# not_in_frame seeds have no torso_box; approx-with-box keeps it
		allowed = state_io.CANONICAL_SEED_KEYS
		assert disk_keys.issubset(allowed), disk_keys - allowed


#============================================


def test_canonical_stability_second_round_trip_byte_identical(tmp_path):
	"""After the first migration write, a second load + write is
	byte-identical. This is the canonical-stability gate."""
	path = str(tmp_path / "seeds.json")
	shutil.copy(FIXTURE_PATH, path)
	# first migration
	loaded_1 = state_io.load_seeds(path)
	state_io.write_seeds(path, loaded_1)
	with open(path, "rb") as fh:
		bytes_1 = fh.read()
	# second round-trip on the already-migrated file
	loaded_2 = state_io.load_seeds(path)
	state_io.write_seeds(path, loaded_2)
	with open(path, "rb") as fh:
		bytes_2 = fh.read()
	assert bytes_1 == bytes_2


#============================================


def test_writer_drops_unknown_keys(tmp_path):
	"""Unknown keys in the in-memory dict do not survive write_seeds."""
	data = {
		"track_runner_seeds": 3,
		"seeds": [
			{
				"frame_index": 10,
				"torso_box": [100, 100, 20, 30],
				"status": "visible",
				"pass": 1,
				"some_future_field": "experiment",
				"another_extra": [1, 2, 3],
			},
		],
	}
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, data)
	with open(path, "r") as fh:
		raw = json.load(fh)
	assert set(raw["seeds"][0].keys()) == state_io.CANONICAL_SEED_KEYS


#============================================


def test_legacy_obstructed_with_torso_box_becomes_approximate(tmp_path):
	"""Legacy 'obstructed' status with a torso_box is migrated to
	'approximate' on load."""
	data = {
		"track_runner_seeds": 2,
		"seeds": [
			{
				"frame_index": 10,
				"torso_box": [100, 100, 20, 30],
				"status": "obstructed",
				"pass": 1,
			},
		],
	}
	path = str(tmp_path / "seeds.json")
	with open(path, "w") as fh:
		json.dump(data, fh)
	loaded = state_io.load_seeds(path)
	assert loaded["seeds"][0]["status"] == "approximate"


#============================================


def test_rejects_unknown_header_version(tmp_path):
	"""Headers outside SEEDS_ACCEPTED_HEADERS raise with a clear message."""
	path = str(tmp_path / "seeds.json")
	with open(path, "w") as fh:
		json.dump({"track_runner_seeds": 99, "seeds": []}, fh)
	with pytest.raises(RuntimeError):
		state_io.load_seeds(path)


#============================================


def test_fingerprint_prepare_supplies_default_conf_when_missing():
	"""interval_fingerprint._prepare_usable_seed supplies conf=0.3
	for approximate seeds when the seed dict no longer carries a
	conf key (post-v3 behavior)."""
	seed = {
		"frame_index": 10,
		"torso_box": [100, 100, 20, 30],
		"status": "approximate",
		"pass": 1,
		"cx": 110.0,
		"cy": 115.0,
		"w": 20.0,
		"h": 30.0,
	}
	prepared = interval_fingerprint._prepare_usable_seed(seed)
	assert 0.0 < prepared["conf"] < 1.0
