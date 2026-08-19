# Plan: Interaction shell, trajectory truth, and offline dolly path

## Context

Track runner works end to end, but three foundational weaknesses limit how good it
can get. A deep read of the annotation UI, the interval solver, the walker, and the
crop stage found each one to be a design problem, not a bug to patch.

**The annotation shell restarts for every mode.** `ui/workspace.py`
`_on_mode_changed` deactivates the running controller and never activates a new
one, so the Seed / Target / Edit toolbar blanks the workspace with no way back.
Each mode is really a separate CLI launch (`cli._mode_seed`, `_mode_edit`,
`_mode_target`), each building its own `QApplication`, video probe, and
`FrameReader`. [docs/TRACK_RUNNER_DESIGN.md](../../TRACK_RUNNER_DESIGN.md)
already states the intended behavior -- "Mode switching (seed, target, edit)
rearranges the workspace without restarting" -- so the code contradicts its own
design doc. On top of that, every frame step calls `reader.read_frame()`
synchronously on the Qt event loop, and the repo's own
[common_tools/README.md](../../../common_tools/README.md)
records that a random-access read on HEVC source costs 2-4 s against 6-14 ms for
a sequential one. Arrow-key scrubbing is the core interaction and it stalls the
window.

**The solver softens the very seeds it is supposed to anchor.** `interval_solver.blend_paths`
weights FWD and BWD by a `conf` that is a pure exponential of distance from the
anchoring seed (`0.97 ** n`, floored at `0.1`). At the left seed frame that gives
the backward pass ~9 % weight. With pure Hermite this is invisible because both
curves pass exactly through both seeds. On a Stage-4 promoted interval the walker
paths are anchored at one end only, so the opposite pass drags the human seed box
off the runner. `_stamp_seed_confidence` restamps `conf` and `seed_status` at seed
frames but never restamps geometry. Contract clause C3 says the solver "must not
silently override or soften" a seed; today it does. Separately, the disagreement
branch picks the higher-`conf` pass, and since `conf` is only distance-from-seed,
the winner flips exactly at the interval midpoint -- a single-frame teleport of at
least a torso width, at precisely the frames the tracker is least sure about.

**The virtual dolly is a real-time controller in a batch tool.** `tr_crop.py:1221`
calls its own path "the existing online CropController pass": per-frame EMA with a
hard 0.05 <-> 0.15 gain switch, a deadband that produces a limit cycle, a velocity
clip, and `alpha *= confidence`. Crop size already gets a zero-phase
forward-backward pass; crop center does not, so the frame lags the runner by
construction. The module's own comments concede that the residual zoom breathing
is unfixed and "a SEPARATE future task". Every frame of the trajectory is known
before encode starts, so nothing forces a causal filter here.

Alongside these, the batch solve has hit out-of-memory failures. The residual
pre-pass caps its rolling decode buffers at 40 frames but keeps a **full-frame
float32 gray** per buffered frame -- 33 MB per frame at `bin 1`, a documented
~2.32 GB per worker and ~16 GB across 7 workers -- and its result store
(`(frame_index, roi) -> (residual_u8, validity_u8)`) is **unbounded**, growing with
interval length. That combination is the OOM.

The repository is pre-production with a single user, so foundational fixes carry no
migration cost. This plan spends that freedom on the schemas, ownership boundaries,
and algorithms that everything else stands on.

## Objectives

- Make the annotation window one persistent session in which Seed, Target, and Edit
  swap in place, with frame navigation that stays responsive because decode runs on
  its own thread.
- Restore contract C3 by making a human seed's geometry survive blending unchanged,
  on Hermite and walker intervals alike.
- Give confidence and FWD/BWD agreement one definition and one owning module, used
  identically by solve, analyze, encode, and crop.
- Give the blended trajectory a continuous position across every interval, removing
  the midpoint teleport, while showing that the pass it commits to is the one
  actually on the runner.
- Give the Hermite endpoint tangents local validity and exact continuity across
  shared seeds, so the stitched trajectory has no first-derivative jump at a seed.
- Replace the causal crop controller with one offline whole-path solve that has zero
  lag and bounded acceleration, and produce the evidence the adoption decision needs.
- Bring solve memory under an explicit, measured, per-worker budget so batch runs
  fit a known memory budget on large videos while keeping their current runtime.

## Design philosophy

This plan applies **fix the design, not the symptom** and **long-term over
short-term** from [docs/REPO_STYLE.md](../../REPO_STYLE.md).
Each item was selected because the current code already contains a workaround that
names the underlying defect: the "Loading heatmap... Please wait" dialog that
disables the frame view exists because the compute runs on the GUI thread; the
`_stamp_seed_confidence` pass exists because blending corrupts seed frames; the
size-spike stabilizer exists because an EMA cannot reject outliers. The plan
removes the causes and deletes the workarounds rather than adding more.

The central trade-off accepted here: **several of these changes alter output on
videos that are already solved and encoded.** The confidence redefinition, the blend
commitment, the tangent model, and the dolly solver each produce different pixels or
different promotion decisions. The plan pays for that with **one output-changing
change per milestone**, each with its own recorded measurement, so a regression is
always attributable to a single cause. This is why the milestone count is higher
than the workstream structure alone would need: attribution boundaries, not task
size, set the milestone granularity.

Rejected alternative: patching each defect in place (clamp the blend jump, add a
second EMA stage to the crop, special-case the seed frame). That was rejected
because each patch adds a branch to code whose problem is that it already has too
many branches, and because the repo's audit trail
([size_spike_hardening_evidence.md](../decisions/size_spike_hardening_evidence.md))
shows the last round of in-place crop patching measured a null effect.

- Evidence strategy for uncertain methods: every output-changing **patch** ships a
  measurement before the next one begins, and the measurement must test the
  **claimed behavior**, not a proxy for it. Continuity evidence alone never
  establishes correctness: a smooth trajectory can be smoothly wrong.
- Evidence strength is stated honestly per instrument. In particular,
  `common_tools/in_box_heat.measure_in_box_heat` measures residual motion energy
  inside a box. That is **not** the same as "the box is on the runner's torso", and
  this plan does not treat it as ground truth. `TRACK_RUNNER_DESIGN.md:212` records
  the H4 audit finding that "leg blobs and foot blobs outscore torso blobs on
  `integrated_mag`", so the metric ranks the most-moving body part, not the torso;
  and in a race there are competing runners whose motion is equally hot. Heat is
  therefore used as a **veto**: a change that loses heat is suspect and fails, while
  a change that keeps heat is not thereby proven correct. Decisive confirmation is
  the rendered overlay artifact, and the design doc's stated ground truth remains a
  dense per-frame human trace.
- Where the design doc marks an instrument as invalid for a comparison -- the
  standing rule about held-out-seed error and walker-versus-Hermite ranking -- the
  plan names an instrument that is valid for that specific comparison.

## Scope

- Restamp human seed geometry after blending so seed frames are exactly the seed box.
- Create one owner for per-frame confidence and one owner for FWD/BWD agreement,
  and route solve, analyze, encode, crop, and scoring through them.
- Replace the per-frame FWD/BWD winner flip with a run-level commitment that keeps
  the blended position continuous and is chosen by motion evidence, with a rendered
  artifact carrying the correctness claim that motion evidence alone cannot.
- Bound residual pre-pass memory, and gate the change on cache miss rate and runtime
  so the memory fix keeps runtime steady.
- Reduce walker cost by memoizing the position-invariant parts of the Viterbi cost,
  and investigate exact DP-state reuse before assuming it.
- Replace index-selected Hermite tangent regression with a frame-distance-windowed
  estimator, and share one tangent per seed across the two intervals that touch it.
- Decide, with measurement, whether the far-endpoint chord tangent survives once
  tangents are shared, since it currently makes part of FWD/BWD disagreement a
  modelling artifact that drives promotion.
- Build a persistent annotation session that owns the reader, seeds, and
  predictions, and swap Seed / Target / Edit controllers inside it.
- Move video decode and heat-map compute off the Qt event loop, with a defined
  thread-ownership model and a bounded prefetch cache.
- Introduce one keybinding registry that generates the hint bar, the F1 help dialog,
  and `docs/TRACK_RUNNER_KEYBINDINGS.md`.
- Adopt `common_tools.coord_space.SourceBox` / `ProcessedBox` for prediction and
  trajectory payloads in place of nested untyped dicts.
- Build an offline crop-path solver (position and log-size) and produce the A/B
  evidence the adoption decision needs.
- Extract the per-mode bodies of `cli.py` into a `track_runner/modes/` package.
- Replace the silent geometry defaults in the walker (`b.get("centroid_x", 0.0)` and
  `.get("centroid_x")`) with direct key access, in the same milestone that touches
  the walker, because they convert malformed geometry into plausible coordinates.
- Delete the dead code this work exposes: `scoring.classify_confidence`, the
  never-written `occlusion_risk` field, and the unused propagator parameters.

## Non-goals

Each item names something that stays as it is, so the scope boundary reads as a
description of the target state:

- Keep the current `SCHEMA_VERSION` and every stored artifact schema. All work here
  is compute-only or in-memory; a package that finds it needs a bump raises that with
  the user first, per C10.
- Keep Stage-4 promotion policy exactly as it is. The user reports prior
  out-of-memory failures, so this plan makes the walker cheaper and memory-safe at
  its current coverage.
- Keep the Viterbi cost weights in `blob_walk/walk_viterbi.py` at their current
  human-approved values (2026-06-13).
- Keep the Viterbi cost recurrence as written. WP-M3 makes the same recurrence
  cheaper to evaluate and preserves selected paths exactly.
- Keep the active evidence set to geometry and residual motion, per contract C8.
- Keep the tool offline and batch.
- Keep the encoder, the camera-motion stage, and the fast-read prepare path as they
  are.
- Leave the open bootstrap-stall root cause behind the walker's Hermite fallback for
  separate work.
- Leave the SOURCE / PROCESSED coordinate contract to the in-flight
  [typed_coordinate_space_plan.md](typed_coordinate_space_plan.md); this plan
  consumes its types as published.

## Current state summary

