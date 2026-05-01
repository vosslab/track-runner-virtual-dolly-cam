"""Single source of truth for track-runner schema version and policy.

Everything related to "what version is this artifact" or "does this
version affect solved geometry" lives here. Per contract C9, there is
exactly one SCHEMA_VERSION authority in the codebase; this module is
that authority. Do not introduce parallel version constants in any
other module (no BLOB_OBSERVER_VERSION, no CACHE_VERSION, no
OBSERVER_VERSION). If a stored artifact needs versioning, it goes
under SCHEMA_VERSION here.

Public surface:
  - SCHEMA_VERSION: the unified version constant.
  - GEOMETRY_AFFECTING_SCHEMAS: set of schema versions that altered
    solved-geometry semantics.
  - latest_geometry_affecting_schema(): helper for fingerprint code.
  - SUPPORTED_ARTIFACT_SCHEMAS: per-artifact compatibility table.
  - is_supported_artifact_schema(artifact, version): central readability
    check; loaders ask this rather than testing == current.
"""

#============================================
# the one and only schema version constant
SCHEMA_VERSION = 9

#============================================
# Schema versions that altered solved-geometry semantics. Membership is
# the contract: present = a bump that requires invalidating geometry
# from before this version; absent = metadata-only bump, geometry caches
# stay valid across this version line. v4 and v5 are intentionally
# absent because they were metadata-only (see TR_SCHEMA_VERSION_HISTORY).
# v6 enters the set because the DoG band-pass added to observe_blob_at
# changes the magnitude landscape extract_frame_blobs sees.
# v7 enters the set because the cache namespace split by stage
# invalidates the unified v6 cache; no legacy migration path is provided
# (Hermite recompute is cheap and avoids fingerprint ambiguity).
# v8 enters the set because the unified torso_box_coords.npz artifact
# was introduced.
# v9 enters the set because the residual-motion heat map window resolution
# changed from fixed-frame-count (half_window=4) to adaptive (window_seconds).
# This alters the motion-cue observation semantics; cached intervals using the
# prior frame-count window are invalidated.
GEOMETRY_AFFECTING_SCHEMAS: set = {3, 6, 7, 8, 9}


#============================================
def latest_geometry_affecting_schema() -> int:
	"""Highest geometry-affecting schema <= SCHEMA_VERSION.

	Used by the geometry-cache fingerprint so that metadata-only schema
	bumps slide past the fingerprint check and old solved geometry stays
	reusable. Geometry-affecting bumps invalidate cached entries
	naturally because the returned integer changes.

	Returns:
		The largest member of GEOMETRY_AFFECTING_SCHEMAS that is
		<= SCHEMA_VERSION.
	"""
	return max(v for v in GEOMETRY_AFFECTING_SCHEMAS if v <= SCHEMA_VERSION)


#============================================
# Per-artifact readability table. A schema bump records which schema
# wrote the artifact, but does NOT automatically make older artifacts
# unusable -- C9 explicitly states this. The set per artifact lists
# every schema version whose on-disk layout this code can still read
# (with migration applied where shape differs). Adding the new
# SCHEMA_VERSION to each artifact's set is the deliberate "we
# considered the layout impact" step on every bump.
SUPPORTED_ARTIFACT_SCHEMAS: dict = {
	# diagnostics JSON: shape was migrated from flat (v2) to nested
	# (v3+) at load time; v3-v9 share the nested shape.
	"diagnostics": {2, 3, 4, 5, 6, 7, 8, 9},
	# torso_box_coords.npz: unified artifact (v8+). Layout stable
	# from v8 onward.
	"torso_box_coords": {8, 9},
}


def is_supported_artifact_schema(artifact: str, version: int) -> bool:
	"""Return True iff `version` is a readable schema for `artifact`.

	Loaders should call this instead of testing equality against
	SCHEMA_VERSION. A bump that only changes semantics (not on-disk
	layout) should add the new version to the artifact's set so older
	files keep loading; a bump that changes layout adds the new version
	and may drop unsupported older versions in the same change.

	Args:
		artifact: One of the keys of SUPPORTED_ARTIFACT_SCHEMAS.
		version: Integer schema version read from the artifact.

	Returns:
		True if the version is in the artifact's supported set.
	"""
	return version in SUPPORTED_ARTIFACT_SCHEMAS[artifact]
