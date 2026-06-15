"""Stage 4 walker input bundle seam.

Defines the self-contained input the windowed walker needs to walk one
interval in one direction, plus a builder that assembles two bundles (FWD
and BWD) from the run-invariant state already live at the
`interval_solver.solve_interval_analytical` seam, and an injectable
invocation seam that hands a bundle to a walker callable.

This module lives next to the Stage 4 integration seam (not inside the core
`track_runner/blob_walk/` package) so the core walker does not absorb
pipeline orchestration.

Maintained invariant (Hermite independence): the bundle carries the seed,
frame range, direction, torso-unit scale, and the candidate-lattice source
plumbing (reader + scene_transform + residual sampling). It deliberately
does NOT carry the Hermite raw_pred path. The walker walks from the seed
over the image-derived candidate lattice; snapping onto raw_pred is v1's
fatal flaw and is rejected by construction. The data-boundary test pairs a
positive assertion (the bundle carries seed + lattice source) with a
negative assertion (no Hermite path field).

The walker IS the Stage-4 producer on promoted intervals (blob_pass=True with
a reader present). `interval_solver.solve_interval_analytical` calls
`walk_bundle_to_path_with_coverage` for both the FWD and BWD passes.
"""

# Standard Library
import dataclasses
import typing

# local repo modules
import blob_walk.walk_walker as walk_walker


#============================================
@dataclasses.dataclass
class WalkCoverage:
	"""Named coverage report returned by walk_bundle_to_path_with_coverage.

	Carries two distinct accepted-frame counts so the Stage-4 fallback gate
	can measure post-seed trajectory evidence separately from total coverage.
	Using a dataclass (rather than a bare integer) forces the gate to access
	the intended field by name, making the seed-only vs. zero-accept
	distinction explicit at the call site.

	Attributes:
		accepted_count: Total frames the walker marked "accepted" for this
			pass, including the seed frame if it was observed via bootstrap.
			Preserved for telemetry and any future consumer; meaning
			unchanged from the previous bare-integer return.
		post_seed_accepted: Accepted frames excluding the pass's own seed
			frame. A pass with post_seed_accepted == 0 carries no
			post-seed trajectory evidence even if accepted_count == 1
			(bootstrap-only stall), and must fall back to Hermite.
	"""
	accepted_count: int
	post_seed_accepted: int


#============================================
@dataclasses.dataclass
class WalkerInputBundle:
	"""Self-contained input to walk one interval in one direction.

	One bundle drives one pass (FWD or BWD). FWD and BWD each receive their
	own bundle and produce their own pass-local standalone path, preserving
	contract C9 (FWD/BWD independence).

	The candidate lattice is not a pre-built array. It is the per-frame
	`corridor_blobs` list the walker gathers internally via
	`residual_motion.observe_blob_at` + trace_sink. The bundle therefore
	carries the lattice *source* plumbing (reader, scene_transform, fps,
	stride, residual sampling parameters) rather than a materialized lattice.
	The seed anchors the walk and supplies the torso-unit scale (seed w/h),
	the contract C2 unit for all spatial thresholds.

	The Hermite raw_pred path is intentionally absent. The walker is
	Hermite-independent; it must be free to diverge from a failed Hermite
	path. A data-boundary test asserts no Hermite path is reachable through
	this bundle.

	Attributes:
		seed: Pass anchor seed dict with frame_index, cx, cy, w, h. FWD
			anchors on the left seed, BWD on the right seed.
		neighbor_seed: The opposite-end seed dict (termination target and
			source of neighbor geometry for per-frame size interpolation).
		start_frame: Interval start frame index (left seed frame).
		end_frame: Interval end frame index (right seed frame).
		direction_sign: +1 for FWD (forward stepping), -1 for BWD (backward
			stepping). This is the walker `sign` argument.
		torso_width: Seed torso width in PROCESSED pixels. The contract C2
			unit; spatial thresholds inside the walker are expressed as
			multiples of this.
		torso_height: Seed torso height in PROCESSED pixels.
		reader: Video FrameReader; the candidate-lattice source.
		scene_transform: SceneTransform for pixel<->scene conversion; part of
			the candidate-lattice source plumbing.
		fps: Video frame rate (passed to observe_blob_at).
		stride: Residual neighbor stride (passed to observe_blob_at).
		precomputed_store: Per-interval residual store (image-derived data
			only) so the walker reuses the Stage 4 sequential-read
			optimization instead of building its own cache. May be None.
	"""
	seed: dict
	neighbor_seed: dict
	start_frame: int
	end_frame: int
	direction_sign: int
	torso_width: float
	torso_height: float
	reader: object
	scene_transform: object
	fps: float
	stride: int
	precomputed_store: object = None


