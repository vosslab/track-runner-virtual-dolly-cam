"""state_io.py

Read/write for track_runner per-video state files.

Seeds files (JSON) store human-authored annotations. Under contract C1
a seed is a torso box drawn by a human. The canonical on-disk shape
(v3) is four fields per seed: frame_index, torso_box, status, pass.
Convenience geometry (cx/cy/w/h) is derived in memory from torso_box
at load time and discarded at write time; it never appears on disk.

SeedsView:
The source-of-truth seeds dict always uses SOURCE-pixel coords.
SeedsView wraps it and exposes PROCESSED-pixel coords via the
held FrameGeometry reference. Use load_seeds_view(path, geometry)
to obtain a view for the walker pipeline; load_seeds(path) returns
the canonical SOURCE-coordinate seeds for callers such as the UI,
encoder, and diagnostic tools.

Torso-box-coords files (NPZ) store the solved per-frame blended interval path
under the format rule "dense per-frame numeric series -> NPZ". Per
interval, four uint16 arrays (cx/cy/w/h) plus a small JSON manifest
mapping fingerprint to array_index. Coordinates are rounded to nearest
integer before storage (pixel-snapped, no subpixel precision retained).
Schema 15 also transports per-frame raw FWD/BWD agreement as uint8 `conf`;
raw coordinate paths are retained only when their quantized geometry differs.

Interval-scores files (JSON) own per-interval diagnostic and review summaries.
No per-frame trajectory or durable operational state is stored there.
"""

# Standard Library
import json
import os
import tempfile

# local repo modules
import tr_schema

#============================================
# Unified schema version per contract C10: single source of truth for all
# schema versioning across track_runner. This convenience re-export keeps
# state I/O callers aligned with the authority in tr_schema.
SCHEMA_VERSION = tr_schema.SCHEMA_VERSION

# Current interval-score header value follows the centralized schema authority.
INTERVAL_SCORES_HEADER_VALUE = SCHEMA_VERSION

#============================================

# header key and version for seeds JSON files
# v3: canonical four-field schema (frame_index, torso_box, status, pass)
SEEDS_HEADER_KEY = "track_runner_seeds"
SEEDS_HEADER_VALUE = 3

# Header key and version for current interval-score JSON files.
# The field name is retained because it is part of the current persisted layout.
INTERVAL_SCORES_HEADER_KEY = "track_runner_diagnostics"
REQUIRED_INTERVAL_SCORE_KEYS = frozenset([
	"agreement", "velocity_consistency", "size_consistency",
	"motion_quality", "occlusion_fraction", "confidence_tier",
	"failure_reasons", "warning_flags",
])


#============================================
def _validate_interval_score_records(intervals: list, context: str) -> None:
	"""Require every score record that the current reader consumes."""
	for interval in intervals:
		if not isinstance(interval, dict) or "interval_score" not in interval:
			start_frame = interval.get("start_frame", "?") if isinstance(interval, dict) else "?"
			end_frame = interval.get("end_frame", "?") if isinstance(interval, dict) else "?"
			raise RuntimeError(
				f"{context}: interval ({start_frame}, {end_frame}) lacks interval_score"
			)
		score = interval["interval_score"]
		if not isinstance(score, dict) or not REQUIRED_INTERVAL_SCORE_KEYS.issubset(score):
			raise RuntimeError(f"{context}: interval_score is incomplete")

# Canonical on-disk allow-list for v3 seeds. The loader rejects other stored
# keys; the writer also drops in-memory derived state before serializing.
CANONICAL_SEED_KEYS = frozenset(
	["frame_index", "torso_box", "status", "pass"]
)
VALID_SEED_STATUSES = frozenset(
	["visible", "partial", "approximate", "not_in_frame"]
)

# derived-in-memory keys added by _derive_seed_geometry after load.
# These are NOT on disk but are needed by interval_fingerprint,
# velocity_model._compare_seed_positions, and UI code that reads cx/cy/w/h.
# write_seeds strips them back out before serializing.
DERIVED_SEED_KEYS = frozenset(["cx", "cy", "w", "h"])

