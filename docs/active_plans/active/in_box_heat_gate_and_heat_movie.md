# Plan: in-box motion-heat gate and optional heat-movie diagnostic

> **Status: retired.** The offline `blob_walk_v2` reporting product and its
> manifest checker were removed on 2026-08-21. This document is retained only
> as design history; it is not an executable plan or a current validation path.

## Context

The former render-manifest gate proved only presence + plumbing: every
non-seed tile had a solved box and
`conversion_count == 1`. It does NOT prove the solved box sits on anything
moving. A box parked on static background passes. The user wants a real
quality signal: positive motion-cue heat inside the torso box. Two deliverables,
distinct value:

1. **Metric (durable gate input).** Per frame (seed and non-seed alike), at least
   one DoG residual pixel inside the solved torso box must clear an intensity
   threshold: PRESENT iff any valid in-box pixel `> threshold`, ABSENT iff every
   in-box pixel is below it. The reported magnitude is the mean of only the
   above-threshold in-box pixels (a "how hot is the box" measure; below-threshold
   pixels are excluded from the average). DoG smoothing already removes single-pixel
   spikes, so the hot-pixel mean is meaningful. Reads the residual already computed
   during the walk (`trace.residual_dog`), so no extra decode. Feeds the render
   manifest as new fields and a reported gate.
2. **Heat movie (optional diagnostic, not a gate).** `--heat-movie` spills each
   per-frame JET heat composite (reusing `residual_heat_map.compute_heat_map_roi`)
   as a raw headerless `.bgr` file (`frame_%06d.bgr`, size `roiW*roiH*3` BGR bytes)
   to a within-run `/tmp` scratch dir during the walk, then invokes ffmpeg's
   `image2` demuxer to read the numbered sequence directly and encode an `.mkv`.
   Only the ROI is saved, not the full frame, and the ROI shape is FIXED for the
   whole interval (so ffmpeg gets one constant `-s`). Python only dumps bytes;
   ffmpeg owns the encode. The scratch dir is deleted at run end (C13: within-run
   cache only). Memory is bounded by the existing per-worker gray-frame cap, not by
   holding all frames.

Intended outcome: a manifest gate that fails a box-on-background interval without
a human opening `walk.html`, plus a visual movie to confirm what the walker saw.

## Objectives

- Add per-tile `in_box_hot_mean` (float or null; mean of above-threshold in-box
  pixels), `in_box_hot_count` (int; count of above-threshold valid in-box pixels),
  and `in_box_heat_present` (bool, `count > 0`) to the render manifest, computed
  from the in-scope trace, for seed and non-seed tiles alike.
- Add a manifest gate that reports, per interval, the fraction of heat-present
  frames (seed frames included), as a WARNING/reported signal.
- Measure the heat-present fraction distribution across the corpus; defer any
  hard-fail threshold until the data supports a floor.
- Add an optional `--heat-movie` flag that writes a per-direction heat movie via a
  within-run raw `.bgr` fixed-shape ROI spill + ffmpeg `image2` encode, memory
  bounded by the existing gray-frame cap and the scratch dir removed at run end.

## Design philosophy

Reuse over reinvention: the residual heat array (`trace.residual_dog`) and the
JET compositor (`residual_heat_map.compute_heat_map_roi`) already exist and are
in scope at render time, so both deliverables wire existing outputs rather than
recompute residuals. Stabilization-first (REPO_STYLE) governs the gate: an
unproven metric ships as a report, not a hard fail, until the corpus shows its
pass-rate distribution. For the movie, a within-run raw `.bgr`
fixed-shape ROI spill (permitted by C13) holds computed heat frames so memory stays
bounded by the existing gray-frame cap without recompute, and ffmpeg's `image2`
demuxer reads the numbered sequence directly so Python never owns the encode; the
scratch dir is deleted at run end. Saving only a fixed-shape ROI (not the full
frame) keeps the spill small and gives ffmpeg one constant `-s`. Rejected
alternatives: PNG (compression overhead on throwaway frames) and `.npy` (header
ffmpeg cannot parse) -- the consumer here is ffmpeg, not Python, so headerless raw
bytes win.

## Scope

- New shared primitive: mean of above-threshold DoG pixels inside a `ProcessedBox`
  plus the above-threshold pixel count, with exactly one `roi_origin` subtraction.
- Manifest emit: add heat fields in `walk_render.py` `_render_direction_tiles`,
  for seed and non-seed tiles.
- Manifest gate: add a per-interval heat-present fraction REPORT in
  `check_render_manifest.py` (WARNING/reported, no hard fail yet).