#============================================
def build_walker_bundles_for_interval(
	seed_start: dict,
	seed_end: dict,
	reader: object,
	scene_transform: object,
	fps: float,
	stride: int,
	precomputed_store: object = None,
) -> tuple:
	"""Build the FWD and BWD walker bundles for one promoted interval.

	The caller (Stage 4) builds both bundles from the interval seed pair and
	the run-invariant state already present at the
	`solve_interval_analytical` seam. The Stage 3 Hermite-only confidence
	tier has already decided promotion before this runs (Stage-3-first), so
	walker output cannot influence eligibility.

	FWD anchors on `seed_start` and targets `seed_end` (sign +1). BWD anchors
	on `seed_end` and targets `seed_start` (sign -1). Each bundle's
	torso-unit scale comes from its own anchor seed, the contract C2 unit.

	Args:
		seed_start: Left interval seed dict (frame_index, cx, cy, w, h).
		seed_end: Right interval seed dict (same fields).
		reader: Video FrameReader (candidate-lattice source).
		scene_transform: SceneTransform for coordinate conversion.
		fps: Video frame rate.
		stride: Residual neighbor stride.
		precomputed_store: Per-interval residual store (image-derived only)
			or None.

	Returns:
		(forward_bundle, backward_bundle) tuple of WalkerInputBundle. Note
		the Hermite raw_pred is NOT read here and is not a bundle field.
	"""
	start_frame = int(seed_start["frame_index"])
	end_frame = int(seed_end["frame_index"])

	# FWD bundle: anchor on the left seed, step toward the right seed.
	# Torso-unit scale is the left seed's box (contract C2 unit).
	forward_bundle = WalkerInputBundle(
		seed=seed_start,
		neighbor_seed=seed_end,
		start_frame=start_frame,
		end_frame=end_frame,
		direction_sign=1,
		torso_width=float(seed_start["w"]),
		torso_height=float(seed_start["h"]),
		reader=reader,
		scene_transform=scene_transform,
		fps=fps,
		stride=stride,
		precomputed_store=precomputed_store,
	)

	# BWD bundle: anchor on the right seed, step toward the left seed.
	# Torso-unit scale is the right seed's box (contract C2 unit).
	backward_bundle = WalkerInputBundle(
		seed=seed_end,
		neighbor_seed=seed_start,
		start_frame=start_frame,
		end_frame=end_frame,
		direction_sign=-1,
		torso_width=float(seed_end["w"]),
		torso_height=float(seed_end["h"]),
		reader=reader,
		scene_transform=scene_transform,
		fps=fps,
		stride=stride,
		precomputed_store=precomputed_store,
	)

	return forward_bundle, backward_bundle


#============================================
def run_walker_pass(
	bundle: WalkerInputBundle,
	walker_callable: typing.Callable,
) -> object:
	"""Run one walker pass by handing a bundle to an injectable walker.

	This is the explicit, injectable invocation seam. Stage 4 passes the
	bundle and a walker callable; tests pass a fake walker that records the
	bundle it received. The real walker (`walk_walker.walk_one_direction`)
	is wired separately; this seam only establishes the interface, so default
	solve behavior is unchanged.

	The seam intentionally does NOT route through the I/O-heavy
	`walk_driver.run_interval_walk` (which writes CSV/PNG tiles). The real
	caller invokes `walk_one_direction` directly without those render side effects.

	Args:
		bundle: The WalkerInputBundle for one direction.
		walker_callable: A callable that takes one WalkerInputBundle and
			returns a standalone per-direction path (the real walker, or a
			fake in tests).

	Returns:
		Whatever the walker callable returns (its standalone direction path).
	"""
	pass_result = walker_callable(bundle)
	return pass_result