def _derive_seed_geometry(seed: dict) -> None:
	"""Derive cx/cy/w/h from torso_box and attach in memory.

	Downstream consumers (interval_fingerprint, velocity_model, UI) see
	these convenience values. They are never stored; write_seeds strips
	them again.

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
	"""Return an approximate seed's frame when its geometry is missing.

	Args:
		seed: Seed dictionary to validate.

	Returns:
		Frame index if approximate seed is missing torso_box, else None.
	"""
	status = seed.get("status")
	if status == "approximate":
		if "torso_box" not in seed or seed["torso_box"] is None:
			frame_index = seed.get("frame_index")
			return frame_index
	return None

#============================================

def _validate_loaded_seed(seed: object, path: str) -> None:
	"""Reject noncanonical seed records before they reach solver consumers."""
	if not isinstance(seed, dict):
		raise RuntimeError(f"seed entry in {path} is not a mapping")
	extra_keys = set(seed) - CANONICAL_SEED_KEYS
	if extra_keys:
		raise RuntimeError(
			f"seed entry in {path} has noncanonical keys: {sorted(extra_keys)}"
		)
	status = seed["status"]
	if status not in VALID_SEED_STATUSES:
		raise RuntimeError(f"seed entry in {path} has invalid status '{status}'")
	if status == "not_in_frame":
		if "torso_box" in seed:
			raise RuntimeError(f"not_in_frame seed in {path} must omit torso_box")
	elif "torso_box" not in seed:
		raise RuntimeError(f"{status} seed in {path} must include torso_box")


#============================================

def load_seeds(path: str) -> dict:
	"""Load a seeds JSON file and normalize to the in-memory v3 shape.

	Accepts only the current v3 header. The returned in-memory dict:

	- Re-derives cx/cy/w/h from torso_box so interval_fingerprint,
	  velocity_model, and UI code see the geometry they expect.
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
	# Reject obsolete headers and noncanonical records at the storage boundary.
	header_val = data.get(SEEDS_HEADER_KEY)
	if header_val != SEEDS_HEADER_VALUE:
		raise RuntimeError(
			f"seeds file header mismatch in {path}: "
			f"expected {SEEDS_HEADER_KEY}={SEEDS_HEADER_VALUE}, got {header_val}"
		)
	if "seeds" in data and isinstance(data["seeds"], list):
		cleaned = []
		for seed in data["seeds"]:
			_validate_loaded_seed(seed, path)
			# re-derive cx/cy/w/h from torso_box for downstream consumers
			_derive_seed_geometry(seed)
			cleaned.append(seed)
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
	# Warn when an approximate seed has no usable geometry.
	if "seeds" in seeds_data and isinstance(seeds_data["seeds"], list):
		bad_frames = []
		for seed in seeds_data["seeds"]:
			bad_frame = validate_seed(seed)
			if bad_frame is not None:
				bad_frames.append(bad_frame)
		if bad_frames:
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

def load_interval_scores(path: str) -> dict:
	"""Load the current per-interval scoring JSON file.

	This file owns per-interval scoring data plus optional race-phase
	and cyclical-prior context.

	Returns an empty structure if the file does not exist. Existing files
	must use the current supported schema and complete current score records.
	Older shapes are rejected so solve can regenerate them rather than
	converting or partially reusing them.

	Args:
		path: Path to the current interval_scores.json file.

	Returns:
		dict: Parsed scoring data with validated header, or empty
			structure if the file is absent.

	Raises:
		RuntimeError: If the file exists but the header version is not
			in `tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"]`.
	"""
	# return empty structure if file does not exist
	if not os.path.isfile(path):
		return {INTERVAL_SCORES_HEADER_KEY: INTERVAL_SCORES_HEADER_VALUE}
	with open(path, "r") as fh:
		data = json.load(fh)
	if not isinstance(data, dict):
		raise RuntimeError(f"interval-score file did not parse as a mapping: {path}")
	# Validate the current header at the storage boundary.
	header_val = data.get(INTERVAL_SCORES_HEADER_KEY)
	if not isinstance(header_val, int) or not tr_schema.is_supported_artifact_schema(
		"diagnostics", header_val,
	):
		supported = sorted(tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["diagnostics"])
		raise RuntimeError(
			f"interval-score file header mismatch in {path}: "
			f"expected {INTERVAL_SCORES_HEADER_KEY} in {supported}, got {header_val}"
		)
	if "fps" not in data:
		raise RuntimeError(f"interval-score file lacks fps: {path}")
	fps = data["fps"]
	if not isinstance(fps, (int, float)):
		raise RuntimeError(f"interval-score file lacks numeric fps: {path}")
	if "video_identity" not in data:
		raise RuntimeError(f"interval-score file lacks video_identity: {path}")
	video_identity = data["video_identity"]
	if not isinstance(video_identity, dict):
		raise RuntimeError(f"interval-score file lacks video_identity: {path}")
	if "intervals" not in data:
		raise RuntimeError(f"interval-score file lacks intervals: {path}")
	intervals = data["intervals"]
	if not isinstance(intervals, list):
		raise RuntimeError(f"interval-score file lacks interval list: {path}")
	_validate_interval_score_records(
		intervals, f"stale interval scores in {path}; delete and re-solve",
	)
	return data