| Area | Owner today | Defect found |
| --- | --- | --- |
| Mode lifecycle | `cli.py` `_mode_*` + `ui/workspace.py` | Toolbar deactivates only; one mode per process launch |
| UI frame access | `ui/seed_controller._refresh_frame` | Synchronous random seek on the Qt event loop |
| Heat overlay | `ui/heat_map_overlay.py` + `ui/workspace._set_heat_busy` | GUI-thread compute; masked by a busy dialog that disables the view |
| Frame display | `ui/frame_view.set_frame` | Full-frame BGR->RGB copy, pixmap item churn, `sceneRect` reset destroys pan each frame |
| Keybindings | controller `if/elif` + `_get_keybinding_hints` strings + `docs/TRACK_RUNNER_KEYBINDINGS.md` | Three sources of truth; the help dialog parses the display string back into rows |
| Mode identity | `ui/target_controller.py` | Does not override `_get_mode_name`, so Target mode labels itself SEED |
| Seed anchoring | `interval_solver.blend_paths` + `_stamp_seed_confidence` | Confidence restamped at seeds, geometry never restamped |
| Blend continuity | `interval_solver.blend_paths` | Disagreement branch flips winner at interval midpoint |
| Confidence | `blend_paths` and `derive_per_frame_confidence` | Two incompatible definitions for the same quantity |
| Agreement | `blend_paths` (Dice) and `derive_per_frame_confidence` (center distance / torso) | Two metrics for the same question; Dice is size-coupled |
| Tier classification | `scoring.classify_confidence` and `scoring.score_interval_analytical` | Duplicated logic; `classify_confidence` is dead |
| Hermite tangents | `velocity_model.estimate_directional_slope` | Neighbors chosen by index with no frame-distance window; far endpoint uses a chord slope |
| Seed continuity | `velocity_model.fit_interval_curves` | Adjacent intervals estimate different tangents at the same shared seed |
| Propagators | `velocity_model._compute_raw_pred_forward` / `_backward` | ~90 % duplicated; decay constants defined twice; three dead parameters |
| Crop stage | `tr_crop.CropController` | Causal EMA, hard gain switch, deadband limit cycle, center lag |
| Pre-pass memory | `residual_pre_pass.precompute_interval_residuals` | Full-frame float32 gray per buffered frame; unbounded result store |
| Walker DP | `blob_walk/walk_viterbi.select_path` | Rebuilt from scratch per one-frame window slide, emits one frame |
| Geometry defaults | `blob_walk/walk_walker.resolve_audit_winner`, `_build_window_entry` | `b.get("centroid_x", 0.0)` maps a missing centroid to the frame corner |
| CLI | `cli.py` (3096 lines) | Argument handling, artifact lifecycle, prediction assembly, and per-mode logic in one module |

Independent corroboration of where the coupling actually is: `graphify god-nodes`
ranks `BaseAnnotationController` (45 edges), `SeedController` (41), `EditController`
(41), `main()` (41), and `AnnotationWindow` (28) as the five most connected symbols
in the repository. Those are precisely the surfaces M6 and M8 restructure, which is
independent support for treating the annotation shell and `cli.py` as the ownership
problems rather than incidental large files. The same listing puts `ProcessedBox`
(32), `ProcessedPoint` (24), and `FrameGeometry` (22) high, confirming the
`coord_space` types are already widely wired and that WP-T5 should adopt them rather
than introduce anything new.

Existing assets this plan reuses rather than reinvents:

- `common_tools/coord_space.py` -- `SourcePoint`, `SourceBox`, `ProcessedPoint`,
  `ProcessedBox` frozen dataclasses with conversion methods, owned by the in-flight
  typed-coordinate-space plan.
- `common_tools/in_box_heat.measure_in_box_heat` -- returns `(hot_mean, hot_count)`
  for residual energy inside a box, with the threshold supplied by the caller.
  `walk_walker.measure_in_box_heat_for_frame` already computes it per emitted frame
  while the residual is still live, so a gate using it adds no extra decode. Used as
  a veto only, for the reason stated in Design philosophy.
- `tools/blob_walk_v2/render/walk_render.py` and
  `tools/blob_walk_v2/make_walk_html_v2.py` -- existing overlay renderers. They
  produce the reviewable artifact that carries the correctness claim heat cannot.
- `common_tools/frame_reader.open_analysis_reader` -- the single reader-opener that
  production and tools must share. The UI decode worker calls it, not a new path.
- `torso_size_stabilizer.py` -- keeps its outlier-rejection role as the robust
  pre-stage feeding the new dolly solver.
- `scipy` -- already a declared dependency ("interpolation for interval solving");
  supplies the banded solve for the dolly path.
- `race_phases.enumerate_seed_to_seed_intervals`, `state_io.load_seeds_view`,
  `scene_coords.SceneTransform` -- the named core owners the anti-parallel-glue rule
  requires.

Two facts established by reading the code, which shape WP-M3:

- `walk_walker.py:355` calls `select_path(window_candidates, seed_w, fps)`. The
  scale argument is `seed_w`, constant for the entire walk, not per frame.
- `walk_viterbi._edge_cost` reads its frame indices only as `gap = frame_b -
  frame_a`, and `_evidence_costs_for_frame` is purely per frame. Both are therefore
  invariant to where the window sits. Window position enters the DP only through the
  leading- and trailing-skip terms, which are computed separately.

## Architecture boundaries and ownership

New durable components introduced by this plan:

- `track_runner/trajectory_confidence.py` -- **the confidence owner**. One function
  computing per-frame confidence from FWD/BWD geometry, and one computing interval
  agreement. Every consumer imports from here.
- `track_runner/seed_tangents.py` -- **SeedTangentField**. Pure function of the seed
  list producing one tangent per seed, computed once before dispatch and passed into
  workers as run-invariant state.
- `track_runner/ui/session.py` -- **AnnotationSession**. Owns the video context,
  reader handle, seed store, prediction store, and the active controller. The only
  object allowed to construct or destroy a controller, and the owner of the decode
  thread's lifetime. Modes become states of this object, not process launches.
- `track_runner/ui/frame_source.py` -- **FrameSource**. Off-thread decode with a
  bounded LRU frame cache and directional prefetch. The only UI-side caller of
  `FrameReader`.
- `track_runner/ui/keymap.py` -- **KeyMap**. Declarative binding table (key,
  modifier, action id, human label, applicable modes). Single source for dispatch,
  hint bar, help dialog, and the generated keybindings doc.
- `track_runner/dolly_path.py` -- **the offline camera-path solver**. Consumes a
  trajectory plus per-frame weights, returns the crop path.
- `track_runner/modes/` -- one module per CLI mode, extracted from `cli.py`.

Boundaries this plan holds. Each is phrased as the behavior to implement, so it can
be pasted directly into a work assignment:

- Contract C6 (interval independence): `SeedTangentField` is computed from seeds
  only, before any interval is dispatched, and handed to workers as immutable input.
  No worker reads another interval's solve state.
- Contract C9 (FWD/BWD independence): the blend-commitment work changes output
  selection only. `trajectory_confidence` computes agreement from the two
  independent pass paths, which are its sole inputs.
- Contract C2 (torso units): every **spatial** threshold introduced here -- blend
  step bound, dolly acceleration limit -- is expressed in torso widths or
  torso-widths-per-frame. **Temporal** quantities (transition band length, tangent
  window) are expressed in frames or seconds and are labelled as such. Each constant
  carries exactly one unit family, named in its comment.
- Contract C13 (cache is temporary): the pre-pass result store and the UI frame cache
  each live in memory for one run and are released when it ends.
- **Thread ownership** (WP-U3): one decode thread creates the `FrameReader`, performs
  every read, and destroys it, making it the sole holder of that object. Requests and
  results cross by queued signal. `AnnotationSession` owns the thread's lifetime and
  joins it during teardown. The Qt mechanism used to realize this is the
  implementer's choice; the ownership rule is fixed here.
- **External plan boundary**: `common_tools/coord_space.py` is owned by the
  typed-coordinate-space plan. WP-T5 consumes those types as published and leaves the
  SOURCE / PROCESSED contract to that plan.
- The UI builds domain objects through a public factory: `seed_color._build_seed_dict`
  is promoted to public API and the controller imports it at module scope.

### Mapping (milestones / workstreams -> components / patches)

| Milestone / Workstream | Component | Review boundary |
| --- | --- | --- |
| M1 / WS-SEED | `interval_solver` seed stamping | Solver output; C3 compliance |
| M2 / WS-CONF | `track_runner/trajectory_confidence.py` (new), `scoring.py` | Cross-module contract; C9 |
| M3 / WS-BLEND | `interval_solver.blend_paths` run-commit logic | Solver output; continuity bound, heat veto, overlay artifact |
| M4 / WS-MEM | `residual_pre_pass.py`, `blob_walk/walk_viterbi.py`, `solve_queue.py` | Memory budget; walker output parity |
| M5 / WS-TAN | `velocity_model.py`, `track_runner/seed_tangents.py` (new) | Hermite geometry; C6 boundary |
| M6 / WS-UI | `ui/session.py`, `ui/frame_source.py`, `ui/keymap.py` (all new), controllers | Session lifecycle; threading boundary; input contract |
| M7 / WS-DOLLY | `track_runner/dolly_path.py` (new), `tr_crop.py` | Numerical method; output framing |
| M8 / WS-OWN | `track_runner/modes/` (new), `cli.py` | CLI surface unchanged |

## Milestone plan

Milestone count is set by attribution boundaries, not task size. Each milestone that
changes solver or encoder output changes **exactly one** thing about it.

| M | Title | Summary | Goal |
| --- | --- | --- | --- |
| M1 | Seed anchoring | Restamp seed geometry after blending | Human seeds stop being softened (C3) |
| M2 | Confidence ownership | One confidence definition, one agreement definition | Promotion shift measured in isolation |
| M3 | Blend commitment | Run-level pass commitment replacing the midpoint flip | Teleport removed, committed pass shown to be on the runner |
| M4 | Memory and cost safety | Bounded pre-pass memory, cheaper walker, measured worker sizing | Batch solves fit a known memory budget and keep their runtime |
| M5 | Tangent model | Windowed tangents, one shared tangent per seed, chord decision | No first-derivative jump at seed frames |
| M6 | Interaction shell | Persistent session, off-thread decode, keybinding registry, clean render path | Annotation stops restarting and stops stalling |
| M7 | Offline dolly path | Whole-path crop solve plus its A/B evidence | Output framing has zero lag and bounded acceleration |
| M8 | Ownership cleanup | Extract `cli.py` mode bodies; delete exposed dead code | Modes have owning modules |

