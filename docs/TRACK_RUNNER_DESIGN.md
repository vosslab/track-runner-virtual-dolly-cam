# Track runner design philosophy

This document is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md). On conflict, the
contract wins and this document is corrected.

This document explains the principles behind the track runner architecture.
For the technical specification, see
[TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md). For the motion-cue heat map and per-frame
blob pipeline, see
`MOTION_CUE_HEAT_MAP.md` (mechanism-level
technical doc). The short consumer-facing summary is
[RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md). For
evolution history, see
[TRACK_RUNNER_HISTORY.md](TRACK_RUNNER_HISTORY.md).

## Per-interval vocabulary

Three terms name the geometry that lives inside one seed-to-seed interval.
Use these in prose:

- **forward interval path** -- what the FWD pass produces for this
  interval.
- **backward interval path** -- what the BWD pass produces for the same
  interval, independently.
- **blended interval path** -- the output trajectory formed by combining
  the two pass paths after both complete. Output artifact only; full
  consumption rules in
  `FWD_BWD_MODEL_METHODOLOGY.md`.

The in-code identifiers match the prose: `forward_path`,
`backward_path`, `blended_path`. See the
"Legacy code names" subsection of
`FWD_BWD_MODEL_METHODOLOGY.md` for the
historical rename record.

## Core principle

> Human establishes identity. Machine interpolates geometry.

The user identifies who the runner is. The machine figures out where the runner
is between those identifications. This division is fundamental: people are good
at recognition, machines are good at frame-to-frame geometry. Mixing the two
roles leads to the tracker either losing the runner (too much machine autonomy)
or requiring constant human supervision (too little).

## Five-stage pipeline structure

The solve process runs as five named, independently observable stages:

| Stage | Code ID | Purpose |
| --- | --- | --- |
| 1 | `stage_1_camera_motion` | Precompute camera motion and build scene transform. See [TR_CAMERA_MOTION_METHOD.md](TR_CAMERA_MOTION_METHOD.md). |
| 2 | `stage_2_race_start_id` | Identify race-start interval (seed pair spanning `race_start_frame`). |
| 3 | `stage_3_hermite_pass` | Hermite-only solve on all post-race intervals; score for confidence tier. |
| 3b | `stage_3b_pre_race_synth` | Stationary pre-race synthesis (scene-anchored, seed-averaged per C4). Fires as soon as Stage 3's race-start interval completes. |
| 4 | `stage_4_blob_promoted` | Blob-coupled re-solve on promoted intervals (low/fair confidence only). |
| 5 | `stage_5_blob_full` | Optional blob-coupled re-solve on every post-race interval (via `--full` flag). |

The pipeline's cost philosophy: "Spend expensive evidence only where cheap evidence is uncertain." Stage 3 runs Hermite (fast, ~3 ms per 100-frame interval) on every post-race interval. Stage 4 then promotes intervals with FWD/BWD disagreement (low or fair confidence tier) into the blob observer (expensive, one per interval). Stage 5 is user-selectable (`--full` flag) and runs blob on every interval for maximum fidelity when speed is less critical.

Default solve runs Stages 1-4. `--hermite-only` stops after Stage 3 for diagnostics. `--full` runs Stages 1-5. Refine mode respects the same stage selection: refined intervals enter at Stage 3 and optionally promote to Stage 4 per their confidence score.

### Stage 4 internal step: per-worker per-interval residual pre-pass

Inside each Stage 4 worker, before FWD/BWD blob snap runs, the worker performs a sequential pre-pass over its interval's frame range and builds a worker-local residual store keyed by `(frame_index, roi)`. The pre-pass is implemented in [residual_pre_pass.py](../track_runner/residual_pre_pass.py) and called from [interval_solver.py](../track_runner/interval_solver.py) `solve_interval_analytical`. Both FWD and BWD passes then read residuals from the store via the `precomputed_store` parameter on `observe_blob_at`; on a miss the legacy reader path still works (used by diagnostic tools).

This is NOT a separate pipeline stage. Per contract clause C5, intervals are independent and the pre-pass is scoped to one interval, owned by one worker, destroyed when the worker process exits. There is no global / master-side walk and no shared memory across intervals.

