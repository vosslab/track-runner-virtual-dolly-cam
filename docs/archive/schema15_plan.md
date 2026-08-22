# Plan: Pair-local solve correction and schema 15 persistence cleanup

## Context

An independent audit of the pair-local ("Hermite") interval solve found that the
interpolator itself is sound while the apparatus around it has drifted. Four
defects are provable by reading the code, and one design question is empirical.

The interpolator calls `hermite_interpolate` with `m0 == m1 == p1 - p0`, which
collapses the cubic exactly to `p0 + t*(p1-p0)`. FWD and BWD run that same
computation on the same inputs, so their geometry is bit-identical on every
Stage-3 interval. `trajectory_confidence.frame_confidence` reads `cx`, `cy`, and
`w`, so `agreement` is exactly 1.0 there, the blend is a no-op, and
`velocity_consistency` sits near 1.0 by construction.

That matters because of what the promotion gate does. Per
`docs/TRACK_RUNNER_DESIGN.md`, the walker is the trusted, more-accurate solver.
Hermite runs first because it is cheap, and Stage 4 spends expensive walker CPU on
the intervals where the cheap pass looks uncertain. **The gate is a
cost-allocation mechanism.** A degenerate uncertainty signal therefore misdirects
the whole expensive-compute budget. Today promotion fires only through the
demotion rules: interval duration, `motion_quality`, `occlusion_fraction`, and a
`size_consistency` metric that compares SCENE-unit values against PIXEL-unit
values.

The same degeneracy inflates the persisted artifact. `torso_box_coords.npz` holds
FWD, BWD, and blended paths per interval; on un-promoted intervals all three are
the same array, and their one functional consumer reads them back to derive a
single float per frame.

The user has approved a `SCHEMA_VERSION` bump, a forced re-solve, and Stage-1
camera-motion computation across all twelve seeded videos. This plan turns the
audit into ordered, one-owner work that a manager and subagents complete end to end
without waiting on a person.

## Objectives

- Replace the degenerate Stage-3 uncertainty signal with a measured geometric-risk
  signal, so Stage-4 walker cost lands on intervals that are actually risky.
- Correct the coordinate space in `size_consistency` so the metric measures
  size-model residual and stays invariant to camera zoom.
- Give every operational mode an in-memory route to its ranking data so
  `interval_scores.json` becomes advisory for `target`, `refine`, and `encode`.
- Land every persistence change in one `SCHEMA_VERSION` bump so the corpus
  re-solves once.
- Preserve the pair-local interpolator's numeric behavior while making its name
  and documentation honest.
- Resolve every branch, threshold, and tolerance through mechanical rules recorded
  ahead of execution, so the plan runs unattended at bounded cost.

## Design philosophy

The central trade-off: this plan invests in **honesty of the surrounding
apparatus** rather than sophistication of the interpolator. The tempting
alternative -- neighbor-seed tangents (Catmull-Rom), circular-arc fitting, or
lap-cyclical priors to model the runner's curved path -- is set aside because it
buys an unmeasured gain and costs the endpoint-only fingerprint that makes cache
reuse and contract C6 interval independence work.

This follows "fix the design, not the symptom" and "use the scientific method"
from `docs/REPO_STYLE.md`. Measurement comes first, and the promotion threshold
falls out of a mechanical rule applied to a measured curve.

A second principle governs the persistence work: **preserve existing contracts and
existing owners.** `trajectory_confidence.py` already declares itself "the single
owner of FWD/BWD geometry confidence", `scoring.py` owns interval scores and
failure reasons, `torso_box_coords_io.py` owns the NPZ, `state_io.py` owns the
scores JSON, `tr_schema.py` owns the version, and
`camera_motion.precompute_camera_motion` owns Stage 1. Every work package
consolidates inside those owners and calls them rather than recomputing what they
produce.

**Milestone philosophy.** Each milestone produces a complete artifact or behavior
that can be independently dispatched, tested, reviewed, and resumed. All branch
decisions resolve through pre-registered rules, and every milestone completes
through automated checks.

**Completeness rule.** Every scope question is decided in-scope with a specified
artifact, or out-of-scope with the reason this version succeeds without it. Every
threshold and tolerance is a literal value or a formula over data already on disk.

**Cost philosophy.** Binding gates run on generated fixtures and a small
acceptance tier chosen for coverage per minute. The full corpus supplies
best-effort regression evidence.

## Scope

- Compute Stage-1 camera motion for every seeded stem that lacks it, and run a
  sagitta measurement across all twelve.
- Apply the pre-registered rules to produce a complete machine-executable
  promotion policy, including budget floor and packing rules.
- Build a fixture with deterministic hard segments and capture crop baselines
  against the plan-start commit, before any production code changes.
- Collapse the dead cubic scaffolding in `velocity_model.py`; retire
  `propagator_path`.
- Compute `size_consistency` entirely in scene space against the producer's
  log-linear expectation, holding the existing score mapping steady.
- Rebuild the promotion decision on retained signals plus the selected risk term,
  allocated against a measured per-video walker-frame budget.
- Add a ranking view that assembles values from the scoring and promotion owners,
  and route `review.py` and `modes/target.py` through it.
- Set `SCHEMA_VERSION` to 15 and rewrite the `torso_box_coords.npz` layout to hold
  per-interval `conf` and conditional FWD/BWD.
- Reduce `video_identity` to the fields that gate correctness.
- Make the solve artifact the race-start source for every consumer.
- Base `refine` reuse identity on manifest-fingerprint membership.
- Prove `target`, `refine`, and `encode` run without `interval_scores.json`, and
  that `analyze` reports its absence clearly, on both the bin=1 and bin=2 paths.
- Re-solve the corpus, gate on the acceptance tier, and classify extended-tier
  outcomes into a filed record.
- Compare allocation and encode output; synchronize docs; archive the plan.

## Non-goals

Each item is decided out of scope, with the reason this version succeeds without
it.

- **Interpolator redesign** (neighbor-seed tangents, arc fitting, cyclical
  priors). Succeeds without it because M1 measures whether chord-cut error is
  material at all; adding an unmeasured curve model first would be the overfitting
  this plan exists to avoid, and every variant makes an interval depend on more
  than its two endpoint seeds, which costs the fingerprint that C6 reuse needs.
- **Making `analyze` run without `interval_scores.json`.** Succeeds without it
  because `analyze` is a reporting mode whose product *is* the diagnostics; the
  objective is that operational modes stop depending on the file. WP-18 asserts
  `analyze` reports the absence clearly instead.
- **Walker bootstrap-stall root cause** behind the `post_seed_accepted == 0`
  fallback. Succeeds without it because that fallback already bounds the worst
  symptom to "no worse than Hermite", and this plan changes which intervals reach
  the walker rather than how the walker walks.
- **Fixing the Jason-3200m end-of-video condition.** Succeeds without it because
  the condition predates this plan and reproduces on v10 artifacts, so it cannot
  validate or invalidate any change here. WP-20 classifies and files it.
- **The Branch C camera investigation.** Succeeds without it because Branch C is a
  measurement outcome, not a blocker: the allocation policy ships fully specified
  under every branch.
- **A contract clause for "diagnostics stay advisory".** Succeeds without it
  because the deliverable is code that satisfies the principle; a clause adds no
  behavior, and `docs/TRACK_RUNNER_CONTRACT.md` reserves new clauses for separate
  user approval.
- **The `solve --upgrade` second pass in `re-solve.sh`.** Succeeds without it
  because M13 runs a full fresh solve, so the upgrade pass has nothing to upgrade.
- **Walker cost weights** in `track_runner/blob_walk/walk_viterbi.py`. Succeeds
  without touching them because they are human-approved constants and this plan
  changes allocation, not walk quality.
- **Artifact conversion from v10.** Succeeds without it because C10 states readers
  accept only what this repository produces; v10 fails loud and regenerates.

## Current state summary

Proven by reading, with corpus support from the twelve `tr_config/` artifacts:

| Defect | Location | Evidence |
| --- | --- | --- |
| Cubic collapses to linear | `velocity_model.py` `_compute_raw_pred` | `m0 == m1 == p1-p0`; algebraic identity |
| FWD and BWD geometry identical at Stage 3 | `velocity_model.py`, `solve_queue.py:798,815` | `conf` is the only difference; `frame_confidence` reads geometry |
| Promotion allocates walker cost on a degenerate signal | `scoring.py`, `interval_solver.select_promoted_intervals` | base tier always `high`; demotion rules are the only promotion path |
| `size_consistency` mixes coordinate spaces | `scoring.py` `score_interval_analytical` | `expected_h` SCENE, `actual_h` PIXEL; corpus Hermite minima cluster at 0.89-0.97 |
| Store-eight-derive-one | `torso_box_coords_io.py`, `trajectory_confidence.py` | 8 of 12 arrays per interval exist to recompute one float per frame |
| Fragile identity fields persisted | `tr_video_identity.py` `_INFORMATIONAL_RULES` | `basename` and `size_bytes` are C13's own named anti-examples |
| Diagnostics artifact is operationally load-bearing | `modes/target.py`, `modes/refine.py:61-76` | `target` requires it; refine builds `scored_keys` from it and drifts toward full re-solve when it is absent |
| Dead fields | `state_io.py:669,686,687`, `interval_analytical.py:242` | written, read nowhere |

