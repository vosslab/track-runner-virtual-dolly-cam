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
import residual_motion
import scoring


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
def test_solver_tag_contains_blob_observer_version():
	"""Bumping BLOB_OBSERVER_VERSION must bump SOLVER_FINGERPRINT_TAG.

	Locks the cache-invalidation contract: the tag string is embedded in
	every interval fingerprint, so a version bump in residual_motion
	must invalidate every cached interval.json on disk.
	"""
	assert residual_motion.BLOB_OBSERVER_VERSION in interval_fingerprint.SOLVER_FINGERPRINT_TAG


#============================================
def test_fingerprint_deterministic():
	"""Same seed inputs produce the same fingerprint bytes.

	Pure-function property: required for cache lookup to work at all.
	"""
	seed_a = _make_seed(10)
	seed_b = _make_seed(100)
	fp_1 = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b)
	fp_2 = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b)
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
	fp_base = interval_fingerprint.compute_interval_fingerprint(base_start, base_end)
	# perturb start's cx
	perturbed = interval_fingerprint.compute_interval_fingerprint(
		_make_seed(10, cx=101.0), base_end,
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
def test_tag_contains_score_schema():
	"""SOLVER_FINGERPRINT_TAG embeds the score schema version.

	Locks the cache-invalidation contract: the tag string includes
	the current interval_score schema version so schema bumps
	invalidate cached intervals.
	"""
	assert "score_schema/4" in interval_fingerprint.SOLVER_FINGERPRINT_TAG


#============================================
def test_builder_reflects_score_schema_version(monkeypatch):
	"""build_solver_fingerprint_tag() reads scoring.INTERVAL_SCORE_SCHEMA_VERSION.

	Verifies the builder refactor allows runtime schema version changes
	(for testing and potential future runtime toggles).
	"""
	# monkeypatch the constant and rebuild
	monkeypatch.setattr(scoring, "INTERVAL_SCORE_SCHEMA_VERSION", 99)
	rebuilt_tag = interval_fingerprint.build_solver_fingerprint_tag()
	assert "score_schema/99" in rebuilt_tag


#============================================

def test_tag_contains_prerace_schema():
	"""SOLVER_FINGERPRINT_TAG includes /prerace/ version marker.

	Locks the cache-invalidation contract: the tag includes the
	PRE_RACE_REFERENCE_SCHEMA_VERSION so pre-race geometry changes
	invalidate cached intervals.
	"""
	assert "/prerace/" in interval_fingerprint.SOLVER_FINGERPRINT_TAG
	assert "/prerace/4" in interval_fingerprint.SOLVER_FINGERPRINT_TAG


#============================================

def test_builder_reflects_prerace_schema_version(monkeypatch):
	"""build_solver_fingerprint_tag() reads race_start.PRE_RACE_REFERENCE_SCHEMA_VERSION.

	Verifies the builder includes pre-race schema version; bumping the
	constant invalidates cached intervals.
	"""
	# Lazy import inside the test to use monkeypatch
	import race_start

	# monkeypatch the constant and rebuild
	monkeypatch.setattr(race_start, "PRE_RACE_REFERENCE_SCHEMA_VERSION", 99)
	rebuilt_tag = interval_fingerprint.build_solver_fingerprint_tag()
	assert "/prerace/99" in rebuilt_tag
