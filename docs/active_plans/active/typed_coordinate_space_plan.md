# Plan: Typed coordinate space for the binning pipeline

## Context

The track_runner / blob_walk_v2 binning pipeline tracks two pixel coordinate
spaces with nothing but comments and bare `(x, y, w, h)` float tuples:

- SOURCE: full-frame source pixels. The npz solved-interval store
  (`track_runner/state_io.py`) is source; the encoder consumes source.
- PROCESSED: the post-bin, goodbox-snapped frame the walker actually decodes
  and draws on. `source == processed` only at `bin_factor == 1`.

Because space is implicit, the same datum is interpreted differently across
module boundaries, and conversions are applied zero, one, or two times with no
guard. Concrete evidence gathered this session:

- `common_tools/frame_reader.py` `FrameGeometry.source_to_processed` is pure
  `x / bin_factor` and ignores the goodbox snap. `scaled_width =
  source // bin_factor` is then snapped DOWN to a goodbox, so
  `processed_width` can be strictly less than `scaled_width`. A near-edge
  source coordinate therefore converts to a processed coordinate just outside
  the real processed frame.
- The "Option A" migration (2026-05-29) made the walker
  (`tools/blob_walk_v2/core/walk_walker.py`) and
  `residual_motion.observe_blob_at` operate in PROCESSED pixels. But
  `observe_blob_at` RETURNS its centroid in SOURCE
  (`residual_motion.py:1326-1329`, `processed_to_source`). One function takes
  processed in and emits source out; the walker stepping feeds that source
  centroid back as the next processed `pred_center`.
- `common_tools/frame_reader.py:18-25` still documents the OLD "model B = all
  public coords are SOURCE" contract, which directly contradicts Option A.
  This stale docstring misled a prior fix.
- A prior in-session "bin fix" added a source->processed conversion in
  `walk_walker._compute_acceptance_and_roi` on inputs that were already
  processed, double-dividing by `bin_factor` (proven:
  `pred_center=(935, ..)` produced an ROI centered at ~467 == 935/2 at
  bin=2), mislocating every walker ROI at bin>1. It did not crash, so it
  passed a presence-only manifest check while drawing geometrically wrong
  boxes.
