"""Source-video geometry identity for durable track_runner artifacts.

Identity records only the frame geometry that affects coordinate persistence:
width, height, and frame_count. File names, container bytes, fps display
precision, and derived duration do not gate a solved trajectory.
"""

# Standard Library
import numbers

#============================================
# A solved geometry is valid only for this exact structural identity.
_IDENTITY_FIELDS = ("width", "height", "frame_count")


#============================================
def _validate_identity(identity: dict) -> None:
	"""Require a positive integer value for every geometry identity field."""
	if not isinstance(identity, dict):
		raise TypeError("video_identity must be a mapping")
	for field in _IDENTITY_FIELDS:
		value = identity[field]
		# ASVS 2.2.1, 15.3.5: validate the trusted geometry structure and type
		# before it controls artifact compatibility through exact comparison.
		if (not isinstance(value, numbers.Integral)
				or isinstance(value, bool) or value <= 0):
			raise ValueError(
				f"video_identity {field} must be a positive integer, got {value!r}"
			)


#============================================
def make_video_identity(input_file: str, video_info: dict) -> dict:
	"""Build the geometry identity block from a current video probe.

	Args:
		input_file: Source-video path retained for the established caller API.
		video_info: Probe mapping carrying width, height, and frame_count.

	Returns:
		A dict containing only width, height, and frame_count.
	"""
	identity = {
		"width": video_info["width"],
		"height": video_info["height"],
		"frame_count": video_info["frame_count"],
	}
	_validate_identity(identity)
	canonical_identity = {
		"width": int(identity["width"]),
		"height": int(identity["height"]),
		"frame_count": int(identity["frame_count"]),
	}
	return canonical_identity


#============================================
def compare_video_identity(stored: dict, current: dict) -> dict:
	"""Return blocking geometry mismatches between persisted and current video.

	Extra fields in legacy identity blocks are intentionally ignored: they do
	not affect frame geometry. Missing or malformed required fields fail loud.

	Returns:
		dict with one ``blocking`` list of mismatch messages.
	"""
	_validate_identity(stored)
	_validate_identity(current)
	blocking = []
	for field in _IDENTITY_FIELDS:
		if stored[field] != current[field]:
			blocking.append(
				f"{field}: stored={stored[field]}, current={current[field]}"
			)
	result = {"blocking": blocking}
	return result


#============================================
def summarize_mismatches(result: dict) -> str:
	"""Format blocking geometry mismatches for a diagnostic message."""
	blocking = result["blocking"]
	lines = []
	if blocking:
		lines.append("blocking:")
		for message in blocking:
			lines.append(f"  {message}")
	summary = "\n".join(lines)
	return summary
