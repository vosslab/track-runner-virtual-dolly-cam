"""Behavioral test for the interval-fingerprint bin-invariance contract.

Per the design: per-frame computations cross the source<->processed
boundary independently inside camera_motion and residual_motion,
upscaling back to source-frame at the boundary. The interval cache
is keyed on source-frame solver state, so changing --bin must NOT
invalidate the interval cache.

(Bin invalidation lives only on camera_motion's `config_hash`, which
caches per-frame phase-correlate output. That is exercised in
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
def test_interval_fingerprint_is_bin_invariant():
	# Property: the interval cache key is a contract on source-frame
	# seed geometry, not a contract on the per-frame computation that
	# produced the trajectory. compute_interval_fingerprint accepts
	# only seed dicts (no bin_factor parameter); changing --bin between
	# runs reuses the cache.
	fp1 = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	fp2 = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	assert fp1 == fp2


#============================================
def test_interval_fingerprint_signature_excludes_bin():
	# Static contract: the public callable accepts exactly two
	# positional seed args. A future regression that re-introduces
	# bin_factor (or processed dims) into the cache key would surface
	# here as a failed call.
	import inspect
	sig = inspect.signature(interval_fingerprint.compute_interval_fingerprint)
	param_names = list(sig.parameters)
	assert param_names == ["seed_start", "seed_end"]