- Bug #101: the walker raises `ValueError: degenerate ROI (h=252, w=0) at
  frame N; prediction off-frame?` (`residual_motion.py:588`) on bin>1 videos
  (observed Jason-3200m, source 2816x1584, bin=2, interval [5781, 5828],
  during the batch run). `roi_h=252 ~= ROI_MULTIPLIER * 35` where 35 is the
  SOURCE torso height, implicating a path that reaches `observe`'s
  `_compute_roi` else-branch (`roi_override=None`) with SOURCE-scale geometry
  clamped against the PROCESSED frame width. The crash did NOT reproduce via
  `run_interval_walk` standalone on the same interval/reader, so the trigger
  is call-path or batch-state dependent and is not yet pinned.

The durable fix is to make coordinate space a typed object with labeled fields
so source and processed can never be silently confused, conversions are
explicit, double-conversion is impossible by construction, and a space
mismatch fails loud. The crashes (#101) and the wrong-box regression are
downstream symptoms of the implicit tracking.

## Objectives

- Reproduce bug #101 deterministically and revert the bin-fix regression
  before any redesign begins.
- Introduce typed coordinate primitives whose Python type encodes the space,
  so passing the wrong space is a type/boundary error, not a silent shift.
- Route every source<->processed conversion through `FrameGeometry` exactly
  once, with goodbox-correct behavior and an explicit in-bounds predicate.
- Replace bare `(x, y, w, h)` float tuples crossing module boundaries between
  `frame_reader`, `residual_motion`, `walk_walker`, `walk_driver`,
  `state_io`, and the render path with typed primitives.
- Resolve the `observe_blob_at` processed-in / source-out asymmetry with an
  explicit typed return so the walker cannot feed a source center back as
  processed.
- Reduce the coordinate contract to one documented source of truth and delete
  the contradictory model-B docstring.

## Design philosophy

This plan leans on "fix the design, not the symptom" and "long-term over
short-term" from `docs/REPO_STYLE.md`: rather than patch each off-by-bin site,
it makes the illegal state unrepresentable. The trade-off taken: encode space
in the Python TYPE (distinct `SourcePoint` / `ProcessedPoint` /
`SourceBox` / `ProcessedBox` classes) rather than a single `Coord` carrying a
runtime `space` string tag. Type-as-space makes double-conversion a method
that does not exist on the wrong class (caught statically by pyflakes/mypy and
at the boundary), whereas a string tag defers the check to runtime and invites
`if coord.space == ...` branching that re-creates the implicit-tracking
problem. Rejected alternative: a string-tagged `Coord` (less boilerplate, but
the mismatch is only ever a runtime surprise, exactly today's failure mode).
Stabilization-first (per the planning skill): #101 must be reproduced and the
regression reverted before the redesign, because the redesign's whole value is
making that specific mismatch impossible.

## Scope

- Add `common_tools/coord_space.py`: frozen dataclasses `SourcePoint`,
  `ProcessedPoint`, `SourceBox`, `ProcessedBox` (center-size `cx, cy, w, h`),
  with explicit conversion methods routed through `FrameGeometry`, an
  `in_bounds(geometry)` predicate, and edge derivation from float center.
- Make `FrameGeometry` conversions goodbox-correct or expose an explicit
  bounds check so off-frame is a predicate result, not a deep crash.
- Migrate module boundaries to typed primitives, one module per work package:
  `frame_reader`, `residual_motion` (`observe_blob_at` in and out),
  `walk_walker`, `walk_driver`, `state_io`, render path.
- Reproduce #101 with a committed failing test, then make the typed redesign
  turn that failure into an explicit in-bounds soft-miss.
- Revert the in-session bin-fix double-conversion and its tests; restore
  processed-native walker ROI building.
- Replace the stale model-B docstring with one documented contract.

## Non-goals

- Do not change the Viterbi path selection, motion gates, or tracking-quality
  heuristics (the BWD drift that sends a prediction off-frame is a separate
  quality question; this plan only makes off-frame an explicit soft-miss).
- Do not alter the on-disk npz format or bump `SCHEMA_VERSION` (the store stays
  source-frame floats; this is an in-memory typing change, additive per C10).
- Do not re-introduce appearance cues or any banned signal (C6).
- Do not revert the separate, correct render-only-npz change in
  `walk_driver._render_direction_tiles` (down-projects npz source boxes to
  processed for render-only mode) or the earlier-session WS1-A/B/C/WS2 work
  (`conf_from_anchor`, `make_size_at_frame`, `lighten_trace`, solved-interval
  npz persistence), which are verified at bin=1/bin=2 and unrelated.
- Do not redesign crop or encoder geometry beyond honoring the typed boundary.

## Current state summary

- Walk pipeline runs in PROCESSED space (driver feeds `seeds_view.seeds`,
  `walk_walker.py:883-898, 935-938`); `observe_blob_at` reads processed
  `pred_cx_p`, clamps processed `roi_override` against `reader.width`.
- `observe_blob_at` returns SOURCE centroid; `state_io` npz is SOURCE;
  `SeedsView` exposes `.source` and `.seeds` (processed).
- The in-session bin fix (`_compute_acceptance_and_roi` source->processed,
  the degenerate-ROI soft-miss guard, the `residual_motion.py:~1356`
  trace-block edit, and `tests/test_blob_walk_v2_roi_bin.py`) is a regression
  built on the stale model-B assumption and is uncommitted.
- The render-only-npz change in `walk_driver._render_direction_tiles` and the
  earlier-session walker-solver work are correct and stay.
- #101 reproduces in the full batch (`make_walk_html_v2.py --walk
  --intervals-from-corpus dump_step1/24corpus/`) on Jason but not in an
  isolated `run_interval_walk` call.

## Architecture boundaries and ownership

- `common_tools/coord_space.py` is the single home of the typed primitives and
  the conversion methods. No other module re-implements source<->processed
  math after migration.
- `common_tools/frame_reader.py` owns `FrameGeometry` and the goodbox
  resolution; conversions live here or are called from here by `coord_space`.
- Each consuming module (`residual_motion`, `walk_walker`, `walk_driver`,
  `state_io`, render) owns its own boundary conversion and nothing else.
- The npz remains the only durable per-frame store (C13); typed primitives are
  in-memory only and serialize to source floats at the `state_io` boundary.

### Mapping (milestones / workstreams -> components / patches)

| Milestone | Workstream | Component (durable) | Patch |
| --- | --- | --- | --- |
| M0 | WS0-A | `walk_walker` / `residual_motion` revert | Patch 1 |
| M0 | WS0-B | #101 reproduction harness (`tests/e2e`) | Patch 2 |
| M1 | WS1-A | `coord_space` primitives + conversions | Patch 3 |
| M1 | WS1-B | `FrameGeometry` goodbox-correct + contract doc | Patch 4 |
| M1 | WS1-C | `coord_space` unit + sentinel tests | Patch 5 |
| M2 | WS2-A | `frame_reader` boundary typed | Patch 6 |
| M2 | WS2-B | `residual_motion` observe in/out typed | Patch 7 |
| M2 | WS2-C | `walk_walker` stepping typed | Patch 8 |
| M2 | WS2-D | `walk_driver` assembly/persistence typed | Patch 9 |
| M2 | WS2-E | `state_io` source boundary typed | Patch 10 |
| M2 | WS2-F | render path typed | Patch 11 |
| M3 | WS3-A | full-corpus bin>1 verification | Patch 12 |
| M3 | WS3-B | shim removal + docstring cleanup | Patch 13 |

## Milestone plan

### Milestone M0: Stabilize (reproduce #101, revert the regression)

Goal: a deterministic #101 reproduction and a clean processed-native baseline,
before any redesign. This milestone is the gate for M1.

- Deliverables: a committed failing test/e2e that reproduces #101; the
  reverted walker ROI path (processed-native, ROIs centered on the runner at
  bin>1, verified against the repro numbers); a written note naming the exact
  call path that passes source coords with no `roi_override` to `observe`.
- Exit criteria: #101 reproduced by WS0-B; regression removed by WS0-A;
  `pytest tests/ -k blob_walk_v2` green except the new expected-failing #101
  reproduction (marked xfail until M2 fixes it); `docs/CHANGELOG.md` updated;
  the exact falsified call path documented for M1/M2 to target.
- Parallel-plan ready: yes (WS0-A and WS0-B are independent; max doers 2).

### Milestone M1: Typed coordinate primitives

Goal: `coord_space.py` exists with the four primitives, goodbox-correct
conversions, an in-bounds predicate, unit tests, and one documented contract.
No consumer migrated yet.

- Depends on: M0 (WS0-A revert) so the baseline is processed-native; M0
  (WS0-B) so the primitives' in-bounds predicate is validated against the real
  #101 case.
- Deliverables: `common_tools/coord_space.py`; goodbox-correct
  `FrameGeometry`; the single coordinate-contract doc; full unit coverage
  including a bin>1 goodbox-edge sentinel.
- Exit criteria: primitives round-trip source->processed->source within
  rounding at bin=1/2/4; converting a near-goodbox-edge source point yields a
  `ProcessedPoint` whose `in_bounds` is False (not a silent out-of-frame
  coord); double-conversion is statically impossible (no `to_processed` on a
  `ProcessedPoint`); stale model-B docstring deleted; `pytest` green;
  `docs/CHANGELOG.md` updated.
- Parallel-plan ready: yes (WS1-A, WS1-B, WS1-C; WS1-C depends on WS1-A; max
  doers 2-3).

### Milestone M2: Migrate module boundaries

Goal: every cross-module coordinate is a typed primitive; the
processed-in/source-out asymmetry is explicit; #101's xfail flips to pass.

- Depends on: M1 (primitives must exist).
- Deliverables: each of `frame_reader`, `residual_motion`, `walk_walker`,
  `walk_driver`, `state_io`, render migrated to typed boundaries with its
  float-tuple shim removed; `observe_blob_at` typed in and out; the #101
  reproduction test now passes as an explicit in-bounds soft-miss.
- Exit criteria: no bare `(cx, cy, w, h)` tuple crosses a migrated boundary
  (enforced by a lint/test gate); `observe_blob_at` signature consumes a
  `ProcessedPoint`/`ProcessedBox` and returns a typed result; #101
  reproduction flips from xfail to pass; `pytest tests/ -k blob_walk_v2`
  green; `docs/CHANGELOG.md` updated per patch.
- Parallel-plan ready: yes; WS2-B..F each own one module and depend on WS2-A
  and M1; sequence only where a caller needs its callee migrated first (stated
  per work package). Max doers 4-6.

### Milestone M3: Verify and clean up

Goal: full-corpus bin>1 correctness (position, not just presence) and removal
of all temporary shims and stale contract text.

- Depends on: M2 (all boundaries typed).
- Deliverables: a full `24corpus --walk` run with errors=0 across Jason,
  Conant, and Lyra-Wheeling (bin=4 stress); visual confirmation that boxes sit
  on the runner at bin>1; all migration shims deleted; one coordinate contract
  doc remains.
- Exit criteria: `make_walk_html_v2.py --walk --intervals-from-corpus
  dump_step1/24corpus/` completes with `errors=0`; render manifest PASS with
  `conversion_count == 1` on bin>1 videos; spot visual check shows seed and
  solved boxes aligned to the runner at bin=2 and bin=4; no `# TODO remove`
  shim remains; `docs/CHANGELOG.md` updated.
