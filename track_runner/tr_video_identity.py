"""tr_video_identity.py

Video identity fingerprinting for track_runner data files.

Builds a metadata-based identity block from video probe info and file
size, and compares stored identity against current video to detect
mismatches. Identity is heuristic (metadata-based, not content-hashed).

Policy per contract C12.3: video_identity is a metadata sanity block.
Blocking mismatches indicate the persisted data was computed against
a different video (different resolution, frame count, or frame rate).
Informational mismatches are cosmetic (file rename, container remux);
they are warnings only and never reject a load.
"""

# Standard Library
import os

#============================================
# Categorization rules. Each entry is (field_name, kind, tolerance).
# kind="exact" requires equality; kind="tol" requires |stored-current| <= tol.
# To add a new field, append one line to the right table -- the comparison
# loop reads from these tables and never grows.
_BLOCKING_RULES = (
	("frame_count", "exact", None),
	("width", "exact", None),
	("height", "exact", None),
	("fps", "tol", 0.01),
)
_INFORMATIONAL_RULES = (
	("basename", "exact", None),
	("size_bytes", "exact", None),
	("duration_s", "tol", 0.5),
)


def _check_rule(stored: dict, current: dict, field: str, kind: str, tol: float) -> str | None:
	"""Apply one comparison rule; return mismatch message or None.

	Both `stored` and `current` are expected to carry every field declared
	by the rules tables. Missing keys raise `KeyError` -- per repo style we
	do not silently paper over absent required data.
	"""
	stored_val = stored[field]
	current_val = current[field]
	if kind == "exact":
		if stored_val != current_val:
			return f"{field}: stored={stored_val}, current={current_val}"
		return None
	# tolerance comparison: float diff
	if abs(float(stored_val) - float(current_val)) > tol:
		return f"{field}: stored={stored_val}, current={current_val}"
	return None


#============================================

def make_video_identity(input_file: str, video_info: dict) -> dict:
	"""Build a video identity dict from file metadata and probe info.

	Args:
		input_file: Path to the input video file.
		video_info: Dict from _probe_video() with keys:
			width, height, fps, frame_count, duration_s.

	Returns:
		dict: Identity block with basename, size_bytes, width, height,
			fps, frame_count, duration_s.
	"""
	basename = os.path.basename(input_file)
	size_bytes = os.path.getsize(input_file)
	identity = {
		"basename": basename,
		"size_bytes": size_bytes,
		"width": video_info["width"],
		"height": video_info["height"],
		"fps": video_info["fps"],
		"frame_count": video_info["frame_count"],
		"duration_s": video_info["duration_s"],
	}
	return identity

#============================================

def compare_video_identity(stored: dict, current: dict) -> dict:
	"""Compare stored video identity against current video identity.

	Returns a dict with two lists: blocking (data-shape mismatches that affect
	solve correctness) and informational (cosmetic surface mismatches).
	An empty result means no mismatches: both lists are empty.

	Comparison rules:
		Blocking (exact match required):
			- frame_count: exact match
			- width, height: exact match
			- fps: within 0.01 tolerance
		Informational (warnings only):
			- basename: exact match
			- size_bytes: exact match
			- duration_s: within 0.5s tolerance

	Args:
		stored: Identity dict from a previously saved data file.
		current: Identity dict from the current video.

	Returns:
		dict with keys "blocking" and "informational", each containing
		a list of mismatch message strings (empty if all fields match).

	Raises:
		KeyError: if either dict is missing a field declared in the rule
			tables. video_identity dicts produced by make_video_identity
			always carry every field; a missing key indicates a tampered
			or buggy producer and is surfaced loudly per repo style.
	"""
	blocking = []
	for field, kind, tol in _BLOCKING_RULES:
		msg = _check_rule(stored, current, field, kind, tol)
		if msg is not None:
			blocking.append(msg)
	informational = []
	for field, kind, tol in _INFORMATIONAL_RULES:
		msg = _check_rule(stored, current, field, kind, tol)
		if msg is not None:
			informational.append(msg)
	return {
		"blocking": blocking,
		"informational": informational,
	}

#============================================

def summarize_mismatches(result: dict) -> str:
	"""Format comparison result dict as human-readable multi-line string.

	Takes the dict returned by compare_video_identity and produces a
	formatted string suitable for printing, with blocking entries labeled
	as blocking and informational entries labeled as informational.

	Args:
		result: Dict with "blocking" and "informational" lists from
			compare_video_identity.

	Returns:
		Multi-line string suitable for printing. Returns empty string if
		both lists are empty.
	"""
	blocking = result["blocking"]
	informational = result["informational"]

	lines = []
	if blocking:
		lines.append("blocking:")
		for msg in blocking:
			lines.append(f"  {msg}")
	if informational:
		lines.append("informational, not rejecting:")
		for msg in informational:
			lines.append(f"  {msg}")

	return "\n".join(lines)