### Corpus tiers

Twelve `tr_config/*.seeds.json` stems define the usable corpus; each resolves to a
`TRACK_VIDEOS/${stem}.mkv` through the repo-root symlink. Default bin factor is
`max(1, floor(width / 1440))` per
`common_tools/frame_reader.select_default_bin_factor`. Six stems already carry a
`camera_motion.npz`; the user has approved computing the other six.

| Stem | Resolution / fps | Frames | Intervals | Default bin | Motion cached | Tier |
| --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 1280x720 @30 | 4223 | 1579 | 1 | no | acceptance |
| IMG_3823 | 1280x720 @30 | 4084 | 713 | 1 | no | acceptance |
| Hononega-Orion_600m-IMG_3702 | 2816x1584 @60 | 5536 | 561 | 1 | yes | acceptance |
| IMG_3627 | 2816x1584 @60 | 8230 | 84 | 1 | yes | extended |
| IMG_3839 | 2816x1584 @60 | 8550 | 22 | 1 | yes | extended |
| Lyra-Hersey-800m-IMG_3882 | 2816x1584 @60 | 13774 | 536 | 1 | no | extended |
| Conant-4x400-2026_April_15 | 2816x1584 @60 | 14464 | 361 | 1 | no | extended |
| Hononega-Varsity_4x400m-IMG_3707 | 2816x1584 @60 | 15173 | 975 | 1 | yes | extended |
| Hononega-Orion-1600m-IMG_3629 | 2816x1584 @60 | 17414 | 413 | 1 | yes | extended |
| 2025-Glenbrook_South-1600m-IMG_1503 | 2816x1584 @60 | 17541 | 69 | 1 | yes | extended |
| Lyra-Wheeling-IMG_3912 | 3840x2160 @119.9 | 27348 | 309 | 2 | no | extended |
| Jason-3200m-sectionals-IMG_4005 | 2816x1584 @60 | 36044 | 634 | 1 | no | extended |

- **Measurement corpus**: all twelve, with camera motion computed where absent
  (user-approved). A stem whose motion computation raises is reported and excluded
  from the pooled statistics; the remaining stems carry M2.
- **Acceptance tier**: IMG_3830, IMG_3823, Hononega-Orion_600m-IMG_3702 -- roughly
  13.8k frames. The two 720p30 videos are the cheapest available and carry the
  densest seed sets (1579 and 713 intervals). Hononega-Orion_600m is the shortest
  2816x1584 case with motion already cached. Binding corpus gates run here.
- **bin=2 coverage**: every corpus stem except Lyra-Wheeling defaults to bin 1, and
  Lyra-Wheeling is the most expensive stem. The M3 fixture therefore includes a
  2880x1620 variant (`select_default_bin_factor(2880) == 2`) so both PROCESSED
  paths carry an automated gate at fixture cost.
- **Extended tier**: the remaining nine, best-effort with filed classifications.
  Lyra-Wheeling and Jason-3200m (with the **known end-of-video condition reported
  by a previous manager**) are the expensive and known-risk cases.

### Environment facts

- `TRACK_VIDEOS` and `tr_config` in the repo root are symlinks into `~/Documents/`.
- `re-solve.sh` implements the corpus loop: derive each stem from
  `tr_config/*.seeds.json`, resolve `TRACK_VIDEOS/${stem}.mkv`, skip a missing
  file, then run `prepare` and `solve --yes --bin 1`, followed by a
  `solve --upgrade` pass. M13 reuses the loop with a stem filter, default binning
  in place of `--bin 1`, and no upgrade pass.
- `tests/e2e/` is absent today. `tests/conftest.py:35` already declares
  `collect_ignore = ["e2e", "playwright"]`, so creating it keeps the fast lane clean.
- `tests/solver/test_tr_solver_integration.py` provides
  `_make_synthetic_motion_track`, `_make_seeds_linear_motion`, `_DummyReader`, and
  `_StubReader`.
- `tr_schema.py` records that artifacts stamped v11-v14 exist in the wild
  (method-only bumps since rolled back). Version 15 is the next clean value.
- `write_torso_box_coords` already omits both FWD and BWD for the pre-race case,
  with all-or-neither validation.
- Solve runs in PROCESSED space and converts to SOURCE exactly once, at the storage
  boundary immediately before `write_torso_box_coords`.

## Architecture boundaries and ownership

- **Interpolator** (`velocity_model.py`): pair-local endpoint geometry.
- **Scoring and promotion** (`scoring.py`, `trajectory_confidence.py`,
  `interval_solver.select_promoted_intervals`): the sole producers of interval
  scores, failure reasons, `conf`, and the promotion set. `review.py` assembles
  views from them.
- **Camera motion** (`camera_motion.py`, `camera_motion_artifact.py`): Stage-1
  owner, computes and caches on a miss.
- **Artifact format** (`tr_schema.py`, `torso_box_coords_io.py`): sole owner of the
  on-disk solve product and its version.
- **Identity** (`tr_video_identity.py`): sole owner of the metadata sanity block.
- **Diagnostics and race start** (`state_io.py`, `race_start.py`, `solve_queue.py`,
  `modes/*`): producer and consumers of the race-start reference.
- **Measurement and acceptance** (`tests/e2e/`): read-only instruments and
  generated-fixture harnesses.

### Mapping (milestones / workstreams -> components / patches)

| Milestone / Workstream | Component | Review boundary |
| --- | --- | --- |
| M1 / WS-MEASURE | `tests/e2e/` | Patch 1 |
| M2 / WS-POLICY | `docs/active_plans/decisions/` | Patch 2 |
| M3 / WS-BASELINE | `tests/e2e/` | Patch 3 |
| M4 / WS-INTERP | `velocity_model.py`, `interval_analytical.py` | Patch 4 |
| M5 / WS-SCORE | `scoring.py` | Patch 5 |
| M6 / WS-GATE | `scoring.py`, `interval_solver.py` | Patch 6 |
| M7 / WS-RANK | `review.py`, `modes/target.py` | Patch 7 |
| M8 / WS-FORMAT | `tr_schema.py`, `torso_box_coords_io.py`, `trajectory_confidence.py` | Patch 8 |
| M9 / WS-IDENTITY | `tr_video_identity.py` | Patch 9 |
| M10 / WS-RACESTART | `state_io.py`, `race_start.py`, `solve_queue.py`, `modes/*` | Patch 10 |
| M11 / WS-REUSE | `modes/refine.py` | Patch 11 |
| M12 / WS-HARNESS | `tests/e2e/` | Patch 12 |
| M13 / WS-RESOLVE | `tr_config/` artifacts, `re-solve.sh` | Patch 13 |
| M14 / WS-ALLOC | `docs/active_plans/reports/` | Patch 14 |
| M15 / WS-ENCODE | `docs/active_plans/reports/` | Patch 15 |
| M16 / WS-DOCS | `docs/`, `docs/archive/` | Patch 16 |

## Milestone plan

| M | Title | Summary | Goal |
| --- | --- | --- | --- |
| M1 | Measure pair-local error | Sagitta instrument across twelve stems, motion computed where absent | Quantify chord error in torso units |
| M2 | Select promotion policy | Apply pre-registered rules; record branch, threshold, budget rules | Produce literal policy values |
| M3 | Build fixture and capture baselines | Hard-segment fixture at two resolutions, baselines at plan-start commit | Establish reproducible tolerances before code changes |
| M4 | Simplify interpolation | Linear form, honest names, `propagator_path` retired | Implementation matches actual behavior |
| M5 | Correct size scoring | `size_consistency` coordinate space | Restore a trustworthy retained signal |
| M6 | Rebuild promotion logic | Retire the fake base tier, implement the M2 policy | Allocate walker CPU from meaningful signals |
| M7 | Rebuild review and target ranking | Assemble a ranking view from the scoring owners | One risk model across prioritization |
| M8 | Establish schema 15 | Version bump, NPZ layout, stored `conf` | Authoritative v15 solve artifact |
| M9 | Simplify identity | Trim `video_identity` | Remove fragile persistence bookkeeping |
| M10 | Relocate race start | Solve artifact owns race start | Remove the diagnostics race-start dependency |
| M11 | Decouple refine reuse | Manifest fingerprints as sole reuse authority | Remove the final refine dependency |
| M12 | Prove diagnostics optional | Fixture cycles at bin 1 and bin 2 | Verify the cleanup end to end |
| M13 | Re-solve corpus | Acceptance tier gated, extended tier classified | Produce the v15 corpus |
| M14 | Evaluate allocation | Compare promotion changes and runtime | Show where walker cost moved |
| M15 | Evaluate encode regression | Crop metrics plus visual comparison | Confirm output quality |
| M16 | Documentation and close-out | Synchronize docs, archive the plan | Docs match code; plan closed |