The pre-pass eliminates scattered random-access reads from Stage 4. On HEVC HDR source video those reads can cost 2-4 seconds per call due to keyframe-relative decode cost; sequential reads on the same file are 6-14 ms per frame. See [README.md](../common_tools/README.md) for measured numbers and a strategy table.

## Why bounded interval solving

Seeds are hard anchors, not suggestions. Each inter-seed interval is solved
independently with forward and backward propagation from the bracketing seeds.

Benefits of this design:

- **Parallelizable**: intervals have no cross-talk, so they can be solved
  concurrently.
- **Debuggable**: a bad interval can be diagnosed in isolation by inspecting
  its forward and backward interval paths.
- **Incrementally refinable**: adding a seed splits one interval into two.
  Only the two new intervals need re-solving.
- **Disagreement is signal**: when forward and backward propagation disagree,
  that disagreement honestly reflects uncertainty. The solver does not hide it.

## Signal hierarchy

This section is subordinate to
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md); on conflict, the
contract wins.

Active machine evidence is interval geometry propagation coupled with
per-frame residual-motion observations. Neither leg is adequate on its
own; the coupling is the point. See
`FWD_BWD_MODEL_METHODOLOGY.md` for the
mechanics of the coupled model, and
[RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md) for
the per-frame measurement pipeline (residual-motion cue map, blob
extraction, corridor filter, per-frame observation).

Appearance cues -- jersey color, HSV matching, color histograms, and
runner-appearance template matching -- are banned as identity or
classification evidence per contract clause C6. Prior versions blended
these cues at scale-gated weights and the results were unreliable; see
[TRACK_RUNNER_HISTORY.md](TRACK_RUNNER_HISTORY.md) and the archived
findings under `archive/` for context.

Person detection (YOLO) is not an active tracking signal in the current
design. It may be referenced as optional seeding assistance, not as
normative active tracking evidence.

After 1-2 laps on a track, cyclical priors become available. The runner
returns to roughly the same image-plane positions every lap period.

Residual-motion blobs are consumed by the windowed Viterbi walker
(`track_runner/blob_walk/`). The walker runs by default on Stage-4-promoted
intervals (low/fair confidence tier, reader present); `blob_pass=True` for that
path. Stage 3 stays pure Hermite on
every interval, and the no-reader test/diagnostic paths stay pure Hermite, so
the walker is gated to the Stage-4 promotion pass only. The analytical FWD/BWD
propagator in [velocity_model.py](../track_runner/velocity_model.py) produces a
pure-Hermite `raw_pred` trajectory and does not apply per-frame blob snap; the
walker walks its own image-derived candidate lattice independent of `raw_pred`.

Pure-stall Hermite fallback: a known bootstrap-stall class of bugs can produce
a degenerate walker pass: either zero accepted frames (path is a straight
interpolation between seeds) or only the seed frame accepted via bootstrap (all
remaining frames frozen at the seed position). Both outcomes are strictly worse
than Hermite. To keep default-on "never worse than Hermite" on promoted
intervals, `solve_interval_analytical` selects output per pass after both
producers run: a pass with `post_seed_accepted == 0` (no accepted frame beyond
the seed) falls back to its Hermite path, while a pass with
`post_seed_accepted >= 1` keeps the walker path. The fallback reads
`WalkCoverage.post_seed_accepted` from the walker's own coverage report, never
`raw_pred` and never FWD/BWD agreement (the C9 scoring signal), so the walk
itself remains Hermite-independent. The underlying bootstrap-stall root cause is
still open; the fallback masks its worst symptom while further Viterbi weight
tuning remains follow-up work.

### Anti-pattern: chained blob state

Blob evidence must not accumulate across frames. No variable named
`last_blob`, `prev_accepted_blob`, `miss_count`, or any `*_chain_*` belongs
in production code. The previous motion-cue fusion carried a
three-frame memory of accepted blobs plus a chain-break counter; the
accumulated state re-introduced exactly the drift the blob pipeline was
meant to fix, and turned refine into an O(total frames) post-process. Code
reviews reject any reintroduction of cross-frame blob state.

