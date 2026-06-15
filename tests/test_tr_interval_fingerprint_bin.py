"""Behavioral test for the interval-fingerprint bin-awareness contract.

WS2-cache contract (corrects a prior, wrong invariance assumption): the
production solve default now bins the walker (default bin_factor > 1 on 4K
floor@1440). bin>1 lowers the analysis resolution, so the walker's per-frame
candidate lattice and the residual-motion blobs are extracted at processed
scale and upscaled to SOURCE. The resulting solved SOURCE torso boxes are
numerically correct but NOT identical to the bin=1 solve. Reusing a bin=1
interval result under a different bin would serve geometry the new run did
not produce.

Therefore bin_factor MUST enter the interval fingerprint so a bin change
forces recompute. This is cache-key bookkeeping only, NOT an on-disk schema
change (no SCHEMA_VERSION bump).

(Camera motion has its own bin staleness rule; that is exercised in
tests/test_tr_camera_motion_bin.py.)
"""

# local repo modules
import interval_fingerprint


_SEED_A = {
	"frame_index": 49,
	"cx": 1635.0, "cy": 754.5, "w": 64.0, "h": 81.0,
	"status": "visible",
}
_SEED_B = {
	"frame_index": 56,
	"cx": 1630.5, "cy": 756.5, "w": 69.0, "h": 87.0,
	"status": "visible",
}


#============================================
def test_bin_change_invalidates_fingerprint():
	# Property: a bin change must yield a different cache key so the
	# stale-bin interval store is never reused. Same seeds, different bin.
	fp_bin1 = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B, bin_factor=1,
	)
	fp_bin2 = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B, bin_factor=2,
	)
	assert fp_bin1 != fp_bin2


#============================================
def test_same_bin_gives_stable_fingerprint():
	# Property: the key is deterministic for the same seeds + same bin, so
	# an unchanged interval under an unchanged bin is reused (no recompute).
	fp_a = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B, bin_factor=2,
	)
	fp_b = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B, bin_factor=2,
	)
	assert fp_a == fp_b


#============================================
def test_default_bin_factor_is_one():
	# Property: the no-arg default keys on bin=1 so existing bin=1 callers
	# (diagnostic tools, refine at bin=1) keep their current cache identity.
	fp_default = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B,
	)
	fp_explicit = interval_fingerprint.compute_interval_fingerprint(
		_SEED_A, _SEED_B, bin_factor=1,
	)
	assert fp_default == fp_explicit


#============================================
def test_geometry_tag_carries_bin():
	# The bin enters via the geometry tag suffix, not the seed-geometry
	# parts, so the tag string itself differs across bins.
	tag1 = interval_fingerprint.build_geometry_tag(bin_factor=1)
	tag3 = interval_fingerprint.build_geometry_tag(bin_factor=3)
	assert tag1 != tag3