### Milestone: M1 measure pair-local error

- Depends on: none.
- Precondition check (a step inside M1): assert each of the twelve
  `tr_config/*.seeds.json` stems resolves to a readable `TRACK_VIDEOS/${stem}.mkv`.
- Deliverables: `tests/e2e/e2e_pair_local_sagitta.py`;
  `docs/active_plans/audits/pair_local_sagitta_audit.md`; `camera_motion.npz` for
  the six stems that lack one.
- Done checks: the instrument covers every stem whose motion artifact exists or
  computes successfully, and names any stem it excluded with the raised error; a
  triple with M at `t == 0` or `t == 1` reports `error_widths == 0`; the audit
  reports per-bin `n`, p50, p90, and max plus the pooled interquartile range, with
  a per-resolution-class breakdown.
- Entry criteria: none.
- Exit criteria: the measured distributions exist as a published artifact.
- Parallel-plan ready: yes -- motion computation and per-video measurement are
  independent across stems.

### Milestone: M2 select promotion policy

- Depends on: M1.
- Deliverables: `docs/active_plans/decisions/pair_local_promotion_gate.md`,
  carrying the selected branch, the numeric threshold, the per-video walker-frame
  budget, the packing rule, and the outlier classification table.
- Done checks: every value M6 needs is a literal number or a stated rule; a
  `reviewer` subagent re-runs the mechanical rules against the M1 audit and reaches
  the same branch, threshold, and budgets.
- Entry criteria: the rules below are recorded ahead of the M1 instrument run.
- Exit criteria: M6 holds a complete machine-executable instruction.
- Parallel-plan ready: no -- one decision.

#### Pre-registered rules

**Seed eligibility.** Triples draw on `visible` and `partial` seeds. An
`approximate` seed marks a deliberately larger uncertain region rather than a
precise torso box, so its width would distort both `error_widths` and
`chord_span_widths` through the denominator.

**Outlier policy.** Outliers stay in the selection. The ten largest-error triples
are classified mechanically: `annotation_suspect` when the middle seed's box area
deviates by more than 2x from the median area of its five nearest eligible seeds by
frame index, `geometry_suspect` otherwise. The classification is reported; the
branch rules read the full eligible set either way.