- Parallel-plan ready: yes (WS3-A verification, WS3-B cleanup; max doers 2).

## Workstream breakdown

- WS0-A (`expert_coder`): revert the bin-fix regression to processed-native.
- WS0-B (`expert_coder`): build the deterministic #101 reproduction harness.
- WS1-A (`expert_coder`): `coord_space` primitives and conversions.
- WS1-B (`expert_coder`): goodbox-correct `FrameGeometry` and the contract doc.
- WS1-C (`coder`): `coord_space` unit and sentinel tests.
- WS2-A (`expert_coder`): `frame_reader` typed boundary.
- WS2-B (`expert_coder`): `residual_motion` observe in/out typed.
- WS2-C (`coder`): `walk_walker` stepping typed.
- WS2-D (`coder`): `walk_driver` assembly and persistence typed.
- WS2-E (`coder`): `state_io` source boundary typed.
- WS2-F (`coder`): render path typed.
- WS3-A (`coder`): full-corpus bin>1 verification.
- WS3-B (`coder`): shim removal and docstring cleanup.

## Work packages

### WS0-A: revert bin-fix regression
- Depends on: none.
- Scope: remove `_compute_acceptance_and_roi` and restore
  `_compute_roi_and_observe` to build `acceptance_box` / `roi_override` /
  `dog_diameter_override` directly from the already-processed `anchor_cx /
  seed_w` (pre-session behavior, clamped against `reader.width`); remove the
  degenerate-ROI soft-miss guard added this session; restore the
  `residual_motion.py:~1356` trace-enrichment block to its pre-session form;
  delete `tests/test_blob_walk_v2_roi_bin.py`.
