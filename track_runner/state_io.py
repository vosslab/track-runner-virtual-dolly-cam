"""state_io.py

Read/write for track_runner per-video state files.

Seeds files (JSON) store human-authored annotations. Under contract C7
a seed is a torso box drawn by a human. The canonical on-disk shape
(v3) is four fields per seed: frame_index, torso_box, status, pass.
Convenience geometry (cx/cy/w/h) is derived in memory from torso_box
at load time and discarded at write time; it never appears on disk.

Geometry-cache files (NPZ) store the solved per-frame blended interval path
under the format rule "dense per-frame numeric series -> NPZ". Per
interval, four float32 arrays (cx/cy/w/h) plus a small JSON manifest
mapping fingerprint to array_index. No scoring content.

Interval-scores files (JSON) are the sole owner of per-interval
scoring and review summary data (agreement, confidence tier, race
phase, cyclical prior). No per-frame trajectory.
"""

# Standard Library
import json
import os
import tempfile

# PIP3 modules
import numpy

#============================================

# header key and version for seeds JSON files
# v3: canonical four-field schema (frame_index, torso_box, status, pass)
# v2: legacy with cx/cy/w/h, mode, conf, source, jersey_hsv, histogram, etc.
# Loader accepts both; writer emits v3 only.
SEEDS_HEADER_KEY = "track_runner_seeds"
SEEDS_HEADER_VALUE = 3
# Accepted on load, migrated to v3 on write
SEEDS_ACCEPTED_HEADERS = frozenset({2, 3})

# header key and version for diagnostics JSON files.
# Track-runner schema versions are kept in lockstep across
# state_io.DIAGNOSTICS_HEADER_VALUE, scoring.INTERVAL_SCORE_SCHEMA_VERSION,
# and race_start.PRE_RACE_REFERENCE_SCHEMA_VERSION. Bump ALL THREE together.
DIAGNOSTICS_HEADER_KEY = "track_runner_diagnostics"
DIAGNOSTICS_HEADER_VALUE = 4

# header key and version for geometry cache (kept on in-memory dicts
# for compatibility with existing call sites; not a JSON on-disk
# header -- the NPZ file carries `schema_version` as its own key).
# v1 = legacy JSON intervals.json (no longer written). v2 = NPZ
# geometry_cache.npz (current).
INTERVALS_HEADER_KEY = "track_runner_intervals"
INTERVALS_HEADER_VALUE = 2

# NPZ schema version key for geometry_cache.npz files
GEOMETRY_CACHE_SCHEMA_VERSION = 2

# valid mode values for seed entries
# Retained for VALID_SEED_MODES import compatibility even though `mode` is
# no longer a canonical on-disk field. Kept so tools that reference the
# frozenset (e.g. validation in older scripts) don't break.
VALID_SEED_MODES = frozenset(
	["initial", "suggested_refine", "interval_refine", "gap_refine",
	"edit_redraw", "solve_refine", "interactive_refine", "bbox_polish",
	"target_refine"]
)

# canonical on-disk allow-list for v3 seeds.
# Writer emits ONLY these keys. Loader tolerates extras in memory (for
# forward compatibility with future fields) but the writer drops them,
# so unknown keys never survive a round-trip to disk.
CANONICAL_SEED_KEYS = frozenset(
	["frame_index", "torso_box", "status", "pass"]
)

# legacy fields stripped at load time (dead on contract C6 or derivable).
# histogram/jersey_hsv: banned by contract C6 (runner appearance not reliable)
# frame: duplicate of frame_index
# time_s: derivable from frame_index / fps
# cx/cy/w/h: derived from torso_box (re-derived in memory, never stored)
# mode: workflow provenance with no solver branches
# conf: derivable from status (visible/partial -> 1.0, approximate -> 0.3)
# source: always "human" under contract C7; legacy "propagated" rows are
#         dropped entirely by the migration tool before state_io ever sees them
LEGACY_SEED_STRIP_KEYS = frozenset(
	["histogram", "jersey_hsv", "frame", "time_s",
	"cx", "cy", "w", "h", "mode", "conf", "source"]
)

# derived-in-memory keys added by _derive_seed_geometry after load.
# These are NOT on disk but are needed by interval_fingerprint,
# velocity_model._compare_seed_positions, and UI code that reads cx/cy/w/h.
# write_seeds strips them back out before serializing.
DERIVED_SEED_KEYS = frozenset(["cx", "cy", "w", "h"])

# flag to avoid repeating legacy obstructed-seed warnings on every save
_WARNED_LEGACY_OBSTRUCTED = False

#============================================

def _set_warned_legacy() -> None:
	"""Set the warned flag so legacy obstructed warnings print only once."""
	global _WARNED_LEGACY_OBSTRUCTED
	_WARNED_LEGACY_OBSTRUCTED = True

#============================================

def _strip_legacy_seed_fields(seed: dict) -> None:
	"""Remove banned and derivable fields from a seed dict in place.

	Strips every key in LEGACY_SEED_STRIP_KEYS. Called by load_seeds so
	downstream code never sees cx/cy/w/h from disk; _derive_seed_geometry
	adds them back from torso_box in memory.

	Args:
		seed: Seed dict mutated in place.
	"""
	for key in LEGACY_SEED_STRIP_KEYS:
		# discard each legacy key if present
		if key in seed:
			del seed[key]


