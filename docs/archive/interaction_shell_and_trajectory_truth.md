# Completed plan: Interaction shell, trajectory truth, and offline dolly path

## Context

Track runner works end to end, but seven design problems limit correctness and usability:

- Seed, Target, and Edit were separate process-level workflows instead of one persistent session.
- Human seed geometry could be softened by Hermite/walker blending, violating contract C3.
- Confidence and FWD/BWD agreement had multiple owners and inconsistent definitions.
- FWD/BWD disagreement could produce a midpoint winner flip instead of one continuous commitment.
- The crop path used a causal controller even though Encode already knows the whole trajectory.
- Residual pre-pass and worker-pool memory were not controlled by an explicit per-worker budget.

Most implementation is now present in the working tree. Progress slowed because earlier versions of
this plan made private videos, historical snapshots, local visual receipts, exact output parity, and
unrelated global hygiene failures into completion gates. Those requirements were not necessary to
establish the seven outcomes and could not be completed from a normal clone.

This revision restores a simple execution boundary. Required verification is repository-local,
deterministic, and tied directly to behavior or a contract. Private media and local receipts may help
diagnosis, but never determine milestone completion.

## Post-implementation review

A code, test, and documentation review on 2026-08-21 concludes that the revised
product outcomes are implemented. Literal completion of every gate in the
original [fixup-plan.md](fixup-plan.md) is neither required nor desirable.
Several original gates depended on private media, local machine measurements,
exact artifact parity, fragile implementation checks, or an implementation
hypothesis that evidence later rejected.

The following items are justified plan corrections, not missing work:

- Private image evidence of high school runners is excluded from the
  repository. Source frames, crops, overlays, thumbnails, and similar derived
  images may be inspected locally when authorized, but must remain untracked.
  Safe repository evidence uses generated inputs, non-identifying data, and
  aggregate numerical results.
- Human seed boxes establish position and size, not velocity or size
  derivatives. The original shared-tangent proposal was therefore replaced by
  pair-local endpoint interpolation, as recorded in
  [chord_tangent_retention_decision.md](../active_plans/decisions/chord_tangent_retention_decision.md).
  A neighboring seed cannot alter an interval whose two endpoints did not
  change.
- Real-video overlays, corpus A/B comparisons, RSS profiles, wall-time
  measurements, and exploratory GUI lifecycle exercises are one-time checks
  unless a grounded product requirement makes them repeatable acceptance
  criteria. They do not automatically belong in the permanent suite.
- The removal of fragile Qt lifecycle, implementation-shape, and
  one-trajectory ranking tests is not evidence that the implemented behavior is
  missing. Permanent tests must satisfy
  [PYTEST_STYLE.md](../PYTEST_STYLE.md): they remain deterministic, offline,
  quick, self-contained, and focused on durable behavior.

Plan gates establish a user-visible behavior, an explicit repository contract,
or a resource bound controlled by the code. Exact equality remains appropriate
where exactness is the requirement, including human-authored seed geometry,
FWD/BWD evidence independence, and unified schema ownership. Byte-equivalent
files, pixel-equivalent renders, arbitrary timing thresholds, and
machine-specific corpus rankings are not default requirements.

Under this standard, the original M3 private-overlay gate, M4 bin-specific
profiling gates, M5 shared-tangent gate, M6 implementation-era GUI checks, and
M7 one-corpus ranking gate are superseded. No new product code, private
fixtures, image artifacts, or permanent E2E tests are required to satisfy them.
Historical reports may preserve what was attempted, but must not be read as the
current completion authority.

Documentation drift remains separate from implementation completeness. Current
reference documentation should describe the shipped pair-local interpolation,
run-level commitment, dolly default, and worker-budget policy without reviving
superseded or privacy-prohibited evidence requirements.

## Objectives

- Keep one persistent annotation session and move frame/heat work off the Qt event loop.
- Preserve visible and partial human seed geometry exactly through Hermite and walker blending.
- Use one confidence/agreement owner in solve, analyze, encode, crop, and scoring.
- Commit continuously to one FWD/BWD pass over a disagreement run using the actual production
  residual evaluator.
- Interpolate each Hermite interval only from its two endpoint seed boxes; do
  not infer a velocity or size derivative from nearby human annotations.