#============================================
class _NullDebugLog:
	"""No-op stand-in for walk_debug_log.DebugLogWriter.

	walk_one_direction requires a debug_log with a write_row method to which it
	emits per-frame CSV telemetry. The real DebugLogWriter opens a CSV file. In
	the solver (Stage 4) path we want the walker's geometry, not its diagnostic
	CSV, so this duck-typed sink swallows every row and writes nothing to disk.
	This is how the solver seam stays free of the driver's CSV/PNG side effects
	(it does NOT route through walk_driver.run_interval_walk).
	"""

	def write_row(self, row: object) -> None:
		"""Discard the row; the solver path keeps no walker CSV."""
		return None


#============================================
def _build_full_span_path(
	direction_path: list,
	start_frame: int,
	end_frame: int,
	seed_start: dict,
	seed_end: dict,
) -> list:
	"""Project a walker direction_path into a full-span aligned state list.

	The solver's FWD/BWD paths are one state dict per frame from start_frame to
	end_frame inclusive, chronological (index 0 == start_frame), and blend_paths
	/ compute_agreement align the two passes slot-by-slot. The walker's
	direction_path may be shorter (the walk can stop early) and is in walk order
	(increasing frames for FWD, decreasing for BWD). This helper:

	  - reindexes walker rows by absolute frame_index (order-independent);
	  - forces both interval endpoints to the bracketing seed boxes (the seed
	    boxes are exact, matching the pure-Hermite propagator endpoints);
	  - fills any interior frame the walker did not emit by linear interpolation
	    between the nearest bracketing emitted frames (or holds the last/first
	    emitted box past the walk's stop point), so FWD and BWD cover exactly
	    the same frame span and the scored span never silently shrinks.

	The result is a chronological list of {cx, cy, w, h, conf, source} dicts of
	length (end_frame - start_frame + 1), in the same PROCESSED pixel space the
	walker and the solver share (both are fed the same scene_transform and
	reader, so no coordinate conversion is needed at any bin_factor). The shape
	matches the pure-Hermite propagator output exactly.

	Args:
		direction_path: walker WalkSummary.direction_path (PROCESSED pixels,
			each row carrying frame_index, cx, cy, w, h, conf, status).
		start_frame: interval start (left seed) absolute frame index.
		end_frame: interval end (right seed) absolute frame index.
		seed_start: left seed dict (cx, cy, w, h) for the start endpoint.
		seed_end: right seed dict (cx, cy, w, h) for the end endpoint.

	Returns:
		Full-span chronological list of solved state dicts.
	"""
	# index emitted walker rows by absolute frame so walk order does not matter
	by_frame = {}
	for row in direction_path:
		frame_index = int(row["frame_index"])
		# guard against duplicate emission: keep the first row for a frame
		if frame_index not in by_frame:
			by_frame[frame_index] = row

	# endpoints are anchored to the seed boxes (the propagators hard-anchor
	# the two interval endpoints at the seeds). Anchoring here keeps the
	# blend endpoints exact.
	endpoint_states = {
		start_frame: {
			"cx": float(seed_start["cx"]),
			"cy": float(seed_start["cy"]),
			"w": float(seed_start["w"]),
			"h": float(seed_start["h"]),
		},
		end_frame: {
			"cx": float(seed_end["cx"]),
			"cy": float(seed_end["cy"]),
			"w": float(seed_end["w"]),
			"h": float(seed_end["h"]),
		},
	}

	span = end_frame - start_frame + 1
	full_span = []
	for offset in range(span):
		frame_index = start_frame + offset
		is_endpoint = frame_index == start_frame or frame_index == end_frame
		if is_endpoint:
			anchor = endpoint_states[frame_index]
			state = {
				"cx": anchor["cx"],
				"cy": anchor["cy"],
				"w": anchor["w"],
				"h": anchor["h"],
				"conf": 1.0,
				"source": "propagated",
			}
			full_span.append(state)
			continue

		walker_row = by_frame.get(frame_index)
		if walker_row is not None:
			state = {
				"cx": float(walker_row["cx"]),
				"cy": float(walker_row["cy"]),
				"w": float(walker_row["w"]),
				"h": float(walker_row["h"]),
				"conf": float(walker_row["conf"]),
				"source": "propagated",
			}
			full_span.append(state)
			continue

		# frame the walker never emitted (it stopped early): fill by linear
		# interpolation between the nearest emitted frames, holding the edge
		# box when only one side is available.
		filled = _interpolate_missing_frame(
			frame_index, by_frame, start_frame, end_frame, endpoint_states,
		)
		full_span.append(filled)

	return full_span