**Bins**, in `chord_span_widths` (scene-space chord length between the two
bracketing seeds, divided by the middle seed's torso width):

`[0,2)`, `[2,5)`, `[5,10)`, `[10,20)`, `[20,inf)`

"Typical seed spacing" is procedural: the bins covering the interquartile range of
`chord_span_widths` across the pooled corpus. Bins holding 30 or more triples are
**decision-bearing**; smaller bins appear as context.

**Branches**, evaluated in order:

1. **Branch C, chord model rejected as the primary explanation.** p90
   `error_widths` reaches 0.5 or above in the `[0,2)` bin. Short chords accumulate
   little sagitta, so error concentrated there points elsewhere. Action: M6 adopts
   the Branch A allocation policy, and the record states chord span is rejected as
   the promotion signal.
2. **Branch B, chord-driven.** p90 `error_widths` increases across every
   decision-bearing bin and reaches 1.0 or above in the highest one. **Threshold
   selection algorithm:** take the lowest decision-bearing bin whose p90
   `error_widths` reaches 1.0 or above; the threshold is that bin's lower edge.
   `chord_span_widths` joins the promotion terms at that value.
3. **Branch A, catch-all.** Every other outcome, including non-monotonic curves,
   thin bins, low-error results, and fewer than three decision-bearing bins.
   Action: retire the degenerate FWD/BWD-derived base tier for Hermite intervals,
   keep `motion_quality`, `occlusion_fraction`, corrected `size_consistency`, and
   interval duration as the tier basis, and allocate through the budget below.

**Walker-frame budget.** Allocation counts walker frames, since a long interval
costs proportionally more than a short one.

```
measured   = sum(end_frame - start_frame + 1)
             over intervals in that video's existing interval_scores.json
             whose interval_score.confidence_tier is "low" or "fair"
floor      = ceil(0.10 * post_race_frame_count)
budget     = max(measured, floor)
```

The 10% floor is the decided answer to two edge cases with one rule: a video whose
current degenerate policy promoted nothing has `measured == 0`, and a video with no
prior artifact has no `measured` at all. Both get a budget that lets the corrected
policy send a genuinely risky interval to the walker. Budgets are literal numbers
in the decision record.

**Packing rule.** Intervals are indivisible, so allocation is a deterministic
first-fit-decreasing walk: sort post-race intervals by risk descending, ties broken
by lower `start_frame`; promote an interval when its frame count fits the remaining
budget; skip it and continue otherwise; stop when no remaining interval fits.

**Budget assertion.** Promoted walker frames are **at most** the budget. Under-spend
is expected and correct with indivisible intervals, so the check is a one-sided cap
rather than a two-sided band. A second check states that when at least one post-race
interval both exceeds the promote floor and fits the budget, allocation promotes at
least one interval.

### Milestone: M3 build fixture and capture baselines

- Depends on: none. **Runs before any production code change**, so the baselines
  come from the plan-start commit.
- Deliverables: `tests/e2e/e2e_full_cycle_fixture.py` with its fixture generator
  and hard segments; recorded baseline constants.
- Done checks: both fixture resolutions generate deterministically from a fixed
  seed; a full cycle completes against unmodified code; baseline metrics are
  written into the harness as literal named constants.
- Entry criteria: none.
- Exit criteria: reproducible tolerances exist before M4 begins.
- Parallel-plan ready: yes -- the two resolutions are independent. Runs
  concurrently with M1 and M2.

### Milestone: M4 simplify interpolation

- Depends on: M3, so baselines predate the first production change.
- Deliverables: linear and log-linear forms in `velocity_model.py`; retired
  `propagator_path`.
- Done checks: `pytest tests/solver/` passes; output matches pre-change output
  within `1e-9` relative tolerance at every frame, exact at both endpoints;
  `grep -rn propagator_path track_runner/` comes back empty.
- Entry criteria: M3 baselines recorded.
- Exit criteria: naming matches computation.
- Parallel-plan ready: no -- two small serial packages.

### Milestone: M5 correct size scoring

- Depends on: M3.
- Deliverables: scene-space `size_consistency` with the existing mapping intact.
- Done checks: a focused test builds two `SceneTransform` instances with
  `cum_scale` 1.0 and 1.5 over identical geometry and shows agreement within 0.01.
- Entry criteria: M3 baselines recorded.
- Exit criteria: the metric is zoom-invariant.
- Parallel-plan ready: no -- one package. Runs concurrently with M4 and M9.

### Milestone: M6 rebuild promotion logic

- Depends on: M2 (policy values), M5 (trustworthy `size_consistency`).
- Deliverables: retained signals plus the branch-selected risk term; budget-capped
  first-fit-decreasing allocation; the recorded semantic split between promotion
  risk and `conf`.
- Done checks: focused tests cover promote, hold, pre-race (C4), the budget cap,
  the skip-and-continue packing path, and the zero-`measured` floor case.
- Entry criteria: M2 and M5 exit criteria met.
- Exit criteria: allocation reflects measured risk within budget.
- Parallel-plan ready: no -- single coherent policy change.

### Milestone: M7 rebuild review and target ranking

- Depends on: M6.
- Deliverables: `review.build_interval_risk_view`, assembling values from the
  scoring and promotion owners; `review.py` ranking and `modes/target.py` routed
  through it.
- Done checks: `target` produces the same ordering from the in-memory view as from
  an equivalent persisted score set on identical inputs; a guard test asserts the
  view delegates rather than recomputing.
- Entry criteria: M6 exit criteria met.
- Exit criteria: `target` reaches its ranking data through authoritative inputs.
- Parallel-plan ready: no -- one shared helper, two call sites.

### Milestone: M8 establish schema 15

- Depends on: M6 (final `conf` values), M9 (trimmed identity dict).
- Deliverables: `SCHEMA_VERSION = 15`; the v15 layout; `conf` read from storage.
- Done checks: round-trip tests pass; a v10 artifact raises the re-solve error;
  `conf` round-trips within 1/255.
- Entry criteria: M6 and M9 exit criteria met.
- Exit criteria: the v15 reader and writer API is available to M10 and M11.
- Parallel-plan ready: no -- one owner for the shared resource.

### Milestone: M9 simplify identity

- Depends on: M3.
- Deliverables: `video_identity` reduced to `width`, `height`, `frame_count`.
- Done checks: a resolution or frame-count mismatch blocks; a rename passes
  quietly.
- Entry criteria: M3 baselines recorded.
- Exit criteria: one blocking bucket.
- Parallel-plan ready: no -- one package. Runs concurrently with M4 and M5.

### Milestone: M10 relocate race start

- Depends on: M8.
- Deliverables: solve artifact as the single race-start source; retired
  `cyclical_prior`, `source_frame_indices`, `source_count`.
- Done checks: `target` and `encode` run with `interval_scores.json` absent;
  `grep -rn cyclical_prior track_runner/` comes back empty.
- Entry criteria: M8 exit criteria met.
- Exit criteria: one source per fact.
- Parallel-plan ready: yes -- relocation and dead-field retirement are separable.

### Milestone: M11 decouple refine reuse

- Depends on: M8.
- Deliverables: manifest-fingerprint reuse in `modes/refine.py`.
- Done checks: unchanged seeds stay quiet with no disk write; one new seed
  re-solves its two intervals; both hold with `interval_scores.json` absent; a
  refine that would become a full solve exits with a reason and preserves the
  existing artifact (C7).
- Entry criteria: M8 exit criteria met.
- Exit criteria: refine works from the solve artifact.
- Parallel-plan ready: no -- one package. Runs concurrently with M10.

### Milestone: M12 prove diagnostics optional

- Depends on: M3 (fixture), M7, M10, M11.
- Deliverables: the M3 harness extended with the diagnostics-absent cycles and the
  full metric assertions.
- Done checks: for each of the two fixture resolutions,
  `solve -> refine -> target -> encode` completes with `interval_scores.json`
  present and again with it absent; `analyze` on the absent case reports the
  missing diagnostics clearly; hard-segment ranking and budget assertions hold;
  every crop metric lands inside its recorded tolerance.
- Entry criteria: M7, M10, M11 exit criteria met.
- Exit criteria: the diagnostics-optional claim is machine-verified on both
  coordinate paths.
- Parallel-plan ready: yes -- the two resolutions are independent.

### Milestone: M13 re-solve corpus

- Depends on: M12.
- Deliverables: v15 artifacts and solve logs for the acceptance tier, then
  best-effort for the extended tier; a per-stem outcome table.
- Done checks: the acceptance tier -- IMG_3830, IMG_3823,
  Hononega-Orion_600m-IMG_3702 -- each produce a v15 artifact with a successful
  log. Extended stems run afterward, each outcome recorded as success or as a
  failure classified `plan_related` or `pre_existing`.
- Entry criteria: M12 green.
- Exit criteria: the acceptance tier is current at schema 15 and every extended
  stem has a filed outcome.
- Parallel-plan ready: yes -- stems are independent; acceptance tier first.

### Milestone: M14 evaluate allocation

- Depends on: M13.
- Deliverables: `docs/active_plans/reports/pair_local_allocation_report.md`.
- Done checks: per-stem promotion count, walker frames, and wall time before and
  after; the branch-appropriate comparison; promoted frames at or under budget for
  every stem.
- Entry criteria: M13 acceptance tier complete.
- Exit criteria: the allocation shift is documented with evidence.
- Parallel-plan ready: no -- one report.

### Milestone: M15 evaluate encode regression

- Depends on: M13.
- Deliverables: `docs/active_plans/reports/pair_local_encode_evaluation.md`.
- Done checks: crop metrics on re-solved acceptance videos land inside the M3
  tolerances; the `image_evaluator` comparison meets its numeric pass criterion.
- Entry criteria: M13 acceptance tier complete.
- Exit criteria: output quality is confirmed by measurement.
- Parallel-plan ready: yes -- runs concurrently with M14.

### Milestone: M16 documentation and close-out

- Depends on: M14, M15.
- Deliverables: v15 entry in `docs/TR_SCHEMA_VERSION_HISTORY.md`; corrected
  `docs/TRACK_RUNNER_DESIGN.md` including its dual-scoring philosophy section;
  corrected `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md`; `docs/CHANGELOG.md`; plan and
  records archived.
- Done checks: `pytest tests/test_markdown_links.py` and
  `tests/test_ascii_compliance.py` pass after the archive move.
- Entry criteria: M14 and M15 exit criteria met.
- Exit criteria: docs match code; the plan is closed with published evidence.
- Parallel-plan ready: no -- one owner keeps cross-references coherent.

## Workstream breakdown

| Workstream | Goal | Owner class | Work packages | Needs | Provides | Review boundary |
| --- | --- | --- | --- | --- | --- | --- |
| WS-MEASURE | Quantify chord error across twelve stems | `tester` | WP-1, WP-2 | seeds; motion computed where absent | Measured distributions | Patch 1 |
| WS-POLICY | Convert distributions into literal policy values | `reviewer` | WP-3 | M1 audit | Branch, threshold, budgets, packing rule | Patch 2 |
| WS-BASELINE | Fixture with hard segments; baselines at plan-start | `tester` | WP-4, WP-5 | none | Reproducible tolerances | Patch 3 |
| WS-INTERP | Name and structure match computation | `coder` | WP-6, WP-7 | M3 | Stable interpolator surface | Patch 4 |
| WS-SCORE | `size_consistency` in scene space | `coder` | WP-8 | M3 | Trustworthy retained signal | Patch 5 |
| WS-GATE | Allocate walker frames on real signals | `expert_coder` | WP-9 | Policy values; corrected `size_consistency` | Promotion set | Patch 6 + `reviewer` |
| WS-RANK | Ranking view assembled from the owners | `coder` | WP-10 | Promotion basis | `build_interval_risk_view` | Patch 7 |
| WS-FORMAT | Own the v15 solve product | `expert_coder` | WP-11, WP-12, WP-13 | Final `conf`; trimmed identity | v15 reader and writer API | Patch 8 + `reviewer` |
| WS-IDENTITY | Trim `video_identity` | `coder` | WP-14 | M3 | Trimmed identity dict | Patch 9 |
| WS-RACESTART | Solve product owns race start | `coder` | WP-15, WP-16 | v15 reader API | One source per fact | Patch 10 |
| WS-REUSE | Manifest-based refine reuse | `expert_coder` | WP-17 | v15 reader API | Refine on the solve artifact | Patch 11 + `reviewer` |
| WS-HARNESS | Diagnostics-absent cycles and assertions | `tester` | WP-18 | M3 fixture; M7, M10, M11 | Machine-verified acceptance | Patch 12 |
| WS-RESOLVE | Regenerate the corpus | `coder` | WP-19, WP-20 | M12 | v15 artifacts, outcome table | Patch 13 |
| WS-ALLOC | Document allocation shift | `coder` | WP-21 | v15 corpus | Allocation report | Patch 14 |
| WS-ENCODE | Confirm output quality | `coder` + `image_evaluator` | WP-22 | v15 corpus | Encode evaluation | Patch 15 |
| WS-DOCS | Synchronize docs, close the plan | `coder` | WP-23, WP-24 | M14, M15 | Synchronized docs, archive | Patch 16 |

## Work packages

### Work package: WP-1 build the sagitta instrument

- Owner: WS-MEASURE.
- Touch points: `tests/e2e/e2e_pair_local_sagitta.py` (new; create `tests/e2e/`).
- Depends on: none.
- Acceptance criteria: begin with the precondition check that each of the twelve
  `tr_config/*.seeds.json` stems resolves to a readable `TRACK_VIDEOS/${stem}.mkv`.
  For each consecutive eligible seed triple `(L, M, R)` after race start, project
  all three boxes with `SceneTransform.pixel_box_to_scene`, compute the chord
  prediction at M as `(1-t)*L_scene + t*R_scene` with `t = (f_M-f_L)/(f_R-f_L)`,
  and report `error_widths = hypot(pred - M_scene) / M_scene_w` alongside chord
  duration and `chord_span_widths`. Triples draw on `visible` and `partial` seeds.
  Express every spatial quantity in torso widths (C2). Read inputs through the
  existing owners: `state_io.load_seeds`,
  `camera_motion_artifact.load_motion_cache` falling through to
  `camera_motion.precompute_camera_motion` when the cache is absent (user-approved
  for all twelve), `scene_coords.SceneTransform`,
  `interval_fingerprint.filter_usable_seeds_sorted`, and
  `state_io.load_interval_scores` for race start. A stem whose motion computation
  raises is reported with its error and excluded from the pooled statistics.
  Follow `docs/PYTHON_STYLE.md`; run via
  `source source_me.sh && python3 tests/e2e/e2e_pair_local_sagitta.py`, with one
  optional `-o/--output` flag.
- Evidence or review: a triple with M at `t == 0` or `t == 1` reports
  `error_widths == 0`; one triple checked against an independently computed value.
- Obvious follow-ons: WP-2.

### Work package: WP-2 publish the measured distributions

- Owner: WS-MEASURE.
- Touch points: `docs/active_plans/audits/pair_local_sagitta_audit.md` (new).
- Depends on: WP-1.
- Acceptance criteria: report per-bin `n`, p50, p90, and max, plus the pooled
  `chord_span_widths` interquartile range, with a per-resolution-class breakdown so
  the 1280x720 and 2816x1584 populations stay visible. Reporting the distribution
  alongside p90 is required because C5 holds that torso boxes are correct-object
  with imprecise boundaries. List the ten largest-error triples with video and
  frame indices, each labeled `annotation_suspect` or `geometry_suspect`. Report
  the measured per-video walker-frame spend of the current promotion set and each
  video's post-race frame count, which are the two inputs to the budget formula.
  Name any stem excluded for a motion-computation error.
- Evidence or review: the audit contains every input the M2 rules consume.
- Obvious follow-ons: WP-3.

### Work package: WP-3 record the promotion policy

- Owner: WS-POLICY.
- Touch points: `docs/active_plans/decisions/pair_local_promotion_gate.md` (new).
- Depends on: WP-2.
- Acceptance criteria: apply the pre-registered ordered branch rules and record, as
  literal values: the selected branch, the Branch B threshold when it applies, the
  per-video budget from `max(measured, ceil(0.10 * post_race_frames))`, the
  first-fit-decreasing packing rule, and the one-sided budget cap. Every number M6
  needs appears here.
- Evidence or review: a second `reviewer` subagent re-applies the rules to the same
  audit and reaches the same branch, threshold, and budgets.
- Obvious follow-ons: unblocks WP-9.

### Work package: WP-4 build the hard-segment fixture

- Owner: WS-BASELINE.
- Touch points: `tests/e2e/e2e_full_cycle_fixture.py` (new).
- Depends on: none.
- Acceptance criteria: build two deterministic fixture videos from a fixed numpy
  seed, each 600 frames at 30 fps: **1280x720** for the bin=1 path and
  **2880x1620** for the bin=2 path. A uniformly smooth target with evenly spaced
  seeds would produce near-ideal analytical interpolation and never exercise
  promotion, so the fixture carries four labeled segments with seeds placed at
  segment boundaries:
  - **chord-risk**: a tight curved arc with widely spaced seeds, so the straight
    chord cuts the corner;
  - **low-motion-quality**: injected frame noise and a camera jump that depresses
    phase-correlation response;
  - **size-residual**: a rapid scale ramp so interpolated height diverges from the
    log-linear expectation;
  - **benign**: straight constant-velocity motion with closely spaced seeds.
  Record each segment's frame range as a module constant so assertions can name it.
- Evidence or review: both resolutions regenerate byte-identically from the seed
  across two runs.
- Obvious follow-ons: WP-5.

### Work package: WP-5 capture baselines at the plan-start commit

- Owner: WS-BASELINE.
- Touch points: `tests/e2e/e2e_full_cycle_fixture.py`.
- Depends on: WP-4.
- Acceptance criteria: run the harness against the **unmodified plan-start
  checkout**, before Patch 4 begins, and write the observed values into the
  harness as literal constants named `BASELINE_<METRIC>_<RES>`. Capturing here
  rather than at M12 is deliberate: by M12 the production code has already changed,
  so "pre-change" would no longer exist in the working tree. The harness drives the
  CLI modes, which exist unchanged at plan start, so one implementation serves both
  the baseline run and the post-change run. Metrics captured per resolution:
  crop-center max step, crop-center median step, crop direction-reversal count,
  crop-size step p95, promoted walker frames, and per-frame `conf`.
- Evidence or review: the constants are committed with a comment naming the commit
  hash they were captured from.
- Obvious follow-ons: none until WP-18.

### Work package: WP-6 collapse the cubic scaffolding

- Owner: WS-INTERP.
- Touch points: `track_runner/velocity_model.py`,
  `tests/solver/test_tr_velocity_model.py`.
- Depends on: WP-5.
- Acceptance criteria: express the interpolation directly as the linear and
  log-linear forms it already computes; the docstring and public names describe
  linear interpolation. A focused test compares output against pre-change output at
  every frame within `1e-9` relative tolerance, exact at both endpoints.
  Reassociating a cubic-basis evaluation into a direct linear expression moves the
  last floating-point bits, so a tight numeric tolerance is the right instrument.
- Evidence or review: `pytest tests/solver/` passes;
  `test_hermite_interpolation_preserves_endpoints` gains a name matching the module.
- Obvious follow-ons: WP-7.

### Work package: WP-7 retire propagator_path

- Owner: WS-INTERP.
- Touch points: `track_runner/interval_analytical.py`.
- Depends on: WP-6.
- Acceptance criteria: the result dict carries the fields consumers read.
- Evidence or review: `grep -rn propagator_path track_runner/` comes back empty.
- Obvious follow-ons: none.

### Work package: WP-8 compute size_consistency in scene space

- Owner: WS-SCORE.
- Touch points: `track_runner/scoring.py`.
- Depends on: WP-5.
- Acceptance criteria: hold the existing score mapping steady and change only its
  inputs. The mapping stays `size_consistency = max(0.0, 1.0 - mean(rel_error))`
  and the existing tier threshold at 0.5 keeps its meaning. Inputs become:
  - `expected_h` from the producer's log-linear expectation,
    `exp((1-t)*log(left_sh) + t*log(right_sh))`, in SCENE units;
  - `actual_h` converted to SCENE units for the same frame;
  - `rel_error = abs(actual_h - expected_h) / expected_h`, dimensionless.
  Solve keeps its single SOURCE/PROCESSED conversion at the storage boundary.
- Evidence or review: a focused test builds two `SceneTransform` instances with
  `cum_scale` 1.0 and 1.5 over identical geometry and shows agreement within 0.01.
- Obvious follow-ons: none.

### Work package: WP-9 rebuild the promotion decision

- Owner: WS-GATE.
- Touch points: `track_runner/scoring.py`,
  `track_runner/interval_solver.py` `select_promoted_intervals`.
- Depends on: WP-3, WP-8.
- Acceptance criteria:
  - The degenerate FWD/BWD-derived base tier is the piece being replaced.
    `motion_quality`, `occlusion_fraction`, corrected `size_consistency`, and
    interval duration carry forward.
  - Under Branch B the `chord_span_widths` term joins those signals at the recorded
    threshold. Under Branches A and C the retained signals rank alone.
  - Allocation is first-fit-decreasing by risk, ties broken by lower `start_frame`,
    skipping an interval that would exceed the remaining budget and continuing to
    the next, stopping when nothing fits.
  - Promoted walker frames are at most the recorded per-video budget, where
    `budget = max(measured, ceil(0.10 * post_race_frames))`.
  - Compute the risk term in memory each run from the two endpoint seeds plus
    camera motion (C12, C13), expressed in torso widths (C2).
  - Promotion applies to post-race intervals (C4). Refine stays scoped to
    `plan.pending_pair_indices` (C6, C7). Scoring reads the raw FWD/BWD paths (C9).
  - Record the semantic split in a module comment: promotion risk is model-risk and
    cost-allocation evidence about the cheap pass, while `conf` is directional-path
    agreement after whichever solver ran. On an un-promoted Stage-3 interval `conf`
    reads 1.0 because two identical analytical passes agree, so it is not
    independent validation of the cheap interpolation. This matters because `conf`
    reaches crop behavior at `tr_crop.py:308`.
- Evidence or review: focused tests cover promote, hold, pre-race, the budget cap,
  the skip-and-continue path, and a video with `measured == 0` promoting through
  the 10% floor; an independent `reviewer` confirms C2, C4, C6, C9, C12, C13.
- Obvious follow-ons: WP-10.

### Work package: WP-10 assemble the ranking view

- Owner: WS-RANK.
- Touch points: `track_runner/review.py`, `track_runner/modes/target.py`.
- Depends on: WP-9.
- Acceptance criteria: add
  `review.build_interval_risk_view(seeds, motion_track, solve_artifact)` returning
  `dict[tuple[int, int], dict]` keyed by `(start_frame, end_frame)`, each value
  carrying `risk` (float), `severity` (str), `promoted` (bool), and
  `failure_reasons` (`list[str]`).
  **The view assembles; it does not recompute.** Every value comes from calling the
  existing owners -- `scoring.score_interval_analytical` for scores and failure
  reasons, `review.classify_interval_severity` for severity, and the WP-9 promotion
  ranking for `risk` and `promoted`. Reimplementing any of those formulas inside
  `review.py` would create a second scoring engine, which is the architectural
  drift this plan removes. `review.py` ranking and `modes/target.py` seed placement
  both source from the view. `analyze` continues reading persisted diagnostics.
  No new persisted field appears.
- Evidence or review: a focused test shows `target` produces the same ordering from
  the in-memory view as from an equivalent persisted score set on identical inputs.
  A guard test asserts `review.py` contains no independent computation of
  `size_consistency`, `motion_quality`, or the promotion score, mirroring the AST
  guard style of `tests/test_pytest_hygiene.py`.
- Obvious follow-ons: none.

### Work package: WP-11 set SCHEMA_VERSION to 15

- Owner: WS-FORMAT.
- Touch points: `track_runner/tr_schema.py`.
- Depends on: WP-9.
- Acceptance criteria: `SCHEMA_VERSION = 15`; both `torso_box_coords` and
  `diagnostics` supported sets read `{15}`; the module comment records that
  rolled-back artifacts carry stamps 11-14, and that this bump is the explicitly
  human-approved persistence change under C10.
- Evidence or review: a v10 artifact raises the existing re-solve `RuntimeError`.
- Obvious follow-ons: WP-12.

### Work package: WP-12 write the v15 artifact layout

- Owner: WS-FORMAT.
- Touch points: `track_runner/torso_box_coords_io.py`.
- Depends on: WP-11, WP-14.
- Acceptance criteria: the layout is

  ```
  schema_version : 15
  manifest       : json [{fingerprint, array_index, start_frame, end_frame}, ...]
  video_identity : json {width, height, frame_count}
  race_start     : json {race_start_frame, race_start_interval, torso_w, torso_h,
                         scene_anchor_x, scene_anchor_y, method, warnings}
  solve_complete : bool
  i{idx}_blended_cx/cy/w/h : uint16  (always)
  i{idx}_conf              : uint8   (always, conf = v / 255)
  i{idx}_fwd_cx/cy/w/h     : uint16  (when a difference survives quantization)
  i{idx}_bwd_cx/cy/w/h     : uint16  (when a difference survives quantization)
  ```

  Omission records one fact: no difference survived uint16 quantization. Two float
  paths can differ and still snap to identical integers, so readers take pass
  agreement from the stored `conf` array, which measures it directly. State this in
  the writer comment and the schema-history entry. The condition widens the
  existing pre-race omit-both rule and reuses its all-or-neither validation.
- Evidence or review: a round-trip test writes one diverging and one
  quantization-equal interval, reads back, and shows the equal interval carries
  blended plus `conf` while the diverging one carries both directions; a
  half-present pair raises; `conf` round-trips within 1/255. Independent `reviewer`
  pass.
- Obvious follow-ons: WP-13.

### Work package: WP-13 read conf from storage

- Owner: WS-FORMAT.
- Touch points: `track_runner/trajectory_confidence.py`.
- Depends on: WP-12.
- Acceptance criteria: `derive_per_frame_confidence` reads the stored `conf` array.
  `trajectory_confidence.py` stays the sole producer and definition of `conf` --
  range `[0,1]`, meaning `exp(-center_separation_in_torso_widths)` -- with uint8
  serving as transport at serialization. Reading stored `conf` supersedes the older
  inference that treated absent FWD/BWD as pre-race and assigned 1.0; once omission
  widens to quantization-equal intervals, the stored array keeps ordinary intervals
  reporting their real confidence.
- Evidence or review: a focused test shows fresh and reloaded `conf` agree within
  1/255, and that a quantization-equal interval reports its stored `conf`.
- Obvious follow-ons: none.

### Work package: WP-14 trim video_identity

- Owner: WS-IDENTITY.
- Touch points: `track_runner/tr_video_identity.py`.
- Depends on: WP-5.
- Acceptance criteria: `make_video_identity` emits `width`, `height`, and
  `frame_count`. C13 names `basename` and `size_bytes` as its own fragile-value
  anti-examples, and `fps` and `duration_s` follow from `frame_count` plus probe,
  so all four retire. `_INFORMATIONAL_RULES` empties, the comparison collapses to
  one blocking bucket, and `summarize_mismatches` simplifies.
- Evidence or review: a focused test shows a resolution or frame-count mismatch
  blocks, and a rename passes quietly.
- Obvious follow-ons: none.

### Work package: WP-15 relocate race start

- Owner: WS-RACESTART.
- Touch points: `track_runner/state_io.py`, `race_start.py`, `solve_queue.py`,
  `interval_solver.py`, `modes/target.py`, `modes/refine.py`, `modes/encode.py`,
  `modes/edit.py`, `modes/shared.py`, `modes/predictions.py`.
- Depends on: WP-12.
- Acceptance criteria: the v15 solve artifact is the single race-start source from
  the first commit. The block carries `race_start_frame`, `race_start_interval`,
  `torso_w`, `torso_h`, `scene_anchor_x`, `scene_anchor_y`, `method`, and
  `warnings`: `solve_queue.py:425-428` needs the anchor and torso values for C4
  pre-race synthesis, `:619` needs the interval, `:456` needs the warnings. Missing
  race start fails loud with a reason.
- Evidence or review: `target` and `encode` run with `interval_scores.json` absent.
- Obvious follow-ons: WP-16.

### Work package: WP-16 retire dead diagnostics fields

- Owner: WS-RACESTART.
- Touch points: `track_runner/state_io.py`, `track_runner/race_start.py`.
- Depends on: WP-15.
- Acceptance criteria: `cyclical_prior` (`state_io.py:669`), `source_frame_indices`
  (`:686`), and `source_count` (read by the summary print at `race_start.py:411`)
  retire; the summary print derives its count from seeds.
- Evidence or review: `grep -rn cyclical_prior track_runner/` comes back empty.
- Obvious follow-ons: none.

### Work package: WP-17 base refine reuse on the manifest

- Owner: WS-REUSE.
- Touch points: `track_runner/modes/refine.py`.
- Depends on: WP-12.
- Acceptance criteria: reuse identity is manifest-fingerprint membership in the v15
  solve artifact, which `refine.py:53` already names as the authority. The
  `scored_keys` cross-check at `refine.py:61-76` retires: it is a second, weaker
  identity source across two files that schema 15 writes together. Today its
  `if os.path.isfile(diag_path)` guard means an absent scores file empties
  `scored_keys`, marks every interval unscored, and pushes refine toward a full
  re-solve. The C7 behavior holds: a refine that would become a full solve exits
  with a reason and preserves the existing artifact.
- Evidence or review: independent `reviewer` pass on C6 and C7. Focused tests:
  unchanged seeds produce no computation and no disk write; one new seed re-solves
  its two intervals; both hold with `interval_scores.json` absent.
- Obvious follow-ons: none.

### Work package: WP-18 assert cycles, hard segments, and metrics

- Owner: WS-HARNESS.
- Touch points: `tests/e2e/e2e_full_cycle_fixture.py`.
- Depends on: WP-5, WP-10, WP-16, WP-17.
- Acceptance criteria: for each fixture resolution, run
  `solve -> refine -> target -> encode` with `interval_scores.json` present and
  again with it absent -- four cycles. `analyze` runs separately on the
  diagnostics-absent case and is asserted to report the missing file clearly, which
  is its reporting-mode contract.
  Behavior assertions over the WP-4 hard segments:
  - the chord-risk, low-motion-quality, and size-residual segments each rank above
    the benign segment in the WP-10 risk view;
  - allocation promotes at least one interval, and promoted walker frames stay at
    or under the budget;
  - `target` places its first suggested seed inside one of the three hard segments.
  Metric assertions against the WP-5 baselines, each with an additive floor so a
  near-zero baseline stays achievable:
  - crop-center max step: at most `max(baseline * 1.10, baseline + 0.05)` torso
    widths;
  - crop-center median step: at most `max(baseline * 1.10, baseline + 0.02)`;
  - crop direction-reversal count: at most `baseline + 2`;
  - crop-size step p95: at most `max(baseline * 1.10, baseline + 0.02)`;
  - promoted walker frames: at most the budget;
  - per-frame `conf` fresh versus reloaded: within 1/255.
  The additive floors exist because a deterministic fixture can produce a zero
  reversal count or a near-zero size step, where a purely multiplicative bound
  would collapse to zero and fail on numerical noise.
- Evidence or review: the harness exits non-zero on any breach and names the
  offending metric, fixture resolution, and segment.
- Obvious follow-ons: none.

### Work package: WP-19 re-solve the acceptance tier

- Owner: WS-RESOLVE.
- Touch points: `tr_config/` artifacts for IMG_3830, IMG_3823, and
  Hononega-Orion_600m-IMG_3702; `re-solve.sh`.
- Depends on: WP-18.
- Acceptance criteria: extend `re-solve.sh` with a stem-filter argument, and run
  `prepare` plus `solve --yes` at default binning for each acceptance stem. Two
  decided changes from the current script: the `--bin 1` override is dropped so the
  corpus matches production policy, and the `solve --upgrade` second pass is
  skipped because a full fresh solve leaves it nothing to upgrade. Each stem
  produces a v15 artifact and a log ending in success. Record per-video promotion
  count, walker frames, and wall time.
- Evidence or review: three v15 artifacts plus logs.
- Obvious follow-ons: WP-20.

### Work package: WP-20 re-solve and classify the extended tier

- Owner: WS-RESOLVE.
- Touch points: `tr_config/` artifacts for the remaining nine stems;
  `docs/active_plans/reports/extended_corpus_outcomes.md` (new).
- Depends on: WP-19.
- Acceptance criteria: run each remaining stem and record the outcome as success,
  or as a failure classified `plan_related` or `pre_existing`. The report is a
  complete artifact, one row per stem, each failure row carrying stem, command,
  failing stage, error text, classification, and evidence. A `pre_existing`
  classification requires demonstrating the same failure on the stem's v10 artifact
  or on the plan-start checkout; that evidence is what makes it non-blocking. A
  `plan_related` classification blocks M14 until resolved. The known Jason-3200m
  end-of-video condition is expected to classify `pre_existing`. Lyra-Wheeling runs
  last as the most expensive stem.
- Evidence or review: the outcome table covers all nine stems with no blank cells.
- Obvious follow-ons: WP-21, WP-22.

### Work package: WP-21 compare allocation

- Owner: WS-ALLOC.
- Touch points: `docs/active_plans/reports/pair_local_allocation_report.md` (new).
- Depends on: WP-19, WP-20.
- Acceptance criteria: report per-stem promotion count, walker frames, and wall
  time before and after, plus a branch-appropriate comparison:
  - **Branch B**: relate each changed promotion to its `chord_span_widths` value,
    showing whether walker frames moved toward high-chord intervals.
  - **Branch A**: relate each changed promotion to the retained-signal ranking.
  - **Branch C**: report the allocation shift and record that chord span was
    rejected as the promotion signal.
  Confirm promoted frames at or under budget for every stem, and note the
  under-spend where whole-interval packing left headroom.
- Evidence or review: the budget column shows promoted frames and budget per stem.
- Obvious follow-ons: none.

### Work package: WP-22 evaluate encode output

- Owner: WS-ENCODE.
- Touch points: `docs/active_plans/reports/pair_local_encode_evaluation.md` (new).
- Depends on: WP-19.
- Acceptance criteria: recompute the WP-18 crop metrics on the re-solved acceptance
  videos against the WP-5 baselines and the same additive-floor tolerances. Then
  run an `image_evaluator` comparison on IMG_3830 and Hononega-Orion_600m-IMG_3702,
  over 20 frames sampled at a fixed stride. Pass criterion: the evaluator reports
  the drawn box containing the runner in at least 95% of sampled after-frames, and
  the after-set containment lands within 2 percentage points of the before-set.
- Evidence or review: the metric table plus the evaluator's per-frame tallies.
- Obvious follow-ons: none.

### Work package: WP-23 synchronize documentation

- Owner: WS-DOCS.
- Touch points: `docs/TR_SCHEMA_VERSION_HISTORY.md`, `docs/TRACK_RUNNER_DESIGN.md`,
  `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md`, `docs/CHANGELOG.md`,
  `docs/active_plans/active/pair_local_schema15_plan.md`.
- Depends on: WP-21, WP-22.
- Acceptance criteria: the schema history carries the v15 entry with the human
  approval and the 11-14 gap. `docs/TRACK_RUNNER_DESIGN.md` describes the linear
  interpolator, and **its "Dual scoring philosophy" section is rewritten**: that
  section currently states first-pass FWD/BWD disagreement drives interval
  confidence, seed recommendation, and severity, which stops being true for Stage 3
  under this plan. The rewrite distinguishes Stage-3 model risk, which now drives
  allocation and ranking, from post-walker FWD/BWD disagreement, which remains the
  uncertainty probe on promoted intervals.
  `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md` states that Stage-3 agreement is
  structurally 1.0 and that `conf` carries the measured uncertainty. The changelog
  carries entries in every affected category.
- Evidence or review: `pytest tests/test_markdown_links.py` and
  `tests/test_ascii_compliance.py` pass.
- Obvious follow-ons: WP-24.

### Work package: WP-24 close out the plan

- Owner: WS-DOCS.
- Touch points: `docs/archive/`, `docs/active_plans/`.
- Depends on: WP-23.
- Acceptance criteria: the plan, audit, decision, extended-outcome, allocation, and
  encode records move to `docs/archive/`; `docs/CHANGELOG.md` carries the close-out
  entry naming each `pre_existing` classification and its evidence.
- Evidence or review: `pytest tests/test_markdown_links.py` passes after the move.
- Obvious follow-ons: none.

## Acceptance criteria and gates

- Per-patch gate: `pytest tests/` passes; `tests/test_pyflakes_code_lint.py`,
  `tests/test_ascii_compliance.py`, `tests/test_function_typing.py`, and
  `tests/test_markdown_links.py` pass; the patch's focused tests pass; the patch
  updates `docs/CHANGELOG.md`.
- Baseline gate: WP-5 constants are recorded against the plan-start commit before
  Patch 4 begins.
- Policy gate: a second `reviewer` re-applies the M2 rules to the M1 audit and
  reaches the same branch, threshold, and budgets.
- Integration gate: with `interval_scores.json` absent, `target`, `refine`, and
  `encode` behave correctly, and `analyze` reports the absence clearly. Refine on a
  v10 artifact exits with a clear "run solve" message and preserves the existing
  artifact (C7).
- Independent review gate: WP-9, WP-12, and WP-17 each get a fresh `reviewer`
  subagent confirming C2, C4, C6, C7, C9, C10, C12, and C13.
- Acceptance gate: the M12 harness completes all four cycles with hard-segment
  ranking, budget, and crop-metric assertions inside tolerance.
- Corpus gate: the three acceptance-tier stems produce v15 artifacts with
  successful logs, and every extended stem carries a filed classification with no
  `plan_related` failures outstanding.

## Test and verification strategy

Fast lane, `tests/` per `docs/PYTEST_STYLE.md` -- deterministic, inline inputs,
`tmp_path`, well under one second each:

- `tests/solver/` -- interpolator output within `1e-9` of pre-change output with
  exact endpoints; `size_consistency` agreement within 0.01 across `cum_scale`;
  promote, hold, pre-race, budget cap, packing skip, and the `measured == 0` floor.
- `tests/storage/` -- v15 round trip with a diverging and a quantization-equal
  interval; a half-present FWD/BWD pair raises; a v10 artifact raises the re-solve
  error; `conf` round-trips within 1/255 and comes from storage.
- `tests/modes/` -- refine reuse with `interval_scores.json` absent; `target`
  ordering from the risk view matches an equivalent persisted set.
- `tests/output/` -- review ranking asserts ordering behavior; the AST guard
  asserts `review.py` delegates rather than recomputing scoring formulas.

Non-browser E2E, `tests/e2e/` per `docs/E2E_TESTS.md`, created by WP-1 and already
excluded by `tests/conftest.py:35`:

- `e2e_pair_local_sagitta.py` -- the measurement instrument.
- `e2e_full_cycle_fixture.py` -- four cycles over two resolutions with hard-segment
  behavior assertions and crop metrics against recorded baselines.

Failure semantics: a red per-patch gate blocks that patch. A red acceptance gate
blocks M13. A red independent review gate blocks the milestone that owns it. An
extended-tier failure classified `pre_existing` with v10 or plan-start evidence is
filed and leaves the gates green; a `plan_related` failure blocks M14.

## Migration and compatibility policy

- Schema 15 is the one readable layout; v10 artifacts fail loud and regenerate
  through a fresh solve (C10).
- Version 15 follows the rolled-back 11-14 stamps.
- Schema 15 is an explicitly human-approved project decision under C10, recorded in
  `tr_schema.py` and `docs/TR_SCHEMA_VERSION_HISTORY.md`.
- Race start and reuse identity each have one source from the first commit.
- Seeds, config, `camera_motion.npz`, and the `.fastread.mkv` working copies carry
  forward.
- The corpus re-solve runs at default binning, so regenerated artifacts match
  production policy rather than the `--bin 1` override in the current script.
- Users with in-flight work run `solve`; refine names that path when it applies.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Fixture too smooth to exercise promotion | High -- green harness proving only plumbing | Evenly spaced seeds on linear motion | WS-BASELINE | WP-4 builds chord-risk, low-motion-quality, size-residual, and benign segments; WP-18 asserts the hard segments outrank the benign one and that allocation promotes something |
| Baseline capture impossible after code changes | High -- tolerances become guesses | Harness authored at M12 | WS-BASELINE | M3 runs before Patch 4 and records constants against the plan-start commit, tagged with its hash |
| Multiplicative tolerance collapses at a zero baseline | Medium -- false regression on noise | Zero reversals or near-zero size step | WS-HARNESS | Additive floors alongside every multiplicative bound; `baseline + 2` for counts |
| Budget of zero blocks all promotion | High -- corrected policy cannot act | A video whose degenerate policy promoted nothing | WS-POLICY | `budget = max(measured, ceil(0.10 * post_race_frames))` |
| Whole-interval packing cannot hit a two-sided band | Medium -- correct implementation fails its own gate | One long interval against a tight budget | WS-POLICY | One-sided cap plus a promote-at-least-one check; under-spend reported, not penalized |
| Ranking view becomes a second scoring engine | High -- recreates the drift this plan removes | `review.py` recomputes formulas | WS-RANK | WP-10 assembles from the owners; an AST guard test asserts no independent computation |
| `analyze` contract stated two ways | Medium -- unsatisfiable gate | Diagnostics-absent cycle includes `analyze` | WS-HARNESS | Cycle covers `target`, `refine`, `encode`; `analyze` asserted to report the absence clearly |
| New allocation exceeds current walker spend | High -- solve time grows sharply | Budget cap check fails | WS-GATE | Budget derives from measured current spend, so allocation is cost-neutral by construction |
| bin=2 coordinate path escapes coverage | High -- PROCESSED-space regression ships | Every acceptance stem defaults to bin 1 | WS-BASELINE | WP-4 generates a 2880x1620 fixture |
| Annotation noise drives the threshold | High -- policy rests on a few loose seeds | Long p90 tail on thin bins | WS-MEASURE | 30-triple floor; p50 beside p90; `visible`/`partial` eligibility; mechanical outlier classification (C5) |
| A branch outcome leaves M6 without an action | High -- measurement completes, gate work waits | Non-monotonic curve or thin bins | WS-POLICY | Branch A is the explicit catch-all; Branch C routes to the same allocation policy |
| Retained risk signals lost with the degenerate tier | Medium -- unrelated regression | WP-9 read as "one metric covers all" | WS-GATE | WP-9 names the signals that carry forward; the reviewer gate checks it |
| Absent FWD/BWD read as pass agreement | Medium -- misleading uncertainty downstream | Future code infers meaning from omission | WS-FORMAT | WP-12 documents quantized storage equivalence; WP-13 makes stored `conf` the agreement source |
| Refine reuse regression after the manifest switch | High -- surprise full re-solve or lost work | Fingerprint membership mishandled | WS-REUSE | Reviewer gate on C6/C7; focused tests for the quiet and single-seed cases |
| Race-start relocation misses a consumer | High -- late mode failure | A mode still reads diagnostics | WS-RACESTART | The absent-`interval_scores.json` integration gate covers the operational modes |
| Corpus gate blocks on an expensive or known-broken stem | High -- unrelated work stalls | Lyra-Wheeling cost or the Jason condition | WS-RESOLVE | Binding gate is the three-stem acceptance tier; WP-20 classifies with v10 evidence |
| Plan and implementation drift across sixteen patches | Medium -- silent scope loss | Long execution window | Manager | Patch labels in every changelog entry; plan updated at each milestone exit |

## Rollout and release checklist

- [ ] M2 eligibility, bins, budget formula, packing rule, and branch rules recorded
      ahead of the M1 run.
- [ ] M1 precondition check confirms twelve stems resolve to readable videos.
- [ ] M1 computes the six missing `camera_motion.npz` artifacts.
- [ ] M1 audit published with per-bin and per-resolution-class distributions.
- [ ] M2 decision record carries literal branch, threshold, and per-video budgets;
      policy gate green.
- [ ] M3 fixture generates deterministically at both resolutions with four labeled
      segments; baselines recorded against the plan-start commit.
- [ ] M4, M5, M9 complete; `pytest tests/` green.
- [ ] M6, M7 complete; reviewer gate green on WP-9; AST guard green on WP-10.
- [ ] M8 complete; reviewer gate green on WP-12.
- [ ] M10, M11 complete; reviewer gate green on WP-17.
- [ ] M12 harness green on all four cycles, hard-segment ranking and budget
      assertions included.
- [ ] M13 acceptance tier produces three v15 artifacts with successful logs.
- [ ] M13 extended tier outcome table complete, no `plan_related` failures open.
- [ ] M14 allocation report published; promoted frames at or under budget per stem.
- [ ] M15 crop metrics inside tolerance; `image_evaluator` comparison meets its
      pass criterion.
- [ ] M16 docs synchronized including the dual-scoring philosophy rewrite; plan and
      records moved to `docs/archive/`.

## Documentation close-out requirements

- Active plan: file this document under `docs/active_plans/active/` as
  `pair_local_schema15_plan.md`, updated at each milestone exit.
- Records: `docs/active_plans/audits/pair_local_sagitta_audit.md`,
  `docs/active_plans/decisions/pair_local_promotion_gate.md`,
  `docs/active_plans/reports/extended_corpus_outcomes.md`,
  `docs/active_plans/reports/pair_local_allocation_report.md`, and
  `docs/active_plans/reports/pair_local_encode_evaluation.md`.
- `docs/CHANGELOG.md`: entries under **Additions and New Features** (sagitta
  instrument, hard-segment fixture harness), **Behavior or Interface Changes**
  (promotion basis and walker-frame budget, ranking view, schema 15, race-start
  relocation, refine reuse identity, default binning in the corpus re-solve),
  **Fixes and Maintenance** (`size_consistency` coordinate space), **Removals and
  Deprecations** (cubic scaffolding, `propagator_path`, `cyclical_prior`,
  `source_frame_indices`, `source_count`, fragile identity fields), and **Decisions
  and Failures** (the 11-14 gap; keeping the pair-local model; what FWD/BWD
  omission records; how promotion risk and `conf` differ; each `pre_existing`
  extended-tier classification with its evidence).
- `docs/TR_SCHEMA_VERSION_HISTORY.md`: v15 entry recording the human approval.
- `docs/TRACK_RUNNER_DESIGN.md`: describe the linear interpolator and rewrite the
  dual-scoring philosophy section.
- `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md`: state that Stage-3 agreement is
  structurally 1.0 and that `conf` carries the measured uncertainty.
- Archive: move the plan and records to `docs/archive/` at close-out.

## Patch plan and reporting format

| Patch | Content | Workstream |
| --- | --- | --- |
| 1 | Sagitta instrument, camera-motion generation, audit | WS-MEASURE |
| 2 | Promotion policy decision record | WS-POLICY |
| 3 | Hard-segment fixture and plan-start baselines | WS-BASELINE |
| 4 | Interpolator honesty, `propagator_path` retirement | WS-INTERP |
| 5 | `size_consistency` coordinate space | WS-SCORE |
| 6 | Promotion logic, budget, packing rule | WS-GATE |
| 7 | Ranking view assembled from the scoring owners | WS-RANK |
| 8 | Schema 15 and the v15 artifact layout | WS-FORMAT |
| 9 | `video_identity` trim | WS-IDENTITY |
| 10 | Race-start relocation and dead diagnostics fields | WS-RACESTART |
| 11 | Refine reuse identity | WS-REUSE |
| 12 | Diagnostics-absent cycles and metric assertions | WS-HARNESS |
| 13 | Corpus re-solve, acceptance then extended | WS-RESOLVE |
| 14 | Allocation report | WS-ALLOC |
| 15 | Encode evaluation | WS-ENCODE |
| 16 | Documentation and close-out | WS-DOCS |

Each patch stages its changes, updates `docs/CHANGELOG.md`, and reports its patch
label with the gates it passed.

## Resolved decisions

- Schema change and forced re-solve: **approved by the user**; schema 15 is an
  approved project decision recorded in the repository.
- Stage-1 camera-motion computation for all twelve seeded stems: **approved by the
  user**, so the measurement corpus is complete and needs no cost-degradation rule.
- Version number: **15**, following the rolled-back 11-14 stamps.
- `chord_span_widths` persistence: computed in memory each run (C12, C13).
- `conf` definition: `trajectory_confidence.py` is the single owner; this plan
  changes transport and records how promotion risk differs from it.
- `analyze` contract: keeps reading `interval_scores.json` as a reporting mode, and
  is asserted to report the file's absence clearly. The diagnostics-absent cycle
  covers `target`, `refine`, and `encode`.
- Ranking view: assembles from `scoring.py` and the promotion owner; an AST guard
  prevents a second scoring implementation in `review.py`.
- Walker-frame budget: `max(measured, ceil(0.10 * post_race_frames))` per video.
- Allocation packing: first-fit-decreasing by risk, ties by lower `start_frame`,
  skip-and-continue, with a one-sided budget cap and a promote-at-least-one check.
- Tolerance derivation: baselines captured against the plan-start commit in M3,
  written in as literal constants, with additive floors alongside multiplicative
  bounds.
- Acceptance basis: generated fixtures with four labeled hard segments, so the gate
  exercises promotion and ranking rather than plumbing alone.
- Corpus tiering: twelve stems measured; three cheap, seed-dense stems gate;
  the remaining nine supply best-effort evidence with filed classifications.
- bin=2 coverage: supplied by a generated 2880x1620 fixture.
- Corpus binning: default binning, dropping the `--bin 1` override.
- `solve --upgrade` pass: skipped in M13.
- Extended-tier failures: `pre_existing` requires v10 or plan-start evidence and is
  non-blocking; `plan_related` blocks M14.

## Open questions and decisions needed

No deferred scope items remain. The one live procedure is the M2 policy selection,
which executes mechanically:

- Decision owner: WS-POLICY, with a second `reviewer` re-applying the same rules to
  the same audit as the policy gate.
- Evidence and decision rule: the eligibility rule, bins, 30-triple floor, outlier
  classification, ordered Branch C / B / A evaluation, Branch B threshold-selection
  algorithm, budget formula, and packing rule recorded under M2. Each branch has a
  fully specified implementation instruction, so any outcome dispatches without
  further design.