- Provide an offline zero-lag, bounded-acceleration dolly path and make its default decision by an
  automated rule.
- Bound solver-owned memory per worker and size the default pool from explicit memory terms.

## Design philosophy

This plan applies **Focus on important issues**, **Perfect is the enemy of good**, **KISS**, and
**Atomic task decomposition** from [REPO_STYLE.md](../REPO_STYLE.md).

The plan verifies the behavior it changes. A completion gate exists only when it checks a user-visible
outcome, a repository contract, or a resource bound controlled by the code. Exact equality is reserved
for contracts where exactness is the point, such as C3 seed geometry and lossless serialization.
Output improvements use semantic properties and relative comparisons, not byte-equivalent artifacts or
pixel-equivalent renders. Wall-clock measurements are diagnostics unless a real product requirement
defines a threshold; no milestone depends on a machine-specific millisecond target.

Uncertain implementation choices use a bounded manager/subagent decision rule over repository-owned
inputs. A private video, mounted configuration directory, historical Git snapshot, manual review,
network service, or user reply is never required to continue.

- Verification strategy for uncertain methods: use inline synthetic transitions,
  fake readers, deterministic residual fields, and debug output written to
  `tmp_path`. Prefer one strong behavioral test to several proxy checks.

## Scope

- Restamp human seed geometry after blending, persistence, and cache reconstruction so seed frames
  keep the authored box exactly.
- Create one owner for per-frame confidence and FWD/BWD agreement, and route solve, analyze,
  encode, crop, and scoring through it.
- Replace the per-frame FWD/BWD winner flip with one continuous run-level commitment selected by
  the production residual-motion evaluator.
- Bound residual pre-pass memory, reduce repeated walker cost, remove silent walker-geometry
  defaults, and size the worker pool from explicit memory terms.
- Use pair-local endpoint interpolation for Hermite fallback geometry.
- Build one persistent annotation session; move frame decode and heat work off the Qt event loop;
  generate dispatch, hints, help, and keybinding docs from one registry; preserve render state;
  route feedback to the GUI; and use typed SOURCE prediction boxes.
- Build an offline whole-path crop solver and integrate it behind the existing containment rules.
- Extract mode bodies into `track_runner/modes/` and remove dead code exposed by this work.
- Complete the user-promoted source-file ownership maintenance work without changing product output
  or performance claims.
- Keep documentation and the durable handoff current.

## Non-goals

- Modify Stage 1 camera-motion estimation, decoding policy, or `SceneTransform` semantics.
- Recover, select, diagnose, or run Jason in any prospective work.
- Require private videos, mounted artifacts, local `/private/tmp` receipts, or manual visual review.
- Add permanent private-video E2E tests.
- Require byte or pixel equality for an intentionally output-changing improvement.
- Require arbitrary startup, rendering, or solve-time thresholds.
- Turn the separately requested function-typing sweep into a goal or completion dependency of this
  plan; it remains independent maintenance.
- Change `SCHEMA_VERSION`, persisted artifact layouts, Stage-4 promotion policy, or Viterbi weights.
- Modify `docs/GRAPH_REPORT.md`.
- Stage, commit, or otherwise modify the Git index. Version-control preparation is outside this plan
  and does not block implementation completion.

Post-milestone current-only maintenance may tighten identity validation and
reader rejection without changing a stored layout or any product milestone.

## Current state summary

| M | Current state | Remaining autonomous work |
| --- | --- | --- |
| M1 | C3 cache-reuse restamping is accepted for fresh, Hermite, walker, and cache paths | Complete |
| M2 | `trajectory_confidence` owns agreement/confidence; portable audit accepts all consumers | Complete |
| M3 | Production-evaluator commitment and generated ground truth are accepted | Complete |
| M4 | Bounded caches, source-ledger worker budget, and pure worker policy are accepted | Complete |
| M5 | Pair-local endpoint interpolation replaces inferred seed slopes | Complete |
| M6 | Portable session, decode, heat, keymap, and render tests are accepted | Complete |
| M7 | The automated rule selected `dolly` as the shipped default | Complete |
| M8 | Real parser plus `cli.main()` dispatch tests accept thin CLI ownership | Complete |

Historical local reports remain useful debugging notes, but the table above is the execution state.
They do not reopen a milestone when its repository-owned behavior tests pass.

