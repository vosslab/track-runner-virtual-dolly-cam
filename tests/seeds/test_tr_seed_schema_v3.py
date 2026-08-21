"""Tests for the current canonical v3 seeds schema."""

# Standard Library
import json
import pathlib

# PIP3 modules
import pytest

# local repo modules
import state_io
import interval_fingerprint


#============================================


def _make_seed_dict() -> dict:
	"""Build a minimal current seed file in memory for tests."""
	data = {
		"track_runner_seeds": 3,
		"seeds": [
			{
				"frame_index": 10,
				"torso_box": [640, 360, 40, 60],
				"pass": 1,
				"status": "visible",
			},
		],
	}
	return data


def test_fingerprint_stable_across_canonical_round_trip(tmp_path: pathlib.Path) -> None:
	"""Derived current-schema geometry yields stable interval fingerprints."""
	path = str(tmp_path / "seeds.json")
	state_io.write_seeds(path, _make_seed_dict())
	seed_before = state_io.load_seeds(path)["seeds"][0]
	# second "seed" to form an interval (synthesize from a shifted box)
	seed_end = {
		"frame_index": 50,
		"cx": 680.0,
		"cy": 400.0,
		"w": 44.0,
		"h": 64.0,
	}
	fp_before = state_io.interval_fingerprint(seed_before, seed_end)
	state_io.write_seeds(path, {"track_runner_seeds": 3, "seeds": [seed_before]})
	seed_after = state_io.load_seeds(path)["seeds"][0]
	fp_after = state_io.interval_fingerprint(seed_after, seed_end)
	assert fp_before == fp_after


#============================================


def test_not_in_frame_seed_round_trips_without_geometry(tmp_path: pathlib.Path) -> None:
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


def test_writer_drops_unknown_keys(tmp_path: pathlib.Path) -> None:
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
	assert "some_future_field" not in raw["seeds"][0]
	assert "another_extra" not in raw["seeds"][0]


#============================================


def test_rejects_obsolete_header_version(tmp_path: pathlib.Path) -> None:
	"""Only the current seed header is accepted."""
	path = str(tmp_path / "seeds.json")
	with open(path, "w") as fh:
		json.dump({"track_runner_seeds": 2, "seeds": []}, fh)
	with pytest.raises(RuntimeError):
		state_io.load_seeds(path)


#============================================


def test_fingerprint_prepare_supplies_default_conf_when_missing() -> None:
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
