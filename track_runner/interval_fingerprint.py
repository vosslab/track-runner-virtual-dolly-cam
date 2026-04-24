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
import state_io
import velocity_model
import residual_motion
import scoring


#============================================
# Solver fingerprint tag. Baked into every `state_io.interval_fingerprint`
# call so the refine cache invalidates correctly when the analytical
# propagator semantics change. Bump `residual_motion.BLOB_OBSERVER_VERSION`
# when observer behavior changes. Bump the numeric-constants suffix (the
# lowercase `a`, `b`, `vf`, `am`, `ms` fragments) when the blob-snap gate
# or blend constants in velocity_model change. Bump
# `scoring.INTERVAL_SCORE_SCHEMA_VERSION` when the interval_score schema
# changes.

def build_solver_fingerprint_tag() -> str:
	"""Build the solver fingerprint tag with current version constants.

	Tests can call this directly to verify schema-version bumps affect
	the tag. Production code reads the cached module-level constant.

	Returns:
		Fingerprint tag string.
	"""
	# Lazy import to avoid circular dependency with race_start
	import race_start

	tag = (
		f"blob_snap/{residual_motion.BLOB_OBSERVER_VERSION}"
		f"/a{velocity_model.BLOB_SNAP_ALPHA:.3f}"
		f"/slk{velocity_model.BLOB_SNAP_PATH_SLACK:.3f}"
		f"/prp{velocity_model.BLOB_SNAP_PATH_PERP_FRACTION:.3f}"
		f"/vf{velocity_model.BLOB_SNAP_VELOCITY_FLOOR:.3f}"
		f"/am{velocity_model.BLOB_SNAP_ALPHA_MAX:.3f}"
		f"/ms{velocity_model.BLOB_SNAP_MAX_SHIFT_FRACTION:.3f}"
		f"/score_schema/{scoring.INTERVAL_SCORE_SCHEMA_VERSION}"
		f"/prerace/{race_start.PRE_RACE_REFERENCE_SCHEMA_VERSION}"
	)
	return tag


SOLVER_FINGERPRINT_TAG = build_solver_fingerprint_tag()


#============================================
def compute_interval_fingerprint(seed_start: dict, seed_end: dict) -> str:
	"""Fingerprint wrapper that includes the solver tag.

	Every caller that computes an interval cache key MUST go through this
	helper (or pass `SOLVER_FINGERPRINT_TAG` to `state_io.interval_fingerprint`
	directly) so solve-mode and refine-mode cache keys line up
	byte-for-byte.

	Args:
		seed_start: Interval start seed dict.
		seed_end: Interval end seed dict.

	Returns:
		Fingerprint string.
	"""
	result = state_io.interval_fingerprint(
		seed_start, seed_end, solver_tag=SOLVER_FINGERPRINT_TAG,
	)
	return result


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