## Architecture boundaries and ownership

- `track_runner/trajectory_confidence.py` owns confidence and agreement.
- `track_runner/blend_commitment.py` owns disagreement-run commitment.
- `track_runner/velocity_model.py` owns pair-local endpoint interpolation.
- `track_runner/residual_pre_pass.py` owns bounded pre-pass storage.
- `track_runner/solver_workers.py` owns worker telemetry; worker-count policy has one pure owner.
- `track_runner/ui/session.py` owns controller and worker lifetime.
- `track_runner/ui/frame_source.py` owns UI-side reader access and newest-request delivery.
- `track_runner/ui/keymap.py` owns key dispatch, hints, help, and generated keybinding docs.
- `track_runner/dolly_path.py` owns the offline path solve; `track_runner/tr_crop.py` owns
  containment and crop-mode selection.
- `track_runner/modes/` owns mode bodies; `track_runner/cli.py` owns parsing and dispatch.

Contract boundaries:

- C1/C3: only human-authored seeds are anchors, and visible/partial seed geometry is exact.
- C2: spatial quantities use torso units. Tests use relationships, not unexplained constants.
- C9: FWD and BWD remain independent until scoring/commitment.
- C10: the unified schema remains unchanged.
- C13: caches are bounded and live for one run/session.
- UI reader ownership: the decode worker creates, reads, and closes the reader; the GUI receives
  queued results and never performs synchronous decode.

### Mapping (milestones / workstreams -> components / patches)

| Milestone / Workstream | Component | Review boundary |
| --- | --- | --- |
| M1 / WS-TRUTH | seed stamping | C3 at solve, cache, and persistence boundaries |
| M2 / WS-TRUTH | confidence owner | one definition and C9 inputs |
| M3 / WS-BLEND | commitment policy | known-runner selection and transition continuity |
| M4 / WS-MEM | pre-pass, workers, walker costs | controlled bytes and worker-count arithmetic |
| M5 / WS-INTERPOLATION | endpoint interpolation | pair-local geometry and pass independence |
| M6 / WS-UI | session, workers, keymap, render path | lifecycle and Qt thread ownership |
| M7 / WS-DOLLY | offline solve and containment | lag, acceleration, convergence, containment |
| M8 / WS-OWN | modes package and CLI | direct dispatch behavior |

## Milestone plan

| M | Title | Summary | Goal |
| --- | --- | --- | --- |
| M1 | Seed anchoring | Restamp seed truth after every solve/reuse path | C3 holds exactly |
| M2 | Confidence ownership | Use one confidence/agreement owner | Consumers cannot drift |
| M3 | Blend commitment | Commit one pass over each disagreement run | No midpoint teleport |
| M4 | Memory safety | Bound owned bytes and size the pool from a budget | Solves avoid preventable OOM |
| M5 | Endpoint interpolation | Endpoint chord at both Hermite ends | No inferred derivative state |
| M6 | Interaction shell | Keep one responsive annotation session | Mode switching and scrubbing stay usable |
| M7 | Offline dolly | Solve the whole crop path and apply a rule-based default | Low-lag, smoother framing |
| M8 | Ownership cleanup | Keep modes in owning modules | CLI remains routing code |

### Milestone: M1 seed anchoring

- Depends on: none.
- Deliverables: C3 stamping at fresh solve, walker fallback, cache reuse, and persistence boundaries.
- Workstreams: WS-TRUTH.
- Entry criteria: none.
- Exit criteria:
  - Inline Hermite and walker cases deliberately disagree at a seed, then return the exact authored
    visible/partial seed box and metadata.
  - Approximate/not-in-frame behavior remains unchanged.
- Parallel-plan ready: yes -- max parallel doers: 2 for implementation and independent review.

### Milestone: M2 confidence ownership

- Depends on: M1 -- consumers should see already-correct seed frames.
- Deliverables: one confidence/agreement owner and migrated consumers.
- Workstreams: WS-TRUTH.
- Entry criteria: M1 behavior passes.
- Exit criteria:
  - Repository search finds one definition site.
  - Generated raw-pass cases verify the owner calculation and direct consumer boundaries without
    a full-stack fixture.
  - Tier changes in the synthetic cases follow directly from the owner value.
