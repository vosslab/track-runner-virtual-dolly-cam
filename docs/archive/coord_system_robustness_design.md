## IMPLEMENTED 2026-05-29

Option A landed cleanly. Summary:

**Files edited:**
- `track_runner/state_io.py` - added `SeedsView` class and `load_seeds_view(path, geometry)`.
- `track_runner/residual_motion.py` - removed source->processed conversion for all `observe_blob_at` entry inputs (`roi_override`, `dog_diameter_override`, `pred_center`, `pred_box`, `acceptance_box`). New contract: all inputs arrive in processed-pixel coords.
- `tools/blob_walk_v2/walk_io.py` - added `load_walker_seeds_view(video_basename, geometry)`; `load_walker_seeds` preserved for legacy callers.
- `tools/blob_walk_v2/walk_walker.py` - ROI clamp sites changed to use `reader.width`/`reader.height` (processed); source-dim lookups removed.
- `tools/blob_walk_v2/walk_driver.py` - migrated to `load_walker_seeds_view` + `assert_geometry_match`; processed seeds passed to walker.
- `tools/blob_walk_v2/dump_step1_inputs.py` - decision: **migrated to processed coords**. Seed coords projected at top of `dump_step1_for_interval`; `has_valid_seed_roi` call fixed to use source dims.
- `tests/test_residual_motion_bin_factor.py` - removed two tests that verified old conversion behavior; 4 tests remain.

**New files:**
- `tests/test_seeds_view.py` - 5/5 pass.
- `tests/test_observe_blob_at_processed_contract.py` - 3/3 pass.
- `tests/test_walker_bin_consistency.py` - 4/4 pass.

**dump_step1_inputs.py migration choice:** Migrated to processed coords (consistent). Conversion applied once at the top of `dump_step1_for_interval` via `reader.geometry`. Legacy `load_walker_seeds` (source coords) still used in the main loop to enumerate intervals; seed coords for observation are converted per-call.

**Acceptance bars:**
- `source == processed` at `bin_factor=1` -> existing solve-mode callers unaffected.
- `view.assert_geometry_match` guard catches mismatched bin_factor at load time.
- All 3 previously-shipped defects (WARP_SCALE_MISMATCH, override source-scale, ROI_CLAMP_SPACE_MISMATCH) have regression tests that pass under the new design.

---

# Coord-system robustness design for the auto-bin pipeline

Date: 2026-05-29. Design-only plan responding to three distinct auto-bin
defects shipped in 24 hours. No production code edits in this document.

Related audits:
[auto_bin_coord_stack_audit.md](auto_bin_coord_stack_audit.md),
[degenerate_roi_investigation.md](degenerate_roi_investigation.md).

## Problem statement

Three coord-system defects shipped between 2026-05-28 and 2026-05-29 on the
auto-bin pipeline introduced in #72:

| # | Defect | Site | Status |
| --- | --- | --- | --- |
| 1 | WARP_SCALE_MISMATCH (scale_factor=1.0) | `residual_motion.py:618` | Fixed in #100 |
| 2 | DoG / roi_override source-scale | `observe_blob_at:1157,1178` | Fixed in #100 |
| 3 | ROI_CLAMP_SPACE_MISMATCH | `walk_walker.py:812-813,900-901` | Fixed in #104 |

All three share one shape: an interface where the seed/walker pipeline
(source-pixel) meets the reader/observer pipeline (post-bin) silently
mixes the two coord systems. The compiler cannot tell a source-pixel
`int` from a post-bin `int`. Each fix plugs one leak; the next leak
appears at a different boundary. The user has correctly flagged the
design itself, not any individual call site, as fragile.

The current "Model B" contract from #72 - "source coords everywhere,
converted at the `observe_blob_at` boundary" - is a convention enforced
only by reviewer attention. It has been violated three times.

## Design philosophy

Per [REPO_STYLE.md](../REPO_STYLE.md) core philosophy "Fix the
design, not the symptom": patching defect sites in place is insufficient
when the defect class is recurring. The auto-bin coord ambiguity is
exactly the structural design bug that clause warns against treating as
a symptom. Adding a fourth fix at a fourth boundary would extend the
pattern, not break it.

Contract clause C2 (torso-unit scale) already forbids raw-pixel
reasoning for runner-relative decisions. Bin-aware coord handling is
the equivalent invariant at the image-IO layer: a coord value without a
declared coord system is structurally meaningless across `bin_factor>1`
and must be made impossible to express. Contract clause C6 (interval
independence) is unaffected by any option below; coord conversion is
local to each interval worker.

## Options

### Option A: Geometry-aware state_io

