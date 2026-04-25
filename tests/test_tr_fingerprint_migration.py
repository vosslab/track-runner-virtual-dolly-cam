"""Tests for interval_fingerprint.migrate_legacy_fingerprints.

Locks the geometry/schema tag split: cache keys that only differ in
trailing schema metadata must migrate to the current `GEOMETRY_TAG` so
schema bumps do not force re-solves. Keys whose blob_snap portion does
not match current geometry must be left alone.
"""

# local repo modules
import interval_fingerprint


#============================================
def _make_body() -> str:
	"""Return a canonical fingerprint body (the pre-`||` portion)."""
	body = (
		"100|10.00|20.00|30.00|40.00|"
		"200|50.00|60.00|70.00|80.00"
	)
	return body


#============================================
def test_migrates_pure_schema_suffix():
	"""Tails of the form GEOMETRY_TAG + /schema/<N> must migrate."""
	body = _make_body()
	legacy_tail = interval_fingerprint.GEOMETRY_TAG + "/schema/5"
	legacy_key = body + "||" + legacy_tail
	solved = {legacy_key: {"start_frame": 100, "end_frame": 200}}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 1
	expected_key = body + "||" + interval_fingerprint.GEOMETRY_TAG
	assert expected_key in migrated
	assert legacy_key not in migrated


#============================================
def test_migrates_pre_unification_v4_tail():
	"""The explicit v4 pre-unification tail must migrate."""
	body = _make_body()
	legacy_tail = interval_fingerprint.GEOMETRY_TAG + "/score_schema/4/prerace/4"
	legacy_key = body + "||" + legacy_tail
	solved = {legacy_key: {"start_frame": 100, "end_frame": 200}}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 1
	assert body + "||" + interval_fingerprint.GEOMETRY_TAG in migrated


#============================================
def test_current_tag_is_unchanged():
	"""Keys already on GEOMETRY_TAG are untouched (migrated_count == 0)."""
	body = _make_body()
	current_key = body + "||" + interval_fingerprint.GEOMETRY_TAG
	solved = {current_key: {"start_frame": 100, "end_frame": 200}}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 0
	assert migrated == solved


#============================================
def test_geometry_incompatible_tail_is_left_alone():
	"""Keys with an unknown tail prefix must not migrate.

	A tag from a future or foreign code path that we do not recognize
	must be left as-is; the cache miss is the correct outcome.
	"""
	body = _make_body()
	# completely unrecognized prefix that is not blob_snap or geometry_schema
	foreign_tail = "unknown_tag/zz9/foo/schema/5"
	foreign_key = body + "||" + foreign_tail
	solved = {foreign_key: {"start_frame": 100, "end_frame": 200}}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 0
	assert foreign_key in migrated


#============================================
def test_legacy_blob_snap_v1_migrates_to_geometry_schema_v3():
	"""Legacy blob_snap/v1 entries are rewritten to geometry_schema_v3.

	Locks the C9 unification migration: pre-unification keys land in
	the unified namespace at the schema version that was current when
	blob_snap/v1 ruled.
	"""
	body = _make_body()
	legacy_tail = (
		"blob_snap/v1/a0.600/slk0.500/prp0.750"
		"/vf1.500/am0.500/ms0.500/schema/5"
	)
	legacy_key = body + "||" + legacy_tail
	solved = {legacy_key: {"start_frame": 100, "end_frame": 200}}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 1
	# the migrated key body is preserved, and the new tail starts with
	# geometry_schema_v3, with the trailing /schema/<N> stripped.
	migrated_keys = [k for k in migrated if k != legacy_key]
	new_key = migrated_keys[0]
	new_tail = new_key.split("||", 1)[1]
	assert new_tail.startswith("geometry_schema_v3/")
	assert "/schema/" not in new_tail


#============================================
def test_migrated_v3_entry_does_not_match_current_tag():
	"""A migrated v3 entry does NOT collide with the current geometry tag.

	v6 is geometry-affecting; the migrated v3-era key must fall through
	as a cache miss so the interval is re-solved under DoG.
	"""
	body = _make_body()
	legacy_tail = "blob_snap/v1/a0.600/slk0.500/prp0.750/vf1.500/am0.500/ms0.500"
	legacy_key = body + "||" + legacy_tail
	solved = {legacy_key: {"start_frame": 100, "end_frame": 200}}
	migrated, _ = interval_fingerprint.migrate_legacy_fingerprints(solved)
	# the migrated key must not equal any key produced by the current
	# fingerprint helper for the same seed pair.
	for key in migrated:
		assert not key.endswith("||" + interval_fingerprint.GEOMETRY_TAG)


#============================================
def test_mixed_dict_partial_migration():
	"""Mixed legacy + current dict: only legacy keys migrate."""
	body_a = "10|1.00|1.00|1.00|1.00|20|2.00|2.00|2.00|2.00"
	body_b = "30|3.00|3.00|3.00|3.00|40|4.00|4.00|4.00|4.00"
	current = body_a + "||" + interval_fingerprint.GEOMETRY_TAG
	legacy = body_b + "||" + interval_fingerprint.GEOMETRY_TAG + "/schema/5"
	solved = {
		current: {"start_frame": 10, "end_frame": 20},
		legacy: {"start_frame": 30, "end_frame": 40},
	}
	migrated, count = interval_fingerprint.migrate_legacy_fingerprints(solved)
	assert count == 1
	assert current in migrated
	assert legacy not in migrated
	assert body_b + "||" + interval_fingerprint.GEOMETRY_TAG in migrated
