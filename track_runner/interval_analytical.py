"""Analytical per-interval solve mechanics, independent of the facade."""

# Standard Library
import collections.abc

# local repo modules
import common_tools.coord_space
import common_tools.frame_reader
import common_tools.in_box_heat
import residual_motion
import residual_pre_pass
import scoring
import interval_seed_anchoring
import track_runner.blend_commitment
import trajectory_confidence
import velocity_model
import walker_bundle

PROMOTION_TIERS = frozenset({"low", "fair"})


#============================================
def measure_canonical_blend_boxes(
	reader: common_tools.frame_reader.FrameReader,
	scene_transform: object,
	fps: float,
	frame_index: int,
	forward_state: dict,
	backward_state: dict,
	compared_states: dict,
	residual_cache: dict,
) -> dict | None:
	"""Measure named SOURCE boxes on the production blend-commitment field."""
	residual_mag, validity_mask = residual_motion.compute_residual_for_frame(
		reader=reader, frame_index=frame_index, scene_transform=scene_transform,
		cache=residual_cache, fps=fps,
	)
	if residual_mag is None or validity_mask is None:
		return None
	geometry = reader.geometry
	boxes = {}
	for name, state in compared_states.items():
		boxes[name] = common_tools.coord_space.SourceBox(
			cx=float(state["cx"]), cy=float(state["cy"]), w=float(state["w"]), h=float(state["h"]),
		).to_processed(geometry)
	forward_box, backward_box = boxes["forward"], boxes["backward"]
	shared_diameter = (forward_box.w + backward_box.w) / 2.0
	residual_dog = residual_motion.dog_filter_blob_scale(residual_mag, shared_diameter)
	result = {}
	for name, box in boxes.items():
		mean, count = common_tools.in_box_heat.measure_in_box_heat(
			residual_dog, validity_mask, (0, 0), box, residual_motion.DEFAULT_THRESHOLD,
		)
		result[name] = 0.0 if mean is None else float(mean) * int(count)
	return result


#============================================
def build_canonical_blend_heat_evaluator(
	reader: common_tools.frame_reader.FrameReader, scene_transform: object, fps: float,
) -> collections.abc.Callable:
	"""Build the offline blend-commitment evidence provider for one interval."""
	residual_cache = {}

	def evaluate(
		frame_index: int, forward_state: dict, backward_state: dict,
	) -> track_runner.blend_commitment.HeatEvidence:
		energies = measure_canonical_blend_boxes(
			reader, scene_transform, fps, frame_index, forward_state, backward_state,
			{"forward": forward_state, "backward": backward_state}, residual_cache,
		)
		if energies is None:
			return track_runner.blend_commitment.HeatEvidence(available=False)
		return track_runner.blend_commitment.HeatEvidence(
			available=True, forward_energy=energies["forward"], backward_energy=energies["backward"],
		)

	return evaluate


#============================================
def blend_paths(
	forward_path: list, backward_path: list, start_frame: int = 0, heat_evaluator: object = None,
) -> list:
	"""Return the canonical commitment-policy blend."""
	result = track_runner.blend_commitment.commit_paths(
		forward_path=forward_path, backward_path=backward_path,
		start_frame=start_frame, heat_evaluator=heat_evaluator,
	)
	return result


#============================================
def _seed_source_to_processed(seed: dict, geometry: object) -> dict:
	"""Project one SOURCE-pixel seed dict to PROCESSED-pixel coordinates."""
	if "cx" not in seed or seed["cx"] is None:
		return dict(seed)
	cx_p, cy_p = geometry.source_to_processed(seed["cx"], seed["cy"])
	w_p, h_p = geometry.source_to_processed_delta(seed["w"], seed["h"])
	processed = dict(seed)
	processed["cx"], processed["cy"] = cx_p, cy_p
	processed["w"], processed["h"] = w_p, h_p
	return processed


#============================================
def _walker_path_processed_to_source(direction_path: list, geometry: object) -> list:
	"""Project a PROCESSED-space walker path to SOURCE-frame pixels."""
	projected = []
	for state in direction_path:
		cx_s, cy_s = geometry.processed_to_source(state["cx"], state["cy"])
		w_s, unused_w = geometry.processed_to_source_delta(state["w"], 0.0)
		unused_h, h_s = geometry.processed_to_source_delta(0.0, state["h"])
		projected_state = dict(state)
		projected_state["cx"], projected_state["cy"] = cx_s, cy_s
		projected_state["w"], projected_state["h"] = w_s, h_s
		projected.append(projected_state)
	return projected


