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
# the one and only schema version constant.
# Schema 10 is current. Method-only changes (residual stride, walker DP, cost
# weights) keep this number and use the `solve` full re-solve; see the decision
# rule in docs/TR_SCHEMA_VERSION_HISTORY.md.
SCHEMA_VERSION = 10

#============================================
# This set tracks schema versions that changed the STORED geometry artifact
# contract (field names, layout, dtype/encoding, coordinate meaning, required
# metadata). Membership is the contract: present = a bump that changed how the
# persisted geometry is read/decoded, requiring invalidation of geometry from
# before this version; absent = a bump that did not change the stored contract.
#
# Method/algorithm changes (walker Viterbi DP, cost weights, residual sampling
# stride, heat-map sampling) do NOT enter this set. They change computed values
# under the SAME stored contract, so they refresh stale values via the `solve`
# full re-solve and keep SCHEMA_VERSION fixed. The schema number is reserved for
# the stored read/validate contract, never for the algorithm that produced the
# stored values.
#
# v4 and v5 are intentionally absent because they were metadata-only (see
# TR_SCHEMA_VERSION_HISTORY).
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
# v10 enters the set because per-frame torso-box coordinate arrays switched
# from float32 to uint16 dtype per contract C12.4; the pixel-snapped int
# representation changes how the persisted data is decoded and reconstructed.
# This is the last change that altered the stored format, so it is the
# rule-consistent floor.
GEOMETRY_AFFECTING_SCHEMAS: set = {3, 6, 7, 8, 9, 10}


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
	# (v3+) at load time; v3-v10 share the nested shape.
	"diagnostics": {2, 3, 4, 5, 6, 7, 8, 9, 10},
	# torso_box_coords.npz: per-frame coordinate arrays changed dtype
	# from float32 (v8, v9) to uint16 (v10) per C12.4. Hard-cut at v10:
	# v8 and v9 are no longer readable; cache invalidation required. v10 is
	# the current floor and the last change that altered the stored format.
	# Artifacts stamped v11-v14 (method-only bumps that were rolled back) are
	# intentionally not accepted as current solver artifacts; `solve`
	# regenerates them fresh at v10. See TR_SCHEMA_VERSION_HISTORY.md.
	"torso_box_coords": {10},
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