#============================================

def _derive_seed_geometry(seed: dict) -> None:
	"""Derive cx/cy/w/h from torso_box and attach in memory.

	Called after _strip_legacy_seed_fields so downstream consumers
	(interval_fingerprint, velocity_model, UI) see the geometry keys
	they expect. Never stored; write_seeds strips them again.

	Skips seeds with no torso_box (e.g. not_in_frame seeds).

	Args:
		seed: Seed dict mutated in place.
	"""
	box = seed.get("torso_box")
	# not_in_frame and approximate-without-box seeds carry no geometry
	if box is None:
		return
	# torso_box is [x, y, w, h] ints; centers can be half-pixel
	tx, ty, tw, th = box
	seed["cx"] = float(tx) + float(tw) / 2.0
	seed["cy"] = float(ty) + float(th) / 2.0
	seed["w"] = float(tw)
	seed["h"] = float(th)


#============================================

def validate_seed(seed: dict) -> int | None:
	"""Validate a seed dict and return frame index if legacy issue found.

	Args:
		seed: Seed dictionary to validate.

	Returns:
		Frame index if approximate seed is missing torso_box, else None.
	"""
	status = seed.get("status")
	# legacy "obstructed" without torso_box is a data problem
	if status == "obstructed":
		if "torso_box" not in seed or seed["torso_box"] is None:
			frame_index = seed.get("frame_index")
			return frame_index
	# "approximate" should always have torso_box
	if status == "approximate":
		if "torso_box" not in seed or seed["torso_box"] is None:
			frame_index = seed.get("frame_index")
			return frame_index
	return None

#============================================