- Verification: `pyflakes` clean on both files; `pytest tests/ -k
  blob_walk_v2` green; a quick instrumented `run_interval_walk` shows
  `roi_override` centered on the processed `pred_center` (not pred_center/bin)
  at bin=2.
- Obvious follow-ons: update `docs/CHANGELOG.md`; do not touch the render-only
  npz change or earlier-session work.

### WS0-B: #101 reproduction harness
- Depends on: none.
- Scope: find and document the exact call path that passes SOURCE-scale
  geometry with `roi_override=None` into `observe_blob_at._compute_roi` at
  bin>1 (candidates: a seed-frame / bootstrap observe, a diagnostic or render
  re-observe, or a batch-state path absent from isolated
  `run_interval_walk`); build a `tests/e2e/e2e_*` reproduction that triggers
  the degenerate-ROI raise deterministically on a bin>1 fixture (Jason
  interval [5781, 5828]); mark the corresponding pytest as `xfail` referencing
  #101.
- Verification: the e2e script exits non-zero on the raise today; the xfail
  test is collected and reported xfail.
- Obvious follow-ons: write the falsified call path into the M1 contract doc;
  update `docs/CHANGELOG.md`.

### WS1-A: coord_space primitives
- Depends on: WS0-A (clean baseline).
- Scope: frozen dataclasses `SourcePoint(cx, cy)`, `ProcessedPoint(cx, cy)`,
  `SourceBox(cx, cy, w, h)`, `ProcessedBox(cx, cy, w, h)`; methods
  `SourcePoint.to_processed(geometry) -> ProcessedPoint`,
  `ProcessedPoint.to_source(geometry) -> SourcePoint`, the analogous box
  conversions (center via point conversion, w/h via the delta helpers),
  `edges()` deriving `(x1, y1, x2, y2)` from the float center, and
  `ProcessedPoint.in_bounds(geometry) -> bool`. No `to_processed` on a
  processed type and no `to_source` on a source type (double-conversion
  unrepresentable). A boundary helper raises `ValueError` (loud, no
  try/except) when a function is handed the wrong primitive.