- Measurement: collect the heat-present fraction distribution on the corpus and
  record it; do not set a hard-fail floor until the data supports one.
- `--heat-movie`: flag, driver wiring, fixed-shape ROI compositor, raw `.bgr`
  spill, ffmpeg `image2` encode, scratch cleanup.

## Non-goals

- Do not feed the metric into solver interval-confidence or seed-recommendation
  scoring (keeps the change render-side; avoids C9 entanglement).
- Do not recompute residuals for the metric (render-only npz runs without a live
  trace record a `null` heat field and are skipped by the gate, documented).
- Do not add a new schema-version constant or bump `SCHEMA_VERSION` for the new
  manifest fields (additive, render-diagnostic only).
- Do not keep the movie's `.bgr` frame spill between runs; it is a within-run cache
  deleted at run end (C13). Do not depend on it for any later run.
- Do not save full frames to the spill; save only the fixed-shape ROI crop.
- Do not use PNG or `.npy` for the movie frame spill: the consumer is ffmpeg, so
  raw headerless `.bgr` (BGR bytes, told to ffmpeg as `bgr24`) is correct.
- Do not let the ROI shape vary across the interval; ffmpeg needs one constant
  `-s`. Fix it once from the larger seed box.
- Do not write the `.bgr` scratch or the `.mkv` outside `/tmp` during encode (keeps
  the ffmpeg call hook-allowed); copy the finished movie to the run dir afterward.
- Do not change the DoG, threshold, or ROI math in `residual_motion` /
  `residual_heat_map`; reuse them as-is.

## Current state summary

- `residual_pre_pass.precompute_interval_residuals` builds a worker-local,
  per-interval residual store keyed `(frame_index, roi) -> (residual_u8,
  validity_u8)`, PROCESSED coords (contract C5).
- `observe_blob_at` populates `trace.residual_dog` (float32, DoG, validity
  zeroed), `trace.validity_mask`, `trace.roi_origin_xy` (PROCESSED) per frame
  when a `trace_sink` is passed.
- At `walk_render.py` `_render_direction_tiles` the per-frame trace and the
  per-frame solved `ProcessedBox` are both in scope (the renderer already reads
  `trace.roi_origin_xy` and draws the solved box).
- `residual_heat_map.compute_heat_map_roi(..., out_arrays=d)` returns a JET BGR
  composite crop + origin and exposes `d["residual_dog"]`, `d["validity_mask"]`,
  `d["roi_bounds"]`.
- `video_io.VideoWriter` pipes raw BGR frames to ffmpeg stdin one at a time.
- `check_render_manifest.check_records` has two per-tile gates; the new metric
  slots in after the `solved_box_present` read (line ~108).
- `residual_motion.DEFAULT_THRESHOLD` / heat-map `threshold=10.0` is the existing
  intensity floor; reuse it, do not invent a new constant.

## Architecture boundaries and ownership

- The in-box heat primitive is the single coordinate-sensitive seam (one ROI
  subtraction); it is owned by one work package and reused by both the manifest
  metric and the movie's per-frame heat readout. No other code computes
  in-box heat.
- Manifest emit owns adding fields; the gate file owns reading them. Field names
  are the interface and are frozen in M1-A.
- The movie compositor reuses `compute_heat_map_roi` for colorization and the
  primitive for the displayed heat number; it owns no residual math.
- PROCESSED is the only coordinate space the primitive accepts; box and
  `roi_origin` are both PROCESSED, matching `walk_draw.processed_box_to_tile_local`.

### Mapping (milestones / workstreams -> components / patches)

| Workstream | Component (durable) | Patch |
| --- | --- | --- |
| M1-A | `common_tools` or `track_runner` in-box heat primitive + tests | Patch 1 |
| M1-B | `walk_render._render_direction_tiles` manifest heat fields | Patch 2 |
| M1-C | `check_render_manifest` per-interval heat gate (warn) | Patch 3 |
| M1-D | corpus heat-present fraction measurement + report | Patch 4 |
| M1-E | CHANGELOG + manifest-field doc | Patch 5 |
| M2-A | per-frame heat-frame compositor (full frame + JET crop + box) | Patch 6 |
| M2-B | `--heat-movie` flag, driver wiring, ffmpeg image2 encode + cleanup | Patch 7 |
| M2-C | e2e heat-movie smoke (file created, frame count, bounded memory) | Patch 8 |
| M2-D | USAGE + CHANGELOG for `--heat-movie` | Patch 9 |

## Milestone plan

### Milestone M1: in-box heat metric gates the manifest

Goal: every non-seed tile records its in-box heat; the gate flags low-heat
intervals.