#============================================
def _interpolate_missing_frame(
	frame_index: int,
	by_frame: dict,
	start_frame: int,
	end_frame: int,
	endpoint_states: dict,
) -> dict:
	"""Fill one un-emitted interior frame by bracketing linear interpolation.

	Searches outward for the nearest emitted (or endpoint-anchored) frame below
	and above `frame_index`, then linearly blends their boxes by frame distance.
	When only one side exists, holds that box. These frames carry no walker
	observation; their box is purely interpolated.
	"""
	# nearest emitted-or-anchor frame at or below frame_index (exclusive of it)
	lower_frame = None
	for cand in range(frame_index - 1, start_frame - 1, -1):
		if cand in by_frame or cand in endpoint_states:
			lower_frame = cand
			break
	# nearest emitted-or-anchor frame at or above frame_index (exclusive of it)
	upper_frame = None
	for cand in range(frame_index + 1, end_frame + 1):
		if cand in by_frame or cand in endpoint_states:
			upper_frame = cand
			break

	lower_box = _box_at(lower_frame, by_frame, endpoint_states)
	upper_box = _box_at(upper_frame, by_frame, endpoint_states)

	if lower_box is not None and upper_box is not None:
		# linear blend weighted by frame distance
		full = float(upper_frame - lower_frame)
		w_upper = (frame_index - lower_frame) / full
		w_lower = 1.0 - w_upper
		cx = w_lower * lower_box["cx"] + w_upper * upper_box["cx"]
		cy = w_lower * lower_box["cy"] + w_upper * upper_box["cy"]
		box_w = w_lower * lower_box["w"] + w_upper * upper_box["w"]
		box_h = w_lower * lower_box["h"] + w_upper * upper_box["h"]
	elif lower_box is not None:
		cx, cy, box_w, box_h = (
			lower_box["cx"], lower_box["cy"], lower_box["w"], lower_box["h"],
		)
	else:
		# upper_box must exist: start_frame is always an anchored endpoint, so
		# at least one side is reachable for any interior frame.
		if upper_box is None:
			raise RuntimeError(f"no bracketing frame found for frame {frame_index}")
		cx, cy, box_w, box_h = (
			upper_box["cx"], upper_box["cy"], upper_box["w"], upper_box["h"],
		)

	filled = {
		"cx": float(cx),
		"cy": float(cy),
		"w": float(box_w),
		"h": float(box_h),
		"conf": 0.1,
		"source": "propagated",
	}
	return filled


#============================================
def _box_at(frame_index: object, by_frame: dict, endpoint_states: dict) -> object:
	"""Return the cx/cy/w/h box for a frame from walker rows or seed anchors."""
	if frame_index is None:
		return None
	if frame_index in by_frame:
		row = by_frame[frame_index]
		box = {
			"cx": float(row["cx"]),
			"cy": float(row["cy"]),
			"w": float(row["w"]),
			"h": float(row["h"]),
		}
		return box
	anchor = endpoint_states[frame_index]
	box = {
		"cx": anchor["cx"],
		"cy": anchor["cy"],
		"w": anchor["w"],
		"h": anchor["h"],
	}
	return box


