"""Unit tests for interval fingerprinting and seed filtering.

Tests the low-level helpers `compute_interval_fingerprint` and
`filter_usable_seeds_sorted`. Both solve mode and refine mode rely on
these helpers producing identical cache keys.

Tests here check behavioral invariants only -- not specific hash bytes,
not tunable constants, not dataclass storage. Per docs/PYTHON_STYLE.md
PYTEST guidance.
"""

# local repo modules
import interval_fingerprint
import state_io
import tr_schema


#============================================
def _make_seed(frame_index: int, cx: float = 100.0, cy: float = 200.0,
		w: float = 30.0, h: float = 60.0, status: str = "visible",
		conf: float = 1.0, pass_num: int = 1, torso_box: dict = None) -> dict:
	"""Helper to build seed dicts with canonical structure."""
	seed = {
		"frame_index": frame_index,
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"status": status,
		"conf": conf,
		"pass": pass_num,
	}
	if torso_box is not None:
		seed["torso_box"] = torso_box
	return seed


#============================================
def test_solver_tag_contains_geometry_schema_version():
	"""SOLVER_FINGERPRINT_TAG embeds the latest geometry-affecting schema.

	Locks the unified-version contract (C9): cache invalidation flows
	from tr_schema.GEOMETRY_AFFECTING_SCHEMAS, not from a parallel
	BLOB_OBSERVER_VERSION constant.
	"""
	geom_v = tr_schema.latest_geometry_affecting_schema()
	expected_token = f"geometry_schema_v{geom_v}"
	assert expected_token in interval_fingerprint.SOLVER_FINGERPRINT_TAG


#============================================
def test_fingerprint_deterministic():
	"""Same seed inputs produce the same fingerprint bytes.

	Pure-function property: required for cache lookup to work at all.
	"""
	seed_a = _make_seed(10)
	seed_b = _make_seed(100)
	fp_1 = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b, stage="blob")
	fp_2 = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b, stage="blob")
	assert fp_1 == fp_2


#============================================
def test_fingerprint_changes_when_seeds_differ():
	"""Different seed inputs produce different fingerprints.

	Tests perturbation sensitivity in one assertion: if two adjacent
	seeds have different geometry, the cache key must differ. Collapses
	the three earlier cx/cy/seed_end perturbation tests.
	"""
	base_start = _make_seed(10)
	base_end = _make_seed(100)
	fp_base = interval_fingerprint.compute_interval_fingerprint(base_start, base_end, stage="blob")
	# perturb start's cx
	perturbed = interval_fingerprint.compute_interval_fingerprint(
		_make_seed(10, cx=101.0), base_end, stage="blob",
	)
	assert fp_base != perturbed


#============================================
def test_filter_rejects_unknown_status():
	"""Seeds with unrecognized status are filtered out.

	The accept list is visible/partial/approximate (plus obstructed
	when torso_box is present). Anything else is treated as not usable
	so downstream interval pairing does not see it.
	"""
	keep = _make_seed(10, status="visible")
	drop = _make_seed(20, status="something_else")
	result = interval_fingerprint.filter_usable_seeds_sorted(
		[keep, drop], verbose=False,
	)
	frames = [s["frame_index"] for s in result]
	assert 10 in frames
	assert 20 not in frames


#============================================
def test_filter_sorts_ascending_by_frame():
	"""Output is sorted by frame_index ascending, regardless of input order.

	Interval pairing iterates adjacent entries so sort order is part of
	the contract; downstream behavior breaks if it drifts.
	"""
	seeds = [_make_seed(200), _make_seed(10), _make_seed(100)]
	result = interval_fingerprint.filter_usable_seeds_sorted(seeds)
	frames = [s["frame_index"] for s in result]
	assert frames == sorted(frames)


#============================================
def test_filter_dedup_keeps_latest_pass():
	"""Duplicate frame_index entries resolve by keeping the latest pass.

	Behavior users rely on when re-annotating a frame.
	"""
	first = _make_seed(50, cx=10.0, pass_num=1)
	second = _make_seed(50, cx=999.0, pass_num=2)
	result = interval_fingerprint.filter_usable_seeds_sorted(
		[first, second], verbose=False,
	)
	# one winner at that frame, and it is the higher-pass one
	matches = [s for s in result if s["frame_index"] == 50]
	assert len(matches) == 1
	assert matches[0]["cx"] == 999.0