- Parallel-plan ready: yes -- max parallel doers: 2.

### Milestone: M3 blend commitment

- Depends on: M2 -- commitment consumes the settled agreement definition.
- Deliverables: run-level commitment, deterministic tie/unavailable behavior, and debug overlay
  adapter.
- Workstreams: WS-BLEND.
- Entry criteria: M2 behavior passes.
- Exit criteria:
  - Generated residual fields select the known runner for both FWD and BWD ordering; every
    unambiguous case passes and ties use the documented deterministic rule.
  - A generated midpoint-teleport case has a strictly smaller transition step after commitment.
  - Entry, internal, and exit edges touched by a commitment are monotone and do not worsen the
    corresponding baseline/raw-path edge; unrelated baseline jumps are preserved, not misclassified.
  - Unavailable heat retains the baseline and reports unavailable.
  - The overlay adapter consumes a tiny generated frame/path payload and writes a decodable debug
    image to `tmp_path`; no pixel-equality assertion is used.
- Parallel-plan ready: no -- commitment and transition semantics form one decision surface.

### Milestone: M4 memory and cost safety

- Depends on: none.
- Deliverables: bounded pre-pass allocations, position-invariant walker-cost reuse, fail-loud
  geometry, and budget-based worker selection.
- Workstreams: WS-MEM.
- Entry criteria: none.
- Exit criteria:
  - Byte-accounting tests prove every solver-owned cache/buffer remains at or below its declared
    cap, including replacement, eviction, and oversize entries.
  - A pure worker-count function satisfies
    `parent_bytes + workers * worker_budget + reserve <= available_bytes`, honors an explicit
    override, returns at least one worker only when it fits, and fails loudly otherwise.
  - The reserve is expressed in worker-budget units or another code-owned capacity term, not a
    machine-specific magic byte count.
  - Generated candidate lattices produce the same selected path and equivalent costs before and
    after memoization; no serialized byte-parity requirement is used.
  - Normal solves continue to report RSS/cache telemetry as diagnostics; no local-video run or
    wall-time threshold blocks completion.
- Parallel-plan ready: yes -- max parallel doers: 3 for memory policy, walker cost, and review.

### Milestone: M5 pair-local endpoint interpolation

- Depends on: M2 -- interpolation feeds agreement/promotion.
- Deliverables: endpoint-only scene conversion, chord-ended interpolation, and
  one direction-parameterized propagator.
- Workstreams: WS-INTERPOLATION.
- Entry criteria: M2 behavior passes.
- Exit criteria:
  - Editing any third seed cannot change an interval's endpoint-only geometry.
  - Both Hermite ends use the interval chord derived from the two human seed
    boxes.
  - FWD/BWD propagation tests cover both directions through the unified implementation.
- Parallel-plan ready: no -- the endpoint interpolation is one geometry path.

### Milestone: M6 interaction shell

- Depends on: none.
- Deliverables: persistent session, worker-owned frame/heat access, keymap, stable render state,
  GUI feedback, and typed prediction payloads.
- Workstreams: WS-UI.
- Entry criteria: none.
- Exit criteria:
  - A repository-local offscreen Qt test performs Seed -> Target -> Edit -> Seed in one session,
    commits a seed through the real controller path, and proves departed controllers disconnect.
  - A fake-reader test proves every open/read/close happens on the worker, newest requests supersede
    stale work, and shutdown joins the worker.
  - No synchronous frame read or residual compute is reachable from a GUI callback.
  - Generated keybinding docs match the registry; a deliberate mismatch fails.
  - A small generated image advances without resetting pan/zoom, and user-facing status reaches the
    GUI presenter.
  - No heartbeat, startup-time, 4K-video, or manual-visual threshold is required.
- Parallel-plan ready: yes -- max parallel doers: 4 for lifecycle, worker access, keymap, and render.

### Milestone: M7 offline dolly path

- Depends on: M2 and M5 -- confidence and complete trajectories are inputs.
- Deliverables: whole-path position/log-size solve, containment integration, and automatic adoption
  decision.