### Milestone: M1 seed anchoring

- Depends on: none. First because it is a pure geometry fix with no coupling to the
  confidence definition, so it can land and be verified before anything semantic
  moves.
- Deliverables: seed geometry restamped after blending and after any walker Hermite
  fallback selection.
- Workstreams: WS-SEED.
- Entry criteria: none.
- Exit criteria: every frame carrying a `visible` or `partial` seed has a solved box
  exactly equal to that seed's box, across the corpus; `pytest tests/` green.
- Parallel-plan ready: yes.

### Milestone: M2 confidence ownership

- Depends on: M1, so that the seed frames are already correct when confidence is
  recomputed and the promotion shift is not confounded by seed softening.
- Deliverables: `track_runner/trajectory_confidence.py` as the single confidence and
  agreement owner; every consumer rewired; dead `scoring.classify_confidence` and the
  duplicated tier arithmetic removed.
- Workstreams: WS-CONF.
- Entry criteria: M1 exit criteria met.
- Exit criteria: exactly one module defines per-frame confidence and exactly one
  defines agreement, verified by grep; interval promotion counts recorded before and
  after, and **every interval whose tier changed is reproducible from the new
  agreement value** -- recompute both the old and new metric on each changed
  interval and confirm the new tier follows from the new metric. Zero unexplained
  tier changes is the completion condition; there is no separate approval step.
  `pytest tests/` green.
- Parallel-plan ready: yes.

### Milestone: M3 blend commitment

- Depends on: M2. The run-winner rule consumes the settled agreement definition, so
  changing both at once would make a promotion or trajectory regression unattributable.
- Deliverables: run-level pass commitment replacing the per-frame winner flip, with
  the winner chosen by motion evidence, plus a rendered overlay of every committed
  run.
- Workstreams: WS-BLEND.
- Entry criteria: M2 exit criteria met.
- Exit criteria: maximum single-frame center step across the corpus at or below
  `walk_motion_gate.ABSOLUTE_MAX_JUMP_W` and strictly below the pre-change value;
  in-box heat over committed runs not worse than the pre-change blended path; the
  overlay artifact for every committed run filed; `pytest tests/` green.
- Parallel-plan ready: no. WS-BLEND is one decision surface with one owner.

### Milestone: M4 memory and cost safety

- Depends on: none. Independent file set; runs concurrently with M1 through M3.
- Deliverables: uint8 gray buffering; byte-bounded result store with a measured miss
  rate; memoized position-invariant Viterbi costs; a recorded finding on whether
  exact DP-state reuse is available; worker sizing from a measured footprint;
  walker geometry defaults replaced with direct key access.
- Workstreams: WS-MEM.
- Entry criteria: none.
- Exit criteria: measured peak RSS per worker at `bin 1` and `bin 2` below the
  declared budget; walker selected paths byte-identical to pre-change on the baseline
  corpus; Stage-4 wall time reduced and recorded; pre-pass cache miss rate recorded
  and total solve wall time not regressed.
- Parallel-plan ready: yes.

### Milestone: M5 tangent model

- Depends on: M2. The tangent change alters FWD/BWD disagreement, which feeds
  promotion, so the agreement definition must be settled and its own promotion shift
  already measured before a second input to it moves.
- Deliverables: frame-distance-windowed tangent estimator; `seed_tangents.py`
  producing one tangent per seed shared by both adjacent intervals; a recorded
  decision on the far-endpoint chord tangent; propagators collapsed with dead
  parameters removed.
- Workstreams: WS-TAN.
- Entry criteria: M2 exit criteria met.
- Exit criteria: the first-derivative mismatch measured across each shared seed is
  zero to numerical tolerance; the chord-tangent decision is recorded with its
  measurement; promotion counts recorded after **each** of WP-N1, WP-N2, and WP-N3
  separately; `pytest tests/` green.
- Parallel-plan ready: no. WP-N1, WP-N2, and WP-N3 each independently alter
  trajectories and promotion, so this milestone is internally serial: each lands as
  its own patch with its own recorded measurement before the next begins. WP-N3 in
  particular runs only after WP-N2's shared-tangent result has been measured, so the
  chord experiment is evaluated against a known baseline rather than against a
  simultaneously-moving one.

### Milestone: M6 interaction shell

- Depends on: none. Shares no files with M1 through M5. WS-UI-E alone has a
  cross-plan dependency and is explicitly droppable: if the external
  typed-coordinate-space plan has not landed stable types, WS-UI-E defers to a later
  patch and M6 completes on its own schedule with its other four workstreams.
- Deliverables: `AnnotationSession` with in-place mode swapping; `FrameSource` with
  off-thread decode under the stated ownership model; heat compute moved off the GUI
  thread and the busy-dialog workaround deleted; `KeyMap` generating dispatch, hints,
  help, and the keybindings doc; corrected render path; typed prediction payloads;
  Target mode correctly identified; user feedback routed to the GUI.
- Workstreams: WS-UI-A, WS-UI-B, WS-UI-C, WS-UI-D, WS-UI-E.
- Entry criteria: none. WS-UI-E is attempted only if the external plan's types are
  landed and stable, and is deferred without blocking the milestone otherwise.
- Exit criteria: seed -> target -> edit -> seed round trip inside one process with no
  restart; no synchronous `read_frame` and no residual compute reachable from the Qt
  event loop; the keybindings doc is generated and a drift check enforces it; a pan
  survives a frame advance; `pytest tests/` green.
- Parallel-plan ready: yes.

### Milestone: M7 offline dolly path

- Depends on: M2 (per-frame confidence is the solver's weight input) and M5 (tangent
  continuity removes seed kinks the dolly would otherwise absorb and be blamed for).
- Deliverables: `dolly_path.py` solving position and log-size over the whole
  trajectory; integration preserving the containment clamp; a complete recorded A/B
  against `direct_center` and `smooth`, with sample frames.
- Workstreams: WS-DOLLY-A, WS-DOLLY-B.
- Entry criteria: M2 and M5 exit criteria met.
- Exit criteria: measured center lag at or near zero; 95th-percentile crop
  acceleration below both current modes; every existing containment test passing;
  A/B artifacts written under `docs/active_plans/decisions/`. **The milestone is
  complete when the evidence exists, not when a default is flipped.** The default
  flip is a separate decision the user makes from the delivered evidence.
- Parallel-plan ready: no. WS-DOLLY-B integrates and measures the output of
  WS-DOLLY-A, so the two are inherently serial.

### Milestone: M8 ownership cleanup

- Depends on: M6. Mode extraction and the session shell both restructure how a mode
  starts, so extracting first would be redone.
- Deliverables: `track_runner/modes/` with one module per mode; `cli.py` reduced to
  argument wiring and dispatch; dead code exposed by M1 through M7 removed.
- Workstreams: WS-OWN.
- Entry criteria: M6 exit criteria met.
- Exit criteria: `cli.py` holds no mode body; `tools/dump_cli_help.py` output diffs
  empty; `pytest tests/` green.
- Parallel-plan ready: no. Single lane by design.

## Workstream breakdown

### Workstream: WS-SEED seed geometry anchoring

- Goal: a human seed's solved box equals the seed box exactly, on every interval type.
- Owner: `coder`
- Work packages: WP-T1
- Needs: nothing.
- Provides: the C3 guarantee WS-BLEND and WS-DOLLY both assume.
- Review boundary, when modifying the repository: `reviewer` confirms the restamp
  runs after blending and after any walker fallback selection, and that it restamps
  geometry for `visible` and `partial` seeds only, leaving `approximate` and
  `not_in_frame` to the existing erasure path.

### Workstream: WS-CONF confidence and agreement ownership

- Goal: one definition of per-frame confidence and one of interval agreement.
- Owner: `expert_coder`
- Work packages: WP-T3, WP-T4
- Needs: WP-T1.
- Provides: the settled semantics WS-BLEND, WS-TAN, and WS-DOLLY all consume.
- Review boundary, when modifying the repository: `architect` approves the chosen
  single definition before consumers are rewired, since promotion, seed
  recommendation, and crop gain all move with it.

### Workstream: WS-BLEND blend commitment

- Goal: remove the single-frame teleport where FWD and BWD disagree, and commit to
  the pass that is actually on the runner.
- Owner: `expert_coder`
- Work packages: WP-T2
- Needs: WP-T3.
- Provides: a continuous blended position for M7.
- Review boundary, when modifying the repository: `reviewer` confirms no scoring
  input reads blended output (C9), and that the disagreement flag still reaches
  `review.py` and `encode_analysis`.

### Workstream: WS-MEM memory and walker cost

- Goal: bound solve memory per worker and cut walker cost while preserving output
  exactly.
- Owner: `expert_coder`
- Work packages: WP-M1, WP-M2, WP-M3, WP-M4, WP-M5
- Needs: nothing.
- Provides: the headroom every later solve-side change depends on.
- Review boundary, when modifying the repository: `reviewer` verifies the
  byte-identical residual contract and walker path equality; `architect` reviews the
  WP-M3 reuse finding before any DP-state change is implemented.

### Workstream: WS-TAN tangent model

- Goal: locally valid tangents that are continuous across shared seeds.
- Owner: `expert_coder`
- Work packages: WP-N1, WP-N2, WP-N3, WP-N4
- Needs: M2 complete.
- Provides: a kink-free trajectory for M7.
- Review boundary, when modifying the repository: `architect` confirms C6 compliance
  -- the tangent field is a pure function of seeds computed before dispatch, and no
  worker reads another interval's state.

### Workstream: WS-UI-A annotation session

- Goal: one process, one window, modes swap in place.
- Owner: `expert_coder`
- Work packages: WP-U1, WP-U2
- Needs: nothing.
- Provides: the container WS-UI-B, WS-UI-C, and M8 all attach to.
- Review boundary, when modifying the repository: `reviewer` confirms controller
  teardown releases every scene item and event filter, and that unsaved-seed
  semantics are preserved by the existing write-on-commit model.

