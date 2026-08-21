"""Single source of truth for track-runner schema version and policy.

Everything related to "what version is this artifact" lives here. Per contract
C9, there is exactly one SCHEMA_VERSION authority in the codebase; this module
is that authority. Do not introduce parallel version constants in other
modules. If a stored artifact needs versioning, it goes under SCHEMA_VERSION
here.

Public surface:
  - SCHEMA_VERSION: the unified version constant.
  - SUPPORTED_ARTIFACT_SCHEMAS: current readable layout for each artifact.
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
# Per-artifact readability table. Loaders accept only the current owned
# on-disk layouts. Older artifacts are regenerated rather than converted.
SUPPORTED_ARTIFACT_SCHEMAS: dict = {
	# diagnostics JSON: only the current nested layout is readable.
	"diagnostics": {SCHEMA_VERSION},
	# torso_box_coords.npz: per-frame coordinate arrays changed dtype
	# from float32 (v8, v9) to uint16 (v10) per C12.4. Hard-cut at v10:
	# v8 and v9 are no longer readable; cache invalidation required. v10 is
	# the current floor and the last change that altered the stored format.
	# Artifacts stamped v11-v14 (method-only bumps that were rolled back) are
	# intentionally not accepted as current solver artifacts; `solve`
	# regenerates them fresh at v10. See TR_SCHEMA_VERSION_HISTORY.md.
	"torso_box_coords": {SCHEMA_VERSION},
}


def is_supported_artifact_schema(artifact: str, version: int) -> bool:
	"""Return True iff `version` is the current readable schema for `artifact`.

	Loaders use this central check rather than defining independent schema
	constants. Unsupported artifact versions fail loud and are regenerated.

	Args:
		artifact: One of the keys of SUPPORTED_ARTIFACT_SCHEMAS.
		version: Integer schema version read from the artifact.

	Returns:
		True if the version is in the artifact's supported set.
	"""
	return version in SUPPORTED_ARTIFACT_SCHEMAS[artifact]
