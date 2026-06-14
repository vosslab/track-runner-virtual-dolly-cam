"""state_io.py

Read/write for track_runner per-video state files.

Seeds files (JSON) store human-authored annotations. Under contract C7
a seed is a torso box drawn by a human. The canonical on-disk shape
(v3) is four fields per seed: frame_index, torso_box, status, pass.
Convenience geometry (cx/cy/w/h) is derived in memory from torso_box
at load time and discarded at write time; it never appears on disk.

SeedsView (Option A coord-system robustness, 2026-05-29):
The source-of-truth seeds dict always uses SOURCE-pixel coords.
SeedsView wraps it and exposes PROCESSED-pixel coords via the
held FrameGeometry reference. Use load_seeds_view(path, geometry)
to obtain a view for the walker pipeline; load_seeds(path) is
the legacy API preserved for callers that need source coords (UI,
encoder, diagnostic tools).

Torso-box-coords files (NPZ) store the solved per-frame blended interval path
under the format rule "dense per-frame numeric series -> NPZ". Per
interval, four uint16 arrays (cx/cy/w/h) plus a small JSON manifest
mapping fingerprint to array_index. Coordinates are rounded to nearest
integer before storage (pixel-snapped, no subpixel precision retained).
No scoring content.

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

# local repo modules
import common_tools.coord_space as coord_space
import tr_schema

#============================================
# Unified schema version per contract C9: single source of truth for all
# schema versioning across track_runner. This value lives in tr_schema;
# the re-export below preserves back-compat for callers that still
# import state_io.SCHEMA_VERSION. Never assign a different value here.
SCHEMA_VERSION = tr_schema.SCHEMA_VERSION

# Aliases for backward-readable code (deprecated, prefer SCHEMA_VERSION)
DIAGNOSTICS_HEADER_VALUE = SCHEMA_VERSION

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
# Track-runner schema versions are kept in lockstep per contract C8.
DIAGNOSTICS_HEADER_KEY = "track_runner_diagnostics"

# header key and version for in-memory interval dicts (kept for
# compatibility with existing call sites; not a JSON on-disk
# header -- the NPZ file carries `schema_version` as its own key).
# v1 = legacy JSON intervals.json (no longer written).
# v2 = NPZ torso_box_coords.npz (current unified artifact).
INTERVALS_HEADER_KEY = "track_runner_intervals"
INTERVALS_HEADER_VALUE = 2

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

class SeedsView:
	"""View over source-pixel seeds projected to a target FrameGeometry.

	Source-of-truth seeds remain in source-pixel coords in the wrapped dict.
	This view exposes processed-pixel coords lazily via the held geometry
	reference.  Mismatched geometry use is caught by assert_geometry_match.

	Use load_seeds_view(path, geometry) to construct.  Do not construct
	directly if load_seeds_view covers your use case.

	The view is read-only. Never mutate view.source in place.

	Attributes:
		geometry: The FrameGeometry used to project coords.
		bin_factor: geometry.bin_factor (convenience accessor).
		source: Original source-pixel seeds dict (unchanged, uncopied).
		header: The seeds header dict (race_start, version, etc.).
		seeds: List of seed dicts with cx/cy/w/h in PROCESSED pixels.
			   Computed once on first access and cached.
	"""

	def __init__(self, source_seeds_dict: dict, geometry: object) -> None:
		# geometry is a FrameGeometry; typed as object to avoid a circular
		# import at the module level (frame_reader is not imported here).
		self._source = source_seeds_dict
		self._geometry = geometry
		# lazy cache: None means not yet computed
		self._processed_cache = None

	#============================================
	@property
	def geometry(self) -> object:
		"""The FrameGeometry this view was built against."""
		return self._geometry

	#============================================
	@property
	def bin_factor(self) -> int:
		"""Bin factor from the geometry; int >= 1."""
		return self._geometry.bin_factor

	#============================================
	@property
	def source(self) -> dict:
		"""Original source-pixel seeds dict; do not mutate."""
		return self._source

	#============================================
	@property
	def header(self) -> dict:
		"""Seeds header passthrough (track_runner_seeds version and any extras)."""
		# Return everything except the 'seeds' list
		return {k: v for k, v in self._source.items() if k != "seeds"}

	#============================================
	@property
	def seeds(self) -> list:
		"""Seed list with cx/cy/w/h in PROCESSED-pixel coords.

		Computed once on first access; cached on subsequent calls.
		Seeds without torso_box (not_in_frame) are passed through
		with no geometry keys (same as load_seeds behavior).
		"""
		if self._processed_cache is None:
			self._processed_cache = self._build_processed()
		return self._processed_cache

	#============================================
	def _build_processed(self) -> list:
		"""Project source seeds to processed-pixel coords via geometry."""
		out = []
		for src in self._source["seeds"]:
			# Seeds without torso_box (not_in_frame) carry no cx/cy/w/h
			if "cx" not in src or src.get("cx") is None:
				out.append(dict(src))
				continue
			cx_p, cy_p = self._geometry.source_to_processed(src["cx"], src["cy"])
			w_p, _ = self._geometry.source_to_processed_delta(src["w"], 0.0)
			h_p, _ = self._geometry.source_to_processed_delta(src["h"], 0.0)
			projected = dict(src)
			projected["cx"] = cx_p
			projected["cy"] = cy_p
			projected["w"] = w_p
			projected["h"] = h_p
			out.append(projected)
		return out

	#============================================
	def assert_geometry_match(self, geometry: object) -> None:
		"""Raise RuntimeError when geometry.bin_factor differs from view.bin_factor.

		Call this once at the top of any function that consumes view.seeds
		alongside a reader or geometry that may have been opened separately.
		Failing loudly here prevents silent double-conversion or mis-scale.

		Args:
			geometry: A FrameGeometry (or any object with .bin_factor) to
				compare against the view's own bin_factor.

		Raises:
			RuntimeError: If bin_factor values differ.
		"""
		if geometry.bin_factor != self.bin_factor:
			raise RuntimeError(
				f"SeedsView bin_factor mismatch: view was built at bin={self.bin_factor},"
				f" used with geometry at bin={geometry.bin_factor}."
				f" Rebuild the view with the correct geometry."
			)


#============================================

def load_seeds_view(path: str, geometry: object) -> SeedsView:
	"""Load seeds and return a SeedsView projected to the target geometry.

	Source-of-truth seeds are loaded as source-pixel coords (via load_seeds).
	The returned SeedsView exposes processed-pixel coords lazily through
	the geometry reference.  The view's assert_geometry_match method guards
	against accidental use with a mismatched geometry.

	Args:
		path: Path to the seeds JSON file.
		geometry: FrameGeometry for the target reader.  Must have
			source_to_processed, source_to_processed_delta, and bin_factor.

	Returns:
		SeedsView wrapping the source seeds and the geometry.
	"""
	source = load_seeds(path)
	return SeedsView(source, geometry)


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

	Returns an empty structure if the file does not exist. Accepts any
	header version listed in
	`tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]` and returns a
	dict shaped under the current schema. The supported set may grow on
	each schema bump; refer to that table for the authoritative list.

	Args:
		path: Path to the interval_scores.json (or legacy
			diagnostics.json) file.

	Returns:
		dict: Parsed scoring data with validated header, or empty
			structure if the file is absent.

	Raises:
		RuntimeError: If the file exists but the header version is not
			in `tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]`.
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {DIAGNOSTICS_HEADER_KEY: DIAGNOSTICS_HEADER_VALUE}
	with open(path, "r") as fh:
		data = json.load(fh)
	if not isinstance(data, dict):
		raise RuntimeError(f"diagnostics file did not parse as a mapping: {path}")
	# Validate the header key and version against the central
	# compatibility table per contract C9.
	header_val = data.get(DIAGNOSTICS_HEADER_KEY)
	if not isinstance(header_val, int) or not tr_schema.is_supported_artifact_schema(
		"diagnostics", header_val,
	):
		supported = sorted(tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"])
		raise RuntimeError(
			f"diagnostics file header mismatch in {path}: "
			f"expected {DIAGNOSTICS_HEADER_KEY} in {supported}, got {header_val}"
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

def _write_npz_atomic(path: str, arrays: dict) -> None:
	"""Write a dict of numpy arrays to an NPZ file atomically.

	Shared helper used by write_torso_box_coords.

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

