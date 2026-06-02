"""Shared generic helpers for blob_walk_v2 modules.

Small conversion and selection utilities used across walk_driver,
make_walk_html_v2, and walk_html. No dependencies on track_runner or other
local modules.
"""

# Standard Library
import random


#============================================
def _to_float(val, default=None):
	"""Safely convert CSV string to float."""
	try:
		return float(val) if val and val.strip() else default
	except (ValueError, TypeError):
		return default


#============================================
def _to_int(val, default=None):
	"""Safely convert CSV string to int."""
	try:
		return int(val) if val and val.strip() else default
	except (ValueError, TypeError):
		return default


#============================================
def _to_float_or_none(val) -> float | None:
	"""Convert CSV string to float, or None if empty/invalid."""
	if val is None:
		return None
	s = str(val).strip()
	if not s:
		return None
	try:
		return float(s)
	except (ValueError, TypeError):
		return None


#============================================
#============================================
def select_random_visible(post_start: list, n: int, rng: random.Random) -> list:
	"""Pick up to n random intervals where both bracketing seeds are visible.

	Filters to intervals where both left_seed["status"] == "visible" and
	right_seed["status"] == "visible".  Excludes "partial" and "not_in_frame".
	If the filtered count is <= n, all visible intervals are returned.
	Otherwise, a random sample of size n is chosen via rng.sample, then
	sorted by left_seed frame_index for stable output ordering.

	Args:
		post_start: List of SeedToSeedInterval objects, each with left_seed and
			right_seed dicts carrying "status" and "frame_index" keys.
		n: Maximum number of intervals to return.
		rng: A random.Random instance.  Pass random.Random(seed) for
			reproducibility or random.Random(None) for a fresh sample.

	Returns:
		list: Filtered (and optionally sampled) intervals sorted by
			left_seed["frame_index"].
	"""
	# keep only intervals where BOTH seeds are strictly visible
	visible = [
		i for i in post_start
		if i.left_seed["status"] == "visible" and i.right_seed["status"] == "visible"
	]
	if len(visible) <= n:
		# all visible; sort for deterministic output order
		chosen = visible
	else:
		# random sample then sort so output order is stable
		chosen = rng.sample(visible, n)
	# sort by left frame_index for consistent ordering across runs
	chosen_sorted = sorted(chosen, key=lambda i: i.left_seed["frame_index"])
	return chosen_sorted