- Workstreams: WS-DOLLY.
- Entry criteria: M2 and M5 behavior passes.
- Exit criteria:
  - Synthetic stationary, constant-velocity, acceleration, gap, and containment cases satisfy the
    solver objective and report convergence/fallback honestly.
  - Generated in-memory cases cover containment, convergence, and smooth fallback; the shipped
    configuration selects the chosen default without a media fixture or human decision gate.
  - Explicit `direct_center` and `smooth` configurations remain available, while a nonconverged
    dolly path falls back to `smooth` and reports that outcome.
  - Optional generated sample images may aid debugging, but exact pixels and manual review are not
    gates.
- Parallel-plan ready: no -- integration and adoption consume the solver result serially.

### Milestone: M8 ownership cleanup

- Depends on: M6 -- modes consume the persistent session boundary.
- Deliverables: one module per mode, thin CLI dispatch, and removal of dead code exposed by M1-M7.
- Workstreams: WS-OWN.
- Entry criteria: M6 behavior passes.
- Exit criteria:
  - Direct parser/dispatcher tests route representative commands to the expected mode functions.
  - Help renders and names supported modes; exact help bytes are not compared.
  - Removed symbols have no live caller, and focused import/lint tests pass.
- Parallel-plan ready: no -- integration owns the shared CLI boundary.

## Workstream breakdown

### Workstream: WS-TRUTH trajectory contracts

- Goal: finish M1 and M2 without corpus attribution requirements.
- Owner: `expert_coder`.
- Work packages: WP-T1, WP-T3, WP-T4.
- Interfaces:
  - Needs: inline seeds and synthetic FWD/BWD paths.
  - Provides: anchored geometry and confidence values to M3, M5, and M7.
- Review boundary: `reviewer` checks C3, C9, and single ownership.

### Workstream: WS-BLEND commitment

- Goal: finish M3 on constructed ground truth and transition properties.
- Owner: `expert_coder`.
- Work packages: WP-T2.
- Interfaces:
  - Needs: confidence owner and deterministic residual fields.
  - Provides: continuous committed paths and debug payloads.
- Review boundary: `reviewer` checks FWD/BWD independence and transition-only changes.

### Workstream: WS-MEM memory and walker cost

- Goal: finish M4 without private RSS or wall-time gates.
- Owner: `expert_coder`.
- Work packages: WP-M1 through WP-M5.
- Interfaces:
  - Needs: frame geometry and configured cache caps.
  - Provides: explicit memory terms and worker count to solve dispatch.
- Review boundary: `architect` checks the budget terms; `reviewer` checks candidate-path semantics.

### Workstream: WS-INTERPOLATION pair-local endpoint interpolation

- Goal: keep Hermite interpolation within its two endpoint seed boxes.
- Owner: `expert_coder`.
- Work packages: WP-N1 through WP-N4.
- Interfaces:
  - Needs: the two endpoint seeds and scene transform.
  - Provides: pair-local Hermite input to interval workers.
- Review boundary: `architect` checks that no inferred derivative state exists.

### Workstream: WS-UI interaction shell

- Goal: finish M6 with deterministic offscreen Qt behavior tests.
- Owner: `expert_coder`.
- Work packages: WP-U1 through WP-U7 and WP-T5.
- Interfaces:
  - Needs: fake reader frames and typed SOURCE boxes.
  - Provides: persistent session to M8.
- Review boundary: `reviewer` checks thread ownership, disconnect, and render state.

### Workstream: WS-DOLLY crop path

- Goal: finish M7 and apply the adoption rule without a human decision.
- Owner: `expert_coder`.
- Work packages: WP-D1 through WP-D3.
- Interfaces:
  - Needs: inline synthetic trajectories.
  - Provides: chosen crop default and provenance.
- Review boundary: `reviewer` checks convergence, containment, lag, acceleration, and rule execution.

### Workstream: WS-OWN integration

- Goal: finish M8 and close the plan.
- Owner: `integrator`.
- Work packages: WP-O1, WP-O2, WP-CLOSE.
- Interfaces:
  - Needs: completed M1-M7 behavior.
  - Provides: final focused validation and documentation.
- Review boundary: independent `reviewer` checks only plan-owned behavior and touched files.

## Work packages