#============================================
def solve_interval_analytical(
	seed_start: dict, seed_end: dict, scene_transform: object, all_seeds_scene: list,
	fps: float, debug: bool = False, motion_track: object = None, all_seeds: list | None = None,
	reader: object = None, blob_pass: bool = True,
	telemetry: dict | None = None,
) -> dict:
	"""Solve one interval through the canonical analytical and walker owners."""
	start_frame, end_frame = int(seed_start["frame_index"]), int(seed_end["frame_index"])
	if start_frame >= end_frame:
		raise RuntimeError(f"degenerate interval: start_frame={start_frame} >= end_frame={end_frame}")
	if debug:
		print(f"    fitting analytical curves {start_frame}-{end_frame}...")
	interval_curves = velocity_model.fit_interval_curves(
		seed_start, seed_end, scene_transform,
	)
	if debug:
		print("    propagating forward/backward analytically...")
	walker_active = blob_pass and reader is not None
	walker_fallback_fwd = False
	walker_fallback_bwd = False
	residual_cache = {} if walker_active else None
	precomputed_store = None
	if walker_active:
		geometry = reader.geometry
		seed_start_processed = _seed_source_to_processed(seed_start, geometry)
		seed_end_processed = _seed_source_to_processed(seed_end, geometry)
		pre_stride = residual_motion.resolve_stride(fps)
		if hasattr(reader, "width") and hasattr(reader, "height"):
			walker_rois = residual_pre_pass.build_walker_initial_rois(
				seed_start=seed_start_processed, seed_end=seed_end_processed, reader=reader,
				stride=pre_stride, window_frames=walker_bundle.walk_walker.WALKER_WINDOW_FRAMES,
			)
		else:
			walker_rois = {}
		precomputed_store = residual_pre_pass.precompute_interval_residuals(
			reader=reader, scene_transform=scene_transform, seed_start=seed_start, seed_end=seed_end,
			fwd_curve=[], bwd_curve=[], half_window=residual_motion.DEFAULT_HALF_WINDOW,
			fps=fps, stride=pre_stride, rois_for_frame=walker_rois,
		)
		forward_bundle, backward_bundle = walker_bundle.build_walker_bundles_for_interval(
			seed_start=seed_start_processed, seed_end=seed_end_processed, reader=reader,
			scene_transform=scene_transform, fps=fps, stride=pre_stride,
			precomputed_store=precomputed_store,
		)
		forward_path_scene, fwd_coverage = (
			walker_bundle.walk_bundle_to_path_with_coverage(forward_bundle)
		)
		backward_path_scene, bwd_coverage = (
			walker_bundle.walk_bundle_to_path_with_coverage(backward_bundle)
		)
		forward_path_scene = _walker_path_processed_to_source(forward_path_scene, geometry)
		backward_path_scene = _walker_path_processed_to_source(backward_path_scene, geometry)
		walker_fallback_fwd = fwd_coverage.post_seed_accepted == 0
		walker_fallback_bwd = bwd_coverage.post_seed_accepted == 0
		if walker_fallback_fwd:
			forward_path_scene = velocity_model.propagate_forward_analytical(
				interval_curves,
				scene_transform,
			)
		if walker_fallback_bwd:
			backward_path_scene = velocity_model.propagate_backward_analytical(
				interval_curves,
				scene_transform,
			)
	else:
		forward_path_scene = velocity_model.propagate_forward_analytical(
			interval_curves,
			scene_transform,
		)
		backward_path_scene = velocity_model.propagate_backward_analytical(
			interval_curves,
			scene_transform,
		)
	if telemetry is not None:
		if precomputed_store is None:
			telemetry["prepass"] = None
		else:
			lookup_count = int(precomputed_store.lookup_count)
			miss_count = int(precomputed_store.miss_count)
			telemetry["prepass"] = {
				"lookup_count": lookup_count,
				"miss_count": miss_count,
				"miss_rate": (
					miss_count / lookup_count if lookup_count else 0.0
				),
				"eviction_count": int(precomputed_store.eviction_count),
				"oversize_count": int(precomputed_store.oversize_count),
				"stored_bytes": int(precomputed_store.total_bytes),
			}
	if residual_cache is not None:
		residual_cache.clear()
	if precomputed_store is not None:
		precomputed_store.clear()
	forward_path, backward_path = list(forward_path_scene), list(backward_path_scene)
	if debug:
		print(
			f"    blending interval paths ({len(forward_path)}+{len(backward_path)} "
			f"states)..."
		)
	heat_evaluator = (
		None
		if reader is None
		else build_canonical_blend_heat_evaluator(reader, scene_transform, fps)
	)
	blend_kwargs = {"start_frame": start_frame, "heat_evaluator": heat_evaluator}
	try:
		blended_path = blend_paths(forward_path, backward_path, **blend_kwargs)
	except track_runner.blend_commitment.BlendTransitionInfeasibleError:
		if not walker_active:
			raise
		# Stage 4 is an optional replacement for this already-valid Stage-3
		# interval. Reject an unsafe committed walker transition by retaining
		# the analytical pair, preserving the default-on never-worse contract.
		forward_path = list(velocity_model.propagate_forward_analytical(
			interval_curves, scene_transform,
		))
		backward_path = list(velocity_model.propagate_backward_analytical(
			interval_curves, scene_transform,
		))
		walker_fallback_fwd = True
		walker_fallback_bwd = True
		blended_path = blend_paths(forward_path, backward_path, **blend_kwargs)
	interval_seed_anchoring.stamp_interval_endpoint_seed_truth(
		blended_path, seed_start, seed_end,
	)
	if debug:
		print("    scoring interval analytically...")
	interval_score = scoring.score_interval_analytical(
		forward_path, backward_path, all_seeds_scene, interval_curves, scene_transform,
		motion_track=motion_track, all_seeds=all_seeds, blended_path=blended_path, fps=fps,
	)
	result = {
		"start_frame": start_frame,
		"end_frame": end_frame,
		"blended_path": blended_path,
		"forward_path": forward_path,
		"backward_path": backward_path,
		"interval_score": interval_score,
		"walker_fallback_fwd": walker_fallback_fwd,
		"walker_fallback_bwd": walker_fallback_bwd,
	}
	if debug:
		result["agreement_debug"] = trajectory_confidence.compute_agreement_debug(
			forward_path, backward_path, start_frame=start_frame,
		)
	return result