#============================================
def count_post_seed_accepts(accepts: list, seed_frame: int) -> int:
	"""Count accepted frames that are NOT the pass's own seed frame.

	The bootstrap step in the walker observes the seed frame before any
	windowed steps and appends it to `accepts` if observed. Windowed steps
	start at seed_frame +/- stride, so the seed frame appears in `accepts`
	at most once and only via bootstrap (the neighbor seed frame is never
	observed; the P12 termination fix ensures the crossing check fires
	before the observe call).

	"Post-seed accepts" is the count of accepted frames that carry real
	post-seed trajectory evidence. A pass whose only accepted frame is the
	seed frame (bootstrap-only stall) has post_seed_accepted == 0 and must
	fall back to Hermite even though accepted_count == 1.

	Args:
		accepts: List of accepted frame indices from WalkSummary.accepts.
		seed_frame: The pass's own anchor seed frame index (left seed for
			FWD, right seed for BWD).

	Returns:
		Number of accepted frames in `accepts` that are not equal to
		`seed_frame`.
	"""
	# count non-seed accepted frames; each non-seed frame counts exactly once
	count = sum(1 for f in accepts if f != seed_frame)
	return count


#============================================
def walk_bundle_to_path(bundle: WalkerInputBundle) -> list:
	"""Adapter: run walk_one_direction for one bundle, return a full-span path.

	This is the production walker callable for the Stage 4 seam. It bridges a
	WalkerInputBundle to the relocated core walker
	blob_walk.walk_walker.walk_one_direction (whose signature takes positional
	seed / frame-range / reader arguments, not a bundle), runs exactly one
	direction, and projects the walker's standalone direction_path into the
	full-span aligned state-dict list the analytical solver's blend/score path
	expects.

	FWD and BWD are driven by separate bundles and separate calls (contract C9):
	this function reads only `bundle`, never the other direction's output. The
	walker walks from `bundle.seed` over its own image-derived candidate
	lattice; no Hermite raw_pred is read (the bundle carries none).

	The walker's direction_path is PROCESSED pixels and this adapter returns
	PROCESSED pixels unchanged. The PROCESSED -> SOURCE conversion happens once,
	downstream, in interval_solver.solve_interval_analytical
	(_walker_path_processed_to_source), immediately after both passes run and
	before blend/score/storage. The bundle's seed must therefore be PROCESSED;
	the production driver projects SOURCE seeds via reader.geometry before
	building the bundle. At bin_factor == 1 both projections are identity.

	precomputed_store note (perf only): walk_one_direction builds its own
	per-walk residual_cache and does not accept the solver's precomputed_store
	through its current public signature. Threading it in would require a core
	API change that risks the no-Hermite import boundary, so it is deliberately
	left for a later pass. Correctness is unaffected; only the Stage 4
	sequential-read optimization is not reused on the walker path.

	Args:
		bundle: WalkerInputBundle for one direction (FWD or BWD).

	Returns:
		Full-span chronological list of solved state dicts (cx, cy, w, h, conf,
		source), one per frame from start_frame to end_frame inclusive,
		aligned slot-by-slot with the opposite direction's path.
	"""
	neighbor_seed = bundle.neighbor_seed
	# neighbor_seed_frame is the walk's termination target: FWD walks toward the
	# right seed frame, BWD toward the left seed frame.
	neighbor_seed_frame = int(neighbor_seed["frame_index"])

	null_log = _NullDebugLog()
	summary = walk_walker.walk_one_direction(
		seed=bundle.seed,
		neighbor_seed_frame=neighbor_seed_frame,
		reader=bundle.reader,
		scene_transform=bundle.scene_transform,
		fps=bundle.fps,
		stride=bundle.stride,
		sign=bundle.direction_sign,
		debug_log=null_log,
		# neighbor geometry enables per-frame torso-size interpolation and
		# supplies the diagnostic displacement target (seed-derived only).
		neighbor_seed_cx=float(neighbor_seed["cx"]),
		neighbor_seed_cy=float(neighbor_seed["cy"]),
		neighbor_seed_w=float(neighbor_seed["w"]),
		neighbor_seed_h=float(neighbor_seed["h"]),
	)

	# The endpoints come from the interval's two seeds regardless of direction.
	# direction_sign tells us which bundle seed is the left vs right endpoint.
	if bundle.direction_sign >= 0:
		seed_start = bundle.seed
		seed_end = bundle.neighbor_seed
	else:
		seed_start = bundle.neighbor_seed
		seed_end = bundle.seed

	full_span_path = _build_full_span_path(
		summary.direction_path,
		bundle.start_frame,
		bundle.end_frame,
		seed_start,
		seed_end,
	)
	return full_span_path