| ID | Owner | Depends on | Acceptance criterion |
| --- | --- | --- | --- |
| WP-T1 | coder | none | C3 exact seed geometry passes inline Hermite/walker/cache cases |
| WP-T3 | expert_coder | WP-T1 | one confidence/agreement owner passes synthetic geometry cases |
| WP-T4 | coder | WP-T3 | every consumer uses the owner; no duplicate definition remains |
| WP-T2 | expert_coder | WP-T3 | known-runner and transition-property tests pass |
| WP-M1 | expert_coder | none | every pre-pass allocation owned by the solver has an enforced byte cap |
| WP-M2 | tester | WP-M1 | pure byte-accounting/allocation harness covers bin/geometry variation |
| WP-M3 | expert_coder | none | memoized cost evaluation preserves selected candidate paths on generated lattices |
| WP-M4 | coder | WP-M3 | malformed walker geometry fails loudly |
| WP-M5 | coder | WP-M2 | worker-count policy obeys explicit available/parent/worker/reserve terms |
| WP-N1 | expert_coder | WP-T3 | endpoint-only geometry ignores every third seed |
| WP-N2 | expert_coder | WP-N1 | both Hermite ends use the endpoint chord |
| WP-N3 | expert_coder | WP-N2 | no inferred derivative state reaches worker dispatch |
| WP-N4 | coder | WP-N3 | one bidirectional propagator covers FWD and BWD semantics |
| WP-U1 | expert_coder | none | one session owns state/controller lifetime |
| WP-U2 | coder | WP-U1 | public seed factory handles every commit status |
| WP-U3 | expert_coder | WP-U1 | fake reader proves worker-only lifecycle and newest-only delivery |
| WP-U4 | expert_coder | WP-U3 | heat work is off-thread and stale results are ignored |
| WP-U5 | coder | WP-U1 | keymap drives dispatch, hints, help, and docs |
| WP-U6 | coder | WP-U1 | frame updates preserve pan/zoom on generated images |
| WP-U7 | coder | WP-U1 | annotation feedback reaches GUI status |
| WP-T5 | coder | WP-U1 | typed SOURCE boxes cross the prediction/UI boundary without schema change |
| WP-D1 | expert_coder | WP-T3, WP-N2 | pure whole-path solve passes synthetic objective cases |
| WP-D2 | expert_coder | WP-D1 | containment, convergence, and fallback behavior pass |
| WP-D3 | tester | WP-D2 | default dispatch, containment, convergence, and fallback pass portable behavior tests |
| WP-O1 | coder | WP-U1 | mode bodies live under `track_runner/modes/`; parser dispatch stays correct |
| WP-O2 | coder | WP-O1 | plan-exposed dead symbols have no callers |
| WP-CLOSE | integrator | all required packages | focused integration, hygiene, docs, and handoff are current |

Every implementation package includes its focused tests. Every output-changing package receives a
fresh independent agent review. The owner continues through the obvious follow-on in the table and
does not pause for a human decision.

## Acceptance criteria and gates

- Per-package gate: focused deterministic tests for changed behavior pass.
- Contract gate: C1, C2, C3, C6, C9, C10, and C13 tests relevant to the package pass.
- Integration gate: the plan-owned portable integration set passes from a normal repository clone.
- Hygiene gate: changed Python files pass typing, pyflakes, ASCII, and indentation checks; changed
  Markdown passes the link checker.
- Global suite policy: the completed portable suite passes. Collection size is
  diagnostic, not an acceptance requirement. The user-promoted source-file
  ownership maintenance work cleared the exclusive
  1,000-line limit without reopening a completed product milestone.
- Review gate: a fresh subagent independently reviews each production behavior change. Review is
  agent-owned and never waits for human approval.
- VCS policy: no staging, commit, or Git-index state is part of any gate.

## Test and verification strategy

Permanent tests follow [PYTEST_STYLE.md](../PYTEST_STYLE.md): fast, deterministic, offline,
and self-contained.

Use these input shapes:

- Inline seed/path dictionaries for C3, confidence, propagation, and failure cases.
- Generated numeric trajectories for dolly math.
- Deterministic residual arrays and fake readers for blend commitment and UI decode.
- `tmp_path` images/config files for loader, renderer, and generated-doc behavior.
- A generated numeric crop trajectory for the three-mode adoption rule.

Do not use these as required verification:

- External videos or mounted configuration trees.
- Historical source snapshots that no normal clone contains.
- Manual clicks, manual visual inspection, or user approval.
- Sleeps, wall-clock thresholds, exact screenshot pixels, or byte-equal output files.
- Collection counts or unrelated global hygiene failures.