### Workstream: WS-UI-B off-thread frame and heat access

- Goal: nothing expensive runs on the Qt event loop.
- Owner: `expert_coder`
- Work packages: WP-U3, WP-U4
- Needs: WP-U1.
- Provides: responsive scrubbing; deletion of the busy-dialog workaround.
- Review boundary, when modifying the repository: `reviewer` confirms the stated
  thread-ownership rule holds exactly, the cache is bounded, and stale results are
  discarded.

### Workstream: WS-UI-C keybinding registry

- Goal: one declarative binding table drives dispatch, hints, help, and the doc.
- Owner: `coder`
- Work packages: WP-U5
- Needs: WP-U1.
- Provides: discoverability and an end to hint drift.
- Review boundary, when modifying the repository: `reviewer` confirms the generated
  doc matches the table and a drift check fails when they diverge.

### Workstream: WS-UI-D render path and feedback routing

- Goal: correct, cheap per-frame display; user feedback in the GUI.
- Owner: `coder`
- Work packages: WP-U6, WP-U7
- Needs: WP-U1.
- Provides: a display path that does not fight the user's pan and zoom.
- Review boundary, when modifying the repository: `reviewer` confirms pan survives a
  frame advance and that no user-facing message is left on stdout only.

### Workstream: WS-UI-E typed prediction payloads

- Goal: prediction and trajectory payloads carry real types.
- Owner: `coder`
- Work packages: WP-T5
- Needs: the external typed-coordinate-space plan to have landed stable
  `coord_space` types. This is a cross-plan dependency, not a file conflict.
- Provides: the typed payload WS-UI-D renders from.
- Review boundary, when modifying the repository: `reviewer` confirms no coordinate
  conversion is introduced and the single SOURCE storage boundary is unchanged.

### Workstream: WS-DOLLY-A offline path solver

- Goal: solve the whole camera path in one pass.
- Owner: `expert_coder`
- Work packages: WP-D1
- Needs: M2 and M5 complete.
- Provides: the path WS-DOLLY-B integrates.
- Review boundary, when modifying the repository: `architect` approves the objective
  and the smoothness parameter's torso-unit formulation before integration.

### Workstream: WS-DOLLY-B integration and evidence

- Goal: integrate under the containment clamp and produce the adoption evidence.
- Owner: `expert_coder`
- Work packages: WP-D2, WP-D3
- Needs: WP-D1.
- Provides: the artifacts the user decides from.
- Review boundary, when modifying the repository: `reviewer` confirms containment
  invariants hold and the A/B artifacts are complete.

### Workstream: WS-OWN mode extraction

- Goal: every mode has an owning module.
- Owner: `coder`
- Work packages: WP-O1, WP-O2
- Needs: M6 complete.
- Provides: a `cli.py` that is argument wiring and dispatch only.
- Review boundary, when modifying the repository: `reviewer` diffs
  `tools/dump_cli_help.py` output before and after and requires it empty.

## Work packages

### Work package: WP-T1 restamp seed geometry after blending

- Owner: `coder`
- Touch points: `track_runner/interval_solver.py`
- Depends on: none.
- Acceptance criteria: after blending and after any walker Hermite fallback
  selection, every frame carrying a `visible` or `partial` seed has `cx`, `cy`, `w`,
  `h` exactly equal to that seed's box. `_stamp_seed_confidence` is widened to stamp
  geometry as well as `conf` and `seed_status`, and renamed to reflect that it stamps
  seed truth. `approximate` and `not_in_frame` seeds keep their confidence-only
  treatment so the erasure path is unchanged.
- Evidence or review: a unit test asserting seed-frame geometry equality on a
  synthetic interval where the two pass paths deliberately disagree at the endpoint;
  a corpus sweep reporting zero mismatches.
- Obvious follow-ons: replace `seed.get("status", "")` with direct key access, since
  the seed schema makes `status` mandatory.

### Work package: WP-T3 create the confidence and agreement owner

- Owner: `expert_coder`
- Touch points: `track_runner/trajectory_confidence.py` (new),
  `track_runner/interval_solver.py`, `track_runner/scoring.py`
- Depends on: WP-T1.
- Acceptance criteria: one module defines per-frame confidence from FWD/BWD geometry
  and one defines interval agreement, both in torso units per C2. The distance-decay
  `conf` produced inside the propagators is removed from the blended output;
  `derive_per_frame_confidence` moves into the new owner and becomes the only
  definition. `blend_paths` consumes agreement from the owner instead of calling
  `scoring._compute_dice_coefficient` directly, ending the Dice-versus-center-distance
  split. Nothing in the new module reads blended output (C9). **The blend selection
  logic is otherwise unchanged in this package** -- the midpoint flip stays until
  WP-T2, so this milestone's promotion shift is attributable to the definition change
  alone.
- Evidence or review: `architect` approves the single definition before consumers are
  rewired. Promotion counts across the corpus recorded before and after, plus a
  per-interval table of tier changes showing the old and new metric values that
  produced each one. The package is complete when that table has no row whose tier
  change does not follow from its new metric value.
- Obvious follow-ons: delete the dead `scoring.classify_confidence` and replace the
  `["low", "fair", "good", "high"].index()` tier arithmetic in
  `score_interval_analytical` with an ordered type, removing the duplicated tier
  logic.

### Work package: WP-T4 route every confidence consumer through the owner

- Owner: `coder`
- Touch points: `track_runner/cli.py` (the two live call sites,
  `derive_per_frame_confidence` at L2216 in `_mode_analyze` and L2445 in
  `_mode_encode`), `track_runner/encode_analysis.py`, `track_runner/tr_crop.py`,
  `track_runner/regime_classifier.py`, `track_runner/analyze_report.py`,
  `track_runner/review.py`
- Depends on: WP-T3.
- Acceptance criteria: no consumer computes confidence itself or reads a
  distance-decay value. A repo-wide grep shows exactly one definition site.