- **M1-A (expert_coder) -- in-box heat primitive.** Pure function: given
  `residual_dog` (float32 HxW, validity already zeroed), `validity_mask` (uint8),
  `roi_origin` (PROCESSED x,y), a `coord_space.ProcessedBox`, and the intensity
  `threshold`, return `(hot_mean, hot_count)`: the mean of in-box valid pixels whose
  DoG value exceeds `threshold`, and the count of such pixels. Exactly one
  `roi_origin` subtraction (mirror `walk_draw.processed_box_to_tile_local`); derive
  box edges from the float center before any int cast; clamp the box region to the
  array; select pixels where `validity_mask != 0 and residual_dog > threshold` inside
  the box; `hot_mean = mean(selected)`, `hot_count = len(selected)`; return
  `(None, 0)` when no pixel qualifies. Add `require_processed_box` guard. Depends on:
  none.
  - Obvious follow-ons: sentinel unit test in `tests/` (asymmetric box, nonzero
    `roi_origin`, bin=4-shaped array, w != h; a box with some above-threshold pixels
    -> correct mean and count; an all-below-threshold box -> `(None, 0)`); no asserts
    in scripts (PYTHON_STYLE); run `pytest tests/ -k heat`; update `docs/CHANGELOG.md`.
- **M1-B (coder) -- manifest heat fields.** In `walk_render.py`
  `_render_direction_tiles`, after the solved box and trace are resolved, call the
  M1-A primitive with `trace.residual_dog`, `trace.validity_mask`,
  `trace.roi_origin_xy`, the solved `ProcessedBox`, and the reused threshold; add
  manifest fields `in_box_hot_mean` (float or null), `in_box_hot_count` (int),
  `in_box_heat_present` (bool, `count > 0`), `in_box_heat_computed` (bool), and
  `heat_threshold_used`. `in_box_heat_computed` is the eligibility discriminator: it
  is `true` only when a live trace AND a solved box were both present so the
  primitive actually ran. A COMPUTED-COLD frame is `computed=true, hot_count=0,
  hot_mean=null, present=false`; a NOT-COMPUTED frame (no live trace, e.g.
  render-only npz, or no solved box) is `computed=false, hot_count=0, hot_mean=null,
  present=false`. This separation lets the gate keep computed-cold frames in the
  denominator while skipping not-computed ones. Seed frames record heat the same as
  non-seed frames (their heat is reported and included; a seed truth box with no
  in-box heat is a strong early failure signal -- but see the M1-D seed-residual
  verification). Depends on: M1-A (field semantics + primitive).
  - Obvious follow-ons: confirm all five fields appear in a real `--walk` manifest;
    confirm render-only npz writes `computed=false`; confirm a real cold live-trace
    frame writes `computed=true, present=false`; update `docs/CHANGELOG.md`.
- **M1-C (coder) -- manifest heat report.** In
  `check_render_manifest.check_records`, aggregate per `(source, direction)` the
  fraction of ELIGIBLE tiles that are heat-present, where eligible means
  `in_box_heat_computed == true` (computed-cold tiles, `present=false`, STAY in the
  denominator; only not-computed tiles are skipped). Seed and non-seed tiles both
  count. Print a per-interval REPORT line with that fraction, the eligible/total
  counts, and the threshold used. This is report-only: it does NOT set the exit code
  (no hard fail). Keep the existing two gates and the exit status unchanged. Depends
  on: M1-A/M1-B (field names + `in_box_heat_computed` semantics).
  - Obvious follow-ons: print each interval's heat-present fraction, eligible count,
    skipped (not-computed) count, and threshold; add a fixture manifest test with a
    warm interval, a computed-cold interval (eligible, present=false -> fraction 0,
    still counted, exit code unchanged), and a not-computed interval (skipped from
    the denominator); update `docs/CHANGELOG.md`.
- **M1-D (tester) -- corpus heat-present fraction measurement.** (Owner is tester
  because runtime/corpus access is required; a reviewer evaluates the resulting
  report.) First VERIFY the seed-frame residual is meaningful: confirm
  `trace.residual_dog` on seed frames is computed on the same temporal basis as
  non-seed frames and that first/last/bracketing frames are not silent edge cases;
  if seed residuals turn out weak by construction, keep seeds in the report but
  stratify. Then run the corpus with `--walk` and record, per video and per
  direction: the heat-present fraction, the `in_box_hot_mean` distribution, the
  seed-vs-non-seed fraction split, the count of skipped (not-computed) tiles, the
  threshold used, and a list of suspicious low-fraction intervals. Do NOT set a
  hard-fail floor (the expected pass-rate is unknown). Depends on: M1-B (real
  manifests), M1-C (report machinery).
  - Obvious follow-ons: record the measured fractions, seed/non-seed split, skipped
    counts, and threshold in the plan's Open decisions and `docs/CHANGELOG.md`; flag
    candidates for a future floor without committing one.