Rules:

- The walker's window buffer holds only image-derived raw candidate lists
  from `BlobObserverTrace.corridor_blobs`. It never holds accepted
  positions, filtered-blob lists, or Viterbi decisions from earlier windows.
- The per-interval residual cache holds image-derived raw data only
  (residual maps, validity masks, raw extracted blobs). Never accepted
  blobs, filtered-blob lists, gate outcomes, or selected-path positions.
- The propagator produces a pure-Hermite `raw_pred`. Missing blobs in the
  walker fall back to interpolated/extrapolated status, not a memorized
  earlier blob position.

### Anti-pattern: parallel tool glue that duplicates core loaders

A tool shim must never re-implement what a core owner already provides.
`tools/blob_walk_v2/walk_io.py` was deleted after it had grown into a
parallel shim: it duplicated seed loading from `state_io`, forked the
bin/reader policy with its own `select_bin_factor` call, rewrapped
`camera_motion` and `scene_coords` for the scene transform, and
re-implemented interval enumeration. That divergence caused tool output
to drift silently from production solve output whenever a core owner
changed.

Rules:

- Production solve and any diagnostic or visualization tool MUST share
  one reader-opener: `common_tools/frame_reader.open_analysis_reader`
  (single `select_default_bin_factor` / `TARGET_DEFAULT_WIDTH_PX` policy).
- Seeds load through `state_io.load_seeds` / `state_io.load_seeds_view`.
- Scene transform is built through `camera_motion.load_motion_cache` +
  `scene_coords.SceneTransform`.
- Race-start loads through `state_io.load_diagnostics` -- fail loud, never
  silent zero.
- Interval enumeration routes through
  `track_runner/race_phases.enumerate_seed_to_seed_intervals`.
- Tool-only path and basename glue (artifact path mapping) may live under
  `tools/` but must call the core owners above; it must not re-implement
  them.

Code reviews reject any new module under `tools/` that wraps or re-enters
one of the named core owners.

## Windowed path-selection walker

The blob walker core (modules `walk_walker.py`, `walk_viterbi.py`,
`walk_motion_gate.py`, `walk_status.py`, `walk_debug_log.py`)
now lives under `track_runner/blob_walk/`. The walker is the default blob pass
on Stage-4-promoted intervals (`blob_pass=True`); Stage 3 and non-promoted
dispatches stay pure Hermite (`blob_pass=False`). It selects per-frame blobs
by window-level trajectory
consistency, not by per-frame `integrated_mag` argmax. Full spec:
[windowed_path_selection_amendment.md](archive/windowed_path_selection_amendment.md).

### Motivation

Per-frame single-winner selection failed the M0 frame-1 acceptance bar
on 4 of 6 audit videos; the 24-corpus baseline settled at 19.7%
`accepted_fraction` FWD and 9.6% BWD. See
`m0_closure_summary.md`.
The H4 root-cause audit traced the failure to a velocity-freeze cascade:
on individual frames, leg blobs and foot blobs outscore torso blobs on
`integrated_mag`, so per-frame argmax oscillates between body parts even
when the torso blob is present every frame. Only window-level
trajectory consistency reliably identifies the runner; the walker must
reason over local trajectories, not isolated frames.

### Mechanism

The walker maintains a 9-frame rolling buffer of
`BlobObserverTrace.corridor_blobs` candidate lists (the geometric-ROI
filter has already run during extraction; the walker consumes the full
list, not the per-frame winner). A Viterbi-style dynamic program over
the candidate lattice picks the globally optimal path under additive
costs:

- soft displacement cost (linear in per-frame torso-widths/frame,
  gap-normalized across skips) plus quadratic overspeed above the
  physical envelope; a single hard prune at `ABSOLUTE_MAX_JUMP_W`
  (1.5 torso-widths/frame) rejects physically impossible edges;
- pairwise velocity-delta scoring: `WEIGHT_SPEED_DELTA` on speed changes
  and `WEIGHT_HEADING_DELTA` on heading changes between consecutive real
  steps -- the DP-compatible form of the window trajectory-consistency
  intent (pairwise deltas penalize acceleration; heading is suppressed
  near zero speed via `SPEED_EPSILON_W`);