#============================================
def walk_bundle_to_path_with_coverage(bundle: WalkerInputBundle) -> tuple:
	"""Run walk_one_direction for one bundle; return path plus WalkCoverage.

	Same projection as `walk_bundle_to_path`, but also surfaces named
	coverage fields so the Stage 4 fallback gate can distinguish a
	bootstrap-only stall (accepted_count == 1, post_seed_accepted == 0)
	from a true zero-accept stall (both == 0) and from a healthy pass
	(post_seed_accepted >= 1). Both quantities are read from WalkSummary
	before the path projection discards per-frame status.

	The fallback gate reads `post_seed_accepted`, not `accepted_count`.
	A bootstrap-only stall carries no post-seed trajectory evidence and
	must fall back to Hermite even though accepted_count is 1.

	Hermite independence (test_blob_walk_v2_no_hermite): this function reads
	only the walker's own output. The fallback decision lives in the caller
	(interval_solver), which already owns velocity_model; walker_bundle stays
	free of any Hermite import.

	Args:
		bundle: WalkerInputBundle for one direction (FWD or BWD).

	Returns:
		Tuple of (full_span_path, WalkCoverage). full_span_path matches
		`walk_bundle_to_path`. WalkCoverage carries accepted_count (total
		"accepted" frames including seed bootstrap) and post_seed_accepted
		(accepted frames excluding the pass's own seed frame).
	"""
	neighbor_seed = bundle.neighbor_seed
	neighbor_seed_frame = int(neighbor_seed["frame_index"])

	null_log = _NullDebugLog()
	summary = walk_walker.walk_one_direction(
		seed=bundle.seed,
		neighbor_seed_frame=neighbor_seed_frame,
		reader=bundle.reader,
		scene_transform=bundle.scene_transform,
		fps=bundle.fps,
		stride=bundle.stride,
		sign=bundle.direction_sign,
		debug_log=null_log,
		neighbor_seed_cx=float(neighbor_seed["cx"]),
		neighbor_seed_cy=float(neighbor_seed["cy"]),
		neighbor_seed_w=float(neighbor_seed["w"]),
		neighbor_seed_h=float(neighbor_seed["h"]),
	)

	if bundle.direction_sign >= 0:
		seed_start = bundle.seed
		seed_end = bundle.neighbor_seed
	else:
		seed_start = bundle.neighbor_seed
		seed_end = bundle.seed

	full_span_path = _build_full_span_path(
		summary.direction_path,
		bundle.start_frame,
		bundle.end_frame,
		seed_start,
		seed_end,
	)
	# seed frame is the pass's own anchor: left seed for FWD, right for BWD
	seed_frame = int(bundle.seed["frame_index"])
	coverage = WalkCoverage(
		accepted_count=int(summary.accepted_count),
		post_seed_accepted=count_post_seed_accepts(summary.accepts, seed_frame),
	)
	return full_span_path, coverage
