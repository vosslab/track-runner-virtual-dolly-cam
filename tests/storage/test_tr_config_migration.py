"""Current-schema validation tests for tr_config."""

import pytest

import tr_config


#============================================
def test_validator_rejects_multiple_below_one() -> None:
	# contract: torso_height_multiple must be >= 1
	cfg = {
		"track_runner": 3,
		"processing": {"torso_height_multiple": 0.5},
	}
	with pytest.raises(RuntimeError, match=">= 1"):
		tr_config.validate_config(cfg)