- Evidence or review: `reviewer` establishes the consumer list with
  `grep -rn "derive_per_frame_confidence" --include=*.py .`, which resolves this
  repo's `module.function()` call idiom. See
  [Using the graphify code map](#using-the-graphify-code-map).
- Obvious follow-ons: none.

### Work package: WP-T2 commit to one pass per disagreement run

- Owner: `expert_coder`
- Touch points: `track_runner/interval_solver.py`, and
  `tools/blob_walk_v2/walk_driver.py:956`, which calls
  `interval_solver.blend_paths` directly from the tools tree. That second consumer
  must keep working or be updated in the same patch; a semantics change to
  `blend_paths` that only considers the solver silently changes diagnostic-tool
  output too.
- Depends on: WP-T3.
- Acceptance criteria:
  - Where FWD and BWD disagree, the blended path selects one pass for the whole
    contiguous disagreement run rather than per frame, so the output follows one
    curve for the whole run.
  - **Winner rule**: the run winner is the pass whose boxes carry more residual
    motion energy over the run, measured with
    `common_tools/in_box_heat.measure_in_box_heat` at the threshold
    `residual_motion.DEFAULT_THRESHOLD`, which is the same single-source threshold
    the per-interval heat summary records. This is a motion-presence criterion, not
    a continuity criterion, so the rule wins on subject evidence while the step bound
    below carries continuity separately. It is
    **not** an identity criterion: per `TRACK_RUNNER_DESIGN.md:212` residual
    magnitude ranks the most-moving body part over the torso, and a competing runner
    is equally hot. The rule is therefore paired with the artifact below, and where
    the two disagree the artifact decides.
  - **Ground-truth gate (primary)**: the winner rule is validated on synthetic
    lattices where the correct pass is known by construction, extending the existing
    harness in `tests/test_walk_cost_model.py`. That file already builds candidate
    lattices with a known runner and asserts the DP picks it over a stationary
    distractor and over oscillating leg blobs (`center_picks >= 7` of 9). The same
    pattern applies directly here: construct FWD/BWD path pairs where one pass is on
    the known runner and the other is on a known distractor, and assert the rule
    selects the runner. This is deterministic, fast, corpus-free, and needs no human,
    which makes it the primary correctness evidence rather than the heat veto.
  - **Reviewable artifact**: for every committed disagreement run in the corpus, the
    package renders the overlay through the existing
    `tools/blob_walk_v2/render/walk_render.py` path, showing the pre-change blended
    path and the committed path together. This is the record on real footage, where
    synthetic ground truth is unavailable.
  - **Units are explicit and separate.** The transition into and out of a committed
    run is **temporal**: a band of `N` frames, `N` a named module constant with a
    stated rationale. The invariant it must satisfy is **spatial**: the per-frame
    center step inside the band stays below a bound in torso widths per frame. The
    band interpolates monotonically between the blended average and the committed
    pass. The coder does not choose the interpolation model: it is linear in the
    band parameter unless the step bound is violated, in which case the band is
    lengthened until it is satisfied.
  - `blend_flag` is still set on every frame of the run, and those frames remain
    visible to `review.py` and `encode_analysis` exactly as today.
- Evidence or review: maximum single-frame center step per trajectory in torso
  widths, recorded before and after, must land at or below
  `walk_motion_gate.ABSOLUTE_MAX_JUMP_W`. Mean in-box heat over committed runs,
  recorded before and after, holds at or above the pre-change value -- a drop fails
  the package, while holding steady is a pass on this gate alone. The overlay
  artifact for every committed run is produced and filed under
  `docs/active_plans/reports/`.
- Obvious follow-ons: delete the never-written `occlusion_risk` field from the blend
  state and from `review.py`, which currently reports on a value no producer sets.

### Work package: WP-T5 adopt typed boxes in trajectory payloads

- Owner: `coder`
- Touch points: `track_runner/interval_solver.py`, `track_runner/cli.py`
  prediction builders, `track_runner/ui/base_controller.py`
- Depends on: the external typed-coordinate-space plan having landed stable
  `coord_space` types. That plan is the source of truth for the SOURCE / PROCESSED
  contract; this package consumes it and changes nothing in `coord_space.py`.
- Acceptance criteria: prediction and trajectory payloads carry
  `common_tools.coord_space.SourceBox` values instead of `{"cx": ..., "cy": ...}`
  dicts, and the `reader: object` / `window: object` / `save_callback: object`
  annotations on the touched signatures are replaced with real types. No new
  coordinate conversion is introduced and the single SOURCE storage boundary is
  unchanged.
- Evidence or review: `reviewer` confirms `coord_space.py` stays as the external plan
  published it and that its types were used as-is.
- Obvious follow-ons: collapse the four near-identical 12-line overlay-construction
  blocks in `base_controller._update_fwd_bwd_overlays` into one loop over a typed
  prediction set.

### Work package: WP-M1 buffer gray at uint8 and bound the result store

- Owner: `expert_coder`
- Touch points: `track_runner/residual_pre_pass.py`
- Depends on: none.
- Acceptance criteria: the rolling gray buffer no longer holds a full-frame float32
  array per frame. The result store is bounded by **total bytes with plain LRU
  eviction**. LRU is chosen because consumption order varies at run time: the
  walker's actual ROI at a frame can differ from the precomputed Hermite ROI, which
  is precisely why the legacy reader fallback exists. A store miss falls through to
  that legacy reader path, which is already tested. The declared
  per-worker budget replaces the current table in the module docstring.
- Evidence or review: the existing byte-identical residual parity test still passes.
  **Cache miss rate and total solve wall time are both recorded**, because a
  too-small store trades an out-of-memory failure for a scattered-seek runtime
  regression. The store size is chosen so the miss rate stays low enough that total
  wall time does not regress against the pre-change run.
- Obvious follow-ons: none.

### Work package: WP-M2 record the memory budget

- Owner: `tester`
- Touch points: `docs/active_plans/reports/` (new report)
- Depends on: WP-M1.
- Acceptance criteria: a measured table of peak RSS per worker by bin factor and
  interval length from a real corpus run, with cache miss rate and wall time
  alongside. The table also records the **driver-process baseline with zero workers
  running**, since WP-M5's sizing equation reserves it, and the worker count that
  fits the user's machine given both terms.
- Evidence or review: numbers come from an actual run, not an estimate.
- Obvious follow-ons: none.

### Work package: WP-M3 reduce walker cost while preserving the recurrence

- Owner: `expert_coder`
- Touch points: `track_runner/blob_walk/walk_viterbi.py`,
  `track_runner/blob_walk/walk_walker.py`
- Depends on: none.
- Acceptance criteria, in two ordered steps:
  - **Step 1, memoize position-invariant costs.** `_edge_cost` reads its frame
    indices only as `gap = frame_b - frame_a`, and `select_path` is called with a
    scale argument that is `seed_w`, constant for the whole walk
    (`walk_walker.py:355`). Edge costs and per-frame evidence costs are therefore
    invariant to where the window sits, and are currently recomputed on every
    one-frame slide. Memoize them across slides, keyed on
    `(absolute_frame_a, candidate_index_a, absolute_frame_b, candidate_index_b)`.
    Exactness is not asserted, it is verified: before writing the memo, enumerate
    every value `_edge_cost` and `_evidence_costs_for_frame` consume -- both
    centroids, the gap, `torso_w`, `fps`, `evidence_b`, and the weight dict -- and
    show each is either captured by that key or constant for the whole walk. Show
    also that a frame's candidate list is immutable once buffered, so a cached entry
    stays valid for the lifetime of the walk; the design doc's
    chained-blob-state rule already requires the window buffer to hold only raw
    image-derived candidate lists, and this package confirms the code honors it. The
    memo lives for one walk and is discarded with it. If any consumed value turns out
    not to be covered, the memo key is widened or the memo is dropped -- it is not
    shipped on an unverified assumption.
  - **Step 2, investigate exact DP-state reuse, then decide.** Reusing DP state
    across a slide is **not assumed to be valid**. The window's left boundary
    advances each step and the leading-skip term is keyed to window position, so
    dropping the oldest frame can change the optimal suffix. Before any DP-state
    change is written, establish from the existing recurrence whether an exact reuse
    property holds and record the finding. Implement reuse only if the property is
    proven; otherwise stop at Step 1 and record why.
  - Selected paths are identical to the current implementation on the baseline corpus
    in either outcome. Peak memory holds at or below the pre-change value. The six
    cost weights keep their current values.
- Evidence or review: `tests/test_walk_viterbi_brute_force.py` green; byte-level path
  equality on the baseline corpus; Stage-4 wall time before and after.
  `architect` reviews the Step 2 finding before any DP-state change is implemented.
- Obvious follow-ons: none.

### Work package: WP-M4 remove silent geometry defaults in the walker

- Owner: `coder`
- Touch points: `track_runner/blob_walk/walk_walker.py`
- Depends on: WP-M3.
- Acceptance criteria: `b.get("centroid_x", 0.0)` and `b.get("centroid_y", 0.0)` in
  `resolve_audit_winner`, and `b.get("centroid_x")` in `_build_window_entry`, become
  direct key access. This is in scope rather than a follow-on because the defaults
  convert malformed geometry into plausible coordinates -- a missing centroid becomes
  the frame's top-left corner, or `None` entering the DP -- inside the same code this
  milestone edits.
- Evidence or review: `reviewer` confirms every candidate producer sets the key;
  walker paths unchanged on the baseline corpus.
- Obvious follow-ons: none.

### Work package: WP-M5 size the worker pool from the measured budget

- Owner: `coder`
- Touch points: `track_runner/cli.py` `_resolve_workers`,
  `track_runner/solve_queue.py`
- Depends on: WP-M2.
- Acceptance criteria: the default worker count is
  `floor((available - parent_baseline - headroom) / per_worker)`, **not**
  `available / per_worker`. `parent_baseline` is the measured resident footprint of
  the driver process with zero workers running, covering shared caches, decoder
  state, and loaded artifacts; `headroom` is a stated reserve. WP-M2 measures both
  terms, not just `per_worker`. An explicit `--workers` value still wins. The chosen
  count, both measured terms, and the reserve appear in the solve banner.
- Evidence or review: a run at `bin 1` on the user's machine selects a count whose
  predicted total (parent baseline plus workers) fits the recorded budget, confirmed
  against observed peak system memory during the run.
- Obvious follow-ons: none.

### Work package: WP-N1 window the tangent estimator by frame distance

- Owner: `expert_coder`
- Touch points: `track_runner/velocity_model.py`
- Depends on: M2 complete.
- Acceptance criteria: `estimate_directional_slope` and
  `estimate_directional_size_slope` select support seeds by **frame distance** within
  a documented window, expressed in seconds and converted with fps, rather than by
  taking up to four neighbors by index. Support seeds outside the window are excluded
  even when fewer than four remain, and the existing degradation ladder (regression,
  then finite difference, then zero slope) is preserved.
- Evidence or review: held-out-seed error for Hermite-before against Hermite-after.
  This instrument is valid here because both arms are the same algorithm class; it is
  explicitly not being used as a walker-versus-Hermite ranking, per the standing rule
  in `TRACK_RUNNER_DESIGN.md`.
- Obvious follow-ons: replace the silent `math.log(x) if x > 1e-6 else 0.0`
  fallbacks, which map a degenerate torso size to a one-pixel box, with a loud
  failure.

### Work package: WP-N2 share one tangent per seed

- Owner: `expert_coder`
- Touch points: `track_runner/seed_tangents.py` (new),
  `track_runner/velocity_model.fit_interval_curves`,
  `track_runner/solve_queue.py`, `track_runner/solver_workers.py`
- Depends on: WP-N1.
- Acceptance criteria: a `SeedTangentField` is computed once from the seed list
  before dispatch and passed to workers as run-invariant state alongside
  `scene_transform` and `motion_track`. The interval to the left of seed S and the
  interval to the right of S use the same tangent at S. No worker reads another
  interval's solve state; the field is a pure function of seeds only.
- Evidence or review: for each shared seed, the difference between the left
  interval's outgoing first derivative and the right interval's incoming first
  derivative is zero to numerical tolerance. This measures the claimed defect
  directly. A second-difference distribution comparison is explicitly **not** used as
  a gate: second differences legitimately differ near seeds when the runner really
  accelerates there, so that test could reject a correct implementation.
  `architect` confirms C6 compliance.
- Obvious follow-ons: none.

### Work package: WP-N3 decide the far-endpoint chord tangent

- Owner: `expert_coder`
- Touch points: `track_runner/velocity_model.py`,
  `docs/active_plans/decisions/` (decision record)
- Depends on: WP-N2.
- Acceptance criteria: each pass currently uses an estimated tangent at its own
  anchor seed and a plain **chord** slope at the far seed, which makes part of the
  FWD/BWD disagreement a modelling artifact rather than genuine uncertainty. Since
  that disagreement drives Stage-4 promotion, this is decided in scope, not deferred.
  Measure disagreement and promotion counts with the chord tangent retained and with
  the shared seed tangent used at both ends, and record which is kept and why.
- Evidence or review: promotion counts and disagreement distributions for both arms;
  in-box heat on the affected intervals so the decision is not made on smoothness
  alone. `architect` signs the decision record.
- Obvious follow-ons: none.

### Work package: WP-N4 collapse the duplicated propagators

- Owner: `coder`
- Touch points: `track_runner/velocity_model.py`
- Depends on: WP-N3.
- Acceptance criteria: `_compute_raw_pred_forward` and `_compute_raw_pred_backward`
  become one direction-parameterized function. The decay constants exist once. The
  documented-unused parameters (`frame_indices` on both estimators, and `reader`,
  `residual_cache`, `precomputed_store` on both propagators) are removed along with
  their call sites.
- Evidence or review: bit-parity against the pre-change propagator on a fixed seed
  set before the old bodies are deleted.
- Obvious follow-ons: none.

### Work package: WP-U1 build the annotation session

- Owner: `expert_coder`
- Touch points: `track_runner/ui/session.py` (new),
  `track_runner/ui/workspace.py`, `track_runner/seeding.py`,
  `track_runner/seed_editor.py`
- Depends on: none.
- Acceptance criteria: one `AnnotationSession` owns the video context, reader, seed
  store, and prediction store for the process lifetime, and owns the decode thread's
  lifetime. `_on_mode_changed` asks the session to activate the selected mode's
  controller instead of calling `set_controller(None)`. Seed, Target, and Edit are
  reachable from each other without quitting. The existing write-on-commit save model
  is preserved, so no unsaved state can be lost by a mode switch. The
  `_init_complete` guard and the `hasattr` self-checks in `workspace.py` are removed,
  because a session that owns construction order does not need them.
- Evidence or review: `reviewer` confirms controller teardown removes every scene
  item and event filter, with no leak across a switch.
- Obvious follow-ons: give `TargetController` its own `_get_mode_name`, since it
  currently inherits `"seed"` and mislabels the mode badge and accent color; then
  evaluate whether the class earns its existence, given it changes only defaults.

### Work package: WP-U2 promote the seed factory to a public API

- Owner: `coder`
- Touch points: `track_runner/seed_color.py`,
  `track_runner/ui/seed_controller.py`
- Depends on: WP-U1.
- Acceptance criteria: `seed_color._build_seed_dict` becomes a public factory taking
  an explicit status, and the controller calls it at module import scope. The
  "import here to avoid circular dependency" comment and the three near-identical
  branches in `_on_box_drawn` collapse to one status resolution, one build, one
  commit.
- Established before dispatch: **the import hoists cleanly.** `seed_color.py` imports
  only `numpy`, and the graph report finds the repository free of import cycles. The
  deferred import moves straight to module scope and the stale comment goes with it,
  so this package is mechanical.
- Evidence or review: `reviewer` confirms the hoisted import introduces no cycle.
- Obvious follow-ons: none.

### Work package: WP-U3 move frame decode off the event loop

- Owner: `expert_coder`
- Touch points: `track_runner/ui/frame_source.py` (new),
  `track_runner/ui/seed_controller.py`, `track_runner/ui/edit_controller.py`
- Depends on: WP-U1.
- Acceptance criteria:
  - **Thread ownership is fixed by this plan, not by the implementer**: one decode
    thread creates the `FrameReader`, performs every read, and destroys it. The GUI
    thread is the sole holder of that object. Requests and results cross by queued
    signal. `AnnotationSession` owns the thread's lifetime and joins it during
    teardown. The Qt mechanism used to realize this is the implementer's choice.
  - The reader is opened through `common_tools.frame_reader.open_analysis_reader`,
    not a parallel path.
  - `FrameSource` serves a byte-bounded LRU cache and prefetches in the direction of
    travel. A request superseded by a newer one is discarded rather than rendered,
    identified by request id.
  - Every `read_frame` call runs on the decode thread. The cache lives in memory for
    the session and is released with it (C13).
- Evidence or review: `reviewer` traces every reader access to the decode thread and
  confirms held-frame memory is bounded.
- Obvious follow-ons: use the prefetch direction so held arrow-key scrubbing serves
  from cache at sequential-read cost.

### Work package: WP-U4 move heat compute off the event loop

- Owner: `expert_coder`
- Touch points: `track_runner/ui/heat_map_overlay.py`,
  `track_runner/ui/workspace.py`, `track_runner/ui/base_controller.py`
- Depends on: WP-U3.
- Acceptance criteria: the residual compute runs off the Qt event loop under the same
  ownership rule as WP-U3, and its result is applied only if still current. The
  "Loading heatmap... Please wait" dialog, `_set_heat_busy`,
  `_build_heat_busy_dialog`, `_center_heat_busy_dialog`, and the `setEnabled(False)`
  view gate are deleted, because their only purpose was to hide a blocking compute.
  The existing drawing-pause behavior is preserved through the same stale-result
  guard.
- Evidence or review: `reviewer` confirms the frame view stays enabled throughout and
  that heat toggles freely during a drag while the window keeps responding.
- Obvious follow-ons: none.

### Work package: WP-U5 build the keybinding registry

- Owner: `coder`
- Touch points: `track_runner/ui/keymap.py` (new), all UI controllers,
  `track_runner/ui/workspace.py`, `docs/TRACK_RUNNER_KEYBINDINGS.md`,
  `tools/refresh_mode_docs.py`
- Depends on: WP-U1.
- Acceptance criteria: one declarative table maps key plus modifier to an action id,
  a human label, and the modes it applies to. Dispatch reads the table instead of
  `if/elif` chains. The hint bar and the F1 help dialog render from the table rather
  than from a hand-written string that the dialog currently parses back apart.
  `docs/TRACK_RUNNER_KEYBINDINGS.md` is generated from the table, and a check fails
  when the file and the table diverge.
- Evidence or review: the generated doc matches the table; the drift check fails on a
  deliberate mismatch.
- Obvious follow-ons: with a registry in place, reconsider the double-press ESC/Q
  quit and the unmodified single-letter bindings, both easy to hit by accident while
  annotating.

### Work package: WP-U6 fix the per-frame render path

- Owner: `coder`
- Touch points: `track_runner/ui/frame_view.py`
- Depends on: none.
- Acceptance criteria: `set_frame` stops copying the frame to swap channels and stops
  removing and re-adding the pixmap item each frame; it updates the existing item in
  place. The scene rect is no longer reset on every frame, so a pan survives a frame
  advance. The function-local imports inside `eventFilter` and `set_zoom` move to
  module scope, and `eventFilter` stops claiming every mouse event unconditionally.
- Evidence or review: pan the view, advance a frame, and confirm the pan holds;
  per-frame display cost recorded before and after on a 4K source.
- Obvious follow-ons: move the remaining inline hex colors in `workspace.py` into
  `ui/theme.py` or `overlay_styles.yaml`, and stop rebuilding the progress-bar
  stylesheet string on every progress tick.

### Work package: WP-U7 route user feedback to the GUI

- Owner: `coder`
- Touch points: `track_runner/ui/seed_controller.py`,
  `track_runner/ui/base_controller.py`, `track_runner/ui/status_presenter.py`
- Depends on: WP-U1.
- Acceptance criteria: messages the user needs while annotating -- draw-mode changes,
  duplicate-seed rejections, coverage warnings, seed statistics -- appear in the
  window. Console output is retained only where it serves a batch or log purpose.
- Evidence or review: `reviewer` greps the UI package for `print(` and confirms each
  survivor is deliberate.
- Obvious follow-ons: surface the seed-coverage warning ("largest gap is much larger
  than average") as an actionable in-window affordance, since it is the one message
  that should change what the user does next.

### Work package: WP-D1 build the offline camera-path solver

- Owner: `expert_coder`
- Touch points: `track_runner/dolly_path.py` (new)
- Depends on: M2 and M5 complete.
- Acceptance criteria: a pure function takes the target trajectory plus per-frame
  weights and returns the crop path, minimizing weighted squared tracking error plus
  a squared-acceleration penalty over the whole sequence. Position is solved in
  pixels and size in log space so zoom is multiplicatively smooth. The resulting
  linear system is banded and solved with the already-declared `scipy` dependency.
  The smoothness parameter is formulated so its meaning is scale-free in torso units
  (C2). Weights come from the M2 confidence owner; frames inside a `not_in_frame`
  span take zero weight so smoothness carries the path across the gap instead of the
  current edge-anchored construction. The function is pure, with no file or reader
  access, so it is directly unit-testable.
- Evidence or review: `architect` approves the objective and the parameter
  formulation before integration. On synthetic input with a known answer, the solver
  reproduces it.
- Obvious follow-ons: none.

### Work package: WP-D2 integrate the solver behind the containment clamp

- Owner: `expert_coder`
- Touch points: `track_runner/tr_crop.py`
- Depends on: WP-D1.
- Acceptance criteria: the solved path passes through the existing containment
  machinery (`_max_centered_fit_size`, `_rolling_min_ceiling_per_frame`,
  `validate_torso_within_central_window`) unchanged. Where the clamp binds, those
  frames are pinned and the path is re-solved, iterating to a fixed point within a
  bounded iteration count, with a documented fallback to the current `smooth` mode on
  non-convergence. **The fallback reports itself**: convergence status, iteration
  count, and whether the fallback fired are returned to the caller and recorded per
  clip, so WP-D3 can tell which path produced each measurement. `torso_size_stabilizer` keeps its outlier-rejection role as the robust
  pre-stage feeding the solver. Every existing crop test still passes.
- Evidence or review: `tests/test_tr_crop_assertion.py` and the other
  `tests/test_tr_crop_*.py` modules green.
- Obvious follow-ons: once the solver owns smoothing, remove
  `CROP_POST_SMOOTH_SIZE_STRENGTH` and the forward-backward size EMA, which the
  solver subsumes.

### Work package: WP-D3 produce the A/B evidence

- Owner: `tester`
- Touch points: `docs/active_plans/decisions/` (new evidence artifact)
- Depends on: WP-D2.
- Acceptance criteria: on the corpus clips already used for the crop-mode decision,
  the new path is measured against `direct_center` and `smooth` on center lag
  (cross-correlation between crop center and target center), 95th-percentile crop
  acceleration in torso widths per frame squared, and crop-height step distribution.
  Sample frames are written alongside the numbers, matching the format of the
  existing crop-mode decision assets. Each evaluated clip records the WP-D2
  convergence status and whether the fallback fired; a clip that fell back to
  `smooth` is reported as such and listed separately from the new-solver aggregate.
  **This package completes when the artifacts exist.** Flipping the default crop mode
  is a separate one-line change the user authorizes after reading the artifacts, and
  sits outside every milestone's exit criteria.
- Evidence or review: `reviewer` confirms all three modes were measured on the same
  clips with the same metric code.
- Obvious follow-ons: once the user adopts a default, retire whichever legacy crop
  mode the evidence shows is strictly dominated.

### Work package: WP-O1 extract mode bodies into a modes package

- Owner: `coder`
- Touch points: `track_runner/modes/` (new), `track_runner/cli.py`
- Depends on: M6 complete.
- Acceptance criteria: each `_mode_*` body moves to `track_runner/modes/<mode>.py`.
  `cli.py` retains argument wiring, artifact path resolution, and dispatch. Helper
  functions currently shared across modes move to the module that owns them. The CLI
  surface is unchanged.
- Evidence or review: `tools/dump_cli_help.py` output diffs empty before and after.
- Obvious follow-ons: none.

### Work package: WP-O2 remove the dead code this plan exposed

- Owner: `maintainer`
- Touch points: `track_runner/scoring.py`, `track_runner/interval_solver.py`,
  `track_runner/review.py`, `track_runner/velocity_model.py`
- Depends on: WP-O1.
- Acceptance criteria: `scoring.classify_confidence` (defined, never called), the
  `occlusion_risk` field (read in three places, written by no producer), and any
  parameter left unused by WP-N4 are removed. `pytest tests/test_pyflakes_code_lint.py`
  passes.
- Evidence or review: `reviewer` confirms each removal has no live caller.
- Obvious follow-ons: audit the 487 `.get(` sites in production code against the
  "do not hide bugs with defaults" rule, beyond the walker geometry keys WP-M4
  already covers.

## Acceptance criteria and gates

- Per-patch gate: `pytest tests/` green, including the hygiene tests this repo
  actually carries -- `tests/test_pyflakes_code_lint.py`,
  `tests/test_ascii_compliance.py`, `tests/test_indentation.py`,
  `tests/test_import_dot.py`, and `tests/test_markdown_links.py`. Focused tests run
  for every changed module, per `AGENTS.md`. Python runs as
  `source source_me.sh && python3`.
- Attribution gate: applied per **patch**, not per milestone. No output-changing
  patch lands while an earlier one's measurement is unrecorded. M2, M3, and M7 each
  contain one output-changing patch. M5 contains three (WP-N1, WP-N2, WP-N3) and is
  therefore internally serial, with a recorded measurement between each.
- Correctness gate: a package claiming better output measures the subject, not only
  the smoothness of the path: a correctness claim carries a subject measure alongside
  its continuity measure. Where the available subject metric acts as a veto rather
  than proof, the package supplies ground truth by construction or a reviewable
  artifact, and the plan names which one carries the claim.
- Integration gate: for each milestone, the exit criteria in
  [Milestone plan](#milestone-plan) are met and recorded with measured numbers.
- Contract gate: every work package lands compute-only or in-memory changes under the
  current `SCHEMA_VERSION`. A package that finds it needs a bump raises that with the
  user first, per C10.
- Independent review gate: `architect` signs off on WP-T3 (the single confidence
  definition), WP-N2 (C6 compliance of the shared tangent field), WP-N3 (the chord
  decision), WP-M3 Step 2 (the DP reuse finding, before any DP-state change is
  written), and WP-D1 (the dolly objective).
- Human decision boundary: exactly one decision in this plan belongs to the user and
  is deliberately outside every milestone's exit criteria -- whether to adopt the new
  crop default after reading the WP-D3 artifacts. Every other gate is satisfiable by
  the manager and its subagents.

## Test and verification strategy

Each claim gets an instrument chosen to be valid for that specific comparison, and
each output claim is separated into a **continuity** measure and a **subject**
measure, so a passing result satisfies both. Where the subject measure acts as a veto
rather than proof -- in-box heat, which ranks the most-moving body part and reads one
runner the same as another -- ground truth by construction carries the claim. `TRACK_RUNNER_DESIGN.md` carries a
standing rule that held-out-seed error is not a walker-versus-Hermite quality
ranking; this plan honors it by using held-out-seed error only where both arms are
the same algorithm class.

| Claim | Instrument | Passing condition |
| --- | --- | --- |
| Seeds are no longer softened (WP-T1) | Exact equality of solved box and seed box at every `visible` / `partial` seed frame | Zero mismatches across the corpus |
| Confidence has one definition (WP-T3) | Count of definition sites; promotion counts before and after | Exactly one site; promotion-count change explained and accepted |
| The midpoint teleport is gone (WP-T2, continuity) | Maximum single-frame center step per trajectory, in torso widths | At or below `ABSOLUTE_MAX_JUMP_W`, and strictly below the pre-change value |
| The winner rule picks the runner (WP-T2, primary) | Synthetic lattices with ground truth by construction, extending `tests/test_walk_cost_model.py` | Rule selects the known-runner pass over a known distractor, at the hit rate that file already uses |
| The committed pass still holds the moving subject (WP-T2, veto) | Mean in-box heat over committed disagreement runs via `measure_in_box_heat` | Not lower than the pre-change blended path. A drop fails; no drop does not by itself pass |
| The commitment behaves on real footage (WP-T2, record) | Rendered overlay of every committed run via `tools/blob_walk_v2/render/walk_render.py`, pre-change and committed paths together | Artifact produced and filed |
| Memory is bounded (WP-M1) | Peak RSS per worker at `bin 1` and `bin 2` on a real corpus video | Below the declared budget with the selected worker count |
| The memory fix is not a slowdown (WP-M1) | Pre-pass cache miss rate and total solve wall time | Miss rate low enough that wall time does not regress |
| The walker is unchanged but cheaper (WP-M3) | Selected-path equality on the baseline corpus; Stage-4 wall time; peak memory | Paths identical; time reduced; memory not increased |
| DP-state reuse is or is not valid (WP-M3 Step 2) | Written finding derived from the existing recurrence, reviewed before implementation | Finding recorded; reuse implemented only if proven |
| Tangents are locally valid (WP-N1) | Held-out-seed error, Hermite-before against Hermite-after (same algorithm class) | Error distribution improves or is unchanged, no new absolute outliers |
| Velocity is continuous at seeds (WP-N2) | First-derivative mismatch across each shared seed: outgoing tangent of the left interval minus incoming tangent of the right | Zero to numerical tolerance at every shared seed |
| The chord tangent decision (WP-N3) | Disagreement distribution, promotion counts, and in-box heat for both arms | Decision recorded with its numbers and signed |
| The UI stays responsive (WP-U3, WP-U4) | Trace every `read_frame` and residual compute to the decode thread; scrub responsiveness on a 4K source | All such calls run off the event loop; scrubbing stays interactive |
| Reader threading is correct (WP-U3) | Trace of every reader access against the stated ownership rule | All accesses on the decode thread; session joins it on teardown |
| Keybindings stay in sync (WP-U5) | Generated doc against the registry table | Drift check fails on a deliberate mismatch |
| Pan survives frame advance (WP-U6) | Pan, advance a frame, read back the view transform | Pan preserved |
| The dolly path is better (WP-D3) | Center lag by cross-correlation; 95th-percentile crop acceleration; crop-height step distribution; sample frames | Wins on lag and acceleration; containment not regressed; artifacts complete |

Test placement follows [docs/PYTEST_STYLE.md](../../PYTEST_STYLE.md):
fast deterministic checks go in `tests/test_*.py` and must finish well under a
second; anything that decodes real video, measures wall time or RSS, or runs a full
solve belongs in `tests/e2e/` per
[docs/E2E_TESTS.md](../../E2E_TESTS.md).
Corpus measurements extend the existing `tests/e2e/e2e_blob_walk_baseline.py` and
`tests/e2e/e2e_walker_ab.py` harnesses rather than adding parallel ones. New tests
must clear the fragility checklist: no assertions on collection sizes, required-key
lists, tunable constants, or dates. Where a higher-level gate already covers a
behavior, no unit test is added for it.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Confidence redefinition shifts promotion counts and changes which intervals get the walker | Solve cost and quality both move, confounding later measurements | WP-T3 lands | `architect` | M2 changes only the definition; the blend flip stays until M3, so the shift is attributable. Promotion counts recorded as a first-class result before M3 or M5 begin |
| Blend run-commitment trades a teleport for a longer wrong stretch | Output follows the wrong pass for a whole run instead of half of one | WP-T2 lands | `expert_coder` | The winner rule is motion presence, not continuity, so a smooth-but-empty commitment fails the heat veto. `blend_flag` stays on every frame so review still surfaces the run |
| In-box heat does not discriminate the intended runner | A commitment onto a competitor or onto legs instead of torso passes the heat veto | WP-T2 lands | `expert_coder` | Heat is scoped to a veto by design, per `TRACK_RUNNER_DESIGN.md:212`; the rendered overlay for every committed run is a completion condition and is what carries the correctness claim |
| WP-M3 memo key misses a consumed value | Walker output changes silently while the change is described as exact | WP-M3 Step 1 | `expert_coder` | Exactness is verified by enumerating every value the cost functions consume against the key before the memo is written; unverified values widen the key or drop the memo |
| Worker sizing ignores the parent process | OOM returns despite a per-worker budget that fits | WP-M5 lands | `coder` | The sizing equation reserves a measured driver baseline plus headroom; WP-M2 measures the baseline as a first-class term |
| Assumed DP reuse property does not hold | Silent walker output change with no visible failure | WP-M3 Step 2 | `architect` | Reuse is gated on a written finding reviewed before implementation; Step 1 is exact by construction and delivers value regardless |
| Cache bound trades OOM for scattered-seek slowness | Solves stop crashing but take far longer | WP-M1 lands | `expert_coder` | Miss rate and total wall time are explicit acceptance criteria, not observations |
| Off-thread decode introduces a stale-frame or teardown race | Wrong frame rendered, or a crash on mode switch | WP-U3, WP-U4 land | `expert_coder` | Ownership rule fixed in the plan, not left to the implementer; requests carry ids and superseded results are discarded; the session joins the thread on teardown |
| Session refactor loses unsaved seeds on a mode switch | User loses annotation work | WP-U1 lands | `expert_coder` | Preserve write-on-commit; every seed is already saved through `save_callback` at commit time, and the refactor keeps that immediate-save path |
| Dolly solver fights the containment clamp and fails to converge | Encode aborts or produces a jerky clamped path | WP-D2 lands | `expert_coder` | Bounded pin-and-re-solve iteration with a documented fallback to the current `smooth` mode |
| Cross-plan collision on `coord_space` types | Two agents change one conceptual contract | WP-T5 starts early | `planner` | WP-T5 declares the external plan as source of truth and does not start until those types are landed and stable; `coord_space.py` is outside this plan's touch points |
| A consumer list is drawn from the code map rather than from grep | A live call site is left unmigrated and fails silently at runtime | Any package that changes a shared function's semantics | `reviewer` | Every consumer list comes from `grep -rn`, with the code map used for orientation; the measured reason is recorded under [Using the graphify code map](#using-the-graphify-code-map) |
| Plan drift as work packages discover new defects | Scope grows before anyone decides it should | Any milestone | `planner` | Each package logs new findings to the plan's follow-on list and finishes its stated outcome; the user decides which findings become packages |

## Documentation close-out requirements

- Active plan tracker: publish this plan to
  `docs/active_plans/active/interaction_shell_and_trajectory_truth.md` at execution
  start and keep milestone status current there. Its relative links are already
  written for that path, so they resolve on GitHub once the file lands. Move it to
  `docs/archive/` with `git mv` when complete, fixing link depth in the same move.
- `docs/CHANGELOG.md` entry: required for every patch, filed under the correct dated
  day block and subsection. Behavior changes to solve output, crop output, and the UI
  session model go under `### Behavior or Interface Changes`. Rejected and accepted
  design alternatives -- including the WP-M1 consumption-order rejection and the
  WP-M3 Step 2 finding -- go under `### Decisions and Failures`, since the changelog
  is meant to stay a useful learning record.
- Doc updates owned by this plan:
  - `docs/TRACK_RUNNER_DESIGN.md` -- the mode-switching claim becomes true with M6;
    the blend and confidence sections need rewriting after M2 and M3; the crop
    separation of concerns section needs rewriting after M7.
  - `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md` -- blend semantics change in M3.
  - `docs/TRACK_RUNNER_KEYBINDINGS.md` -- becomes generated in M6.
  - `docs/modes/*.md` -- refreshed through `tools/refresh_mode_docs.py` after M8.
  - `docs/TROUBLESHOOTING.md` -- add the memory budget and worker-sizing guidance
    from M4.
- Evidence artifacts: WP-M2, WP-N2, WP-N3, WP-T2, WP-T3, and WP-D3 each write a
  report under `docs/active_plans/reports/` or `docs/active_plans/decisions/`
  following the existing naming and structure there.
- Contract note: no clause of
  [docs/TRACK_RUNNER_CONTRACT.md](../../TRACK_RUNNER_CONTRACT.md)
  is edited by this plan. The contract is the source of truth this work reads from,
  and the work restores compliance with C3 and C6 as they already stand.
- Each patch stages its files and adds its `docs/CHANGELOG.md` lines, leaving the
  working tree ready for review.

## Patch plan and reporting format

- Patch 1: WP-T1 -- seed anchoring.
- Patch 2: WP-T3, WP-T4 -- confidence and agreement ownership.
- Patch 3: WP-T2 -- blend commitment.
- Patch 4: WP-M1, WP-M2 -- pre-pass memory and its budget report.
- Patch 5: WP-M3, WP-M4, WP-M5 -- walker cost, geometry defaults, worker sizing.
- Patch 6: WP-N1, WP-N2, WP-N3, WP-N4 -- tangent model and the chord decision.
- Patch 7: WP-U1, WP-U2 -- annotation session.
- Patch 8: WP-U3, WP-U4 -- off-thread decode and heat.
- Patch 9: WP-U5, WP-U6, WP-U7, WP-T5 -- keybindings, render path, feedback, typed
  payloads.
- Patch 10: WP-D1, WP-D2, WP-D3 -- offline dolly path and its evidence.
- Patch 11: WP-O1, WP-O2 -- mode extraction and dead-code removal.
- Patch 12: remaining repository-required work -- doc refresh, changelog rotation if
  `docs/CHANGELOG.md` has reached about 1000 lines, and archival of this plan.

Each patch reports: files touched, tests run with their result, the measurement its
gate required, and the changelog lines added.

## Resolved decisions

- **Crop stage**: replace the causal `CropController` with an offline whole-path
  solve. Decided by the user after the stage was clarified as the encode-time framing
  stage downstream of both solvers, not as one of the trackers. Adoption of the new
  default is a separate user decision made from the WP-D3 artifacts.
- **Session model**: one persistent session with real in-place mode switching, making
  the existing `TRACK_RUNNER_DESIGN.md` claim true. Decided by the user.
- **Walker coverage**: keep Stage-4 promotion policy exactly as it is. The user
  reports prior out-of-memory failures, so walker work targets cost and memory safety
  at the current coverage.
- **Milestone granularity**: attribution boundaries set the milestone count. M2, M3,
  M5, and M7 each change exactly one thing about output, sequenced so a regression
  always has a single candidate cause.
- **Pre-pass cache policy**: byte-bounded LRU, not consumption-ordered eviction.
  Consumption order is not deterministic because the walker's actual ROI can differ
  from the precomputed Hermite ROI, which is why the legacy fallback exists.
- **Walker DP reuse**: treated as an open mathematical question, not an
  implementation assumption. Step 1 (memoizing position-invariant costs) is exact and
  proceeds; Step 2 proceeds only on a proven reuse property.
- **Chord tangent and walker geometry defaults**: pulled from follow-on status into
  scope (WP-N3, WP-M4), because both affect correctness of the milestones that touch
  them. The remaining follow-ons stay follow-ons: the full `.get(` audit, walker
  emission policy, UI theme cleanup, and `TargetController` simplification are none
  of them needed to establish a stated milestone outcome, and scope stops here.
- **In-box heat is a veto, not ground truth**: it measures motion presence, and
  `TRACK_RUNNER_DESIGN.md:212` records that residual magnitude ranks legs and feet
  above the torso, while a competing runner is equally hot. The correctness claim for
  WP-T2 rests on the rendered overlay artifact instead.

## Assumptions

- The measured corpus used for before/after evidence is the same set already
  exercised by `tests/e2e/e2e_blob_walk_baseline.py` and the reports under
  `docs/active_plans/reports/`. If a video in that set is unavailable, the affected
  measurement is recorded as not run rather than silently skipped.
- `scipy` remains available; it is already declared in `pip_requirements.txt` for
  interval-solving interpolation, so the banded solve adds no new dependency.
- `common_tools/in_box_heat.measure_in_box_heat` can be evaluated on a solved
  trajectory offline. The walker already computes it per emitted frame while the
  residual is live, so the WP-T2 veto consumes an existing measurement rather than
  forcing a new decode pass. If that turns out to require re-decoding, WP-T2 records
  the added cost rather than dropping the veto. The overlay artifact, not this
  metric, is what establishes correctness.
- The typed-coordinate-space plan reaches stable, landed `coord_space` types before
  Patch 9. If it has not, WP-T5 defers to a later patch and the rest of M6 proceeds
  without it.
- The repository has a `graphify` code map under `graphify-out/`, built from commit
  `d89c586a`. This plan uses it for orientation and uses `grep` to establish consumer
  lists. See [Using the graphify code map](#using-the-graphify-code-map) for the
  commands and the measurement behind that split.

## Using the graphify code map

A `graphify` map of this repository lives in `graphify-out/`, built from commit
`d89c586a`. Refresh it with `graphify update .` after landing a patch.

Use it for orientation, which is what it is reliably good at here:

```bash
# highest-coupling symbols; use to pick where a boundary belongs
graphify god-nodes --top 20

# what one symbol means, plus its neighbors
graphify explain "BaseAnnotationController"

# candidate impact area, as a starting point for reading
graphify affected "blend_paths" --depth 2

# dependency route between two symbols when a boundary decision needs the flow
graphify path "SeedController" "FrameReader"
```

Establish consumer lists and touch-point completeness with `grep`, which resolves
this repo's import idiom:

```bash
# the authoritative consumer list for a symbol
grep -rn "symbol_name" --include=*.py .
```

Why the split, measured on this repo rather than assumed: `docs/PYTHON_STYLE.md`
mandates `import module` plus `module.function()` calls, and the extractor resolves
direct calls rather than that attribute form, so most cross-module calls fall outside
its graph. It returned the identical `No affected nodes found` for a genuinely dead
symbol (`scoring.classify_confidence`) and for two live ones
(`scoring.compute_seed_confidences`, called at `cli.py:1287`, and
`interval_solver.derive_per_frame_confidence`, called at `cli.py:2216` and
`cli.py:2445`), and it listed `blend_paths` as solver-internal while
`tools/blob_walk_v2/walk_driver.py:956` calls it directly. The report's
"98 % extracted" figure describes precision on the edges it found; recall over the
attribute-call edges is what `grep` supplies.

Findings from the map that this plan relies on, each confirmed by reading source:

- `god-nodes` ranks `BaseAnnotationController` (45), `SeedController` (41),
  `EditController` (41), `main()` (41), and `AnnotationWindow` (28) highest,
  supporting M6 and M8 as the ownership work.
- `ProcessedBox` (32), `ProcessedPoint` (24), and `FrameGeometry` (22) rank high,
  supporting WP-T5 adopting the existing `coord_space` types.
- The report finds no import cycles, and `seed_color.py` imports only `numpy`. WP-U2
  can therefore hoist its deferred import directly, and WP-O1's mode extraction has a
  clear path.
- The "Walk Cost Model" community surfaced `tests/test_walk_cost_model.py`, whose
  synthetic-lattice harness supplies WP-T2's primary ground-truth gate.

## Open questions and decisions needed

None of the following blocks execution.

- Manager/subagent decision procedure for the WP-T2 transition band length:
  - Decision owner or dedicated class: `expert_coder`.
  - Evidence and decision rule: the band is temporal, in frames. Sweep a small set of
    lengths on the corpus intervals already flagged as disagreeing, and pick the
    shortest band whose maximum per-frame center step stays under the torso-width
    bound. Record the sweep in the module docstring so the constant carries its
    rationale.
- Manager/subagent decision procedure for the tangent window length (WP-N1):
  - Decision owner or dedicated class: `expert_coder`.
  - Evidence and decision rule: sweep a small set of window lengths in seconds on the
    corpus and pick the shortest window whose held-out-seed error stops improving.
    Document the sweep in the module docstring.
- Manager/subagent decision procedure for the pre-pass store size (WP-M1):
  - Decision owner or dedicated class: `expert_coder`, with `tester` producing the
    numbers.
  - Evidence and decision rule: sweep store sizes, record peak RSS, miss rate, and
    wall time at each, and pick the smallest size that keeps wall time within noise of
    the pre-change run while fitting the per-worker budget.
- Non-blocking follow-up: whether the walker should emit more than one frame per
  window slide once its cost is reduced. This is a quality question, not a cost one,
  and it needs the dense per-frame human trace that `TRACK_RUNNER_DESIGN.md` names as
  the proper quality truth.
- Non-blocking follow-up: the remaining `.get(` sites in production code beyond the
  walker geometry keys WP-M4 covers. A full audit is a separate effort.
