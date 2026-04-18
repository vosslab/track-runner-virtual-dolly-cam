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
def test_validator_rejects_multiple_below_one():
	# contract: torso_height_multiple must be >= 1
	cfg = {
		"track_runner": 3,
		"detection": {"model": "yolov8n", "confidence_threshold": 0.25},
		"processing": {"torso_height_multiple": 0.5},
	}
	with pytest.raises(RuntimeError, match=">= 1"):
		tr_config.validate_config(cfg)
