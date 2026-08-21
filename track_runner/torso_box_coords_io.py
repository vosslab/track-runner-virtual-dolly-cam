"""Read and write SOURCE-space torso-box coordinate NPZ artifacts."""

# Standard Library
import json
import os
import tempfile

# PIP3 modules
import numpy

# local repo modules
import common_tools.coord_space as coord_space
import tr_schema

#============================================

# Header for the in-memory interval dictionary. The NPZ artifact carries its
# schema version independently in its `schema_version` array.
INTERVALS_HEADER_KEY = "track_runner_intervals"
INTERVALS_HEADER_VALUE = 2


#============================================

def _write_npz_atomic(path: str, arrays: dict) -> None:
	"""Write a dict of numpy arrays to an NPZ file atomically.

	Args:
		path: Target NPZ file path.
		arrays: Dict of {key: numpy.ndarray} to persist.
	"""
	dir_path = os.path.dirname(os.path.abspath(path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_path, **arrays)
		# numpy.savez appends .npz if the temp did not already end in .npz.
		real_tmp = tmp_path
		if not tmp_path.endswith(".npz"):
			real_tmp = tmp_path + ".npz"
		os.replace(real_tmp, path)
	except Exception:
		for candidate in (tmp_path, tmp_path + ".npz"):
			if os.path.exists(candidate):
				os.unlink(candidate)
		raise


#============================================

def peek_torso_box_coords_schema(path: str) -> int | None:
	"""Read the schema version from a torso_box_coords.npz without validation.

	Args:
		path: Path to the torso_box_coords.npz file.

	Returns:
		Schema version, or None if the file or key is unavailable.
	"""
	if not os.path.isfile(path):
		return None
	with numpy.load(path, allow_pickle=False) as npz:
		if "schema_version" not in npz.files:
			return None
		return int(npz["schema_version"])


#============================================

def load_torso_box_coords(path: str) -> dict:
	"""Load a unified SOURCE-space torso_box_coords.npz artifact.

	Pre-race intervals persist only their blended arrays and reload with both
	directional paths set to None. Regular intervals must have complete FWD and
	BWD array groups.

	Args:
		path: Path to the torso_box_coords.npz file.

	Returns:
		Solved interval data, video identity when available, and solve status.

	Raises:
		RuntimeError: If the schema is unsupported or persisted data is corrupt.
	"""
	if not os.path.isfile(path):
		return {
			INTERVALS_HEADER_KEY: INTERVALS_HEADER_VALUE,
			"solved_intervals": {},
		}
	with numpy.load(path, allow_pickle=False) as npz:
		schema_version = int(npz["schema_version"])
		if not tr_schema.is_supported_artifact_schema(
			"torso_box_coords", schema_version,
		):
			supported = sorted(tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"])
			raise RuntimeError(
				f"torso_box_coords schema v{schema_version} is no longer supported; "
				f"expected version in {supported}. "
				f"Please re-solve the video to upgrade to schema v{max(supported)}"
			)
		manifest_bytes = bytes(npz["manifest"])
		manifest = json.loads(manifest_bytes.decode("utf-8"))
		if "video_identity" not in npz.files:
			raise RuntimeError(
				"torso_box_coords artifact is missing video_identity; re-solve"
			)
		vid_bytes = bytes(npz["video_identity"])
		video_identity = json.loads(vid_bytes.decode("utf-8"))
		if not isinstance(video_identity, dict):
			raise RuntimeError("torso_box_coords video_identity must be a mapping")
		if "solve_complete" not in npz.files:
			raise RuntimeError(
				"torso_box_coords artifact is missing solve_complete; re-solve"
			)
		solve_complete = bool(npz["solve_complete"])
		solved = {}
		for entry in manifest:
			fingerprint = entry["fingerprint"]
			idx = int(entry["array_index"])
			start_frame = int(entry["start_frame"])
			end_frame = int(entry["end_frame"])
			if end_frame < start_frame:
				raise RuntimeError(
					f"torso_box_coords interval {idx} has reversed frame bounds"
				)
			expected_length = end_frame - start_frame + 1
			cx_key = f"i{idx}_blended_cx"
			cy_key = f"i{idx}_blended_cy"
			w_key = f"i{idx}_blended_w"
			h_key = f"i{idx}_blended_h"
			blended_keys = (cx_key, cy_key, w_key, h_key)
			if not all(key in npz.files for key in blended_keys):
				raise RuntimeError(
					f"torso_box_coords interval {idx} has an incomplete blended path"
				)
			blended_path = _load_path(npz, blended_keys, expected_length)
			fwd_keys = (
				f"i{idx}_fwd_cx", f"i{idx}_fwd_cy",
				f"i{idx}_fwd_w", f"i{idx}_fwd_h"
			)
			bwd_keys = (
				f"i{idx}_bwd_cx", f"i{idx}_bwd_cy",
				f"i{idx}_bwd_w", f"i{idx}_bwd_h"
			)
			has_fwd = [key in npz.files for key in fwd_keys]
			has_bwd = [key in npz.files for key in bwd_keys]
			if any(has_fwd) != all(has_fwd) or any(has_bwd) != all(has_bwd):
				raise RuntimeError(
					f"torso_box_coords interval {idx} has an incomplete FWD/BWD path"
				)
			if all(has_fwd) != all(has_bwd):
				raise RuntimeError(
					f"torso_box_coords interval {idx} has only one FWD/BWD path"
				)
			forward_path = None
			backward_path = None
			if all(has_fwd):
				forward_path = _load_path(npz, fwd_keys, expected_length)
				backward_path = _load_path(npz, bwd_keys, expected_length)
			solved[fingerprint] = {
				"start_frame": start_frame,
				"end_frame": end_frame,
				"forward_path": forward_path,
				"backward_path": backward_path,
				"blended_path": blended_path,
			}
	if video_identity is not None and "frame_count" in video_identity:
		persisted_frame_count = int(video_identity["frame_count"])
		max_end_frame = max((iv["end_frame"] for iv in solved.values()), default=0)
		if max_end_frame > 0 and persisted_frame_count < max_end_frame:
			raise RuntimeError(
				f"torso_box_coords.npz frame_count={persisted_frame_count} "
				f"but manifest has end_frame up to {max_end_frame}; "
				"file is corrupt or was trimmed"
			)
	result = {
		INTERVALS_HEADER_KEY: INTERVALS_HEADER_VALUE,
		"solved_intervals": solved,
		"solve_complete": solve_complete,
	}
	if video_identity is not None:
		result["video_identity"] = video_identity
	return result


#============================================

def _load_path(npz: object, keys: tuple, expected_length: int) -> list[dict]:
	"""Rebuild one complete persisted coordinate path as integer dicts."""
	cx_arr = npz[keys[0]]
	cy_arr = npz[keys[1]]
	w_arr = npz[keys[2]]
	h_arr = npz[keys[3]]
	lengths = {len(cx_arr), len(cy_arr), len(w_arr), len(h_arr)}
	if len(lengths) != 1 or len(cx_arr) != expected_length:
		raise RuntimeError(
			"torso_box_coords path length does not match its manifest interval"
		)
	path = [
		{
			"cx": int(cx_arr[i]),
			"cy": int(cy_arr[i]),
			"w": int(w_arr[i]),
			"h": int(h_arr[i]),
		}
		for i in range(len(cx_arr))
	]
	return path


#============================================

def _extract_source_box_coords(frame_obj: object) -> tuple:
	"""Extract (cx, cy, w, h) floats from a SOURCE-space frame box object."""
	if isinstance(frame_obj, (coord_space.SourceBox, coord_space.ProcessedBox,
		coord_space.SourcePoint, coord_space.ProcessedPoint)):
		coord_space.require_source_box(frame_obj)
		result = (float(frame_obj.cx), float(frame_obj.cy),
			float(frame_obj.w), float(frame_obj.h))
		return result
	result = (float(frame_obj["cx"]), float(frame_obj["cy"]),
		float(frame_obj["w"]), float(frame_obj["h"]))
	return result


#============================================

def frame_dict_to_source_box(frame_dict: dict) -> coord_space.SourceBox:
	"""Wrap a loaded SOURCE-space torso-box frame dict as a SourceBox."""
	source_box = coord_space.SourceBox(
		cx=float(frame_dict["cx"]),
		cy=float(frame_dict["cy"]),
		w=float(frame_dict["w"]),
		h=float(frame_dict["h"]),
	)
	return source_box


#============================================

def _round_clip_uint16(arr: numpy.ndarray) -> numpy.ndarray:
	"""Round float coordinates, clip to uint16 range, and cast to uint16."""
	clipped = numpy.clip(numpy.round(arr), 0, 65535)
	out = clipped.astype(numpy.uint16)
	return out


#============================================

def _write_path_arrays(arrays: dict, idx: int, direction_tag: str, track: list) -> None:
	"""Convert one SOURCE-space path to the four pixel-snapped NPZ arrays."""
	coords = [_extract_source_box_coords(frame_obj) for frame_obj in track]
	cx_in = numpy.asarray([coord[0] for coord in coords], dtype=numpy.float32)
	cy_in = numpy.asarray([coord[1] for coord in coords], dtype=numpy.float32)
	w_in = numpy.asarray([coord[2] for coord in coords], dtype=numpy.float32)
	h_in = numpy.asarray([coord[3] for coord in coords], dtype=numpy.float32)
	arrays[f"i{idx}_{direction_tag}_cx"] = _round_clip_uint16(cx_in)
	arrays[f"i{idx}_{direction_tag}_cy"] = _round_clip_uint16(cy_in)
	arrays[f"i{idx}_{direction_tag}_w"] = _round_clip_uint16(w_in)
	arrays[f"i{idx}_{direction_tag}_h"] = _round_clip_uint16(h_in)


#============================================

def write_torso_box_coords(path: str, cache_data: dict) -> None:
	"""Write unified SOURCE-space torso-box coordinates to an NPZ artifact.

	Coordinates preserve schema-v10 pixel snapping and unsigned-16-bit storage.
	A pre-race interval writes a blended path only; other intervals must supply
	both FWD and BWD paths.

	Args:
		path: Output NPZ file path.
		cache_data: Solved interval data returned by load_torso_box_coords.
	"""
	solved_intervals = cache_data["solved_intervals"]
	if not isinstance(solved_intervals, dict):
		raise RuntimeError("solved_intervals must be a mapping")
	video_identity = cache_data["video_identity"]
	if not isinstance(video_identity, dict):
		raise RuntimeError("video_identity must be a mapping")
	solve_complete = cache_data["solve_complete"]
	manifest = []
	arrays = {}
	for idx, (fingerprint, entry) in enumerate(solved_intervals.items()):
		start_frame = int(entry["start_frame"])
		end_frame = int(entry["end_frame"])
		if end_frame < start_frame:
			raise ValueError("solved interval has reversed frame bounds")
		expected_length = end_frame - start_frame + 1
		fwd = entry["forward_path"]
		bwd = entry["backward_path"]
		blended = entry["blended_path"]
		if blended is None:
			raise RuntimeError("solved interval must provide a blended path")
		if (fwd is None) != (bwd is None):
			raise ValueError(
				"solved interval must provide both FWD/BWD paths or neither; "
				"pre-race intervals omit both"
			)
		if len(blended) != expected_length:
			raise ValueError("blended path length does not match interval bounds")
		if fwd is not None and len(fwd) != expected_length:
			raise ValueError("FWD path length does not match interval bounds")
		if bwd is not None and len(bwd) != expected_length:
			raise ValueError("BWD path length does not match interval bounds")
		_write_path_arrays(arrays, idx, "blended", blended)
		if fwd is not None and bwd is not None:
			_write_path_arrays(arrays, idx, "fwd", fwd)
			_write_path_arrays(arrays, idx, "bwd", bwd)
		manifest.append({
			"fingerprint": fingerprint,
			"start_frame": start_frame,
			"end_frame": end_frame,
			"array_index": idx,
		})
	arrays["schema_version"] = numpy.asarray(
		tr_schema.SCHEMA_VERSION, dtype=numpy.int32
	)
	manifest_json = json.dumps(manifest).encode("utf-8")
	arrays["manifest"] = numpy.frombuffer(manifest_json, dtype=numpy.uint8)
	vid_json = json.dumps(video_identity).encode("utf-8")
	arrays["video_identity"] = numpy.frombuffer(vid_json, dtype=numpy.uint8)
	arrays["solve_complete"] = numpy.asarray(bool(solve_complete))
	_write_npz_atomic(path, arrays)
