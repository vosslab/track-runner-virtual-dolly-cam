"""Single source of truth for track-runner schema version and policy.

Everything related to "what version is this artifact" lives here. Per contract
C10, there is exactly one SCHEMA_VERSION authority in the codebase; this module
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
# Schema 15 is the explicitly human-approved persistence change under C10.
# Rolled-back method-only artifacts carry stamps 11-14, but are deliberately
# unreadable: the v15 layout stores per-interval confidence and conditionally
# stores the raw paths.  See docs/TR_SCHEMA_VERSION_HISTORY.md.
SCHEMA_VERSION = 15

#============================================
# Per-artifact readability table. Loaders accept only the current owned
# on-disk layouts. Older artifacts are regenerated rather than converted.
SUPPORTED_ARTIFACT_SCHEMAS: dict = {
	# diagnostics JSON: only the current nested layout is readable.
	"diagnostics": {SCHEMA_VERSION},
	# torso_box_coords.npz: v15 keeps uint16 SOURCE-space coordinates, adds
	# uint8 per-frame raw-pass confidence, and omits both raw paths when their
	# difference does not survive uint16 quantization. Hard-cut at v15: older
	# artifacts, including rolled-back v11-v14 stamps, must be re-solved.
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