def load_seeds(path: str) -> dict:
	"""Load a seeds JSON file and normalize to the in-memory v3 shape.

	Accepts headers 2 (legacy) and 3 (canonical). Regardless of on-disk
	header, the returned in-memory dict:

	- Strips banned and derivable fields (histogram, jersey_hsv, frame,
	  time_s, cx, cy, w, h, mode, conf, source) from each seed.
	- Re-derives cx/cy/w/h from torso_box so interval_fingerprint,
	  velocity_model, and UI code see the geometry they expect.
	- Migrates legacy "obstructed" seeds: keep as "approximate" if a
	  torso_box is present; drop otherwise (no position data).
	- Sorts seeds by frame_index.

	Returns an empty seeds structure if the file does not exist.

	Args:
		path: Path to the seeds JSON file.

	Returns:
		dict: In-memory seeds data with derived geometry attached.

	Raises:
		RuntimeError: If the file exists but header version is not accepted.
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {SEEDS_HEADER_KEY: SEEDS_HEADER_VALUE, "seeds": []}
	with open(path, "r") as fh:
		data = json.load(fh)
	if not isinstance(data, dict):
		raise RuntimeError(f"seeds file did not parse as a mapping: {path}")
	# validate the header key and version (accept v2 legacy or v3 canonical)
	header_val = data.get(SEEDS_HEADER_KEY)
	if header_val not in SEEDS_ACCEPTED_HEADERS:
		accepted = sorted(SEEDS_ACCEPTED_HEADERS)
		raise RuntimeError(
			f"seeds file header mismatch in {path}: "
			f"expected {SEEDS_HEADER_KEY} in {accepted}, got {header_val}"
		)
	if "seeds" in data and isinstance(data["seeds"], list):
		cleaned = []
		dropped_count = 0
		for seed in data["seeds"]:
			status = seed.get("status")
			# migrate legacy "obstructed" (from pre-v3 files)
			if status == "obstructed":
				if seed.get("torso_box") is not None:
					# has approx area, migrate to "approximate"
					seed["status"] = "approximate"
				else:
					# legacy obstructed without position data, drop it
					dropped_count += 1
					continue
			# strip banned and derivable fields (belt-and-suspenders even
			# on v3 files, so residue from a buggy writer gets cleaned)
			_strip_legacy_seed_fields(seed)
			# re-derive cx/cy/w/h from torso_box for downstream consumers
			_derive_seed_geometry(seed)
			cleaned.append(seed)
		if dropped_count > 0:
			print(f"  dropped {dropped_count} legacy obstructed seed(s) "
				f"with no position data")
		# sort seeds by frame_index so consumers always get time-ordered data
		data["seeds"] = sorted(
			cleaned,
			key=lambda s: int(s["frame_index"]),
		)
	return data


#============================================

def _canonicalize_seed_for_write(seed: dict) -> dict:
	"""Return a new seed dict containing only canonical allow-list keys.

	Read-tolerant, write-strict: the caller's in-memory dict may carry
	derived geometry (cx/cy/w/h) or other extras; the on-disk record
	is exactly CANONICAL_SEED_KEYS.

	Args:
		seed: In-memory seed dict.

	Returns:
		dict: New dict with only canonical keys present.
	"""
	canonical = {}
	for key in CANONICAL_SEED_KEYS:
		# omit torso_box cleanly when the seed has none (not_in_frame)
		if key == "torso_box" and seed.get("torso_box") is None:
			continue
		if key in seed:
			canonical[key] = seed[key]
	return canonical


#============================================

def write_seeds(path: str, seeds_data: dict) -> None:
	"""Write seeds data to a JSON file with atomic write semantics.

	Emits v3 canonical shape: each seed contains only frame_index,
	torso_box (when present), status, pass. Derived geometry
	(cx/cy/w/h) and any unknown keys in the in-memory dict are
	discarded at write time so the on-disk record stays canonical.

	Args:
		path: Output file path.
		seeds_data: Seeds dictionary to write (must include 'seeds' list).
	"""
	# validate all seeds before writing; collect legacy warnings
	if "seeds" in seeds_data and isinstance(seeds_data["seeds"], list):
		bad_frames = []
		for seed in seeds_data["seeds"]:
			bad_frame = validate_seed(seed)
			if bad_frame is not None:
				bad_frames.append(bad_frame)
		if bad_frames and not _WARNED_LEGACY_OBSTRUCTED:
			_set_warned_legacy()
			print(f"  warning: {len(bad_frames)} approx seed(s) missing "
				f"torso_box (use 'a' key to fix): frames {bad_frames}")

	# force header to v3; this is the canonical writer
	seeds_data[SEEDS_HEADER_KEY] = SEEDS_HEADER_VALUE
	# strip each seed to canonical keys only, then sort by frame_index
	if "seeds" in seeds_data and isinstance(seeds_data["seeds"], list):
		canonical_seeds = [
			_canonicalize_seed_for_write(s) for s in seeds_data["seeds"]
		]
		seeds_data["seeds"] = sorted(
			canonical_seeds,
			key=lambda s: int(s["frame_index"]),
		)

	# write to temp file in same directory (same filesystem for atomic rename)
	dir_path = os.path.dirname(os.path.abspath(path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.json")
	try:
		with os.fdopen(fd, "w") as fh:
			json.dump(seeds_data, fh, indent=2)
		# atomic rename - never truncates original until new content is ready
		os.replace(tmp_path, path)
	except Exception:
		# clean up temp file if anything failed before replace
		if os.path.exists(tmp_path):
			os.unlink(tmp_path)
		raise


#============================================

def load_diagnostics(path: str) -> dict:
	"""Load an interval-scores JSON file (formerly diagnostics.json).

	Sole owner of per-interval scoring data plus optional race-phase
	and cyclical-prior context. The file has been renamed from
	`.diagnostics.json` to `.interval_scores.json` to match its
	responsibility; this loader accepts either filename via the caller.
	Function name is retained for callsite compatibility.

	Returns an empty structure if the file does not exist. Accepts
	legacy header values `2`, `3`, and current `4`; returns a v4-shaped dict.

	Args:
		path: Path to the interval_scores.json (or legacy
			diagnostics.json) file.

	Returns:
		dict: Parsed scoring data with validated header, or empty
			structure if the file is absent.

	Raises:
		RuntimeError: If the file exists but the header version is not
			in (2, 3, 4).
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {DIAGNOSTICS_HEADER_KEY: DIAGNOSTICS_HEADER_VALUE}
	with open(path, "r") as fh:
		data = json.load(fh)
	if not isinstance(data, dict):
		raise RuntimeError(f"diagnostics file did not parse as a mapping: {path}")
	# validate the header key and version: accept v2, v3, and v4
	header_val = data.get(DIAGNOSTICS_HEADER_KEY)
	if header_val not in (2, 3, 4):
		raise RuntimeError(
			f"diagnostics file header mismatch in {path}: "
			f"expected {DIAGNOSTICS_HEADER_KEY} in (2, 3, 4), got {header_val}"
		)
	# migrate from flat v2 shape to nested v3 shape unconditionally based on
	# presence of interval_score key (not header version). Header v2 or v3 are
	# both accepted; migration fires when shape is legacy-flat.
	for iv in data.get("intervals", []):
		if not isinstance(iv, dict):
			continue
		if "interval_score" not in iv:
			# v2 flat-shape entry missing nested interval_score.
			# Migrate to v3 nested structure, preserving numeric fields and
			# flagging the migration in failure_reasons. Do not silently
			# fabricate confidence_tier; raise if required flat keys are absent.
			if "agreement_score" not in iv:
				# real v2 entries always wrote agreement_score; its absence
				# signals a stale/corrupt file. Do not allow the load to hide it.
				start_frame = iv.get("start_frame", "?")
				end_frame = iv.get("end_frame", "?")
				raise RuntimeError(
					f"stale diagnostics for interval ({start_frame}, {end_frame}); "
					f"delete {path} and re-solve"
				)
			iv["interval_score"] = {
				"agreement": float(iv["agreement_score"]),
				"velocity_consistency": 0.0,
				"size_consistency": 0.0,
				"motion_quality": 0.0,
				"occlusion_fraction": 0.0,
				"confidence_tier": "legacy_migrated",
				"failure_reasons": list(iv.get("failure_reasons", [])) + ["legacy_schema"],
				"warning_flags": [],
			}
	# synthesize pre_race_reference if missing (legacy v2/v3 files do not have it).
	# v2 and v3 files are shape-migrated to v4 in-memory; missing key -> None.
	if "pre_race_reference" not in data:
		data["pre_race_reference"] = None
	# drop legacy race_phase block; v4 does not use it. discard if present from v3.
	data.pop("race_phase", None)
	return data


#============================================

