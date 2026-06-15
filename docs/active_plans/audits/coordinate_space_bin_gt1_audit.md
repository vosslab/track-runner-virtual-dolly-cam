# Coordinate space audit at bin greater than one

Date: 2026-06-14. Read-only audit (M1 / WS1-audit). Gates M2/M3.

Decisive question: at `bin_factor > 1`, does the solve pipeline carry every
value in one consistent space and convert to SOURCE only at the storage
boundary? Or does the walker path store PROCESSED values where SOURCE is
required?

Source of truth for spaces: [docs/COORDINATE_SPACES.md](../../COORDINATE_SPACES.md)
and [docs/TRACK_RUNNER_V3_SPEC.md](../../TRACK_RUNNER_V3_SPEC.md). The V3 spec
lacked an explicit storage-space section; this audit strengthens it (see
section "V3 spec strengthening").

## Verdict

Storage is NOT correct at `bin_factor > 1` for the walker (Stage 4) path.
Two coupled gaps exist; both are invisible at `bin_factor = 1` because
SOURCE and PROCESSED are byte-identical there. WS1-fix IS needed before any
default to `bin > 1` (M2).

- Gap A (input): the production driver feeds SOURCE seed dicts into the
  walker, but the contract requires PROCESSED seeds at the walker boundary
  ([docs/COORDINATE_SPACES.md](../../COORDINATE_SPACES.md) line 62: "Driver
  seeds fed to the walker | PROCESSED"). No `SeedsView` projection happens in
  the production solve path.
- Gap B (output/storage): the walker emits a PROCESSED `direction_path`
  ([interval_solver.py](../../../track_runner/interval_solver.py) line 514
  comment: "PROCESSED-pixel state lists"). The production write boundary
  ([cli.py](../../../track_runner/cli.py) calls
  `state_io.write_torso_box_coords`) applies NO `processed_to_source`
  projection. The standalone tool
  [walk_driver.py](../../../tools/blob_walk_v2/walk_driver.py) DOES project
  via `_project_path_to_source` before writing; production does not.

Hermite (Stage 3) is correct at all bin factors: it converts SOURCE seed ->
SCENE -> SOURCE pixel internally, so its stored boxes are SOURCE.

## Per-stage space table at bin greater than one

| Stage | file:line | Value | Space at bin>1 | Conversion applied |
| --- | --- | --- | --- | --- |
| Camera motion persist | [camera_motion.py](../../../track_runner/camera_motion.py):717-723 | `MotionTrack.dx`, `MotionTrack.dy` | SOURCE | processed dx/dy multiplied by `bin_factor` before persist |
| Scene transform build | [scene_coords.py](../../../track_runner/scene_coords.py):38-40 | `cum_dx`, `cum_dy`, `cum_scale` | SOURCE | none (built from SOURCE `MotionTrack`) |
| Scene transform ops | [scene_coords.py](../../../track_runner/scene_coords.py):66-67, 94-95 | `pixel_to_scene` / `scene_to_pixel` pixel arg | SOURCE pixel <-> SCENE | none; pixel side is SOURCE because cum_dx/dy are SOURCE |
| Seed source of truth | [state_io.py](../../../track_runner/state_io.py):12, 845-851 | seed `cx,cy,w,h` from JSON | SOURCE | none (seed JSON is SOURCE) |
| Usable seeds (solver) | [interval_solver.py](../../../track_runner/interval_solver.py):1531-1533 | `usable_seeds`, `seed_start`, `seed_end` | SOURCE | none (`filter_usable_seeds_sorted`, no `SeedsView`) |
| Hermite fit | [velocity_model.py](../../../track_runner/velocity_model.py):284-288 | `pixel_box_to_scene(seed)` | SOURCE in -> SCENE | none; SOURCE seed against SOURCE scene_transform is consistent |
| Hermite propagate | [velocity_model.py](../../../track_runner/velocity_model.py):404-417 | `scene_box_to_pixel` output `pixel_cx,...` | SOURCE | SCENE -> SOURCE pixel |
| Walker bundle seed | [walker_bundle.py](../../../track_runner/walker_bundle.py):159-177, 511-525 | `bundle.seed["cx"]` etc. | SOURCE (production) | none; contract REQUIRES PROCESSED here (Gap A) |
| Walker anchor / ROI | [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py):634-665, 1418-1422 | `anchor_cx`, `pred_center=ProcessedPoint`, acceptance/ROI boxes | PROCESSED (assumed) | none; "All inputs are already PROCESSED" (line 634) |
| observe_blob_at INPUTS | [residual_motion.py](../../../track_runner/residual_motion.py):1029-1034 | `pred_center`, `pred_box`, `roi_override` | PROCESSED | guards reject SOURCE primitives loudly |
| observe_blob_at RETURN centroid | [residual_motion.py](../../../track_runner/residual_motion.py):916-937, 1029-1030 | `BlobObservation.center_pixel` | SOURCE | observe converts PROCESSED -> SOURCE on return |
| Candidate list (corridor_blobs) | [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py):733-737, 791-805 | `centroid_x`, `centroid_y` | PROCESSED (full-frame) | none; ROI origin already added back |
| Walker selected candidate | [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py):412-417, 447-460 | `r["cx"]`, `direction_path` entry | PROCESSED | none (candidate comes from PROCESSED `corridor_blobs`) |
| Solver path collect | [interval_solver.py](../../../track_runner/interval_solver.py):514, 536-541, 592-593 | `forward_path`, `backward_path` (walker) | PROCESSED | none |
| Solver path collect | [interval_solver.py](../../../track_runner/interval_solver.py):572-583, 591-593 | `forward_path`, `backward_path` (Hermite) | SOURCE | none needed (already SOURCE) |
| Blend / store dict | [interval_solver.py](../../../track_runner/interval_solver.py):600, 626-638 | `blended_path`, `forward_path`, `backward_path` | walker=PROCESSED, Hermite=SOURCE | none |
| Storage boundary | [state_io.py](../../../track_runner/state_io.py):845-877, 924 | `_extract_source_box_coords`, `write_torso_box_coords` | requires SOURCE | plain-dict path assumes SOURCE; NO conversion (Gap B) |
| Production write call | [cli.py](../../../track_runner/cli.py):581, 618, 660, 1052 | `write_torso_box_coords(...)` | stores whatever it is given | none (no `processed_to_source`) |
| Standalone tool write | [walk_driver.py](../../../tools/blob_walk_v2/walk_driver.py):159-184, 949-968, 1036 | `_project_path_to_source` then write | SOURCE | PROCESSED -> SOURCE applied (production lacks this) |

## The single consistent contract and the exact gap

Intended contract (from [docs/COORDINATE_SPACES.md](../../COORDINATE_SPACES.md)
lines 16-72): solve has TWO spaces, reconciled by typed conversions, not one.
SCENE is an internal anchored space inside the Hermite leg. The two pixel
spaces are:

- SOURCE -- storage and consumption space. The torso-box npz (`state_io`
  write/load) is SOURCE; the encoder consumes SOURCE.
- PROCESSED -- analysis space. The walker decodes, steps, and draws here;
  `reader.width`/`reader.height` are PROCESSED.

The contract requires the walker to be fed PROCESSED seeds (line 62) and the
PROCESSED walker output to be projected to SOURCE at the storage boundary
(lines 17-18, 70). `scene_transform` operates in SOURCE pixels because
`MotionTrack.dx/dy` are SOURCE (upscaled by `bin_factor` at
[camera_motion.py](../../../track_runner/camera_motion.py):717-723); the
Hermite leg's pixel side is therefore SOURCE end to end.

Exact gap: the production solve path (cli -> interval_solver -> solve_queue ->
solver_workers) never builds a `SeedsView`, so it feeds SOURCE seeds into the
PROCESSED walker (Gap A), and never projects the PROCESSED walker output to
SOURCE before `write_torso_box_coords` (Gap B). The standalone diagnostic tool
`walk_driver.py` does both correctly; the production path does neither. At
`bin_factor = 1` both omissions are no-ops. At `bin_factor > 1`:

- Gap A degrades the walk itself: a SOURCE seed cx near the right/bottom edge,
  treated as PROCESSED, builds a degenerate or mis-placed ROI (the #101 class;
  see [docs/COORDINATE_SPACES.md](../../COORDINATE_SPACES.md) lines 115-127).
- Gap B mis-stores any surviving walker geometry: PROCESSED pixels written as
  SOURCE are too small by a factor of `bin_factor`.

## The single PROCESSED to SOURCE boundary

The PROCESSED -> SOURCE conversion must occur exactly once, at the storage
boundary, immediately before `state_io.write_torso_box_coords`, for the walker
(PROCESSED) path. This is the boundary the standalone tool already implements:
`_project_path_to_source`
([walk_driver.py](../../../tools/blob_walk_v2/walk_driver.py):159-184) projects
center via `geometry.processed_to_source` and width/height via
`geometry.processed_to_source_delta`, then writes
([walk_driver.py](../../../tools/blob_walk_v2/walk_driver.py):1036).

The current production code does NOT do this conversion at that boundary
([cli.py](../../../track_runner/cli.py):581/618/660/1052 call
`write_torso_box_coords` directly). The Hermite path needs no conversion (it is
already SOURCE), so a correct fix must apply the projection ONLY to walker
geometry, keyed off the per-pass producer (the result already records
`propagator_path` and `walker_fallback_fwd/bwd` at
[interval_solver.py](../../../track_runner/interval_solver.py):626-638). Gap A
(PROCESSED seeds into the walker) must be fixed in tandem, because converting a
walk that ran against mis-placed ROIs would store SOURCE-correct values for a
walk that tracked the wrong pixels.

## Hermite vs walker agreement on space at bin greater than one

They do NOT agree at `bin_factor > 1`. Hermite `forward_path`/`backward_path`
are SOURCE
([velocity_model.py](../../../track_runner/velocity_model.py):404-417). Walker
`forward_path`/`backward_path` are PROCESSED
([interval_solver.py](../../../track_runner/interval_solver.py):514). Both flow
into the same `blended_path` and the same `write_torso_box_coords` call without
a per-producer space normalization. A mixed interval (one pass walker, one pass
Hermite, via the per-pass stall fallback at
[interval_solver.py](../../../track_runner/interval_solver.py):556-569) blends a
PROCESSED path with a SOURCE path frame-by-frame, which is incoherent at
`bin > 1`. At `bin_factor = 1` they coincide, which is why this has never
surfaced in production.

## Task #99 / auto_bin_coord_stack_audit citation

The PROCESSED/SOURCE-at-bin stack has prior defect history. See
[docs/archive/auto_bin_coord_stack_audit.md](../../archive/auto_bin_coord_stack_audit.md)
(task #99, dated 2026-05-28). That audit found and fixed:

- WARP_SCALE_MISMATCH (#99 Fix 1): `compute_residual_for_frame` used
  hard-coded `scale_factor=1.0` instead of `1/bin_factor`, confirming
  `MotionTrack.dx/dy` are SOURCE and frames being warped are PROCESSED.
- DoG diameter / `roi_override` source-scale (#100 Fix 2): override args were
  not converted SOURCE -> PROCESSED before use.
- ROI_CLAMP_SPACE_MISMATCH (#101 Fix 3): documented in
  [docs/archive/degenerate_roi_investigation.md](../../archive/degenerate_roi_investigation.md).

Those fixes addressed the residual / observe interior. They did NOT add the
two missing production-path boundary conversions this audit names (Gap A: seeds
into the walker; Gap B: walker output to storage). The #99-#101 work was
validated through the standalone `walk_driver.py` / `make_walk_html_v2.py` tool
path, which already carries both boundary conversions; the production
cli/interval_solver path was not exercised at `bin > 1` (production defaults to
`bin_factor = 1`, [cli.py](../../../track_runner/cli.py):859).

## V3 spec strengthening

[docs/TRACK_RUNNER_V3_SPEC.md](../../TRACK_RUNNER_V3_SPEC.md) carried a single
authoritative storage line:

> Coordinate convention: `torso_box` stores `[x, y, w, h]` where `x, y` is ...

It did not state the SOURCE/PROCESSED/SCENE split nor name the storage-space
boundary, so a reader could not tell which space the stored npz is in or where
conversion must occur. This audit adds a "Coordinate spaces and storage
boundary" subsection to the spec that names the three spaces, states the
storage npz is SOURCE, and names the single PROCESSED -> SOURCE boundary before
`write_torso_box_coords`. The added text describes only what the code and
[docs/COORDINATE_SPACES.md](../../COORDINATE_SPACES.md) already establish; it
invents no new contract. Ambiguity removed: "which space is the stored npz,
and where does conversion happen" now has an explicit answer in the spec.