`state_io.load_seeds(path, geometry=None)` returns seeds in source-pixel
coords by default; when `geometry` is passed, returns seeds already in
processed-pixel coords. Walker calls `load_seeds(path,
geometry=reader.geometry)` and never touches a source coord again.
`observe_blob_at` no longer converts on entry.

```
+--------------------+---------------------------------------------+
| Dimension          | Assessment                                  |
+--------------------+---------------------------------------------+
| Refactor blast     | Small. Touches state_io + walker call site  |
|                    | + observe_blob_at entry. ~6 files.          |
| Performance        | No change. Conversion moves, does not add.  |
| Defect prevention  | Strong inside walker pipeline. Single       |
|                    | conversion point at load boundary.          |
| Maintainability    | Walker becomes coord-uniform; one mental    |
|                    | model per pipeline.                         |
| Coupling           | state_io gains FrameGeometry dependency.    |
|                    | Acceptable; both are infra-layer.           |
| Scene-coord layer  | Unchanged. Stays separate (see below).      |
| Test impact        | Existing bin=1 tests still pass (geometry   |
|                    | arg defaults to None; conversion is no-op). |
| Failure mode if    | Caller forgets to pass geometry: behaves    |
| misused            | as today; covered by a single guard test.   |
+--------------------+---------------------------------------------+
```

### Option B: Tagged coord type

Introduce `Coord(value, system)` (or `SourceCoord` / `ProcessedCoord` /
`SceneCoord` dataclasses). Mixing types is a type error.

```
+--------------------+---------------------------------------------+
| Dimension          | Assessment                                  |
+--------------------+---------------------------------------------+
| Refactor blast     | Large. Hundreds of lines across walker,     |
|                    | residual_motion, scene_coords, state_io,    |
|                    | UI, encoder, tests.                         |
| Performance        | Per-call boxing overhead. Hot loops         |
|                    | (per-frame residual / blob extraction) are  |
|                    | the worst case.                             |
| Defect prevention  | Strongest. Catches at code-review and at    |
|                    | runtime by construction.                    |
| Maintainability    | Self-documenting; high long-term value.     |
| Coupling           | Forces every module onto the new type.      |
| Scene-coord layer  | Naturally folds in as third tag.            |
| Test impact        | Every fixture touching coords must update.  |
| Failure mode if    | Cannot be misused; that is the point.       |
| misused            |                                             |
+--------------------+---------------------------------------------+
```

### Option C: Pass geometry everywhere; convert at use site

Status quo Model B with stricter review discipline.

```
+--------------------+---------------------------------------------+
| Dimension          | Assessment                                  |
+--------------------+---------------------------------------------+
| Refactor blast     | None.                                       |
| Performance        | Unchanged.                                  |
| Defect prevention  | Demonstrated weak: 3 leaks in 24 hours.     |
| Maintainability    | Boilerplate at every boundary.              |
| Coupling           | None added.                                 |
| Scene-coord layer  | Unchanged.                                  |
| Test impact        | None.                                       |
| Failure mode if    | Same as the 3 shipped defects.              |
| misused            |                                             |
+--------------------+---------------------------------------------+
```

## Recommendation

**Option A.** Rationale:

1. The empirical evidence (3 defects, 24 hours, all at source/processed
   boundaries inside the walker pipeline) localizes the failure mode to
   one pipeline. Option A makes that pipeline coord-uniform with a
   small, surgical change.
2. Option B's defect-prevention strength does not justify its blast
   radius given C2-style "torso-unit scale" reasoning is the dominant
   contract; coord-system tagging would mostly police infrastructure
   code, not solve-logic.
3. Option C is already falsified by the 3 shipped defects.

Option B remains a reasonable future move if a fourth defect appears
after Option A lands. Option A does not preclude Option B.

## Scene-coord layer

The scene coord system (camera-motion-compensated, from
`scene_coords.SceneTransform`) should stay **distinct** from the
source/processed split under Option A.

Reasoning:

- Scene coords are a semantic translation (correcting for camera
  motion), not a resolution translation. They consume processed-pixel
  inputs and emit a different geometric meaning, not a different scale.
- The 3 shipped defects do not involve scene coords. Folding scene
  coords in would expand Option A's blast radius without addressing
  evidence.
- Contract C6 (interval independence) means scene-coord state never
  crosses interval boundaries; the existing `SceneTransform` boundary
  is already a strong seam.

If Option B is later adopted, scene-coord tagging is the natural place
to fold in the third tag. Until then, scene coords stay out of scope.

## Migration sketch (Option A)

Dependency order:

1. **`common_tools/frame_reader.py`** - no API change. Verify
   `FrameGeometry.source_to_processed` and `source_to_processed_delta`
   are the only conversion primitives callers need. Add
   `processed_width` / `processed_height` aliases if walker readability
   benefits.