def write_diagnostics(path: str, diagnostics_data: dict) -> None:
	"""Write diagnostics data to a JSON file.

	Ensures the required header key is present before writing.

	Args:
		path: Output file path.
		diagnostics_data: Diagnostics dictionary to write.
	"""
	# ensure header version is set correctly before writing
	diagnostics_data[DIAGNOSTICS_HEADER_KEY] = DIAGNOSTICS_HEADER_VALUE
	# atomic write: temp file + rename to avoid corrupt file on interruption
	dir_path = os.path.dirname(os.path.abspath(path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.json")
	try:
		with os.fdopen(fd, "w") as fh:
			json.dump(diagnostics_data, fh, indent=2)
		os.replace(tmp_path, path)
	except Exception:
		if os.path.exists(tmp_path):
			os.unlink(tmp_path)
		raise


#============================================

def load_geometry_cache(path: str) -> dict:
	"""Load a geometry_cache.npz file into the legacy in-memory shape.

	Returns a dict shaped like the old `load_intervals` output so that
	call sites iterate unchanged:

		{
			"track_runner_intervals": 2,
			"solved_intervals": {
				"<fingerprint>": {
					"start_frame": int,
					"end_frame": int,
					"blended_path": [
						{"cx": float, "cy": float, "w": float, "h": float},
						...
					],
				},
				...
			},
			"video_identity": {...},
			"solve_complete": bool,
		}

	On disk the NPZ stores a JSON manifest mapping fingerprint to an
	`array_index` plus four float32 arrays per interval
	(`i<k>_cx`, `i<k>_cy`, `i<k>_w`, `i<k>_h`). The loader reassembles
	the list-of-dicts shape so downstream iteration is unchanged.

	Returns an empty skeleton if the file does not exist. Raises if the
	file exists but has a different or missing schema_version.

	Args:
		path: Path to the geometry_cache.npz file.

	Returns:
		dict with the shape described above.

	Raises:
		RuntimeError: If the schema version is not recognized or a
			declared per-interval array is missing.
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {
			INTERVALS_HEADER_KEY: INTERVALS_HEADER_VALUE,
			"solved_intervals": {},
		}
	with numpy.load(path, allow_pickle=False) as npz:
		schema_version = int(npz["schema_version"])
		if schema_version != GEOMETRY_CACHE_SCHEMA_VERSION:
			raise RuntimeError(
				f"geometry cache schema mismatch in {path}: "
				f"expected schema_version={GEOMETRY_CACHE_SCHEMA_VERSION}, "
				f"got {schema_version}. Run tools/_migrate_tr_config.py "
				f"to archive old caches; the next solve will regenerate."
			)
		# manifest is a 0-d bytes array containing JSON
		manifest_bytes = bytes(npz["manifest"])
		manifest = json.loads(manifest_bytes.decode("utf-8"))
		# video_identity is optional; same encoding
		video_identity = None
		if "video_identity" in npz.files:
			vid_bytes = bytes(npz["video_identity"])
			video_identity = json.loads(vid_bytes.decode("utf-8"))
		solve_complete = False
		if "solve_complete" in npz.files:
			solve_complete = bool(npz["solve_complete"])
		# rebuild the solved_intervals dict from indexed arrays
		solved = {}
		for entry in manifest:
			fingerprint = entry["fingerprint"]
			idx = int(entry["array_index"])
			start_frame = int(entry["start_frame"])
			end_frame = int(entry["end_frame"])
			# read the four per-frame arrays for this interval
			cx_key = f"i{idx}_cx"
			cy_key = f"i{idx}_cy"
			w_key = f"i{idx}_w"
			h_key = f"i{idx}_h"
			for key in (cx_key, cy_key, w_key, h_key):
				if key not in npz.files:
					raise RuntimeError(
						f"geometry cache missing array {key} in {path}"
					)
			cx_arr = npz[cx_key]
			cy_arr = npz[cy_key]
			w_arr = npz[w_key]
			h_arr = npz[h_key]
			# reassemble list-of-dicts for in-memory consumers
			blended_path = [
				{
					"cx": float(cx_arr[i]),
					"cy": float(cy_arr[i]),
					"w": float(w_arr[i]),
					"h": float(h_arr[i]),
				}
				for i in range(len(cx_arr))
			]
			solved[fingerprint] = {
				"start_frame": start_frame,
				"end_frame": end_frame,
				"blended_path": blended_path,
			}
	result = {
		INTERVALS_HEADER_KEY: INTERVALS_HEADER_VALUE,
		"solved_intervals": solved,
		"solve_complete": solve_complete,
	}
	if video_identity is not None:
		result["video_identity"] = video_identity
	return result


#============================================

def write_geometry_cache(path: str, cache_data: dict) -> None:
	"""Write a geometry cache as NPZ with a JSON manifest.

	Extracts `blended_path` (blended interval path) cx/cy/w/h arrays from
	each solved interval and writes them as float32 NPZ arrays. The
	manifest maps each fingerprint to an `array_index`, `start_frame`,
	and `end_frame` so the loader can reassemble the in-memory shape.

	Does not persist `interval_score`, `forward_path`, `backward_path`,
	or any per-frame extras. Scoring lives in `interval_scores.json`
	(see `write_solver_diagnostics`, which writes to that file;
	function name is retained for callsite compatibility). Forward and
	backward interval paths live in the opt-in `debug_paths.npz` sidecar
	when solve runs with `--debug-paths`.

	Atomic write via a sibling temp file and `os.replace`.

	Args:
		path: Output NPZ file path.
		cache_data: Dict shaped like `load_geometry_cache` returns --
			at minimum `{"solved_intervals": {fp: {start_frame,
			end_frame, blended_path: [dicts]}}}`. Unknown top-level keys
			are tolerated; only `video_identity` and `solve_complete`
			are persisted beyond the manifest and per-interval arrays.
	"""
	solved_intervals = cache_data.get("solved_intervals", {}) or {}
	manifest = []
	arrays = {}
	for idx, (fingerprint, entry) in enumerate(solved_intervals.items()):
		start_frame = int(entry["start_frame"])
		end_frame = int(entry["end_frame"])
		blended_path = entry.get("blended_path") or []
		# extract columnar arrays from list-of-dicts
		cx = numpy.asarray(
			[float(s["cx"]) for s in blended_path], dtype=numpy.float32
		)
		cy = numpy.asarray(
			[float(s["cy"]) for s in blended_path], dtype=numpy.float32
		)
		w = numpy.asarray(
			[float(s["w"]) for s in blended_path], dtype=numpy.float32
		)
		h = numpy.asarray(
			[float(s["h"]) for s in blended_path], dtype=numpy.float32
		)
		arrays[f"i{idx}_cx"] = cx
		arrays[f"i{idx}_cy"] = cy
		arrays[f"i{idx}_w"] = w
		arrays[f"i{idx}_h"] = h
		manifest.append({
			"fingerprint": fingerprint,
			"start_frame": start_frame,
			"end_frame": end_frame,
			"array_index": idx,
		})
	# top-level small metadata
	arrays["schema_version"] = numpy.asarray(
		GEOMETRY_CACHE_SCHEMA_VERSION, dtype=numpy.int32
	)
	manifest_json = json.dumps(manifest).encode("utf-8")
	arrays["manifest"] = numpy.frombuffer(manifest_json, dtype=numpy.uint8)
	video_identity = cache_data.get("video_identity")
	if video_identity is not None:
		vid_json = json.dumps(video_identity).encode("utf-8")
		arrays["video_identity"] = numpy.frombuffer(vid_json, dtype=numpy.uint8)
	solve_complete = cache_data.get("solve_complete", False)
	arrays["solve_complete"] = numpy.asarray(bool(solve_complete))
	_write_npz_atomic(path, arrays)


#============================================

# NPZ schema version key for debug_paths.npz sidecar files
DEBUG_PATHS_SCHEMA_VERSION = 2


def _write_npz_atomic(path: str, arrays: dict) -> None:
	"""Write a dict of numpy arrays to an NPZ file atomically.

	Shared helper used by write_geometry_cache and write_debug_paths.

	Args:
		path: Target NPZ file path.
		arrays: Dict of {key: numpy.ndarray} to persist.
	"""
	dir_path = os.path.dirname(os.path.abspath(path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.npz")
	os.close(fd)
	try:
		numpy.savez(tmp_path, **arrays)
		# numpy.savez appends .npz if the temp didn't already end in .npz
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

def write_debug_paths(path: str, cache_data: dict) -> None:
	"""Write forward/backward interval path arrays as an NPZ debug sidecar.

	Only called when solve runs with `--debug-paths`. Accepts the same
	in-memory shape produced by solve_all_intervals, picks out each
	interval's `forward_path` and `backward_path` (list-of-dicts with
	cx/cy/w/h, the forward and backward interval paths), and writes two
	sets of four float32 arrays per
	interval: `i<k>_fwd_{cx,cy,w,h}` and `i<k>_bwd_{cx,cy,w,h}`.

	Intervals without forward_path/backward_path are silently skipped
	(e.g. when solve was interrupted mid-interval).

	The sidecar's manifest mirrors the geometry cache's manifest so the
	debug overlay can intersect fingerprint sets on load.

	Args:
		path: Target NPZ file path (typically
			`<video>.track_runner.debug_paths.npz`).
		cache_data: Dict shaped like `load_geometry_cache` output --
			`{"solved_intervals": {fp: {start_frame, end_frame,
			forward_path: [...], backward_path: [...]}}}`.
	"""
	solved_intervals = cache_data.get("solved_intervals", {}) or {}
	manifest = []
	arrays = {}
	for idx, (fingerprint, entry) in enumerate(solved_intervals.items()):
		fwd = entry.get("forward_path")
		bwd = entry.get("backward_path")
		# skip intervals with no debug interval paths attached
		if not fwd or not bwd:
			continue
		start_frame = int(entry["start_frame"])
		end_frame = int(entry["end_frame"])
		for direction, track in (("fwd", fwd), ("bwd", bwd)):
			cx = numpy.asarray(
				[float(s["cx"]) for s in track], dtype=numpy.float32
			)
			cy = numpy.asarray(
				[float(s["cy"]) for s in track], dtype=numpy.float32
			)
			w = numpy.asarray(
				[float(s["w"]) for s in track], dtype=numpy.float32
			)
			h = numpy.asarray(
				[float(s["h"]) for s in track], dtype=numpy.float32
			)
			arrays[f"i{idx}_{direction}_cx"] = cx
			arrays[f"i{idx}_{direction}_cy"] = cy
			arrays[f"i{idx}_{direction}_w"] = w
			arrays[f"i{idx}_{direction}_h"] = h
		manifest.append({
			"fingerprint": fingerprint,
			"start_frame": start_frame,
			"end_frame": end_frame,
			"array_index": idx,
		})
	arrays["schema_version"] = numpy.asarray(
		DEBUG_PATHS_SCHEMA_VERSION, dtype=numpy.int32
	)
	manifest_json = json.dumps(manifest).encode("utf-8")
	arrays["manifest"] = numpy.frombuffer(manifest_json, dtype=numpy.uint8)
	_write_npz_atomic(path, arrays)


#============================================

def load_debug_paths(path: str) -> dict:
	"""Load a debug_paths.npz sidecar into an in-memory dict.

	Returns a dict keyed by fingerprint, shape:
		{
			"<fingerprint>": {
				"start_frame": int,
				"end_frame": int,
				"forward_path": [{cx, cy, w, h}, ...],
				"backward_path": [{cx, cy, w, h}, ...],
			},
			...
		}

	Returns an empty dict if the file does not exist. Raises on
	unknown schema version.

	Args:
		path: Path to the debug_paths.npz sidecar.

	Returns:
		dict mapping fingerprint to per-direction track dicts.
	"""
	if not os.path.isfile(path):
		return {}
	result = {}
	with numpy.load(path, allow_pickle=False) as npz:
		schema_version = int(npz["schema_version"])
		if schema_version != DEBUG_PATHS_SCHEMA_VERSION:
			raise RuntimeError(
				f"debug interval paths schema mismatch in {path}: expected "
				f"schema_version={DEBUG_PATHS_SCHEMA_VERSION}, got "
				f"{schema_version}"
			)
		manifest_bytes = bytes(npz["manifest"])
		manifest = json.loads(manifest_bytes.decode("utf-8"))
		for entry in manifest:
			fingerprint = entry["fingerprint"]
			idx = int(entry["array_index"])
			tracks = {}
			for direction in ("fwd", "bwd"):
				cx = npz[f"i{idx}_{direction}_cx"]
				cy = npz[f"i{idx}_{direction}_cy"]
				w = npz[f"i{idx}_{direction}_w"]
				h = npz[f"i{idx}_{direction}_h"]
				track_records = [
					{
						"cx": float(cx[i]),
						"cy": float(cy[i]),
						"w": float(w[i]),
						"h": float(h[i]),
					}
					for i in range(len(cx))
				]
				key = "forward_path" if direction == "fwd" else "backward_path"
				tracks[key] = track_records
			result[fingerprint] = {
				"start_frame": int(entry["start_frame"]),
				"end_frame": int(entry["end_frame"]),
				**tracks,
			}
	return result


#============================================

def interval_fingerprint(
	seed_start: dict,
	seed_end: dict,
	solver_tag: str = "",
) -> str:
	"""Compute a deterministic lookup key from two seed endpoint states.

	The fingerprint encodes frame_index and position (cx, cy, w, h rounded
	to 2 decimal places) for both seeds. Any change in seed position or
	frame index produces a different key, so stale results are never reused.

	When `solver_tag` is non-empty, it is appended. This lets the interval
	solver invalidate the cache when its own semantics change (new gates,
	new blend rule, observer version bump) without requiring seed edits.
	Both `solve_all_intervals` and `cli._mode_refine` MUST pass the same
	solver_tag for cache hits to work.

	Args:
		seed_start: Seed state dict at the start of the interval.
		seed_end: Seed state dict at the end of the interval.
		solver_tag: Optional short string identifying the solver version and
			its blob-snap configuration. When empty, no tag is appended
			(legacy behavior).

	Returns:
		String fingerprint. With solver_tag, the suffix is included.
	"""
	parts = []
	for seed in (seed_start, seed_end):
		fi = int(seed["frame_index"])
		cx = round(float(seed["cx"]), 2)
		cy = round(float(seed["cy"]), 2)
		w = round(float(seed["w"]), 2)
		h = round(float(seed["h"]), 2)
		parts.append(f"{fi}|{cx:.2f}|{cy:.2f}|{w:.2f}|{h:.2f}")
	fingerprint = "|".join(parts)
	if solver_tag:
		fingerprint = fingerprint + "||" + solver_tag
	return fingerprint


#============================================

def merge_seeds(existing_seeds: list, new_seeds: list) -> list:
	"""Merge new seeds into an existing seeds list.

	New seeds never overwrite existing seeds at the same frame number.
	Seeds at frames not already in existing_seeds are appended.

	Args:
		existing_seeds: List of existing seed dicts, each with a 'frame_index' key.
		new_seeds: List of new seed dicts to merge in.

	Returns:
		list: Merged list of seed dicts with no duplicate frame entries.
	"""
	# build a set of frame numbers already present in existing seeds
	existing_frames = {seed["frame_index"] for seed in existing_seeds}
	# start with a copy of the existing seeds list
	merged = list(existing_seeds)
	# append only new seeds whose frame is not already present
	for seed in new_seeds:
		frame_num = seed["frame_index"]
		if frame_num not in existing_frames:
			merged.append(seed)
			# track this frame so duplicates within new_seeds are also skipped
			existing_frames.add(frame_num)
	return merged


#============================================
def write_solver_diagnostics(
	diagnostics: dict,
	path: str,
	fps: float,
) -> None:
	"""Serialize interval solver diagnostics to a JSON file.

	Strips non-serializable objects and builds a compact summary
	from the raw solver output before writing.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().
		path: Output JSON file path.
		fps: Video fps for inclusion in the file.
	"""
	# build a JSON-safe summary (do not write full per-frame trajectory)
	intervals_summary = []
	for iv in diagnostics.get("intervals", []):
		score = iv["interval_score"]
		entry = {
			"start_frame": iv["start_frame"],
			"end_frame": iv["end_frame"],
			"start_s": round(iv["start_frame"] / max(1.0, fps), 3),
			"end_s": round(iv["end_frame"] / max(1.0, fps), 3),
		}
		# v3-only writer: always emit the nested interval_score shape.
		# When the input score dict lacks confidence_tier (legacy v2 producer
		# or any non-analytical caller), synthesize an "unsolved" v3 entry
		# that preserves any present numeric fields and flags the legacy
		# origin in failure_reasons. The next solve/refine will overwrite it.
		if "confidence_tier" in score:
			confidence_tier = score.get("confidence_tier", "low")
			failure_reasons = list(score.get("failure_reasons", []))
		else:
			confidence_tier = "unsolved"
			# extend (do not replace) any prior failure reasons
			failure_reasons = list(score.get("failure_reasons", [])) + ["legacy_schema"]
		# v2 agreement_score maps 1:1 to v3 agreement; prefer v3 when both present
		agreement = float(score.get("agreement", score.get("agreement_score", 0.0)))
		entry["interval_score"] = {
			"agreement": round(agreement, 4),
			"velocity_consistency": round(
				float(score.get("velocity_consistency", 0.0)), 4,
			),
			"size_consistency": round(
				float(score.get("size_consistency", 0.0)), 4,
			),
			"motion_quality": round(
				float(score.get("motion_quality", 0.0)), 4,
			),
			"occlusion_fraction": round(
				float(score.get("occlusion_fraction", 0.0)), 4,
			),
			"confidence_tier": confidence_tier,
			"failure_reasons": failure_reasons,
			"warning_flags": list(score.get("warning_flags", [])),
		}
		intervals_summary.append(entry)

	# preserve cyclical prior if detected
	cyclical = diagnostics.get("cyclical_prior")
	cyclical_safe = None
	if cyclical is not None:
		cyclical_safe = {
			"period_frames": int(cyclical.get("period_frames", 0)),
			"period_s": round(
				float(cyclical.get("period_s", 0.0)), 3,
			),
			"correlation": round(
				float(cyclical.get("correlation", 0.0)), 4,
			),
		}

	diag_out = {
		DIAGNOSTICS_HEADER_KEY: DIAGNOSTICS_HEADER_VALUE,
		"fps": round(fps, 6),
		"intervals": intervals_summary,
		"cyclical_prior": cyclical_safe,
	}
	# serialize pre_race_reference if present. The top-level presence check is
	# an intentional optional (callers that do not have pre-race data simply
	# omit the key). All inner keys are required by compute_pre_race_reference;
	# accessing them directly fails loud on a malformed reference rather than
	# writing zeros silently.
	pre_race_reference = diagnostics.get("pre_race_reference")
	if pre_race_reference is not None:
		diag_out["pre_race_reference"] = {
			"race_start_frame": int(pre_race_reference["race_start_frame"]),
			"torso_w": round(float(pre_race_reference["torso_w"]), 4),
			"torso_h": round(float(pre_race_reference["torso_h"]), 4),
			"scene_anchor_x": round(float(pre_race_reference["scene_anchor_x"]), 4),
			"scene_anchor_y": round(float(pre_race_reference["scene_anchor_y"]), 4),
			"source_frame_indices": list(pre_race_reference["source_frame_indices"]),
			"source_count": int(pre_race_reference["source_count"]),
			"method": pre_race_reference["method"],
			"warnings": list(pre_race_reference["warnings"]),
		}
	# preserve video_identity if provided in the input diagnostics
	video_identity = diagnostics.get("video_identity")
	if video_identity is not None:
		diag_out["video_identity"] = video_identity
	write_diagnostics(path, diag_out)


#============================================
def write_agreement_debug_sidecar(
	diagnostics: dict,
	path: str,
) -> int:
	"""Write per-frame agreement debug data to a sidecar JSON.

	Only intervals that carry an `agreement_debug` sub-dict are written
	(populated when solve runs with --debug). Safe to call when no interval
	has debug data: this returns 0 and writes nothing.

	Args:
		diagnostics: Dict from interval_solver.solve_all_intervals().
		path: Output sidecar JSON path (e.g.
			`<basename>.track_runner.agreement_debug.json`).

	Returns:
		Number of intervals written. 0 means nothing written.
	"""
	intervals_with_debug = []
	for iv in diagnostics.get("intervals", []):
		ad = iv.get("agreement_debug")
		if ad is None:
			continue
		intervals_with_debug.append({
			"start_frame": iv["start_frame"],
			"end_frame": iv["end_frame"],
			"agreement_mean": round(float(ad["agreement"]), 4),
			"iou_p10": round(float(ad["iou_p10"]), 4),
			"iou_p50": round(float(ad["iou_p50"]), 4),
			"iou_p90": round(float(ad["iou_p90"]), 4),
			"per_frame": ad["per_frame"],
		})
	if not intervals_with_debug:
		return 0
	payload = {
		"schema": "track_runner.agreement_debug.v1",
		"intervals": intervals_with_debug,
	}
	with open(path, "w") as f:
		json.dump(payload, f, indent=2)
	return len(intervals_with_debug)


#============================================

# round-trip self-check for load/write pairs
if __name__ == "__main__":
	# test seeds round-trip: v3 canonical shape
	# input dict carries legacy fields that must be stripped
	seeds_data = {
		"video_file": "test.mov",
		"seeds": [
			{
				"frame_index": 150,
				"time_s": 5.0,
				"torso_box": [640, 360, 40, 60],
				"jersey_hsv": [120, 180, 200],
				"pass": 1,
				"source": "human",
				"mode": "initial",
				"status": "visible",
				"cx": 660.0,
				"cy": 390.0,
			}
		],
	}
	with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
		tmp_path = tmp.name
	write_seeds(tmp_path, seeds_data)
	loaded = load_seeds(tmp_path)
	assert loaded["seeds"][0]["frame_index"] == 150
	assert loaded[SEEDS_HEADER_KEY] == SEEDS_HEADER_VALUE
	# on-disk: only canonical keys survive the writer
	with open(tmp_path, "r") as fh:
		raw = json.load(fh)
	disk_seed_keys = set(raw["seeds"][0].keys())
	assert disk_seed_keys == CANONICAL_SEED_KEYS, disk_seed_keys
	# in-memory after load: canonical + derived geometry
	loaded_seed_keys = set(loaded["seeds"][0].keys())
	assert loaded_seed_keys == CANONICAL_SEED_KEYS | DERIVED_SEED_KEYS
	# derived geometry matches torso_box
	assert loaded["seeds"][0]["cx"] == 660.0
	assert loaded["seeds"][0]["cy"] == 390.0
	# second round-trip is byte-identical to the first (canonical stability)
	with open(tmp_path, "rb") as fh:
		first_bytes = fh.read()
	write_seeds(tmp_path, loaded)
	with open(tmp_path, "rb") as fh:
		second_bytes = fh.read()
	assert first_bytes == second_bytes
	os.unlink(tmp_path)

	# test diagnostics round-trip
	diag_data = {
		"intervals": [1, 2, 3],
		"trajectory": [{"frame_index": 0, "x": 100}],
	}
	with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
		tmp_path = tmp.name
	write_diagnostics(tmp_path, diag_data)
	loaded_diag = load_diagnostics(tmp_path)
	assert loaded_diag["intervals"] == [1, 2, 3]
	assert loaded_diag[DIAGNOSTICS_HEADER_KEY] == DIAGNOSTICS_HEADER_VALUE
	os.unlink(tmp_path)

	# test merge_seeds
	existing = [{"frame_index": 10, "mode": "initial"}, {"frame_index": 20, "mode": "initial"}]
	new = [{"frame_index": 10, "mode": "gap_refine"}, {"frame_index": 30, "mode": "interval_refine"}]
	merged = merge_seeds(existing, new)
	assert len(merged) == 3
	# frame 10 must be the original, not overwritten
	frame_10 = next(s for s in merged if s["frame_index"] == 10)
	assert frame_10["mode"] == "initial"

	# test geometry-cache NPZ round-trip
	fingerprint = "100|1731.50|629.50|39.00|59.00|450|1700.00|600.00|38.00|58.00"
	intervals_data = {
		"solved_intervals": {
			fingerprint: {
				"start_frame": 100,
				"end_frame": 101,
				"blended_path": [
					{"cx": 100.0, "cy": 200.0, "w": 40.0, "h": 60.0},
					{"cx": 101.5, "cy": 200.5, "w": 40.0, "h": 60.0},
				],
			},
		},
		"solve_complete": True,
		"video_identity": {"basename": "self_check.mov"},
	}
	with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as tmp:
		tmp_path = tmp.name
	write_geometry_cache(tmp_path, intervals_data)
	loaded_iv = load_geometry_cache(tmp_path)
	assert loaded_iv[INTERVALS_HEADER_KEY] == INTERVALS_HEADER_VALUE
	assert len(loaded_iv["solved_intervals"]) == 1
	reloaded = loaded_iv["solved_intervals"][fingerprint]
	assert reloaded["start_frame"] == 100
	assert len(reloaded["blended_path"]) == 2
	assert reloaded["blended_path"][0]["cx"] == 100.0
	assert loaded_iv["solve_complete"] is True
	assert loaded_iv["video_identity"]["basename"] == "self_check.mov"
	os.unlink(tmp_path)

	# test interval_fingerprint determinism
	seed_a = {"frame_index": 100, "cx": 1731.5, "cy": 629.5, "w": 39.0, "h": 59.0}
	seed_b = {"frame_index": 450, "cx": 1700.0, "cy": 600.0, "w": 38.0, "h": 58.0}
	fp = interval_fingerprint(seed_a, seed_b)
	assert fp == "100|1731.50|629.50|39.00|59.00|450|1700.00|600.00|38.00|58.00"
	# same inputs must produce same fingerprint
	fp2 = interval_fingerprint(seed_a, seed_b)
	assert fp == fp2

	print("all round-trip checks passed")
