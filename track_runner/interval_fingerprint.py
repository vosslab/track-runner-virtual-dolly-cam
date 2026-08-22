"""Low-level interval fingerprint and seed-filter helpers.

Holds the fingerprint primitives for the per-interval solved-result store
(`GEOMETRY_TAG`, `compute_interval_fingerprint`) and the seed-filter
(`filter_usable_seeds_sorted`) that callers use to compute identical interval
fingerprints.

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
# The unified geometry tag per contract C10 encodes the current readable schema
# version. An interval's identity is determined by its seed-pair geometry
# (frame indices and positions) plus schema version.
# How the interval was solved (analytical vs blob propagator) is metadata
# on the result, not part of the fingerprint key.
#
# * GEOMETRY_TAG -- the unified fingerprint suffix. Format: `schema_v<N>`
#   where N is the current readable schema version.
#   bin_factor is a per-run performance setting and stays OUT of the key
#   (stored boxes are always unbinned SOURCE-frame).
#
# Fingerprint invalidation rule: a stored-format change updates
# SCHEMA_VERSION and therefore this tag. Method changes refresh output through
# solve without changing the stored format or this key.
# Per contract C10, do NOT introduce parallel version constants
# (BLOB_OBSERVER_VERSION, etc.) to bypass this scheme.

def build_geometry_tag() -> str:
	"""Build the unified geometry fingerprint tag.

	Encodes the current readable schema version. Tuning method constants or
	changing which propagator ran does not change the tag; only a real stored
	geometry-format bump does.

	The reuse key carries exactly three inputs (seed frame indices, the
	human-authored SOURCE seed box coords, and this current schema tag).
	bin_factor is a performance setting of the current run and is NOT
	part of the key: stored torso boxes are always unbinned SOURCE-frame, so
	bin cannot be part of a durable result's identity. See the reuse-identity
	rule in `docs/TR_SCHEMA_VERSION_HISTORY.md`.

	Returns:
		Geometry tag string: `schema_v<N>`.
	"""
	tag = f"schema_v{tr_schema.SCHEMA_VERSION}"
	return tag


GEOMETRY_TAG = build_geometry_tag()


#============================================
def compute_interval_fingerprint(
	seed_start: dict,
	seed_end: dict,
) -> str:
	"""Fingerprint wrapper that includes the unified geometry tag.

	Every caller that computes an interval fingerprint MUST go through this
	helper (or pass the tag to `state_io.interval_fingerprint` directly)
	so solve-mode and refine-mode fingerprints line up byte-for-byte.
	The key is seed-pair SOURCE geometry plus the current schema
	tag only. bin_factor is NOT part of the key: stored coordinates are
	always unbinned SOURCE-frame, so the same seed pair returns an identical
	fingerprint regardless of which bin the run uses. Solve mode and refine
	mode therefore line up byte-for-byte across any bin.

	Args:
		seed_start: Interval start seed dict.
		seed_end: Interval end seed dict.

	Returns:
		Fingerprint string with `||<GEOMETRY_TAG>` suffix.
	"""
	# GEOMETRY_TAG is already the module-level result of build_geometry_tag();
	# no need to re-invoke the pure function on every call.
	geometry_tag = GEOMETRY_TAG
	result = state_io.interval_fingerprint(
		seed_start, seed_end, solver_tag=geometry_tag,
	)
	return result


#============================================
def _prepare_usable_seed(seed: dict) -> dict:
	"""Copy seed and set default conf=0.3 for approx seeds.

	When a runner is fully hidden, the user draws a larger approx area
	indicating the general region. This guides the solver through the
	gap but confidence is low because the exact position is unknown.

	Args:
		seed: Seed dict, possibly with status "approximate".

	Returns:
		Original seed if not approx, or a copy with conf=0.3 if
		approx and conf was not already set.
	"""
	if seed["status"] == "approximate" and seed.get("conf") is None:
		prepared = dict(seed)
		# approx area, uncertain position -- lower confidence
		prepared["conf"] = 0.3
		return prepared
	return seed


#============================================
def filter_usable_seeds_sorted(seeds: list, verbose: bool = True) -> list:
	"""Filter, sort, and deduplicate seeds to their interval-endpoint form.

	This is the single source of truth for which seeds become interval
	endpoints. `solve_all_intervals` and `modes.refine.run` both reach this
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
	# Only current canonical seed statuses can become interval endpoints.
	usable_seeds = [
		_prepare_usable_seed(s) for s in seeds
		if s["status"] in ("visible", "partial", "approximate")
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