- **M1-E (docs) -- documentation.** One `docs/CHANGELOG.md` entry per patch; note
  the new manifest fields and the gate in the blob_walk_v2 README and
  `docs/COORDINATE_SPACES.md` (heat read is PROCESSED, single ROI subtraction).
  Depends on: M1-B, M1-C.

Exit criteria: a `--walk` manifest carries `in_box_hot_mean` / `in_box_hot_count`
/ `in_box_heat_present` / `in_box_heat_computed` / `heat_threshold_used`;
`check_render_manifest` REPORTS (does not fail the exit code on) a planted cold
interval as computed-cold and reports the calibrated corpus fractions; the existing
two gates still govern exit status; sentinel + fixture tests green; CHANGELOG
updated; corpus fractions recorded. Parallel-plan ready: yes (M1-A first; M1-B and
M1-C run concurrently after A; M1-D after B+C; M1-E trails B+C). Max doers in M1: 2.

### Milestone M2: optional heat-movie diagnostic

Goal: `--heat-movie` writes a per-direction heat movie without accumulating a
list of frames in memory, not gating anything.

Depends on: M1-A (reuses the primitive for the on-frame heat readout).

**Frame handling (memory contract).** Use a within-run on-disk raw-frame spill,
which C13 now permits (cache may be saved within a run; it must not be depended on
between runs). This decouples the slow heat compute from the encode, lets ffmpeg
own the encode, and keeps memory bounded WITHOUT recompute.

- Fixed ROI shape (computed once per interval, before the spill): take the larger
  of the two bracketing seed boxes (seed1 vs seed2, by torso height) and derive a
  single fixed `(roiW, roiH)` for the whole interval via the solver's ROI rule
  (`ROI_MULTIPLIER` on the larger seed). This shape is constant across every frame
  so ffmpeg gets one `-s`.