- per-frame normalized evidence tie-breaker: each candidate's
  `integrated_mag` normalized against the frame's strongest candidate,
  bounded by `WEIGHT_EVIDENCE_NORM` so evidence cannot dominate geometry;
- a skip cost (`SKIP_COST`) charged once per skipped frame; geometry
  bridges across gaps via gap-normalized velocity.

All weights are fixed constants defined in
`track_runner/blob_walk/walk_viterbi.py` (human-approved 2026-06-13).
The prior text claiming weights live in `walker_costs` in the config
YAML and can be tuned without code edits was unapproved doc drift and
has been corrected here. No appearance, color, or template-matching
term enters the cost (C8).

### Status enum

The selected path defines a five-value per-frame status, replacing the
old per-frame accept/reject pair:

- `accepted` -- frame had a real candidate on the selected path;
- `interpolated` -- gap inside the path; position is linear interpolation
  between bracketing accepted frames;
- `extrapolated` -- past the last accepted frame in the window; position
  is the linear extension of the last two accepted frames, valid for at
  most two frames before demoting to `soft_miss_no_path`;
- `soft_miss_no_blob` -- extraction returned an empty candidate list;
- `soft_miss_no_path` -- candidates exist but no edge survived the
  displacement cap.

The hard-stop statuses (`rejected_max_jump`, `rejected_direction_reversal`)
are removed: the walker never stops on a few bad frames, it always
traverses to the neighbor seed and emits a per-frame status from the
post-window decision.

Note: the five values above are the per-frame status enum. Debug-log rows
also carry walk-level stop and diagnostic markers (`hit_neighbor_seed`,
`boundary`, `after_walk_terminated`) in the `stop_reason` field or as
the `status` of diagnostic-only rows emitted after walk termination; these
are not per-frame tracking statuses -- `hit_neighbor_seed` and `boundary`
appear only once per walk as stop reasons, and `after_walk_terminated` marks
post-termination diagnostic rows only.

### Contract compliance

- C6 (interval independence): each window is scoped within a single
  seed-to-seed interval; no buffer entry crosses an interval boundary.
- C9 (FWD/BWD independence): the FWD and BWD passes each own their own
  window buffer and their own DP state; neither reads the other's
  selected path.
- C5 (boundary imprecision): trajectory consistency degrades gracefully
  under imprecise centroid boundaries, where per-frame argmax did not.
- C2 (torso-unit scale): the displacement cap and all spatial cost
  terms are expressed in torso-width units, never raw pixels.
- C10 (unified SCHEMA_VERSION): the walker carries no schema constant of its
  own; the solver `SCHEMA_VERSION` lives only in `tr_schema.py`. The
  `walk_debug_log` verdict CSV is a diagnostic artifact and does not carry or
  bump the solver schema; see
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md).

### What changed in walker state

