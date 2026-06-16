"""Anti-drift tripwire for interval fingerprint allow-list.

Fingerprint allow-list (official-schema-only rule from the plan):
  1. seed-pair start/end FRAME INDICES
  2. human-authored SOURCE-frame seed geometry (cx, cy, w, h of each endpoint)
  3. the approved geometry-affecting schema tag (`tr_schema.latest_geometry_affecting_schema()`)

Nothing else may enter the key: bin_factor, solver mode, stage, blob constants,
processed dims, and performance settings are all excluded. Any coder who adds a
non-schema parameter to the key will see failures in this file and should read
`docs/TR_SCHEMA_VERSION_HISTORY.md` (Reuse identity rule) before proceeding.

Shape modeled on `tests/test_tr_schema_version_single_source.py` so a reviewer
looking at one gate finds the same pattern in the other.
"""

# Standard Library
import inspect

# local repo modules
import tr_schema
import interval_fingerprint


#============================================
def _make_seed(frame_index: int, cx: float, cy: float, w: float, h: float) -> dict:
	"""Build a minimal seed dict for fingerprint testing.

	Args:
		frame_index: Frame number in SOURCE space.
		cx: Center x of torso box in SOURCE pixels.
		cy: Center y of torso box in SOURCE pixels.
		w: Torso box width in SOURCE pixels.
		h: Torso box height in SOURCE pixels.

	Returns:
		Seed dict with the fields that interval_fingerprint reads.
	"""
	return {
		"frame_index": frame_index,
		"cx": cx,
		"cy": cy,
		"w": w,
		"h": h,
		"status": "visible",
		"pass": 1,
	}


#============================================
def test_build_geometry_tag_total_parameter_count() -> None:
	"""Shape gate: build_geometry_tag has zero parameters total (required or optional).

	Catches any reintroduction of parameters such as bin_factor=None that would
	re-open the door to bin-dependent keys. The geometry tag is a pure function of
	the schema and accepts no caller input at all.

	If this test fails, read the Reuse identity rule in
	docs/TR_SCHEMA_VERSION_HISTORY.md before adding any parameter to
	build_geometry_tag.
	"""
	sig = inspect.signature(interval_fingerprint.build_geometry_tag)
	total_params = len(sig.parameters)
	assert total_params == 0, (
		f"build_geometry_tag has {total_params} parameter(s): "
		f"{list(sig.parameters.keys())}. "
		f"All parameters -- required or optional -- are prohibited. "
		f"The geometry tag is a pure function of the schema; no caller input "
		f"is accepted. See the Reuse identity rule in docs/TR_SCHEMA_VERSION_HISTORY.md."
	)



#============================================
def test_compute_interval_fingerprint_parameter_count() -> None:
	"""Shape gate: compute_interval_fingerprint accepts exactly two parameters.

	Catches any future reintroduction of an optional parameter such as
	bin_factor=1 that would silently re-open the door to bin-dependent keys.
	The function signature must remain (seed_start, seed_end) with no optional
	parameters.

	If this test fails, read the Reuse identity rule in
	docs/TR_SCHEMA_VERSION_HISTORY.md before adding any parameter to
	compute_interval_fingerprint.
	"""
	sig = inspect.signature(interval_fingerprint.compute_interval_fingerprint)
	total_params = len(sig.parameters)
	assert total_params == 2, (
		f"compute_interval_fingerprint has {total_params} parameter(s): "
		f"{list(sig.parameters.keys())}. "
		f"Expected exactly two parameters: seed_start, seed_end. "
		f"Optional parameters such as bin_factor are prohibited because they "
		f"re-open the door to bin-dependent fingerprints. "
		f"See the Reuse identity rule in docs/TR_SCHEMA_VERSION_HISTORY.md."
	)


#============================================
def test_geometry_tag_encodes_schema_version() -> None:
	"""Behavior check: the geometry tag encodes the geometry-affecting schema.

	Confirms the tag format carries the value from
	tr_schema.latest_geometry_affecting_schema() so a schema bump that affects
	geometry produces a different tag and invalidates cached intervals.
	"""
	geom_v = tr_schema.latest_geometry_affecting_schema()
	tag = interval_fingerprint.build_geometry_tag()
	expected_fragment = f"schema_v{geom_v}"
	assert expected_fragment in tag, (
		f"build_geometry_tag() returned '{tag}' but expected it to contain "
		f"'{expected_fragment}' (geometry-affecting schema v{geom_v}). "
		f"The tag must encode the geometry-affecting schema version."
	)


#============================================
def test_fingerprint_sensitive_to_source_seed_geometry() -> None:
	"""Behavioral sensitivity: redrawing a box at the same frame produces a different key.

	The user re-annotating a seed at the same frame_index must invalidate the
	cached interval so the re-drawn box is not silently reused. This guards
	against drift toward (start_frame, end_frame)-only identity.
	"""
	seed_a_original = _make_seed(100, 320.0, 240.0, 80.0, 120.0)
	seed_a_redrawn  = _make_seed(100, 325.0, 242.0, 82.0, 122.0)  # same frame, different box
	seed_b = _make_seed(300, 450.0, 270.0, 76.0, 115.0)

	key_original = interval_fingerprint.compute_interval_fingerprint(seed_a_original, seed_b)
	key_redrawn  = interval_fingerprint.compute_interval_fingerprint(seed_a_redrawn, seed_b)

	assert key_original != key_redrawn, (
		"Fingerprint did not change when the start seed box was redrawn at the "
		"same frame index. The key must encode SOURCE seed geometry (cx, cy, w, h) "
		"so user re-annotations invalidate stale intervals."
	)


#============================================
def test_fingerprint_sensitive_to_frame_index() -> None:
	"""Behavioral sensitivity: the same box at a different frame produces a different key.

	If frame_index were dropped from the key, two intervals at different frames
	but with identical geometry would share a fingerprint and reuse each other's
	solved result. This test pins frame index as a required key component.
	"""
	seed_a_frame_100 = _make_seed(100, 320.0, 240.0, 80.0, 120.0)
	seed_a_frame_101 = _make_seed(101, 320.0, 240.0, 80.0, 120.0)  # same box, different frame
	seed_b = _make_seed(300, 450.0, 270.0, 76.0, 115.0)

	key_100 = interval_fingerprint.compute_interval_fingerprint(seed_a_frame_100, seed_b)
	key_101 = interval_fingerprint.compute_interval_fingerprint(seed_a_frame_101, seed_b)

	assert key_100 != key_101, (
		"Fingerprint was identical for seeds at different frame indices with the "
		"same box geometry. Frame index must be encoded in the key."
	)


#============================================
def test_fingerprint_stable_value() -> None:
	"""Stability: a fixed seed pair always produces the same fingerprint format.

	Confirms the key embeds the geometry-affecting schema tag so the fingerprint
	is both stable (same pair -> same key) and invalidating (schema bump -> new tag).
	This is not a hardcoded-value assertion; it checks the FORMAT (tag present,
	frame indices present) rather than the exact string.
	"""
	seed_a = _make_seed(10, 100.0, 200.0, 60.0, 90.0)
	seed_b = _make_seed(90, 180.0, 210.0, 62.0, 92.0)
	key = interval_fingerprint.compute_interval_fingerprint(seed_a, seed_b)

	# key must contain the geometry tag produced by build_geometry_tag
	geom_tag = interval_fingerprint.build_geometry_tag()
	assert geom_tag in key, (
		f"Expected geometry tag '{geom_tag}' not found in fingerprint '{key}'. "
		f"The key must carry the geometry-affecting schema tag."
	)