#============================================
def test_filter_obstructed_requires_torso_box():
	"""Obstructed seeds accepted only when torso_box is present.

	Non-obvious business rule: "obstructed" alone is skipped, but a
	legacy obstructed seed with a torso_box still counts as an anchor.
	"""
	without = _make_seed(10, status="obstructed")
	with_torso = _make_seed(20, status="obstructed",
		torso_box={"x": 0, "y": 0, "w": 10, "h": 20})
	result = interval_fingerprint.filter_usable_seeds_sorted(
		[without, with_torso], verbose=False,
	)
	frames = [s["frame_index"] for s in result]
	assert 10 not in frames
	assert 20 in frames




#============================================

def test_geometry_tag_excludes_schema_metadata():
	"""GEOMETRY_TAG (the cache-key suffix) must NOT contain /schema/.

	Policy: schema bumps that are metadata-only must not invalidate
	solved-geometry cache keys. Only geometry-affecting versioning and
	blob-snap numeric constants belong in GEOMETRY_TAG.
	"""
	assert "/schema/" not in interval_fingerprint.GEOMETRY_TAG
	assert "geometry_schema_v" in interval_fingerprint.GEOMETRY_TAG


#============================================

def test_geometry_tag_drops_legacy_blob_snap_prefix():
	"""Legacy parallel-version constant must not return."""
	assert "blob_snap/" not in interval_fingerprint.GEOMETRY_TAG


#============================================

def test_solver_fingerprint_tag_includes_schema():
	"""SOLVER_FINGERPRINT_TAG (telemetry) includes the schema version.

	The informational tag is used for diagnostics headers, so schema
	version must be visible there even though it is NOT in the cache key.
	"""
	tag = interval_fingerprint.SOLVER_FINGERPRINT_TAG
	expected = f"/schema/{state_io.SCHEMA_VERSION}"
	assert expected in tag
	assert tag.startswith(interval_fingerprint.GEOMETRY_TAG)


#============================================

def test_cache_key_stable_across_metadata_only_schema_bump(monkeypatch):
	"""A SCHEMA_VERSION bump that is NOT in GEOMETRY_AFFECTING_SCHEMAS
	must leave the geometry tag unchanged.

	Locks the unified-version contract (C9): metadata-only schema bumps
	keep existing geometry caches valid; only versions in
	GEOMETRY_AFFECTING_SCHEMAS invalidate.
	"""
	seed_a = {"frame_index": 10, "cx": 1.0, "cy": 2.0, "w": 3.0, "h": 4.0}
	seed_b = {"frame_index": 20, "cx": 5.0, "cy": 6.0, "w": 7.0, "h": 8.0}
	key_before = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b, stage="blob")
	# Pick a bumped version that is strictly higher than every member
	# of GEOMETRY_AFFECTING_SCHEMAS so the helper still returns the
	# previous max.
	bumped = max(tr_schema.GEOMETRY_AFFECTING_SCHEMAS) + 100
	monkeypatch.setattr(tr_schema, "SCHEMA_VERSION", bumped)
	monkeypatch.setattr(state_io, "SCHEMA_VERSION", bumped)
	# Blob tag is captured at import time; rebuild to simulate what
	# a fresh process would do after a metadata-only bump.
	rebuilt_hermite = interval_fingerprint.build_hermite_geometry_tag()
	rebuilt_blob = interval_fingerprint.build_blob_geometry_tag()
	assert "/schema/" not in rebuilt_hermite
	assert "/schema/" not in rebuilt_blob
	assert str(bumped) not in rebuilt_hermite
	assert str(bumped) not in rebuilt_blob
	# The telemetry tag, by contrast, MUST reflect the bump.
	rebuilt_full = interval_fingerprint.build_solver_fingerprint_tag()
	assert f"/schema/{bumped}" in rebuilt_full
	key_after = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b, stage="blob")
	assert key_before == key_after


#============================================

def test_cache_key_changes_when_schema_is_geometry_affecting(monkeypatch):
	"""Adding a higher version to GEOMETRY_AFFECTING_SCHEMAS DOES change
	the geometry tag.

	The complement of the previous test: geometry-affecting bumps are
	the lever that invalidates caches.
	"""
	original_set = set(tr_schema.GEOMETRY_AFFECTING_SCHEMAS)
	bumped = max(original_set) + 1
	monkeypatch.setattr(tr_schema, "SCHEMA_VERSION", bumped)
	monkeypatch.setattr(
		tr_schema, "GEOMETRY_AFFECTING_SCHEMAS", original_set | {bumped},
	)
	rebuilt_hermite = interval_fingerprint.build_hermite_geometry_tag()
	rebuilt_blob = interval_fingerprint.build_blob_geometry_tag()
	assert f"geometry_schema_v{bumped}" in rebuilt_hermite
	assert f"geometry_schema_v{bumped}" in rebuilt_blob