2. **`track_runner/state_io.py`** - extend `load_seeds` and
   `_derive_seed_geometry` with an optional `geometry: FrameGeometry |
   None = None` parameter. When `None`, behavior is unchanged
   (source-pixel return; backwards compatible). When provided, every
   returned seed coord (`cx`, `cy`, `w`, `h`, plus any acceptance-box
   derivations) is run through `source_to_processed` /
   `source_to_processed_delta` before return.
3. **`tools/blob_walk_v2/walk_io.py`** - `load_walker_seeds` passes
   `geometry=reader.geometry` to `state_io.load_seeds` after opening
   the reader. Single call-site change.
4. **`tools/blob_walk_v2/walk_walker.py`** - delete every site that
   touches `reader.geometry.source_to_processed*` directly (defects 2
   and 3 lived here). All seed coords are now processed-pixel; clamp
   against `reader.width` / `reader.height` is correct by construction.
   `roi_pad`, `dog_diameter_override`, and `roi_override` become
   post-bin natively.
5. **`track_runner/residual_motion.py`** - `observe_blob_at` entry
   stops converting `roi_override` and `dog_diameter_override` (defect
   2). The function's documented contract changes: "roi_override
   arrives in processed-pixel coords." Old behavior remains correct at
   bin=1. The warp `scale_factor` fix from #100 is independent and
   stays; it is not a coord-input issue.
6. **`tests/`** - add the three regression tests below. Existing
   bin_factor=1 tests are unchanged. Add an opt-in fixture that loads
   seeds via the new geometry-aware path for any new tests.

Breaking changes:

- Any consumer outside the walker that called
  `state_io.load_seeds(...)` and then forwarded the result into
  `observe_blob_at` with `roi_override` set must either (a) keep the
  old call (no geometry) and the old code path, or (b) adopt the new
  call. Diagnostic tools using the legacy `observe_blob_at` reader
  path retain backward compatibility because the conversion is removed,
  not inverted.
- `observe_blob_at` docstring contract changes; that is the intended
  break.

## Acceptance bar

A coord-mismatch defect must be impossible by construction in the
walker pipeline, or caught by a single guard at the source/processed
boundary. New tests, one per shipped defect:

1. **`test_walker_seeds_loaded_in_processed_space_bin4`** - load a
   seed at source `cx=2346`, open reader at `bin_factor=4`, call
   `load_walker_seeds`; assert returned `cx == 586` (or
   `586.5`). Falsifies a re-introduction of defect 3 at load time.
2. **`test_roi_override_passes_through_unchanged_at_bin4`** - construct
   a walker frame at `bin_factor=4` where the seed sits at source
   `cx > reader.width * bin_factor / 2`; assert `roi_override` reaching
   `observe_blob_at` is a non-degenerate post-bin rect (w > 0, h > 0)
   and is NOT converted again inside `observe_blob_at`. Falsifies
   double-conversion.
3. **`test_dog_diameter_override_is_post_bin_at_bin4`** - assert the
   DoG kernel diameter used inside `observe_blob_at` at `bin_factor=4`
   equals `0.7 * seed_w / 4`, not `0.7 * seed_w`. Falsifies defect 2.

Plus a structural guard:

4. **`test_state_io_load_seeds_geometry_roundtrip`** - load seeds with
   `geometry=None` and with `geometry=reader.geometry`; assert
   `processed_to_source` of the geometry-aware result equals the
   geometry-free result within float epsilon. Single guard that any
   future coord drift trips.

The WARP_SCALE_MISMATCH (#100 Fix 1) regression test already exists in
`test_residual_motion_bin_factor.py` and stays.

## Out of scope

- `scene_coords.SceneTransform` API changes. Scene coords are a
  separate translation layer per the section above.
- UI controllers and annotation workspace. Annotation is authored in
  source-pixel space by design; the geometry-aware load path is opt-in.
- Crop encoder. The encoder consumes the solved trajectory in source
  pixels; no encoder path crosses the source/processed boundary.
- Stage 1 camera-motion estimator. `motion_track.dx/dy` are already in
  source pixels per contract; the post-bin upscale is correct.
- Option B adoption. Tracked as a future option if a 4th defect surfaces
  after Option A lands.

## References

- [REPO_STYLE.md](../REPO_STYLE.md) core philosophy: fix the
  design, not the symptom.
- [TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md) C2
  (torso-unit scale), C6 (interval independence).
- [auto_bin_coord_stack_audit.md](auto_bin_coord_stack_audit.md)
  defects 1 and 2.
- [degenerate_roi_investigation.md](degenerate_roi_investigation.md)
  defect 3.