- Verification: unit round-trip at bin=1/2/4 within rounding; `pyflakes`
  clean; `import coord_space` is module-style.
- Obvious follow-ons: hand the public API to WS2 owners; update
  `docs/CHANGELOG.md`.

### WS1-B: goodbox-correct FrameGeometry + contract doc
- Depends on: WS0-B (the documented falsified path informs the contract).
- Scope: make `source_to_processed` goodbox-correct or document that
  conversion is pure scale and bounds are checked separately via
  `ProcessedPoint.in_bounds`; decide and document which (the in-bounds
  predicate is the preferred elegant resolution since pure scale keeps the
  delta math linear). Delete the stale model-B docstring at
  `frame_reader.py:18-25` and replace the scattered contract with one section
  in a single doc (`docs/` reference) stating: walker + observe operate in
  processed; `observe` returns a typed source center; npz is source; the typed
  primitives are the enforcement.
- Verification: `pytest` green; `tests/test_markdown_links.py` green;
  near-goodbox-edge source point has `in_bounds == False` after conversion.
- Obvious follow-ons: cross-link the contract doc from
  `TRACK_RUNNER_DESIGN.md`; update `docs/CHANGELOG.md`.

### WS1-C: coord_space tests
- Depends on: WS1-A.
- Scope: unit tests for round-trip, edge derivation from float center,
  in-bounds at goodbox edges, and a bin=4 asymmetric sentinel (nonzero ROI
  origin, `w != h`, center not divisible by bin) mirroring the existing
  coord-sentinel test; assert double-conversion is unavailable (the method
  does not exist) via a typed-API contract test.
- Verification: `pytest tests/test_coord_space.py` green.
- Obvious follow-ons: update `docs/CHANGELOG.md`.

### WS2-A..F: boundary migration (one module each)
- Depends on: M1 (WS1-A primitives); WS2-B..F additionally depend on WS2-A
  where they consume `frame_reader` output. WS2-C (walk_walker) depends on
  WS2-B (observe signature) since the stepping consumes observe's typed
  return.
- Scope (per module): replace bare `(cx, cy, w, h)` tuples crossing the
  module's public boundary with the typed primitive in the correct space;
  for WS2-B, change `observe_blob_at` to consume `ProcessedPoint`/
  `ProcessedBox` and return a typed `SourcePoint` (or `ProcessedPoint` plus an
  explicit documented conversion at the call site), removing the silent
  processed-in/source-out flip; remove that module's float-tuple shim.
- Verification (per module): `pyflakes` clean; `pytest tests/ -k
  blob_walk_v2` green; for WS2-B/C, the #101 xfail flips to pass once both
  land (off-frame becomes an explicit `in_bounds == False` soft-miss).
- Obvious follow-ons: update `docs/CHANGELOG.md` per patch; hand the migrated
  signature to dependent WS owners.

### WS3-A: full-corpus bin>1 verification
- Depends on: M2.
- Scope: run `make_walk_html_v2.py --walk --intervals-from-corpus
  dump_step1/24corpus/ -o output_smoke_24corpus -j 2`; confirm `errors=0`,
  manifest PASS with `conversion_count == 1`, and visually confirm boxes sit
  on the runner at bin=2 (Jason) and bin=4 (Lyra-Wheeling).
- Verification: log shows `errors=0`; `check_render_manifest.py` PASS; before
  /after tile PNGs attached.
- Obvious follow-ons: file residual issues as new tickets; update
  `docs/CHANGELOG.md`.

### WS3-B: shim removal and docstring cleanup
- Depends on: M2.
- Scope: delete every temporary float-tuple shim added during M2; confirm one
  coordinate contract doc remains and no module restates source<->processed
  math.
- Verification: `grep` shows no `# TODO remove` coord shim; `pyflakes` and
  `pytest` green.
- Obvious follow-ons: update `docs/CHANGELOG.md`.

## Acceptance criteria and gates

- #101 reproduced deterministically (M0) then resolved as an explicit
  in-bounds soft-miss (M2), proven by the same test flipping xfail->pass.
- Regression gone: walker ROIs centered on the processed `pred_center` at
  bin>1 (not `pred_center / bin`).
- Double-conversion unrepresentable: no `to_processed` method on a processed
  primitive; a contract test asserts the API shape.
- Goodbox edge: a near-edge source point converts to a `ProcessedPoint` with
  `in_bounds == False`, rather than a silent out-of-frame coordinate.