The per-frame gate is gone. The provisional anchor is gone. The
`rejected_*` hard-stop statuses are gone. The walker no longer carries
`last_blob`, miss-count, or any chain-memory variable; the only
cross-frame state is the bounded 9-frame buffer of raw candidate lists,
which is image-derived data per the
[Anti-pattern: chained blob state](#anti-pattern-chained-blob-state)
rules above.

## Dual scoring philosophy

The first-pass FWD/BWD propagation is intentionally independent. The
disagreement between directions is the honest uncertainty probe. This raw
diagnostic signal drives:

- Interval confidence scoring (agreement and related geometry-based terms)
- Seed recommendation (which intervals need more seeds)
- Severity classification (how urgently an interval needs attention)

Any later refinement step (if and when present) operates after the two
pass paths have already produced a blended interval path; it must not
replace the diagnostic signal. If refinement-derived geometry were used
for scoring, it would mask real identity ambiguity under smooth geometry.
See `FWD_BWD_MODEL_METHODOLOGY.md` and
[TRACK_RUNNER_V3_SPEC.md](TRACK_RUNNER_V3_SPEC.md) for the current state
of any refinement step.

Rule: scoring uses first-pass signal; output uses the blended interval
path (and any refined geometry layered on top of it).

## Interpreting walker-vs-Hermite and held-out-seed error

This subsection is a standing rule for reading any walker-vs-Hermite
measurement. It exists because the held-out-seed instrument is repeatedly
misread as a quality ranking and the wrong conclusion ("walker worse than
Hermite") keeps getting re-derived.

Trust ordering (well documented, not up for re-derivation): the walker is
the trusted, more-accurate solver for its intervals. Hermite is the cheap
incumbent -- acceptable, not great. Hermite runs first on every interval
because it is cheap; the walker is spent ONLY on Stage-4-promoted intervals
because the walker is significantly more CPU expensive. The gating is cost,
not quality. When the walker and Hermite disagree, the prior is that the
walker is closer to the runner, not Hermite.

Consequence for the held-out-seed instrument (`e2e_walker_ab`, which holds
out an interior human seed M and measures the solved box at M against M):
it must NOT be read as a quality ranking of walker against Hermite. Two
structural biases push it toward Hermite:

- A single interior held-out seed under-samples the interval. It scores one
  frame, not the trajectory; Hermite can be mediocre across the whole span
  yet score well at one held-out midpoint.
- On smooth motion the held-out frame lies near Hermite's L-to-R cubic, so a
  small `hermite_err` means the held-out frame was EASY (the runner moved
  where interpolation predicted), not that Hermite tracked well. Scoring the
  trusted tracker by closeness to a yardstick the cheap floor passes
  trivially is the wrong axis.

Correct uses of held-out-seed error:

- ABSOLUTE walker outliers. A walker box two or more torso-widths off the
  runner is the walker failing at its own job regardless of Hermite, and is
  a real eyes-on-tiles lead. Small deltas on easy frames are noise.
- Rescues on hard, non-smooth intervals where Hermite visibly fails (the
  walker's design domain, e.g. drift-stall and long-run intervals). A walker
  win there is real signal because the instrument is informative there.

The proper quality truth is a dense per-frame human trace over a few full
promoted intervals, comparing both full trajectories integrated over the
interval -- not a single held-out point. Until that exists, the strongest
quality evidence for a walker change is its effect on the promoted /
ranking-failure bucket (intervals where Hermite already failed), not
held-out single-seed distance on ordinary intervals.

## Separation of concerns

Four distinct jobs, four distinct systems:

- **Tracker** follows accurately (interval_solver, propagator, hypothesis).
  Its job is to locate the runner at every frame with honest confidence.
- **Crop** moves smoothly (crop.py). It acts as a virtual camera operator:
  exponential smoothing, velocity capping, deadband. Crop quality is about
  cinematic feel, not tracking accuracy.
- **Annotation** captures identity (UI controllers, seeding). Its job is to
  collect ground truth efficiently from the user.
- **Encoder** produces output (encoder.py). It handles decode, resize,
  optional filters, and ffmpeg encoding.

These systems communicate through well-defined interfaces (trajectory arrays,
crop rectangles, seed JSON) rather than sharing internal state.

The solve and walker analyze in PROCESSED (binned, goodbox) space by default.
The default bin factor is `floor(source_width / 1440)`, a project-wide constant
(`TARGET_DEFAULT_WIDTH_PX`) in `common_tools/frame_reader.py`; 4K (3840 px wide)
bins at 2 (processed 1920x1080), 2.8K (2880) bins at 2, 1440p (2560) and 1080p
(1920) stay at full resolution (bin=1). The entire solve runs in one coordinate
space (PROCESSED at bin > 1) and converts to SOURCE exactly once, at the storage
boundary, immediately before `state_io.write_torso_box_coords`. Hermite and walker
both emit correct SOURCE boxes via that single boundary. The encoder consumes SOURCE
and decodes the original full-resolution video.

The pixel coordinate spaces those interfaces use (SOURCE vs PROCESSED, and why
analysis runs in PROCESSED while storage and encode stay SOURCE) are defined in
[COORDINATE_SPACES.md](COORDINATE_SPACES.md).

Within the tracker, the interval-solve execution layer splits along the same
line. The solver driver in `interval_solver.solve_all_intervals` owns all I/O
and control: cache lookup, dispatch, result aggregation, progress rendering,
disk persistence, and keyboard quit/pause polling. Worker processes in
`solver_workers` own the compute: each worker opens its own `VideoReader`,
receives the run-invariant state (`scene_transform`, `motion_track`, seed
lists) once via the pool initializer, and returns pure interval results. No
worker writes to stdout or to disk, and no file handle crosses the process
boundary. The driver-side queueing concern -- seed filtering, fingerprint
walk, cache-hit partition, pool dispatch, and result aggregation -- lives in
its own module, `solve_queue.py`, which both solve mode and refine mode
consume so the two call sites cannot drift on fingerprint semantics;
`solver_workers.py` continues to own only the per-process worker state.

**Race-start confirmation artifact** is a required post-detection PNG contact
sheet owned jointly by `race_start.py` (frame selection helper
`choose_race_start_confirmation_frames`) and `race_start_contact_sheet.py`
(renderer). The artifact is mandatory whenever Stage 2 detects `race_start_frame`
(in both solve and refine) and aborts the command if PNG write fails. This
trade-off is intentional: a visual confirmation artifact that occasionally fails
beats a working command that silently skips the artifact. The target-mode
sub-mode `--race-start` reuses the frame-selection helper for user-driven
refinement of race-start seeds without manual seed insertion.

## Annotation UI principles

### Fast-pick first

Keyboard shortcuts are the primary path. The user should be able to annotate a
frame in under 2 seconds: navigate with arrow keys, draw a box with the mouse,
move to the next frame. Toolbar buttons exist for discovery, not daily use.

### Write-on-commit

Every annotation change is saved immediately. There is no undo stack. The
correction model is re-annotation: if you made a mistake, edit the seed.
This eliminates a class of bugs around session state, unsaved changes, and
crash recovery.

### Workspace, not project manager

The annotation window is a workspace around a frame stream. It shows the
current frame, the current seed status, and overlay previews. It does not
manage files, projects, or render queues. Mode switching (seed, target, edit)
rearranges the workspace without restarting.

## Trajectory erasure philosophy

When the runner is genuinely not visible (hidden or off-screen), the solver
must not pretend to know where they are. Approximate seeds provide a
directional hint (the user drew a general area) but the position is still
uncertain, so trajectory is erased in a short radius around those seeds.

For `not_in_frame` (NIF) spans the encoder no longer relies on a
hold-last fallback. Instead, it fills the gap with edge-anchored crop
geometry derived from the bracketing solved torso boxes: width and height
are stable across the span, the pinned axis sits at the exit edge, and
the non-pinned axis is interpolated. NIF context is provided in memory
alongside the encode trajectory; the on-disk solved trajectory artifact
is unchanged. See [OFF_FRAME_GEOMETRY.md](OFF_FRAME_GEOMETRY.md) for
the full storage, interpretation, and inference contract.

## What this tool is not

- **Not a general object tracker.** It tracks a single pre-identified subject
  in footage where the operator already framed the runner. Multi-object
  tracking is a different problem.
- **Not a search-and-discover tool.** The user tells the tool who to follow.
  The tool does not search for interesting subjects.
- **Not a template matcher.** Runner appearance changes with pose, distance,
  lighting, and occlusion. Pure template matching fails on these changes.
  The tool uses geometry propagation coupled with motion-cue
  observations instead. Appearance is banned per contract C6.

## Visual encoding principles

Overlay visuals use a consistent semantic encoding across the UI and encoder
debug output. The mapping is defined in `overlay_styles.yaml` and loaded by
`overlay_config.py`.

- **Color** conveys semantic state: what the annotation means (seed status,
  prediction direction, tracking source). Each semantic role has one color
  used identically in the UI and encoder.
- **Line style** conveys certainty: solid lines for confirmed/user-authored
  positions, dashed lines for inferred/predicted positions.
- **Opacity** conveys spatial extent: low fill opacity (~6%) lets the video
  show through overlay boxes without obscuring the frame.
- **Thickness** conveys emphasis tier: heavy (2x) for user-authored seed
  boxes, normal (1x) for algorithm predictions. Emphasis is about authorship,
  not state.
