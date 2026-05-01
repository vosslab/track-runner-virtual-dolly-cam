"""Low-level interval fingerprint and seed-filter helpers.

Holds the cache-key primitives (`SOLVER_FINGERPRINT_TAG`,
`compute_interval_fingerprint`) and the seed-filter
(`filter_usable_seeds_sorted`) that callers use to compute identical
interval cache keys.

This module exists so the queue driver (`solve_queue`) and refine-mode
CLI can depend on the fingerprint helpers without pulling in the heavy
`interval_solver` module. See `docs/TRACK_RUNNER_DESIGN.md` for the
separation-of-concerns rationale.

Scope lock: fingerprinting + seed-filter ONLY. Do not grow this module
into a junk drawer of generic interval utilities.
"""

# local repo modules
import tr_schema
import state_io


#============================================
# Fingerprint tags.
#
# The unified geometry tag per contract C9 encodes the latest geometry-
# affecting schema version. An interval's identity is determined by its
# seed-pair geometry (frame indices and positions) plus schema version.
# How the interval was solved (hermite vs blob propagator) is metadata
# on the result, not part of the cache key.
#
# * GEOMETRY_TAG -- the unified cache-key suffix, keyed off the latest
#   geometry-affecting schema (from tr_schema.GEOMETRY_AFFECTING_SCHEMAS).
#   Format: `schema_v<N>` where N is the geometry-affecting schema version.
#
# * SOLVER_FINGERPRINT_TAG -- the informational/telemetry tag. Includes
#   GEOMETRY_TAG plus full `/schema/<SCHEMA_VERSION>`. Used for diagnostics
#   headers and log lines. NEVER used as a cache key.
#
# Cache invalidation rules:
#   - Adding a version to GEOMETRY_AFFECTING_SCHEMAS (e.g. for an
#     observer or solver algorithm change) bumps GEOMETRY_TAG.
#   - Bumping SCHEMA_VERSION alone (without adding to the affecting set)
#     is metadata-only and does not invalidate the tag.
# Per contract C9, do NOT introduce parallel version constants
# (BLOB_OBSERVER_VERSION, etc.) to bypass this scheme.

def build_geometry_tag() -> str:
	"""Build the unified geometry cache-key tag.

	Encodes only the latest geometry-affecting schema version. Tuning
	blob-snap constants or changing which propagator ran does not change
	the tag -- only a real geometry-affecting schema bump does.

	bin_factor is intentionally NOT part of this tag: the interval
	fingerprint is a contract on per-frame source-frame outputs (the
	final torso boxes), and per-frame computations cross the bin
	boundary independently inside camera_motion and residual_motion,
	upscaling back to source-frame before the interval solver consumes
	them. Bin participates in the camera-motion `config_hash` (which
	caches per-frame phase-correlate output) but not in interval cache
	keys, so changing `--bin` between runs reuses the interval cache.

	Returns:
		Geometry tag string: `schema_v<N>`.
	"""
	geom_v = tr_schema.latest_geometry_affecting_schema()
	tag = f"schema_v{geom_v}"
	return tag


GEOMETRY_TAG = build_geometry_tag()


def build_solver_fingerprint_tag() -> str:
	"""Build the full informational tag (geometry + schema).

	Use for diagnostics headers and telemetry, NOT cache keys.

	Returns:
		Full tag including `/schema/<SCHEMA_VERSION>`.
	"""
	tag = f"{GEOMETRY_TAG}/schema/{tr_schema.SCHEMA_VERSION}"
	return tag


SOLVER_FINGERPRINT_TAG = build_solver_fingerprint_tag()



