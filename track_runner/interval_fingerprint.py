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

# Standard Library
import re

# local repo modules
import state_io
import velocity_model
import residual_motion


#============================================
# Fingerprint tags.
#
# Two related-but-distinct tags exist on purpose:
#
# * GEOMETRY_TAG -- the cache-key suffix. Encodes everything that, when
#   changed, produces different solved geometry: blob-observer version
#   and the numeric blob-snap gate/blend constants. This is what goes
#   into solved-intervals cache keys. Schema version is deliberately NOT
#   here: per contract C8, schema bumps may be metadata-only and those
#   must not invalidate solved geometry. Migration handles legacy tails
#   that predate this split (see `migrate_legacy_fingerprints`).
#
# * SOLVER_FINGERPRINT_TAG -- the informational/telemetry tag. Same as
#   GEOMETRY_TAG plus `/schema/<SCHEMA_VERSION>`. Used for diagnostics
#   headers and log lines. NEVER used as a cache key.
#
# Bump `residual_motion.BLOB_OBSERVER_VERSION` when observer behavior
# changes. Bump the numeric constants (`a`, `slk`, `prp`, `vf`, `am`,
# `ms`) when blob-snap gate/blend behavior changes. Either of those is a
# legitimate cache invalidator. Bumping `state_io.SCHEMA_VERSION` alone
# is not.

def build_geometry_tag() -> str:
	"""Build the geometry-only cache-key tag.

	Includes blob-observer version and blob-snap numeric constants. Does
	NOT include `/schema/` -- schema version is metadata and is tracked
	separately via `SOLVER_FINGERPRINT_TAG`.

	Returns:
		Geometry tag string used as `solver_tag` to
		`state_io.interval_fingerprint` when forming cache keys.
	"""
	tag = (
		f"blob_snap/{residual_motion.BLOB_OBSERVER_VERSION}"
		f"/a{velocity_model.BLOB_SNAP_ALPHA:.3f}"
		f"/slk{velocity_model.BLOB_SNAP_PATH_SLACK:.3f}"
		f"/prp{velocity_model.BLOB_SNAP_PATH_PERP_FRACTION:.3f}"
		f"/vf{velocity_model.BLOB_SNAP_VELOCITY_FLOOR:.3f}"
		f"/am{velocity_model.BLOB_SNAP_ALPHA_MAX:.3f}"
		f"/ms{velocity_model.BLOB_SNAP_MAX_SHIFT_FRACTION:.3f}"
	)
	return tag


def build_solver_fingerprint_tag() -> str:
	"""Build the full informational tag (geometry + schema).

	Use for diagnostics headers and telemetry, NOT cache keys.

	Returns:
		Full tag including `/schema/<SCHEMA_VERSION>`.
	"""
	tag = f"{build_geometry_tag()}/schema/{state_io.SCHEMA_VERSION}"
	return tag


GEOMETRY_TAG = build_geometry_tag()
SOLVER_FINGERPRINT_TAG = build_solver_fingerprint_tag()

# Known-compatible legacy cache-key tails. Each entry is a full
# `solver_tag` string that was used in older code but produces
# geometry identical to the current build. Load-time migration
# rewrites cache keys ending in these tails to the current
# `GEOMETRY_TAG`. Do not add a tail here unless the blob_snap
# constants match current code byte-for-byte.
COMPATIBLE_LEGACY_TAILS = frozenset({
	# 2026-04-23: schema 4, pre-C8-unification. `score_schema` and
	# `prerace` were independent metadata axes appended to the
	# blob_snap prefix; neither affected geometry.
	GEOMETRY_TAG + "/score_schema/4/prerace/4",
})

# Matches any pure `schema/<int>` suffix, e.g. `schema/5`, `schema/12`.
_SCHEMA_ONLY_SUFFIX_RE = re.compile(r"^schema/\d+$")


#============================================
def compute_interval_fingerprint(seed_start: dict, seed_end: dict) -> str:
	"""Fingerprint wrapper that includes the geometry tag.

	Every caller that computes an interval cache key MUST go through this
	helper (or pass `GEOMETRY_TAG` to `state_io.interval_fingerprint`
	directly) so solve-mode and refine-mode cache keys line up byte-for-
	byte. Do NOT pass `SOLVER_FINGERPRINT_TAG` here -- that tag carries
	schema-version metadata and would couple cache keys to schema bumps.

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
	"""Rewrite geometry-compatible legacy cache keys to the current tag.

	On-disk cache keys predating the geometry/schema tag split carry
	schema-metadata in their suffix (e.g. `/schema/5`, or the older
	pre-unification `/score_schema/4/prerace/4`). When the blob_snap
	portion matches current code, the stored geometry is identical to
	what we would compute now; only the trailing metadata differs.
	This helper rewrites those keys to use the current `GEOMETRY_TAG`
	so the next `plan_interval_work` hits the cache.

	Keys with an unknown tail or with a blob_snap prefix that does not
	match current code are left alone and will be treated as cache
	misses -- that is the correct behavior when geometry actually
	changed.

	Args:
		solved: Mapping of fingerprint -> solved-interval result dict.

	Returns:
		Tuple `(migrated_dict, migrated_count)`. `migrated_dict` is a
		new dict; caller may replace the original. `migrated_count` is
		the number of keys rewritten.
	"""
	migrated = {}
	count = 0
	geom_prefix = GEOMETRY_TAG + "/"
	for key, value in solved.items():
		parts = key.rsplit("||", 1)
		if len(parts) != 2:
			migrated[key] = value
			continue
		body, tail = parts
		# already on the current tag: no rewrite needed.
		if tail == GEOMETRY_TAG:
			migrated[key] = value
			continue
		# explicit legacy tails first (pre-split schema-axis variants).
		if tail in COMPATIBLE_LEGACY_TAILS:
			migrated[body + "||" + GEOMETRY_TAG] = value
			count += 1
			continue
		# tails that share our geometry prefix and only add a pure
		# `schema/<N>` suffix are metadata-only and migratable. This
		# covers `/schema/5` plus any future schema bumps.
		if tail.startswith(geom_prefix):
			suffix = tail[len(geom_prefix):]
			if _SCHEMA_ONLY_SUFFIX_RE.match(suffix):
				migrated[body + "||" + GEOMETRY_TAG] = value
				count += 1
				continue
		# unknown or geometry-incompatible tail: leave as-is, will
		# fall through as a cache miss.
		migrated[key] = value
	return (migrated, count)


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
