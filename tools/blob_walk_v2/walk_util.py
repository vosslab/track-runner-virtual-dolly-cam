"""Shared generic helpers for blob_walk_v2 modules.

Small conversion and selection utilities used across walk_driver,
make_walk_html_v2, and walk_html. No dependencies on track_runner or other
local modules.
"""


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
def _evenly_spread(items: list, n: int | None) -> list:
	"""Pick n items evenly spread across items. Returns items unchanged if len <= n."""
	if n is None or len(items) <= n:
		return items
	if n == 1:
		return [items[len(items) // 2]]
	# evenly-spaced indices including first and last
	step = (len(items) - 1) / (n - 1)
	indices = [round(i * step) for i in range(n)]
	return [items[i] for i in indices]
