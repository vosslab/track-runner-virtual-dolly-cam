"""Pure seed-status validation shared by solving modes."""

# local repo modules
import interval_fingerprint


#============================================
def validate_usable_seeds(seeds: list[dict]) -> tuple[list[dict], int, int]:
	"""Validate that enough usable seeds exist for solving.

	Args:
		seeds: List of seed dicts.

	Returns:
		Tuple of (canonical usable seeds, visible endpoint count, partial
		endpoint count).

	Raises:
		RuntimeError: If fewer than two canonical interval endpoints exist.
	"""
	# Interval planning, solve, and refine must agree exactly about which
	# human anchors can bound an interval. This helper also sorts and resolves
	# duplicate frame entries by latest pass.
	usable_seeds = interval_fingerprint.filter_usable_seeds_sorted(seeds)
	visible_count = sum(
		1 for s in usable_seeds
		if s["status"] == "visible"
	)
	partial_count = sum(
		1 for s in usable_seeds if s["status"] == "partial"
	)
	approx_count = sum(
		1 for s in usable_seeds if s["status"] == "approximate"
	)
	not_in_frame_count = sum(
		1 for s in seeds if s["status"] == "not_in_frame"
	)
	if len(usable_seeds) < 2:
		raise RuntimeError(
			f"need at least 2 usable seed endpoints after canonical filtering; "
			f"got {len(usable_seeds)} from {len(seeds)} raw seeds"
		)
	if not_in_frame_count > 0 or approx_count > 0 or partial_count > 0:
		print(
			"  usable seed endpoint breakdown: "
			f"{visible_count} visible, {partial_count} partial, "
			f"{approx_count} approximate; "
			f"excluded {not_in_frame_count} not_in_frame"
		)
	result = (usable_seeds, visible_count, partial_count)
	return result