- No bare `(cx, cy, w, h)` tuple crosses a migrated module boundary (lint/test
  gate in M2).
- Full `24corpus --walk` completes `errors=0` with manifest
  `conversion_count == 1` on bin=2 and bin=4 videos; boxes visually aligned to
  the runner.
- `pytest tests/ -k blob_walk_v2` and `tests/test_tr_state_io.py` green; no
  `SCHEMA_VERSION` bump (additive, C10).

## Test and verification strategy

- Unit: `coord_space` round-trip, edge derivation, in-bounds, and the bin=4
  asymmetric sentinel (WS1-C). Asserts live in `tests/` only (PYTHON_STYLE).
- Reproduction: a `tests/e2e/e2e_*` script that triggers #101 on a bin>1
  fixture (WS0-B), plus the xfail pytest marker that flips to pass at M2.
- Boundary: a lint/test gate that fails if a bare coordinate tuple crosses a
  migrated boundary (M2).
- E2E: full `24corpus --walk` plus `check_render_manifest.py` (M3). Visual
  tile check is the acceptance artifact for box position at bin>1; no pytest
  assertions on tile pixels.

## Migration and compatibility policy

- Additive and in-memory only: the npz format and `SCHEMA_VERSION` are
  unchanged; typed primitives serialize to source floats at the `state_io`
  boundary.
- Per-module migration with temporary float-tuple shims marked
  `# TODO remove (typed-coord migration)`; all shims deleted in M3.
- The render-only-npz change and earlier-session walker-solver work are
  preserved unchanged.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| #101 not reproducible in isolation | M0 stalls | call-path/batch-state dependence | WS0-B | Reproduce via the full `--intervals-from-corpus` batch path, not isolated `run_interval_walk`; bisect the call path with instrumented `observe` |
| Goodbox snap vs pure-scale conversion | off-frame coords pass silently | bin>1 near-edge prediction | WS1-B | `in_bounds` predicate is the gate; document pure-scale + separate bounds check |
| observe in/out asymmetry leaks during migration | walker feeds source as processed | partial WS2 migration | WS2-B/C | Migrate observe signature and walker stepping together; xfail->pass gate proves it |
| Wide blast radius of typed migration | broad churn, review bottleneck | many modules touched | M2 owners | One module per work package; shims keep callers compiling; split patches at module seams |
| Reverting regression loses good in-session work | regress verified bin=1/2 features | over-broad revert | WS0-A | Revert only the named bin-fix hunks; keep render-only npz + WS1/WS2 solver work |

## Rollout and release checklist

- Land M0 (revert + reproduction) first; confirm baseline green with #101
  xfail.
- Land M1 primitives + contract doc; confirm unit/sentinel green.
- Land M2 module-by-module; confirm #101 xfail flips to pass when WS2-B/C
  land.
- Land M3 full-corpus verification and shim cleanup.
- Human reviews `git diff` and commits each milestone; AI does not commit.

## Documentation close-out requirements

- `docs/CHANGELOG.md`: one entry per patch (Patch 1..13) under the current
  date, in the canonical category order.
- One coordinate contract doc (WS1-B) replaces the stale model-B docstring;
  cross-linked from `TRACK_RUNNER_DESIGN.md`.
- `tools/blob_walk_v2/README.md`: note the typed-coordinate boundary.
- This plan moves to `docs/archive/` via `git mv` when M3 closes.

## Patch plan and reporting format

- Patches 1-2 (M0), 3-5 (M1), 6-11 (M2, one per module), 12-13 (M3).
- Each patch touches at most two components; split if it exceeds that.
- Report per patch: files changed, exact verification command + success line,
  residual risks.

## Open questions and decisions needed

- Goodbox conversion policy (WS1-B): pure-scale `source_to_processed` plus an
  explicit `in_bounds` predicate (preferred), versus a goodbox-aware
  conversion that clamps. Decision owner: WS1-B, informed by WS0-B's
  reproduction. Decision needed before WS2-B migrates observe.
- `observe_blob_at` return type (WS2-B): return `SourcePoint` (matches today's
  source output) versus return `ProcessedPoint` and convert at the single call
  site. Decision owner: WS2-B; must be made before WS2-C migrates the walker
  stepping.
- Whether `SourceBox`/`ProcessedBox` should also carry an explicit
  `roi_origin` field or keep ROI-local handling separate. Decision owner:
  WS2-B, with WS2-F (render) as consumer.