def peek_torso_box_coords_schema(path: str) -> int | None:
	"""Read just the schema_version from a torso_box_coords.npz, no validation.

	Returns None if the file does not exist or the schema_version key is
	missing. Callers can use this to decide whether a full
	`load_torso_box_coords` would raise on an unsupported schema, without
	paying the schema-rejection cost or wrapping the load in try/except.

	Args:
		path: Path to the torso_box_coords.npz file.

	Returns:
		int schema version, or None if unavailable.
	"""
	if not os.path.isfile(path):
		return None
	with numpy.load(path, allow_pickle=False) as npz:
		if "schema_version" not in npz.files:
			return None
		return int(npz["schema_version"])


#============================================
def peek_diagnostics_schema(path: str) -> int | None:
	"""Read just the header version from an interval-scores JSON, no validation.

	Returns None if the file does not exist or the header key is missing or
	non-integer. Callers can use this to decide whether a full
	`load_diagnostics` would raise on an unsupported schema, without paying the
	schema-rejection cost or wrapping the load in try/except.

	Args:
		path: Path to the interval_scores.json (or legacy diagnostics.json) file.

	Returns:
		int header version, or None if unavailable.
	"""
	if not os.path.isfile(path):
		return None
	with open(path, "r") as fh:
		data = json.load(fh)
	if not isinstance(data, dict):
		return None
	header_val = data.get(DIAGNOSTICS_HEADER_KEY)
	if not isinstance(header_val, int):
		return None
	return header_val