Optional production telemetry may report RSS, cache misses, decode reads, and wall time during normal
use. It helps future tuning but never blocks this plan.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Synthetic case does not exercise production code | False confidence | Test reimplements algorithm | tester | Inject only inputs; call production owner/evaluator directly |
| Memory formula omits an owned allocation | OOM can return | Allocation lacks cap/accounting | architect | Inventory code-owned buffers and test every term |
| Optimization changes walker semantics | Tracking changes unexpectedly | Generated lattice selects different path | expert_coder | Drop or widen memo key; keep recurrence unchanged |
| Qt worker race survives simple sequence | Wrong frame or teardown crash | Stale result reaches inactive controller | expert_coder | Deterministic interleaving and disconnect tests |
| Dolly behavior regresses | Poor crop path | Generated containment or fallback case fails | tester | Keep generated containment, convergence, and fallback cases alongside explicit-mode dispatch |
| Plan drifts into Stage 1 or private media | Lost time and unrelated risk | Proposed task needs camera-motion/video files | manager | Reject task and return to repository-owned plan package |
| Global hygiene debt hides a real regression | Broken touched behavior ships | Full suite fails | integrator | Classify each failure by touched path and behavior; block only related failures |

## Documentation close-out requirements

- Keep this plan's current-state table and package status current.
- Record behavior/interface changes and important rejected approaches in
  [CHANGELOG.md](../CHANGELOG.md).
- Keep [progress_handoff_2026-08-20.md](../active_plans/reports/progress_handoff_2026-08-20.md) concise and
  restartable.
- Keep local/private historical reports labeled optional and non-gating.
- Leave all files unstaged. Plan completion means the working implementation and documentation are
  ready for the user's normal Git workflow, not that the index changed.

## Patch plan and reporting format

- Patch 1: M1-M3 trajectory truth validation and any focused corrections.
- Patch 2: M4 budget-based worker selection and walker-memory validation.
- Patch 3: M5 pair-local endpoint interpolation validation.
- Patch 4: M6/M8 portable UI and dispatch validation.
- Patch 5: M7 automatic crop-default decision.
- Patch 6: integration, focused hygiene, changelog, and durable handoff.

Each report states: files changed, behavior completed, focused tests run, related failures, reviewer
result, and the next dependency. It does not require a local media receipt or Git staging state.

## Resolved decisions

- Stage 1 is outside this plan; the reverted experiment stays reverted.
- Jason is permanently excluded.
- Private-video E2Es are not permanent validation.
- Repository-owned behavior tests, not local corpus receipts, close milestones.
- Exact equality is used only where exactness is an explicit contract.
- Wall-clock numbers are diagnostics unless a requirement derives the threshold.
- The crop default is selected by WP-D3's automated rule; no human adoption gate remains.
- The user promoted source-file ownership from non-goal debt to active
  maintainability work; it is complete without reopening product milestones.
- Git staging and commits are outside plan completion.

## Completion status

All eight milestones are accepted through repository-local implementation and independent review.
The seven original product outcomes are complete; the separate typing maintenance is recorded below
but is not a product-milestone dependency. The plan remains in `active/` for the user's normal
review and Git workflow; it is neither archived nor staged here.

The completed source-file ownership refactor separates interval
progress/analysis/seed anchoring, residual frames/blob traces, walker
engine/observer/summary, torso-box I/O, crop math/direct/controller, Encode
reports/audio/pool control, UI heat/edit/status support, mode video/seed
support, and walk-report tools. Every worktree Python file is below the
exclusive 1,000-line limit; a direct `rg`/`wc` scan finds the largest at 994
lines. The structural splits make no output or performance claim.
`camera_motion_artifact` owns artifacts/cache only, so Stage 1 estimation
algorithms remain unchanged. Review corrected canonical approximate seed
eligibility with a real NPZ identity round-trip and made
parallel Encode quit terminate and reap its pool. The portable suite passes
without depending on a collection count.
Refreshed Graphify reports 2,306 nodes, 3,450 edges, and 164 communities, and
benchmarks a 44.6x average token reduction. Stage 1 estimation algorithms are
unchanged; Jason, private-video, manual, and staging gates were not used.