- Spill pass (during the walk): place the fixed `(roiW, roiH)` window centered on
  the solved box center; if the window extends past any frame edge, BLACK-FILL the
  off-frame region (do not shift the window and do not scale -- shifting moves the
  box off-center, scaling distorts overlay coordinates). The output is ALWAYS
  exactly `(roiW, roiH)`. Write the raw bytes to a `/tmp` run-scoped scratch dir as
  `frame_%06d.bgr` (headerless, exactly `roiW*roiH*3` bytes, zero-padded index for
  ffmpeg's `image2` glob). Raw `.bgr` is chosen because the consumer is ffmpeg, not
  Python: PNG adds compression overhead on throwaway frames and `.npy` carries a
  header ffmpeg cannot parse. The bytes are BGR; ffmpeg is told `bgr24` (no channel
  swap, no conversion copy).
- Encode pass: invoke ffmpeg's `image2` demuxer directly on the numbered sequence:
  `ffmpeg -f image2 -framerate <fps> -s <roiW>x<roiH> -pix_fmt bgr24 -i
  <scratch>/frame_%06d.bgr -c:v libx264 -pix_fmt yuv420p <scratch>/heat.mkv`
  (mirror the `subprocess.run` + returncode-check pattern in `video_io` /
  `encoder.copy_audio`). Python never holds more than the one frame it is writing.
  ffmpeg is REQUIRED only for `--heat-movie`; normal `--walk` must not depend on it.
  If ffmpeg is missing when `--heat-movie` is requested, raise a clean user-facing
  error (do not crash mid-encode, do not silently skip).
- Output + cleanup (ordered, verified): write `.mkv` under `/tmp` (so the ffmpeg
  call is hook-allowed: all path args under `/tmp`); verify it exists and is
  non-empty; copy it beside the per-interval render output; verify the destination
  exists and is non-empty; ONLY THEN delete the scratch dir. On ffmpeg failure or a
  failed copy, delete the scratch `.bgr` frames but preserve any partial diagnostic
  the user would want and surface the error. The scratch dir is never kept as a
  between-runs artifact.
- Memory bound: the spill never holds more than the already-bounded gray-frame
  cache (`residual_motion.MAX_GRAY_CACHE_FRAMES`, currently 40 -- the same cap the
  solver workers and pre-pass use, mirrored as `MAX_PREPASS_BUFFER_FRAMES = 40`)
  plus the single frame being written; ffmpeg streams the sequence itself. Do not
  invent a new frame-limit constant; if one is needed, import
  `MAX_GRAY_CACHE_FRAMES`. The full per-frame trace map is never held in memory all
  at once -- frames go to the `.bgr` spill instead.
- Disk note: raw ROI frames are uncompressed but ROI-sized, not full-frame, so the
  footprint is far smaller than full frames; the per-interval frame count is bounded
  and the scratch dir is deleted at run end. Comment this in the spill code.

- **M2-A (expert_coder) -- fixed-ROI heat compositor + raw spill.** Two parts: (i)
  a helper that computes the fixed `(roiW, roiH)` for the interval from the larger
  of seed1/seed2 (by torso height) via the solver's ROI rule; (ii) a per-frame
  function: given a source frame index, solved `ProcessedBox`, reader,
  scene_transform, the fixed `(roiW, roiH)`, and the scratch dir, build the BGR ROI
  composite (JET heat from `residual_heat_map.compute_heat_map_roi`, solved box drawn
  with `walk_draw` styling, M1-A in-box hot-mean drawn as text). The OUTPUT is always
  exactly `(roiW, roiH)`: place a fixed `(roiW, roiH)` window centered on the solved
  box; if the window extends past any frame edge, BLACK-FILL the off-frame region
  (do not shift -- that moves the box off-center; do not scale -- that distorts
  overlay coordinates).
  Write raw BGR bytes to `<scratch>/frame_%06d.bgr` (contiguous `tobytes()`, exactly
  `roiW*roiH*3` bytes) and return the path (not the array held long-term).
  Extra-decode note: the metric path (M1) never decodes extra frames; this OPTIONAL
  movie compositor MAY read frame imagery via `compute_heat_map_roi` (it reads the
  center frame plus its warp neighbors through the bounded gray cache). M2-B's report
  states whether extra frame reads occurred. Depends on: M1-A.
  - Obvious follow-ons: assert every written file is exactly `roiW*roiH*3` bytes
    (constant across the interval, independent of the box's position); handle the
    `compute_heat_map_roi`-returns-None edge frame (fixed-shape crop of base + box
    only); update `docs/CHANGELOG.md`.
- **M2-B (coder) -- flag, wiring, ffmpeg encode, cleanup.** Add `--heat-movie` (off
  by default) to the blob_walk_v2 CLI; in the driver, when set and a live walk is
  running, compute the fixed `(roiW, roiH)` once, create a `/tmp` run-scoped scratch
  dir, drive the M2-A spill across the interval frames (zero-padded sequential names
  per direction), then run ffmpeg's `image2` encode (`-f image2 -framerate <fps> -s
  <roiW>x<roiH> -pix_fmt bgr24 -i frame_%06d.bgr -c:v libx264 -pix_fmt yuv420p
  heat.mkv`) via `subprocess.run` with
  a returncode check (mirror `encoder.copy_audio`); on success verify the `.mkv` is
  non-empty, copy it beside the per-interval render output, verify the destination,
  then delete the scratch dir (the verified-before-cleanup order above). Use an
  injectable/deterministic scratch parent under `/tmp` and surface the scratch path
  in the run's returned metadata or log. Define ffmpeg-missing behavior (clean error
  when `--heat-movie` requested; normal `--walk` unaffected) and failure-path
  cleanup (remove `.bgr` frames, surface the error). Report whether the compositor
  performed extra frame reads. Depends on: M2-A.
  - Obvious follow-ons: confirm ffmpeg returns 0 and the `.mkv` plays; confirm the
    scratch dir is removed on both the success and ffmpeg-failure paths; confirm that
    with `--heat-movie` absent no movie is produced and existing solve/render/gate
    behavior is unchanged (existing tests still pass) -- do NOT require byte-identical
    output; update `docs/CHANGELOG.md`.
- **M2-C (tester) -- e2e smoke.** Under `tests/e2e/`, run `--walk --heat-movie` on
  one short interval using an injectable `/tmp` scratch parent; assert the movie file
  exists and is non-empty; assert every `.bgr` file is the same size `roiW*roiH*3`
  bytes (constant ROI shape, independent of box position); assert that AFTER the run
  no run-scoped scratch directory remains (post-run leak check, not a mid-run probe);
  assert the movie path is reported in the run metadata/log. Skip cleanly when ffmpeg
  is absent. Do NOT write a literal "one frame resident" memory assertion; if memory
  is checked at all, assert no growing N-frame list (no existing memory harness to
  lean on). Depends on: M2-A, M2-B.
  - Obvious follow-ons: name it `e2e_heat_movie_*.py` per E2E_TESTS naming; reuse a
    stable `output_smoke/` dir; add a check (test or code review) for cleanup on the
    ffmpeg-failure path; update `docs/CHANGELOG.md`.
- **M2-D (docs) -- documentation.** Document `--heat-movie` in `docs/USAGE.md` and
  the blob_walk_v2 README; one `docs/CHANGELOG.md` entry per patch. Depends on:
  M2-B.

Exit criteria: `--walk --heat-movie` produces a playable per-direction `.mkv`
with the heat overlay, solved box, and per-frame heat value; e2e smoke green; no
growing in-memory frame list; with `--heat-movie` absent no movie is produced and
existing solve/render/gate behavior is unchanged (existing tests pass; NOT a
byte-identical requirement); ffmpeg-missing and failure-path cleanup defined; docs
updated. Parallel-plan ready:
yes (M2-A after M1-A; M2-B after M2-A; M2-C after M2-B; M2-D trails M2-B). Max
doers in M2: 2.

## Acceptance criteria and gates

- Primitive: in-box hot-pixel mean and count are correct on a sentinel array with
  bin=4 shape, nonzero `roi_origin`, asymmetric box, w != h. Box-region selection
  convention is explicit and tested: edges derived from the float center, rounding
  rule fixed (floor left/top, right/bottom exclusive), a fractional-center case with
  named expected pixels, and a zero-area / fully-out-of-ROI box returns `(None, 0)`
  without crashing. A sentinel pixel that is above threshold but INVALID
  (`validity_mask == 0`) is excluded -- validity mask is authoritative, not the
  zeroed residual. (Single-conversion correctness is verified behaviorally via the
  nonzero-`roi_origin` asymmetric-box case; a literal `conversion_count == 1` assert
  is a code-review concern, not a hard test, unless that counter already exists.)
- Threshold single source of truth: one imported constant feeds the primitive, the
  manifest `heat_threshold_used`, and the movie overlay; a test or code inspection
  confirms the manifest-reported threshold equals the one passed to the primitive.
- Manifest: `--walk` tiles (seed and non-seed) carry `in_box_hot_mean` (float or
  null), `in_box_hot_count` (int), `in_box_heat_present` (bool),
  `in_box_heat_computed` (bool), `heat_threshold_used`; computed-cold is
  distinguishable from not-computed via `in_box_heat_computed`.
- Report: `check_render_manifest` prints a per-interval heat-present fraction
  (report-only, no exit-code change); eligibility is `in_box_heat_computed == true`,
  so computed-cold tiles stay in the denominator and only not-computed tiles are
  skipped. A planted computed-cold interval reports fraction 0 WITHOUT changing the
  exit code; the existing two gates alone govern exit status.
- C9: the metric is computed per-direction tile (FWD and BWD independent), never
  from blended output.
- C2: the only magnitude constant is the reused intensity threshold (an image
  floor, not a geometry-pixel threshold); no runner-relative geometry uses raw
  pixels.
- Movie: `.mkv` created and plays; every `.bgr` frame is the same `roiW*roiH*3`
  bytes; no growing in-memory frame list; `--heat-movie` absent produces no movie
  and leaves existing behavior unchanged (existing tests pass; not byte-identical).
- ffmpeg availability: normal `--walk` does not require ffmpeg; `--heat-movie`
  requires it and emits a clean error (or e2e skip) when it is missing.
- `pytest tests/ -k heat` green; `check_render_manifest` run on a fresh `--walk`
  corpus green.

## Test and verification strategy

- Unit: primitive sentinel test (coordinate convention, validity exclusion of
  above-threshold invalid pixels, fractional center, zero-area box) in `tests/`;
  fixture manifest test for the report (warm interval; computed-cold interval that
  reports fraction 0 yet leaves the exit code unchanged; not-computed interval
  excluded from the denominator). Asserts live only in `tests/` (PYTHON_STYLE).
- E2E: `tests/e2e/e2e_heat_movie_*.py` for the movie (file present + non-empty,
  constant `.bgr` size, post-run no scratch leak, ffmpeg-skip when missing); not
  collected by pytest (`collect_ignore`).
- Calibration: a one-off corpus run recorded as evidence in the plan, not a pinned
  test (no brittle value asserts on heat magnitudes).
- Historical verification used the retired offline walker and manifest
  checker. The retained application and permanent pytest suite no longer call
  either entry point.

## Migration and compatibility policy

- Additive: new manifest fields and the gate are additive; older manifests without
  the heat fields are handled by the gate skipping null/absent heat (same pattern
  as the existing `non_seed_missing_solved_box` fallback). No `SCHEMA_VERSION` bump.
- `--heat-movie` defaults off; with the flag absent no movie is produced and
  existing solve/render/gate behavior is unchanged (existing tests pass). Not a
  byte-identical guarantee -- M1 already adds additive manifest fields.
- No artifact deletion; the movie is a new optional output.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Premature hard-fail floor blocks real intervals | Blocks good runs | Floor set before pass-rate is known | M1-D | Report-only this round; no hard fail until the corpus pass-rate distribution is understood |
| Computed-cold vs not-computed conflated | Inflated/skewed fraction | `present=false` used for both | M1-B/M1-C | `in_box_heat_computed` flag: computed-cold stays in denominator, only not-computed (no trace / no box) is skipped |
| Seed residual weak by construction | Misleading seed heat | Seed frame residual on a different temporal basis | M1-D | Verify seed residual basis before relying on it; stratify seed vs non-seed in the report |
| Double coordinate subtraction in primitive | Wrong box region, wrong heat | bin>1, nonzero roi_origin | M1-A | One subtraction; sentinel test with asymmetric box + nonzero roi; mirror walk_draw |
| Movie accumulates frames -> OOM | Crash on long interval | Holding all frames in memory | M2-B | Within-run raw `.bgr` ROI spill + ffmpeg image2 encode; footprint bounded by the existing MAX_GRAY_CACHE_FRAMES cache + one frame; reuse that cap, no new one; e2e memory bound |
| Channel-order swap (R/B) in movie | Wrong colors | Writing BGR but telling ffmpeg rgb24 | M2-A/M2-B | Bytes are BGR; tell ffmpeg `bgr24` (no conversion); file extension `.bgr` keeps it honest; e2e sanity-checks the movie is valid |
| Varying ROI shape breaks ffmpeg `-s` | Encode fails or garbled | ROI size changes per frame | M2-A | Fix `(roiW, roiH)` once from the larger seed; black-fill off-frame regions (no shift, no scale) so output is always that shape; e2e asserts constant file size |
| Raw ROI scratch fills disk on long intervals | Disk pressure | Many uncompressed ROI frames | M2-A | ROI-sized (not full-frame); bounded frame count; deleted at run end; `/tmp` scratch |
| Scratch frame cache leaks between runs | Disk fill, C13 breach | Scratch dir not removed | M2-B | Delete scratch dir at run end; e2e asserts no leak; cache is within-run only, never depended on across runs |
| Occlusion frames legitimately cold | Misread as bad tracking | Runner briefly hidden | M1-D | Report per-interval fraction, not per-frame pass/fail; any future floor must tolerate occlusion |
| Threshold is a scene-geometry pixel value (C2) | Contract breach | New magnitude constant invented | M1-A/M1-D | Reuse existing intensity DEFAULT_THRESHOLD; document it as an image floor, not geometry |

## Rollout and release checklist

- M1 lands the heat report (no hard fail); corpus heat-present fractions recorded;
  a hard-fail floor is a deferred follow-up once the pass-rate is understood.
- M2 lands `--heat-movie` default-off; no impact on existing solve/render/gate.
- Each milestone updates `docs/CHANGELOG.md` per the repo rule. Version control
  (staging, commits, plan-doc archival) is the human reviewer's step after the work
  lands; it is not a coder deliverable or a gate.

## Documentation close-out requirements

- `docs/CHANGELOG.md`: one entry per patch (Patch 1..9) under today's date.
- blob_walk_v2 README: new manifest heat fields, the gate, and `--heat-movie`.
- `docs/COORDINATE_SPACES.md`: heat read is PROCESSED, one ROI subtraction.
- `docs/USAGE.md`: `--heat-movie` usage.
- Open decisions below updated with the measured corpus fractions and threshold.

## Patch plan and reporting format

- Nine patches total (Patch 1..9 per the Mapping table), each one coder, each at
  least one reviewable patch. M1 patches 1-5; M2 patches 6-9.
- Report each patch with: files changed, the exact `pytest` / gate command run and
  its success line, and any null-heat or memory observations.

## Open questions and decisions needed

- Exact per-interval heat-present fraction floor for a future HARD gate:
  DECISION-NEEDED. BLOCKED on M1-B defect fix (see below). The M1-D corpus run
  (2026-05-30) measured 100% NOT-COMPUTED (0/0 eligible) for every tile across
  4 of 6 videos (568 tiles total, Conant / IMG_3823 / IMG_3830 / Jason; Lyra-Hersey
  and Lyra-Wheeling still running but identical by code path). Root cause: the heat
  computation in walk_driver reads trace.residual_dog after lighten_trace has already
  set it to None, so in_box_heat_computed is always False. No meaningful fraction data
  is available until the defect is fixed. A full re-run with the corrected computation
  is required before any floor can be chosen.
- M1-B design defect: the heat metric (in_box_heat_computed, in_box_hot_mean) is
  computed in the render phase using trace.residual_dog, but walk_walker.lighten_trace
  drops residual_dog from every stored trace BEFORE the render phase reads it. Fix
  options: (A) compute (hot_mean, hot_count) during the walk loop, immediately after
  observe_blob_at returns, store the scalar results in the lightened trace or a
  parallel per-frame dict, and read them in the manifest phase; (B) recompute heat
  from residual_heat_map.compute_heat_map_roi during the render phase (re-derives
  residual from the reader, no lighten_trace dependency). Option A is cheaper (no
  extra frame read); option B is more independent but costs an extra decode per frame.
- Seed-frame residual basis: VERIFIED (2026-05-30). Both seed and non-seed frames
  have residual_dog dropped by lighten_trace; the "seed residual weak by construction"
  risk is superseded by the M1-B defect -- NO frame's residual survives to the
  manifest phase. Verification: the bootstrap step calls observe_blob_at at the seed
  frame (step=0), then lighten_trace drops residual_dog. The neighbor seed frame is
  NOT observed by the walker (loop breaks at frame_f==neighbor_seed_frame). Conclusion:
  seed and non-seed frames are on the same temporal basis; both lose their residual to
  lighten_trace; neither is a silent edge case for a different reason. When the M1-B
  defect is fixed, seed and non-seed frames will both have valid heat data and no
  stratification is needed for the residual basis reason.

## Resolved decisions

- Movie frame delivery: RESOLVED -- within-run raw `.bgr` disk spill of composited
  fixed-shape ROI frames (`frame_%06d.bgr`, `roiW*roiH*3` BGR bytes) during the
  walk, then ffmpeg's `image2` demuxer reads the numbered sequence directly and
  encodes; scratch dir deleted at run end. C13 (as updated by the user) permits a
  within-run cache; it must not be depended on between runs. Raw `.bgr` chosen over
  PNG (compression overhead on throwaway frames) and `.npy` (header ffmpeg cannot
  parse): the consumer is ffmpeg, so headerless raw bytes win. ffmpeg owns the
  encode (Python just dumps bytes); `video_io.VideoWriter` is not used for the movie.
- Movie ROI: RESOLVED -- only the ROI is saved, not the full frame; the ROI shape
  is FIXED for the whole interval (one constant ffmpeg `-s`), derived from the
  larger of seed1/seed2 (by torso height) via the solver's ROI rule. Frames crop to
  that fixed shape centered on the solved box; off-frame regions are black-filled
  (no shift, no scale), so output is always exactly `(roiW, roiH)`.
- Movie color + container: RESOLVED -- bytes are BGR, told to ffmpeg as `bgr24`
  (no channel swap, no conversion copy); output `.mkv` with libx264 + yuv420p (the
  codec and pixel format matter, not the `.mkv` vs `.mp4` container). Scratch and
  encode happen under `/tmp` (hook-allowed); the finished `.mkv` is copied beside
  the render output. Memory bound reuses the existing `MAX_GRAY_CACHE_FRAMES` cap;
  no new constant.
- Metric form: RESOLVED -- per-frame heat is PRESENT iff any valid in-box DoG pixel
  `> threshold`; the reported magnitude is the mean of only the above-threshold
  in-box pixels (below-threshold pixels excluded). Not a max, not a mean-of-all, not
  a ratio. DoG smoothing removes single-pixel spikes, so the hot-pixel mean is
  meaningful. Reuses `residual_motion.DEFAULT_THRESHOLD` (image intensity floor,
  C2-safe).
- Seed-frame heat: RESOLVED -- seed frames ARE heat-checked, same as non-seed. A
  seed (human-truth) box with no in-box heat predicts a failing interval, so it is
  the strongest early signal; not exempt.
- Gate hardness: RESOLVED (this round) -- report-only, no hard fail. The pass-rate
  per interval is unknown, so a hard floor is deferred to a follow-up after M1-D
  measures the corpus distribution.
- Metric data source: RESOLVED -- `trace.residual_dog` already in scope at
  `_render_direction_tiles`; no extra decode. Render-only npz has no trace -> null
  heat, skipped by gate.
- Scope boundary: RESOLVED -- render-side only; the metric does not feed solver
  scoring (avoids C9 entanglement, smaller blast radius).