#============================================

def write_interval_scores(path: str, interval_scores_data: dict) -> None:
	"""Write current interval scores to a JSON file.

	Ensures the required header key is present before writing.

	Args:
		path: Output file path.
		interval_scores_data: Interval-score dictionary to write.
	"""
	if "fps" not in interval_scores_data:
		raise RuntimeError("interval scores require fps")
	fps = interval_scores_data["fps"]
	if not isinstance(fps, (int, float)):
		raise RuntimeError("interval scores require numeric fps")
	if "video_identity" not in interval_scores_data:
		raise RuntimeError("interval scores require video_identity")
	video_identity = interval_scores_data["video_identity"]
	if not isinstance(video_identity, dict):
		raise RuntimeError("interval scores require video_identity")
	if "intervals" not in interval_scores_data:
		raise RuntimeError("interval scores require intervals")
	intervals = interval_scores_data["intervals"]
	if not isinstance(intervals, list):
		raise RuntimeError("interval scores require an intervals list")
	_validate_interval_score_records(intervals, "interval scores")
	# Diagnostics are advisory interval scores only.  Emit the owned fields
	# explicitly so solve-state metadata cannot leak back into this artifact.
	payload = {
		INTERVAL_SCORES_HEADER_KEY: INTERVAL_SCORES_HEADER_VALUE,
		"fps": fps,
		"video_identity": video_identity,
		"intervals": intervals,
	}
	# atomic write: temp file + rename to avoid corrupt file on interruption
	dir_path = os.path.dirname(os.path.abspath(path))
	fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp.json")
	try:
		with os.fdopen(fd, "w") as fh:
			json.dump(payload, fh, indent=2)
		os.replace(tmp_path, path)
	except Exception:
		if os.path.exists(tmp_path):
			os.unlink(tmp_path)
		raise


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

	When `solver_tag` is non-empty, it is appended. Both solve and refine pass
	the same current storage-schema tag so cache hits have the same persisted
	contract. Method changes refresh derived values with `solve`; they do not
	add algorithm details to the fingerprint.

	The unified `solver_tag` encodes the current schema only (format:
	`schema_v<N>`). How an interval was solved (analytical or blob producer) is
	metadata on the result, not part of the fingerprint.

	Args:
		seed_start: Seed state dict at the start of the interval.
		seed_end: Seed state dict at the end of the interval.
		solver_tag: Optional short string identifying the current schema
			version. When empty, no tag is appended. Current standard is
			`schema_v<N>` where N is the current schema version.

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
def write_solver_interval_scores(
	solve_data: dict,
	path: str,
	fps: float,
) -> None:
	"""Serialize interval solver scores to a current JSON file.

	Strips non-serializable objects and builds a compact summary
	from the raw solver output before writing.

	Args:
		solve_data: Dict from interval_solver.solve_all_intervals().
		path: Output JSON file path.
		fps: Video fps for inclusion in the file.
	"""
	# build a JSON-safe summary (do not write full per-frame trajectory)
	intervals_summary = []
	for iv in solve_data.get("intervals", []):
		score = iv["interval_score"]
		entry = {
			"start_frame": iv["start_frame"],
			"end_frame": iv["end_frame"],
			"start_s": round(iv["start_frame"] / max(1.0, fps), 3),
			"end_s": round(iv["end_frame"] / max(1.0, fps), 3),
		}
		if "confidence_tier" not in score:
			raise RuntimeError("interval score missing confidence_tier")
		confidence_tier = score["confidence_tier"]
		failure_reasons = list(score.get("failure_reasons", []))
		agreement = float(score.get("agreement", 0.0))
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

	diag_out = {
		INTERVAL_SCORES_HEADER_KEY: INTERVAL_SCORES_HEADER_VALUE,
		"fps": round(fps, 6),
		"intervals": intervals_summary,
		"video_identity": solve_data["video_identity"],
	}
	write_interval_scores(path, diag_out)


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
			"confidence_p10": round(float(ad["confidence_p10"]), 4),
			"confidence_p50": round(float(ad["confidence_p50"]), 4),
			"confidence_p90": round(float(ad["confidence_p90"]), 4),
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