#============================================
def compute_interval_fingerprint(
	seed_start: dict,
	seed_end: dict,
) -> str:
	"""Fingerprint wrapper that includes the unified geometry tag.

	Every caller that computes an interval cache key MUST go through this
	helper (or pass the tag to `state_io.interval_fingerprint` directly)
	so solve-mode and refine-mode cache keys line up byte-for-byte.
	Do NOT pass `SOLVER_FINGERPRINT_TAG` here -- that tag carries
	schema-version metadata and would couple cache keys to schema bumps.

	Note: bin_factor is intentionally NOT a parameter here. Per-frame
	bin-aware computations cross the source<->processed boundary inside
	camera_motion and residual_motion, upscaling back to source-frame
	before the interval solver consumes them. Interval-level cache keys
	therefore stay bin-invariant.

	Args:
		seed_start: Interval start seed dict.
		seed_end: Interval end seed dict.

	Returns:
		Fingerprint string with `||<GEOMETRY_TAG>` suffix.
	"""
	result = state_io.interval_fingerprint(
		seed_start, seed_end, solver_tag=GEOMETRY_TAG,
	)
	return result


#============================================
def migrate_legacy_fingerprints(solved: dict) -> tuple:
	"""Placeholder for legacy fingerprint migration (deprecated).

	With the unified GEOMETRY_TAG format, legacy cache-key migration is no
	longer needed. This function returns the input unchanged; callers are
	retained for compatibility but the function is a no-op.

	Args:
		solved: Mapping of fingerprint -> solved-interval result dict.

	Returns:
		Tuple `(solved, 0)` -- the input unchanged, no migrations.
	"""
	return (solved, 0)


#============================================
def _prepare_usable_seed(seed: dict) -> dict:
	"""Copy seed and set default conf=0.3 for approx seeds.

	When a runner is fully hidden, the user draws a larger approx area
	indicating the general region. This guides the solver through the
	gap but confidence is low because the exact position is unknown.

	Args:
		seed: Seed dict, possibly with status "approximate" or legacy
			"obstructed".

	Returns:
		Original seed if not approx, or a copy with conf=0.3 if
		approx and conf was not already set.
	"""
	if seed["status"] in ("approximate", "obstructed") and seed.get("conf") is None:
		prepared = dict(seed)
		# approx area, uncertain position -- lower confidence
		prepared["conf"] = 0.3
		return prepared
	return seed


#============================================
def filter_usable_seeds_sorted(seeds: list, verbose: bool = True) -> list:
	"""Filter, sort, and deduplicate seeds to their interval-endpoint form.

	This is the single source of truth for which seeds become interval
	endpoints. `solve_all_intervals` and `cli._mode_refine` both reach this
	helper (directly or via `solve_queue.plan_interval_work`) so they
	compute identical `state_io.interval_fingerprint` keys.

	Args:
		seeds: Raw seed list from the seeds file.
		verbose: If True, print WARNING lines for duplicate frame_index.

	Returns:
		List of usable-seed dicts (post `_prepare_usable_seed`), sorted by
		frame_index, deduplicated by frame_index (keeping the latest pass).
		May be empty if fewer than 2 usable seeds remain.
	"""
	# accept visible/partial/approximate unconditionally; accept legacy
	# obstructed only when a torso_box is present
	usable_seeds = [
		_prepare_usable_seed(s) for s in seeds
		if s["status"] in ("visible", "partial", "approximate")
		or (s["status"] == "obstructed" and s.get("torso_box") is not None)
	]
	if not usable_seeds:
		return []
	# sort by frame_index so consecutive pairs are adjacent
	usable_sorted = sorted(usable_seeds, key=lambda s: int(s["frame_index"]))
	# deduplicate: keep the seed from the latest pass when frames collide
	seen_frames = {}
	for seed in usable_sorted:
		fi = int(seed["frame_index"])
		if fi in seen_frames:
			existing = seen_frames[fi]
			if int(seed["pass"]) >= int(existing["pass"]):
				if verbose:
					print(f"  WARNING: duplicate seed at frame {fi}, "
						f"keeping pass {seed['pass']} over pass {existing['pass']}")
				seen_frames[fi] = seed
			else:
				if verbose:
					print(f"  WARNING: duplicate seed at frame {fi}, "
						f"keeping pass {existing['pass']} over pass {seed['pass']}")
		else:
			seen_frames[fi] = seed
	if len(seen_frames) < len(usable_sorted):
		dropped = len(usable_sorted) - len(seen_frames)
		if verbose:
			print(f"  deduplicated {dropped} seeds with duplicate frame_index values")
		usable_sorted = sorted(seen_frames.values(), key=lambda s: int(s["frame_index"]))
	return usable_sorted