#============================================
def load_torso_box_coords(path: str) -> dict:
	"""Load a unified torso_box_coords.npz file with all three paths merged.

	The unified artifact (a durable solved-result store) contains per-frame torso
	boxes (cx/cy/w/h) for all three interval paths: forward (FWD), backward (BWD),
	and blended (the production trajectory). Per-interval keys in the NPZ follow
	the pattern: `i<k>_{fwd,bwd,blended}_{cx,cy,w,h}` (uint16 arrays per schema v10+).

	Pre-race intervals (synthesized, scene-anchored per C4) write only the
	blended arrays; forward and backward keys are absent. The loader
	reconstructs `forward_path = None` and `backward_path = None` for those
	intervals so downstream code can detect pre-race intervals and skip them
	from FWD/BWD scoring logic via the same code path as the endpoint case.

	Returns a dict with the unified three-path shape:

		{
			"track_runner_intervals": 2,
			"solved_intervals": {
				"<fingerprint>": {
					"start_frame": int,
					"end_frame": int,
					"forward_path": [...] or None,
					"backward_path": [...] or None,
					"blended_path": [...],
				},
				...
			},
			"video_identity": {...},
			"solve_complete": bool,
		}

	All per-frame cx/cy/w/h values in the returned paths are SOURCE-frame
	pixels (written and stored in source space per the coordinate contract in
	docs/COORDINATE_SPACES.md).  To obtain a typed coord_space.SourceBox from
	a frame dict, call frame_dict_to_source_box(frame_dict).

	Returns an empty skeleton if the file does not exist. Raises if the file
	exists but has a different or missing schema_version.

	Args:
		path: Path to the torso_box_coords.npz file.

	Returns:
		dict with the shape described above.

	Raises:
		RuntimeError: If the schema version is not recognized or a declared
			per-interval array is missing.
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {
			INTERVALS_HEADER_KEY: INTERVALS_HEADER_VALUE,
			"solved_intervals": {},
		}
	with numpy.load(path, allow_pickle=False) as npz:
		schema_version = int(npz["schema_version"])
		# Per contract C9, validate against the central compatibility table.
		if not tr_schema.is_supported_artifact_schema(
			"torso_box_coords", schema_version,
		):
			supported = sorted(tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"])
			raise RuntimeError(
				f"torso_box_coords schema v{schema_version} is no longer supported; "
				f"expected version in {supported}. "
				f"Please re-solve the video to upgrade to schema v{max(supported)}"
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
			# blended arrays are always present
			blended_path = None
			cx_key = f"i{idx}_blended_cx"
			cy_key = f"i{idx}_blended_cy"
			w_key = f"i{idx}_blended_w"
			h_key = f"i{idx}_blended_h"
			if all(key in npz.files for key in (cx_key, cy_key, w_key, h_key)):
				cx_arr = npz[cx_key]
				cy_arr = npz[cy_key]
				w_arr = npz[w_key]
				h_arr = npz[h_key]
				blended_path = [
					{
						"cx": int(cx_arr[i]),
						"cy": int(cy_arr[i]),
						"w": int(w_arr[i]),
						"h": int(h_arr[i]),
					}
					for i in range(len(cx_arr))
				]
			# forward and backward arrays are optional (pre-race intervals only have blended)
			forward_path = None
			fwd_keys = (
				f"i{idx}_fwd_cx", f"i{idx}_fwd_cy",
				f"i{idx}_fwd_w", f"i{idx}_fwd_h"
			)
			if all(key in npz.files for key in fwd_keys):
				cx_arr = npz[fwd_keys[0]]
				cy_arr = npz[fwd_keys[1]]
				w_arr = npz[fwd_keys[2]]
				h_arr = npz[fwd_keys[3]]
				forward_path = [
					{
						"cx": int(cx_arr[i]),
						"cy": int(cy_arr[i]),
						"w": int(w_arr[i]),
						"h": int(h_arr[i]),
					}
					for i in range(len(cx_arr))
				]
			backward_path = None
			bwd_keys = (
				f"i{idx}_bwd_cx", f"i{idx}_bwd_cy",
				f"i{idx}_bwd_w", f"i{idx}_bwd_h"
			)
			if all(key in npz.files for key in bwd_keys):
				cx_arr = npz[bwd_keys[0]]
				cy_arr = npz[bwd_keys[1]]
				w_arr = npz[bwd_keys[2]]
				h_arr = npz[bwd_keys[3]]
				backward_path = [
					{
						"cx": int(cx_arr[i]),
						"cy": int(cy_arr[i]),
						"w": int(w_arr[i]),
						"h": int(h_arr[i]),
					}
					for i in range(len(cx_arr))
				]
			solved[fingerprint] = {
				"start_frame": start_frame,
				"end_frame": end_frame,
				"forward_path": forward_path,
				"backward_path": backward_path,
				"blended_path": blended_path,
			}
	# Internal consistency check: if video_identity is present, verify
	# its frame_count is consistent with the maximum end_frame from the
	# manifest. Mismatches indicate file corruption or a trimmed file.
	if video_identity is not None and "frame_count" in video_identity:
		persisted_frame_count = int(video_identity["frame_count"])
		max_end_frame = max((iv["end_frame"] for iv in solved.values()), default=0)
		if max_end_frame > 0 and persisted_frame_count < max_end_frame:
			raise RuntimeError(
				f"torso_box_coords.npz frame_count={persisted_frame_count} "
				f"but manifest has end_frame up to {max_end_frame}; "
				f"file is corrupt or was trimmed"
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

def _extract_source_box_coords(frame_obj) -> tuple:
	"""Extract (cx, cy, w, h) floats from a SOURCE-space frame box object.

	Accepts either:
	  - A coord_space.SourceBox typed primitive (validated via require_source_box).
	  - A plain dict with cx/cy/w/h keys (assumed SOURCE-frame; callers must
	    ensure they project to source before calling write_torso_box_coords).

	Typed primitives that are NOT SourceBox (e.g. ProcessedBox) are rejected
	loudly by require_source_box, so the write boundary fails loud on a
	wrong-space caller rather than silently storing processed coordinates.

	Args:
		frame_obj: SourceBox typed primitive or plain {cx, cy, w, h} dict.

	Returns:
		tuple: (cx, cy, w, h) as Python floats.

	Raises:
		ValueError: If frame_obj is a typed primitive that is not SourceBox.
	"""
	# typed primitive: validate space then extract
	if isinstance(frame_obj, (coord_space.SourceBox, coord_space.ProcessedBox,
		coord_space.SourcePoint, coord_space.ProcessedPoint)):
		# require_source_box raises ValueError for any non-SourceBox primitive
		coord_space.require_source_box(frame_obj)
		result = (float(frame_obj.cx), float(frame_obj.cy),
			float(frame_obj.w), float(frame_obj.h))
		return result
	# plain dict assumed SOURCE (walk_driver projects processed->source before writing)
	result = (float(frame_obj["cx"]), float(frame_obj["cy"]),
		float(frame_obj["w"]), float(frame_obj["h"]))
	return result


#============================================

def frame_dict_to_source_box(frame_dict: dict) -> coord_space.SourceBox:
	"""Wrap a loaded torso-box frame dict as a typed SourceBox.

	The npz store written by write_torso_box_coords and read by
	load_torso_box_coords is SOURCE-frame pixels per the coordinate contract
	(docs/COORDINATE_SPACES.md). This accessor lets callers obtain a typed
	SourceBox from a loaded frame dict so they can pass it to coord_space
	conversions without re-introducing implicit space tracking.

	Args:
		frame_dict: One element from a forward_path / backward_path /
			blended_path list returned by load_torso_box_coords. Must have
			cx, cy, w, h keys.

	Returns:
		coord_space.SourceBox with the same cx/cy/w/h values.
	"""
	source_box = coord_space.SourceBox(
		cx=float(frame_dict["cx"]),
		cy=float(frame_dict["cy"]),
		w=float(frame_dict["w"]),
		h=float(frame_dict["h"]),
	)
	return source_box


#============================================

def _round_clip_uint16(arr: numpy.ndarray) -> numpy.ndarray:
	"""Round float coords to nearest int, clip to [0, 65535], cast to uint16.

	Per C12.4, per-frame torso-box coords are pixel-snapped on disk. uint16
	covers up to ~16K frame dimensions (max 65535); existing footage fits
	easily. See docs/TR_SCHEMA_VERSION_HISTORY.md v10 for the policy.
	"""
	clipped = numpy.clip(numpy.round(arr), 0, 65535)
	out = clipped.astype(numpy.uint16)
	return out


#============================================

def write_torso_box_coords(path: str, cache_data: dict) -> None:
	"""Write unified torso box coordinates to an NPZ file.

	Extracts forward_path, backward_path, and blended_path (list-of-dicts
	with cx/cy/w/h) from each solved interval and writes three sets of
	four uint16 arrays per interval when all three paths are present:
	- `i<k>_fwd_{cx,cy,w,h}` for forward interval path
	- `i<k>_bwd_{cx,cy,w,h}` for backward interval path
	- `i<k>_blended_{cx,cy,w,h}` for blended interval path

	Per C12.4, coordinates are rounded to nearest integer and cast to
	uint16 (pixel-snapped, no subpixel precision). Bounds are clipped
	to [0, 65535] to cover up to ~16K frame dimensions (uint16 max=65535).

	Pre-race intervals (per C4) write only the blended arrays; forward and
	backward arrays are omitted. The loader reconstructs forward_path = None
	and backward_path = None for such intervals.

	The manifest maps each fingerprint to an array_index, start_frame, and
	end_frame so the loader can reassemble the in-memory shape and
	distinguish pre-race from post-race intervals.

	Atomic write via a sibling temp file and `os.replace`.

	SOURCE-space boundary: all per-frame box objects in forward_path,
	backward_path, and blended_path must be in SOURCE pixels.  If a caller
	passes typed coord_space primitives, they must be SourceBox (not ProcessedBox);
	_extract_source_box_coords raises ValueError on a wrong-space typed input,
	failing loud rather than silently storing processed coordinates.  Plain dicts
	are accepted as-is and assumed SOURCE (walk_driver projects processed->source
	before calling here).  See docs/COORDINATE_SPACES.md.

	Args:
		path: Output NPZ file path.
		cache_data: Dict shaped like `load_torso_box_coords` returns --
			at minimum `{"solved_intervals": {fp: {start_frame, end_frame,
			forward_path, backward_path, blended_path}}}`. Unknown top-level
			keys are tolerated; only `video_identity` and `solve_complete`
			are persisted beyond the manifest and per-interval arrays. The
			per-interval solved data is a durable artifact, not cache.
	"""
	solved_intervals = cache_data.get("solved_intervals", {}) or {}
	manifest = []
	arrays = {}
	for idx, (fingerprint, entry) in enumerate(solved_intervals.items()):
		start_frame = int(entry["start_frame"])
		end_frame = int(entry["end_frame"])
		fwd = entry.get("forward_path")
		bwd = entry.get("backward_path")
		blended = entry.get("blended_path") or []
		# always write blended path; each frame goes through _extract_source_box_coords
		# so typed ProcessedBox passed here raises ValueError at the boundary
		for direction_tag, track in (("blended", blended),):
			coords = [_extract_source_box_coords(s) for s in track]
			cx_in = numpy.asarray([c[0] for c in coords], dtype=numpy.float32)
			cy_in = numpy.asarray([c[1] for c in coords], dtype=numpy.float32)
			w_in = numpy.asarray([c[2] for c in coords], dtype=numpy.float32)
			h_in = numpy.asarray([c[3] for c in coords], dtype=numpy.float32)
			arrays[f"i{idx}_{direction_tag}_cx"] = _round_clip_uint16(cx_in)
			arrays[f"i{idx}_{direction_tag}_cy"] = _round_clip_uint16(cy_in)
			arrays[f"i{idx}_{direction_tag}_w"] = _round_clip_uint16(w_in)
			arrays[f"i{idx}_{direction_tag}_h"] = _round_clip_uint16(h_in)
		# write forward and backward only if both present (post-race intervals)
		if fwd is not None and bwd is not None:
			for direction_tag, track in (("fwd", fwd), ("bwd", bwd)):
				coords = [_extract_source_box_coords(s) for s in track]
				cx_in = numpy.asarray([c[0] for c in coords], dtype=numpy.float32)
				cy_in = numpy.asarray([c[1] for c in coords], dtype=numpy.float32)
				w_in = numpy.asarray([c[2] for c in coords], dtype=numpy.float32)
				h_in = numpy.asarray([c[3] for c in coords], dtype=numpy.float32)
				arrays[f"i{idx}_{direction_tag}_cx"] = _round_clip_uint16(cx_in)
				arrays[f"i{idx}_{direction_tag}_cy"] = _round_clip_uint16(cy_in)
				arrays[f"i{idx}_{direction_tag}_w"] = _round_clip_uint16(w_in)
				arrays[f"i{idx}_{direction_tag}_h"] = _round_clip_uint16(h_in)
		manifest.append({
			"fingerprint": fingerprint,
			"start_frame": start_frame,
			"end_frame": end_frame,
			"array_index": idx,
		})
	# top-level metadata
	arrays["schema_version"] = numpy.asarray(
		tr_schema.SCHEMA_VERSION, dtype=numpy.int32
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

def interval_fingerprint(
	seed_start: dict,
	seed_end: dict,
	solver_tag: str = "",
) -> str:
	"""Compute a deterministic lookup key from two seed endpoint states.

	The fingerprint encodes frame_index and position (cx, cy, w, h rounded
	to 2 decimal places) for both seeds. Any change in seed position or
	frame index produces a different fingerprint, so stale results are never reused.

	When `solver_tag` is non-empty, it is appended. This lets the interval
	solver invalidate the store when its own semantics change (new gates,
	new blend rule, observer version bump) without requiring seed edits.
	Both `solve_all_intervals` and `cli._mode_refine` MUST pass the same
	solver_tag for store hits to work.

	The unified `solver_tag` encodes the geometry-affecting schema version
	only (format: `schema_v<N>`). How an interval was solved (hermite vs
	blob propagator) is metadata on the result, not part of the fingerprint.

	Args:
		seed_start: Seed state dict at the start of the interval.
		seed_end: Seed state dict at the end of the interval.
		solver_tag: Optional short string identifying the geometry-affecting
			schema version. When empty, no tag is appended (legacy behavior).
			Current standard is `schema_v<N>` where N is the geometry-
			affecting schema version.

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
			"race_start_interval": list(pre_race_reference["race_start_interval"]),
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

	# test interval_fingerprint determinism
	seed_a = {"frame_index": 100, "cx": 1731.5, "cy": 629.5, "w": 39.0, "h": 59.0}
	seed_b = {"frame_index": 450, "cx": 1700.0, "cy": 600.0, "w": 38.0, "h": 58.0}
	fp = interval_fingerprint(seed_a, seed_b)
	assert fp == "100|1731.50|629.50|39.00|59.00|450|1700.00|600.00|38.00|58.00"
	# same inputs must produce same fingerprint
	fp2 = interval_fingerprint(seed_a, seed_b)
	assert fp == fp2

	print("all round-trip checks passed")
