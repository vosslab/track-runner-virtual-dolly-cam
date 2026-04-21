"""Schema migration tests for tr_config.

Focused behavioral check: legacy crop_fill_ratio -> torso_height_multiple
is a reciprocal round-trip, and the validator rejects sub-1 multiples.
Avoids trivial identity assertions and hard-coded defaults per the
repo pytest style guide.
"""

import pytest

import tr_config


#============================================
def test_migration_is_reciprocal_round_trip():
	# round-trip invariant: torso_height_multiple == 1 / crop_fill_ratio
	cfg = {
		"track_runner": 2,
		"detection": {"model": "yolov8n", "confidence_threshold": 0.25},
		"processing": {"crop_fill_ratio": 0.25},
	}
	tr_config._migrate_crop_fill_ratio(cfg)
	assert cfg["processing"]["torso_height_multiple"] == pytest.approx(4.0)
	# the legacy key is removed so later code cannot silently read it
	assert "crop_fill_ratio" not in cfg["processing"]


#============================================
def test_load_config_auto_save_persists_migration(tmp_path):
	"""auto_save_migration=True rewrites the YAML so the deprecation
	notice never fires twice on the same file."""
	path = tmp_path / "config.yaml"
	path.write_text(
		"track_runner: 2\n"
		"detection:\n  model: yolov8n\n  confidence_threshold: 0.25\n"
		"processing:\n  crop_fill_ratio: 0.25\n"
	)
	tr_config.load_config(str(path), auto_save_migration=True)
	# on-disk: the v2 key is gone and the v3 key is present
	rewritten = path.read_text()
	assert "crop_fill_ratio" not in rewritten
	assert "torso_height_multiple" in rewritten


#============================================
def test_load_config_default_does_not_rewrite_on_disk(tmp_path):
	"""Without auto_save_migration, the on-disk file is left alone
	even when the in-memory config is migrated."""
	path = tmp_path / "config.yaml"
	original = (
		"track_runner: 2\n"
		"detection:\n  model: yolov8n\n  confidence_threshold: 0.25\n"
		"processing:\n  crop_fill_ratio: 0.25\n"
	)
	path.write_text(original)
	cfg = tr_config.load_config(str(path))
	# in-memory: migrated
	assert "torso_height_multiple" in cfg["processing"]
	# on-disk: unchanged
	assert path.read_text() == original


#============================================
def test_validator_rejects_multiple_below_one():
	# contract: torso_height_multiple must be >= 1
	cfg = {
		"track_runner": 3,
		"detection": {"model": "yolov8n", "confidence_threshold": 0.25},
		"processing": {"torso_height_multiple": 0.5},
	}
	with pytest.raises(RuntimeError, match=">= 1"):
		tr_config.validate_config(cfg)
