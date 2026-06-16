"""Behavioral test for the bin-invariant interval-fingerprint contract.

Reuse identity is a contract about the persisted SOURCE-frame result, not
about how it was computed. Stored torso boxes are always unbinned SOURCE-frame
(see docs/COORDINATE_SPACES.md), so bin_factor is a per-run performance setting
and stays OUT of the fingerprint. The key carries seed-pair SOURCE geometry
plus the geometry-affecting schema tag only.

Therefore the same seed pair returns an identical fingerprint regardless of
which bin the run uses, and a legacy `/bin<B>`-tagged store key migrates to the
bin-invariant key and matches a fresh compute. This is cache-key bookkeeping
only, NOT an on-disk schema change (no SCHEMA_VERSION bump).
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
def test_fingerprint_is_bin_invariant():
	# Property: the same seed pair yields one key regardless of any bin the
	# run uses, so a solve at one bin and a refine at another reuse the
	# interval. bin_factor is no longer an argument; the key is identical.
	fp_first = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	fp_second = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	assert fp_first == fp_second


#============================================
def test_geometry_tag_has_no_bin_segment():
	# The geometry tag is schema_v<N> only; no `/bin<B>` segment rides in it.
	tag = interval_fingerprint.build_geometry_tag()
	assert "/bin" not in tag
	assert tag.startswith("schema_v")


#============================================
def test_legacy_bin_key_migrates_to_fresh_compute():
	# A stored key tagged `||schema_v<N>/bin3` strips its bin segment via
	# migrate_legacy_fingerprints and matches a freshly computed key.
	fresh_key = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	legacy_key = fresh_key + "/bin3"
	store = {legacy_key: {"start_frame": 49, "end_frame": 56}}
	migrated, n_changed = interval_fingerprint.migrate_legacy_fingerprints(store)
	assert n_changed == 1
	assert fresh_key in migrated
	assert legacy_key not in migrated


#============================================
def test_migration_no_op_on_already_bin_invariant_key():
	# A key with no `/bin<B>` suffix passes through unchanged, so a second
	# migration of an already-migrated store reports zero changes.
	fresh_key = interval_fingerprint.compute_interval_fingerprint(_SEED_A, _SEED_B)
	store = {fresh_key: {"start_frame": 49, "end_frame": 56}}
	migrated, n_changed = interval_fingerprint.migrate_legacy_fingerprints(store)
	assert n_changed == 0
	assert fresh_key in migrated
