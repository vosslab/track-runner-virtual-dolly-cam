# Plan: blob walk v2 fix-phase roadmap

> **Status: retired.** The separate `blob_walk_v2` diagnostic product was
> removed on 2026-08-21 after its production walker ownership moved under
> `track_runner/blob_walk/`. This roadmap is retained as design history and
> contains no current implementation or validation work.

## Context

The blob walk v2 audit (P1-P17,
[blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md))
and the nine-check validation
([blob_walk_v2_validation_report.md](../reports/blob_walk_v2_validation_report.md))
are complete. The P15 telemetry fix is complete (decision-equality gated,
SCHEMA_VERSION 12). The P12 stride termination fix is implemented and staged
for review (SCHEMA_VERSION 13); it becomes the baseline only after human
review accepts it. No other behavior change is approved. This roadmap answers: how do we get from the current validated
state to a better blob walk v2 implementation while avoiding risky bundled
changes? It fixes the order and boundaries of the fix phase; each milestone
still requires its own user-approved plan before implementation. The human
reviewer handles all repository commit workflow; nothing in this roadmap
depends on it.

Drafted fresh from the audit report, validation report, and Check 0-8 /
Check G workstream docs. The old `blob_walk_refine.md` repair ladder was not
used as a source of ordering.

## Objectives

- The last observed concrete bug (P10 bootstrap fallback masking) is fixed
  with the smallest possible blast radius.
- The fix phase has a clean post-fix quality baseline with a defined
  measurement tolerance before any quality trial is judged.
- The ranking-quality work (the dominant regression class) starts from a
  position-verified diagnosis and a binding approach-selection rule, not a
  preferred implementation.
- Anchor and emission design work proceeds only behind a design-safety
  checklist that keeps chained blob state out of the walker.
- Every contraindicated or premature change is parked with the explicit
  evidence that would activate it.

## Design philosophy

This roadmap trades speed for sequence: one narrow, individually validated
change at a time, ordered by evidence strength (observed bugs first, then
measurement, then design-sensitive trials). The rejected alternative is a
bundled quality overhaul (ranking + gates + anchor in one campaign), which
the validation evidence explicitly undermines: claim A refutes the box
widening such an overhaul would lean on, and claim B shows the obvious
normalization lever has no guaranteed effect. This is long-term over
short-term and fix-the-design-not-the-symptom applied at roadmap scale:
guards that enforce the wrong model are corrected or removed; new hard gates
are not added.

## Scope

- Order the fix phase into five milestones (M1-M5) with explicit
  dependencies, gates, and per-milestone requirements.
- Specify milestone M1 (P10 fallback correction) to implementation-ready
  detail, including the coverage-seam semantics and a mandatory call-site
  audit.
- Specify milestone M2 (re-baseline and ranking evidence) as four
  independent, parallel-dispatchable workstreams with named decisions.
- Bind milestone M3 (ranking-quality trial) to an evidence-keyed
  approach-selection rule.
- Gate milestone M4 (anchor-advance) behind a design-safety checklist and
  milestone M5 (emission redesign) behind M4 evidence.
- Park contraindicated and premature items with activation evidence.

## Non-goals

- Implement any behavior change in this deliverable; every milestone needs
  its own approved plan first.
- Re-plan P12; it is implemented and staged, and enters this roadmap only
  as a baseline precondition.
- Widen the acceptance box; refuted by claim A on the tested stall
  intervals (see Deferred items for the activation evidence).
- Ship evidence normalization as a standalone fix; claim B is MIXED and
  direction-asymmetric.
- Change skip-cap geometry or extrapolation hold-vs-linear; claims L and G
  show no exercised effect surface today.
- Add new hard exclusions, movement caps, acceptance boxes, or
  interval-specific special cases; any such proposal needs separate
  explicit user approval before design work starts.
- Bundle dead-code cleanup (P13), cache-bypass narrowing (P17), or
  pre-pass threading (P16) into any behavior fix; each ships alone if
  activated.
- Specify git/commit workflow; the human reviewer owns it.

## Current state summary

Completed:

- **Audit complete** (2026-06-10): proven findings P1-P17, four clean
  negative findings (coordinate handling clean, no temporal bias,
  integration clean, centroid interpretation), and a twelve-claim
  assumption table (A-L) gating all behavior changes.
- **Validation complete** (2026-06-10/11): all nine checks executed; every
  claim has a verdict; the validation report is the evidence base that
  orders this fix phase.
- **P15 telemetry fix complete** (Check 1): truthful `path_step_cost` and
  `window_head_frame` columns; field-wise decision equality exact on all 8
  baseline passes; SCHEMA_VERSION 11 -> 12 (metadata-only).
- **P12 stride termination fix implemented and staged** (claim K):
  directional crossing test with clamp (`_neighbor_reached` in
  `track_runner/blob_walk/walk_walker.py`); SCHEMA_VERSION 12 -> 13
  (geometry-affecting for stride > 1 sources only); 7 new unit tests;
  8-pass stride-1 harness EQUAL; full suite 1578 passed.
- **P1/P2/P3/P4/P5 cost-model findings fixed** (WP-COST-1, 2026-06-12):
  pairwise velocity-delta Viterbi rewrite in `walk_viterbi.py` addresses
  all five findings: P1 (raw evidence scale now normalized per frame with
  a bounded tie-breaking bonus), P2 (WEIGHT_SPEED_DELTA and
  WEIGHT_HEADING_DELTA are now live in the DP), P3 (weights now reside in
  the `walker_costs` YAML section via tr_config.py), P4 (skip is charged
  once per skipped frame and geometry bridges across gaps), P5 (bootstrap
  slack removed; single generous physical-sanity prune replaces the
  always-on tight hard prune). SCHEMA_VERSION 13 -> 14 (geometry-affecting).
  Release evidence in
  [blob_walk_v2_cost_model_ab.md](../workstreams/blob_walk_v2_cost_model_ab.md).
- **P10 seed-only fallback fix complete** (WP-P10-1, 2026-06-12):
  `WalkCoverage` dataclass with `post_seed_accepted` field; fallback gate
  reads `post_seed_accepted == 0`; bootstrap-only stall now correctly
  selects Hermite. Part of SCHEMA_VERSION 14 bump.

Unresolved:

- The ranking-quality regression class -- the largest quality problem by
  count -- has a verdict but no chosen intervention.
- The two stall sub-types (drift stall, signal-absence stall) have
  confirmed distinct mechanisms and no fix; anchor staleness is structural
  and untreated.
- Two P12 closure caveats stand: interval #164 was never re-solved
  post-fix (decode cost), and stride-1 preservation rests on the analytic
  predicate proof plus unit tests rather than a pre/post snapshot.
- Corpus-level quality numbers are stale: the m4 A/B regression set
  predates P12 and P10, and Lyra-Wheeling results carry the stride-2
  caveat.

**Baseline precondition.** P12 is implemented and staged for review; it
becomes the baseline only after human review accepts it. That acceptance
must happen before M1 validation numbers are treated as authoritative. This
is a review precondition, not a milestone: it has no success metrics. After
acceptance, the two completed planning documents
(`blob_walk_v2_p12_fix_plan.md`, `blob_walk_v2_validation_plan.md`) may be
archived per the project's documentation convention; archiving is a
convention, not a success criterion of this roadmap. The residual P12
confirmation (interval #164 post-fix debug CSV) folds into WS-2A's corpus
run so the expensive 4K HEVC decode is paid once.

## Evidence summary

What the validation established, ordered by how much it constrains fix
ordering:

1. **Ranking-driven regressions are the dominant quality class** (claim C,
   RANKING-DOMINANT). Of 34 classified m4 regressed passes: 17 pure
   ranking-driven plus 5 of 10 mixed passes selection-leaning -- an
   effective 65% ranking share vs 35% starvation share. Decisive secondary
   finding: `soft_miss_no_path` is near zero everywhere; when candidates
   exist the walker accepts one. Failures are wrong-blob-wins, not cap
   rejections, so the acceptance box and displacement cap are not the
   primary drivers. Ranking quality is the main quality work -- and it is
   design-sensitive.
2. **Small-runner blob behavior is scale-dependent** (claims D, E). At
   ~30 px torso height (Conant) the DoG merges the runner into one blob
   (97-100% single-blob frames). At ~11 px (Jason) every candidate frame
   has 4-6 distinct limb/trunk blobs within one torso width, and Viterbi
   alternates between upper and lower clusters -- the observed within-body
   vertical centroid jitter (0.26 flips/step global; one 0.384-torso-height
   single step). Wrong-blob-wins concentrates where candidate supply is
   fragmented, pointing the ranking work at scale handling rather than
   generic smoothness penalties.
3. **P10 bootstrap fallback masking is observed in production data**
   (claim J). Conant `seed_1126_1134` FWD: bootstrap frame accepted, all 7
   remaining frames missed, fallback gate (`accepted_count == 0`) not
   satisfied, shipped path frozen at the seed -- strictly worse than
   Hermite. 3.8% of sampled passes; short intervals most vulnerable.
4. **Rejected blobs on the tested stall intervals are not the runner**
   (claim A, REFUTED). Rejected blobs are background athletes at 5-24
   torso widths; runner signal is below the DoG detection threshold inside
   the box (Jason, ~3 proc-px torso) or at noise level at the frame edge
   (Conant). **Acceptance-box widening is contraindicated by current
   evidence**: it would admit background blobs, not recover runner signal.
5. **The two stalls have different mechanisms** (claims F, I). Anchor age
   is structurally >= 9 frames at observation; every sampled rejection
   occurred at anchor age >= 7. Conant 1080-1111 FWD is a positional-drift
   stall (anchor-to-runner drift reaches 2.35 TW; anchor-advance would
   address it). Jason 564-583 FWD is a signal-absence stall (drift under
   0.53 TW; anchor position is fine and anchor-advance would not help).
   One fix cannot cover both.
6. **Evidence normalization alone is not a proven first fix** (claim B,
   MIXED). Raw `integrated_mag` dominates per-node cost statically
   (100-1000x displacement), but dynamic selection is
   direction-asymmetric: FWD multi-candidate frames track min-displacement
   (96.2%), not max-evidence (11.5%); BWD tracks max-evidence (88.9%).
   Window-level DP momentum already overrides per-node evidence on the
   passes that matter most.
7. **Skip-cap and extrapolation changes are not first-priority** (claims
   L, G). Zero identity-jump events in 82 steps; the corridor filter
   bounds skip-bridging jumps in practice (hole real, unexercised). The
   `extrapolated` status never executes in any available log (flush-only,
   P6); the hold-vs-linear comparison is empirically indeterminate and the
   effect surface is at most 2 frames per pass.

## Guiding principles

Stated positively; prohibitions live in milestone gates.

1. **Correct wrong guards; keep the gate count falling.** The fix phase
   removes or corrects guards that enforce the wrong model (stride
   equality termination -- done; bootstrap-accept fallback predicate --
   next). New behavior is preferentially expressed as soft scoring the
   window DP can override, per the user-directed design orientation
   (2026-06-11): better tracking with less gating.
2. **One fix, one plan, one schema decision, one rollback.** Every
   milestone ships alone, with its rollback path stated in technical terms
   (restore the previous implementation of the named functions and
   constants).
3. **Measure with held-out-seed distance; corroborate with overlays;
   treat `accepted_fraction` as diagnostic.** Held-out-seed distance is
   the quality authority (m4 report; FWD/BWD agreement is structurally
   biased). Ranking changes additionally require overlay evidence that the
   accepted blob is the runner, so a distance improvement cannot be
   claimed off a background blob that happens to land closer.
4. **Define measurement tolerance before trials.** "Within tolerance on
   preserved/rescued passes" needs a numeric tolerance derived from
   run-to-run noise; WS-2A delivers that estimate before any M3 trial is
   judged against it. Contingency: if repeat-sample noise is too large to
   define a useful numeric tolerance, M3/M4 trials are judged on
   categorical pass buckets (rescued / preserved / regressed transitions)
   plus overlay-confirmed improvement, with the measurement uncertainty
   reported alongside the verdict.
5. **Reason in image space, in torso units** (contract C2; standing
   constraint 2026-06-10). Physical runner speed stays out of the
   arguments.
6. **Let overlays establish blob identity** before any gate or supply
   change is designed, the way Check 2 settled claim A.
7. **Preserve the contract invariants in every milestone**: seeds are
   anchors and are never observed (C1/C3); intervals stay independent
   (C5/C6); FWD/BWD stay independent for scoring (C9); one unified
   SCHEMA_VERSION (C10); no appearance evidence (C6/C8); no chained blob
   state.
8. **Schema bumps gate adoption, not exploration.** Trials run behind the
   A/B harness without a bump; a geometry-affecting bump is made when a
   change ships as default behavior.

## Architecture boundaries and ownership

Durable components touched by this fix phase:

- **Walker core** (`track_runner/blob_walk/`): selection, statuses,
  emission, anchor. Owned by the milestone plans that name it; M1 does not
  touch it.
- **Stage-4 seam** (`track_runner/walker_bundle.py`,
  `track_runner/interval_solver.py`): coverage reporting and the per-pass
  Hermite fallback. M1's entire surface.
- **Extraction** (`track_runner/residual_motion.py`): DoG, blob
  extraction, caches. Touched only under M3 decision-rule branch 2.
- **Schema authority** (`track_runner/tr_schema.py`,
  `docs/TR_SCHEMA_VERSION_HISTORY.md`): one bump per adopted
  geometry-affecting change.
- **Measurement instruments** (`tests/e2e/e2e_blob_walk_baseline.py`,
  `tests/e2e/e2e_walker_ab.py`, Check 2/4/5 overlay and telemetry
  methods): read-only consumers; M2's tooling base.

### Mapping (milestones / workstreams -> components / patches)

| Milestone / Workstream | Component | Expected patches |
| --- | --- | --- |
| M1 / WS-1A | Stage-4 seam + schema authority | 1 |
| M1 / WS-1B | tests (seam + helper) + validation runs | 1 |
| M2 / WS-2A | measurement instruments (A/B re-baseline) | 1 (report + artifacts) |
| M2 / WS-2B | measurement instruments (overlay taxonomy) | 1 (workstream doc) |
| M2 / WS-2C | measurement instruments (scale census) | 1 (workstream doc) |
| M2 / WS-2D | measurement instruments (ROI reconciliation) | 1 (workstream doc) |
| M3 / (lanes set by its own plan) | walker core cost terms; extraction only under branch 2 | 2 to 4 |
| M4 / (lanes set by its own plan) | walker core anchor | 1 to 3 |
| M5 / (conditional) | walker core emission | sized by its own plan |

## Milestone plan

Milestone summary table (human review):

| M | Title | Summary | Goal |
| --- | --- | --- | --- |
| M1 | P10 fallback correction | Make the Hermite stall fallback count post-bootstrap accepts at an explicit coverage seam | Last observed concrete bug fixed; "never worse than Hermite" restored on promoted intervals |
| M2 | Re-baseline and ranking evidence | Post-fix corpus A/B re-baseline plus three targeted measurements | A clean quality baseline with tolerance, and a position-verified diagnosis that selects the M3 approach |
| M3 | Ranking-quality trial | Compare candidate approaches by the evidence rule; run one approved trial against the held-out-seed gate | The dominant regression class (wrong-blob-wins, 65% effective share) measurably reduced |
| M4 | Anchor-advance design phase | Design-safety checklist, then a scoped trial so the acceptance geometry follows the runner | The drift-stall sub-type recovers observations without reintroducing chained blob state |
| M5 | Emission-design work (conditional) | Center-emission / window redesign | Structural anchor-lag floor removed, only if M4 evidence shows remaining drift-driven loss |

### Milestone M1: P10 fallback correction

- Depends on: none (baseline precondition: P12 reviewed and accepted as
  current baseline before M1 validation numbers are authoritative).
- Workstreams: WS-1A, WS-1B (WS-1B follows WS-1A; inherently serial core
  change, documented below).
- Entry criteria: baseline precondition met; M1 fix plan approved by user.
- Exit criteria:
  - Targeted Stage-4 re-solve of Conant `seed_1126_1134` shows
    `walker_fallback_fwd = True` with Hermite FWD geometry and an
    unchanged BWD pass (primary proof).
  - Fallback-seam pytest gate green, including the new bootstrap-only
    case; pure-helper unit tests green.
  - 8-pass harness EQUAL (safety check only -- the harness drives the
    walker via `walk_driver` and never crosses the interval_solver seam).
  - `pyflakes` on changed files clean; full `pytest tests/` green.
  - Obvious follow-ons completed: `docs/CHANGELOG.md` entry,
    `docs/TR_SCHEMA_VERSION_HISTORY.md` entry, any failed gate rerun
    after its cause is fixed.
- Parallel-plan ready: no -- single-seam change; WS-1B depends on WS-1A.
  Max parallel doers: 1 (then 1).

Details:

- **Finding addressed**: claim J / audit P10 (OBSERVED, 1 of 26 passes).
  This milestone corrects the existing fallback predicate; it does not add
  a second fallback mechanism or an interval-specific special case. It
  also closes a latent P12 interaction: the degenerate
  span-smaller-than-stride case relies on the zero-accept fallback, which
  a bootstrap accept can mask.
- **Coverage-seam semantics (the core of the fix)**: today
  `walker_bundle.walk_bundle_to_path_with_coverage` returns
  `(full_span_path, int(summary.accepted_count))` and
  `interval_solver.solve_interval_analytical` gates the per-pass Hermite
  fallback on that count being zero. The bootstrap observation at the
  seed frame increments `accepted_count`, so a bootstrap-only stall
  reports 1 and skips the gate. The corrected seam makes both quantities
  explicit rather than silently changing the meaning of one integer: the
  coverage return becomes a **named type, `WalkCoverage`** (a small
  dataclass in `walker_bundle.py`, matching the project's existing
  `WalkSummary` dataclass style), with two named fields:
  `accepted_count` (total, unchanged meaning, preserved for any telemetry
  or future consumer) and `post_bootstrap_accepted` (computed from
  `WalkSummary.accepts` and the bundle's seed frame; the seed frame can
  appear in `accepts` at most once and only via bootstrap, since windowed
  steps start at `seed_frame + sign*stride` and the neighbor seed is
  never observed). The fallback gate reads
  `coverage.post_bootstrap_accepted` by name, so the new meaning cannot
  be misread at the call site, and **tests assert field names, never
  tuple positions** -- positional access is exactly the ambiguity this
  design removes. The M1 plan may substitute an equivalent named form
  only with a recorded reason. This explicit-both design is chosen over
  repointing the existing integer because the seam then documents itself
  and total-count consumers keep working.
- **Required pre-implementation check (call-site audit)**: enumerate all
  direct callers of `walk_bundle_to_path_with_coverage` AND all
  monkeypatched uses, recorded as grep output or a short table in the M1
  plan. The implementation **stops** if any caller or test helper assumes
  the second return value is a bare integer outside the known fallback
  seam. Initial audit from this planning session (to be re-run and
  re-recorded at implementation time): the only production caller is
  `interval_solver.solve_interval_analytical` (the two fallback flags);
  `tests/test_walker_stall_fallback.py` and `tests/test_walker_flag.py`
  monkeypatch the function and must adapt their fakes to the named type.
- **Expected behavior change**: bootstrap-only passes fire the per-pass
  Hermite fallback. Every pass with at least one post-bootstrap accept is
  byte-identical. True zero-accept passes are unchanged.
- **What must stay unchanged**: walker selection, statuses,
  `WalkSummary.accepted_count` and `accepted_fraction`, debug CSV columns
  and values, bootstrap status semantics, Viterbi costs, acceptance box,
  emission, C9 pass independence.
- **Schema decision**: SCHEMA_VERSION 13 -> 14, geometry-affecting
  (shipped geometry changes on masked passes), recorded in
  `docs/TR_SCHEMA_VERSION_HISTORY.md` annotated "geometry-affecting only
  for bootstrap-only-masked walker passes; byte-identical for all other
  passes". Same honest cache-invalidation tradeoff as the P12 bump.
- **Success metric**: exit criteria above; correctness fix -- corpus
  quality movement is expected to be small at 3.8% incidence and is not
  the justification.
- **Rollback path**: restore the previous implementation of
  `walk_bundle_to_path_with_coverage`, the fallback-gate reads in
  `solve_interval_analytical`, and the schema constants; remove the new
  test module.
- **Stop condition**: any walker-decision diff in the 8-pass harness; any
  non-masked pass changing output in the targeted re-solve; or the
  call-site audit finding a consumer that needs the total count where the
  plan assumed the post-bootstrap count.
- **Approval needed**: user approves the M1 fix plan
  ([blob_walk_v2_p10_fix_plan.md](../../archive/blob_walk_v2_p10_fix_plan.md), implemented 2026-06-12, archived)
  before implementation.

### Milestone M2: re-baseline and ranking evidence

- Depends on: WS-2A depends on M1 (the baseline must include both fixed
  bugs); WS-2B, WS-2C, WS-2D depend on nothing (read-only measurements of
  walker decisions, which M1 does not touch) and can start immediately.
- Workstreams: WS-2A, WS-2B, WS-2C, WS-2D (WS-2B/C/D fully parallel;
  WS-2A joins when M1 lands).
- Entry criteria: M2 measurement plan approved (one approval covers the
  four pre-scoped workstreams; no behavior gates crossed).
- Exit criteria:
  - Four durable workstream artifacts published under
    `docs/active_plans/workstreams/`, each answering its named decision:
    - WS-2A: `blob_walk_v2_postfix_ab_rebaseline.md`
    - WS-2B: `blob_walk_v2_wrong_blob_overlay_taxonomy.md`
    - WS-2C: `blob_walk_v2_candidate_supply_scale_census.md`
    - WS-2D: `blob_walk_v2_roi_discrepancy_recheck.md`
  - WS-2A reports the degradation tolerance and `extrapolated_count`.
  - ASCII and markdown-link checks green on the new docs; obvious
    follow-ons completed (`docs/CHANGELOG.md` entry per artifact batch,
    temp scripts removed).
- Parallel-plan ready: yes -- WS-2B, WS-2C, WS-2D concurrently, WS-2A
  joining after M1. Max parallel doers: 4.

Details:

- **Findings addressed**: claim C position-verification caveat (open item
  2), P12 closure caveat (interval #164), stale m4 baseline, Check 2 vs
  stall-diagnosis ROI discrepancy (open item 4), claim D crossover scale.
- **What must stay unchanged**: production behavior -- this milestone is
  measurement. Probes live in `_temp_*.py` scripts removed after handoff;
  smoke outputs under `output_smoke/`. A small behavior-neutral helper
  may be added for measurement tooling when reuse warrants it, with the
  neutrality stated in the workstream doc.
- **Schema decision**: none.
- **Rollback path**: not applicable (read-only).
- **Stop condition / re-rank triggers**: listed per workstream below; any
  trigger pauses M3/M4 planning for a re-rank.
- **Approval needed**: one user approval for the M2 measurement plan.

### Milestone M3: ranking-quality trial (design-sensitive)

- Depends on: WS-2A (baseline + tolerance), WS-2B (taxonomy), WS-2C
  (census) -- the decision rule consumes all three.
- Workstreams: set by the chosen approach's own plan (approach comparison
  is part of this milestone's planning, not coding).
- Entry criteria: M2 artifacts published; user approves the approach
  choice AND the specific trial plan (two decision points, per the
  validation stop rule).
- Exit criteria:
  - One trial executed behind the A/B harness; held-out-seed distance
    improves on the regressed set and stays within the WS-2A tolerance on
    preserved/rescued sets.
  - Overlay corroboration on the worst WS-2B-classified passes shows the
    torso blob winning.
  - Adoption decision recorded; on adoption, geometry-affecting schema
    bump + history entry + changelog (obvious follow-ons).
- Parallel-plan ready: no until the trial plan exists -- design-sensitive;
  one-sentence reason: the approach choice determines the lanes.

Details:

- **Findings addressed**: claims C, D, E, B.
- **Choose-first-trial decision rule** (binding; keyed to WS-2B/WS-2C):
  1. Wrong winners are mostly within-runner limb/trunk fragments (WS-2B)
     AND fragments cluster within ~1 torso width (WS-2C) -> first trial
     is **post-extraction blob merge at runner scale**. Required
     failure-mode check in that trial: overlays must show the MERGED
     candidate landing on a useful runner body point AND held-out-seed
     distance improving -- a reduced candidate count or reduced jitter
     alone is insufficient, because merging asymmetrically strong
     upper/lower blobs can average the centroid to a point that is no
     better a track point than the jitter it removes.
  2. Wrong winners are within-runner fragments BUT too separated to merge
     reliably at the smallest scales (WS-2C) -> first trial is
     **scale-adaptive DoG diameter** (extraction-side; the higher blast
     radius is justified in that plan against the residual cache and all
     extraction consumers).
  3. Wrong winners are background or spatially distant candidates (WS-2B)
     -> first trial is a **soft proximity / body-center preference term**
     in torso units (soft scoring per the claim H direction; no hard
     gate).
  4. **Evidence normalization** runs standalone only if overlays show
     high-magnitude wrong candidates beating spatially plausible runner
     candidates -- the one pattern claim B left open; otherwise it is at
     most a component inside the chosen trial.
  5. **Centroid-stability / vertical-jitter penalty** is considered only
     if jitter persists after the supply-side fix; Check 5 already
     cautions it treats the symptom while the cause is candidate
     over-supply.
  A coder preference for any approach outside this rule is rejected at
  plan review. Combinations are allowed only after the primary approach
  has its own clean A/B result.
- **Configuration boundary**: weight/flag movement into YAML is allowed
  only as needed to run the approved trial safely (A/B on/off without
  code edits). General P3 weight-residence cleanup stays out of M3 and
  ships, if ever, as its own patch.
- **Likely files**: `track_runner/blob_walk/walk_viterbi.py` (cost
  terms); possibly a small new module for a post-extraction merge pass;
  `track_runner/residual_motion.py` only under decision-rule branch 2;
  tests. Exact list comes from the chosen approach's plan.
- **What must stay unchanged**: hard-gate count does not increase (soft
  terms preferred); no appearance evidence (C6/C8); no cross-frame blob
  state; C9; torso-unit scaling (C2).
- **Schema decision**: no bump for harness trials; geometry-affecting
  bump on adoption.
- **Success metrics**: held-out-seed improvement on the regressed set
  within tolerance elsewhere; overlay-sampled wrong-blob-wins rate drops;
  ncy alternation on small-runner intervals drops.
- **Rollback path**: trial configuration off, or restore the previous
  implementation of the touched cost functions before adoption.
- **Stop condition**: a trial that exceeds the degradation tolerance on
  preserved passes is rejected and the next decision-rule branch is
  tried. A redesign discussion opens as soon as failures share a
  structural cause -- one clearly structural failure is enough, and three
  same-cause failures is the latest acceptable point; this is a judgment
  call, not a mechanical counter.

### Milestone M4: anchor-advance design phase (drift-stall sub-type)

- Depends on: M3 outcome (M3 may change the candidate supply this design
  assumes); WS-2A tolerance.
- Workstreams: design doc first; implementation lanes set by the trial
  plan if the design is accepted.
- Entry criteria: user accepts the design doc (safety checklist below
  answered), then approves the trial plan -- two separate decision points.
- Exit criteria (if trial approved and run):
  - Anchor-age telemetry (Check 4 method) shows age dropping below the
    structural 9-frame floor when in-window accepts exist.
  - Conant 1080-1111 FWD recovers non-zero accepted frames with accepted
    positions tracking the reference (overlay evidence).
  - Held-out-seed A/B on starvation and mixed buckets within the WS-2A
    tolerance on healthy passes.
  - Obvious follow-ons completed (schema history + changelog on
    adoption).
- Parallel-plan ready: no -- design phase; one-sentence reason: the design
  boundary must exist before lanes can.

Details:

- **Findings addressed**: claims F (CONFIRMED structural) and I
  (CONDITIONALLY CONFIRMED; drift sub-type). Scope is the drift sub-type
  only; the Jason-class signal-absence stall is explicitly outside this
  milestone's success claims (anchor position is already correct there;
  that case belongs to M3's extraction-scale branch).
- **Design-safety checklist** (answered in the design doc before any plan
  is accepted):
  1. What may the anchor depend on -- Viterbi-selected path candidates,
     emitted-frame accepts, provisional center-window decisions, or
     something else? Name the single source.
  2. How is the update bounded so one bad blob selection cannot drag the
     future acceptance geometry into runaway drift (the failure mode
     chained `last_blob` state produced historically)?
  3. How does the design differ, mechanically, from reintroducing
     `last_blob` memory? State what crosses window boundaries and why it
     is image-derived or single-anchor state, not accumulated decisions.
  4. How is FWD/BWD independence preserved (C9) -- each pass owns its own
     anchor with no shared or blended state?
  5. How does the walk recover after a bad anchor update -- is there a
     bounded way back to a good anchor, achieved within the existing
     mechanism, or does one wrong update bias the rest of the interval?
     Recovery must come from the design itself; a design that needs a
     new special-case fallback to recover answers this question "no".
  6. What telemetry and overlay evidence will demonstrate the box
     tracking the runner on Conant 1080-1111, before and after?
- **Schema decision**: no bump for harness trials; geometry-affecting
  bump on adoption.
- **Rollback path**: restore the previous anchor-update implementation in
  `walk_walker.py`.
- **Stop condition**: any design whose anchor update accumulates
  selection decisions across windows (chained-state semantics) is
  rejected at design review, not patched.

### Milestone M5: emission-design work (conditional)

- Depends on: M4 evidence (activation evidence below).
- Workstreams: sized by its own plan if activated.
- Entry criteria: user approves activation based on M4 evidence --
  post-M4 telemetry still showing drift-driven losses attributable to the
  emission design, or a quality ceiling on the A/B set traced to
  zero-past-context decisions. Until then this milestone is inactive.
- Exit criteria (if activated): set by its own plan, which must include
  spec-vs-implemented reconciliation (center emission per amendment spec
  section 2), schema bump on adoption, full A/B gate with the WS-2A
  tolerance, and an explicit migration note for every consumer of the
  five-value status enum.
- Parallel-plan ready: no -- inactive until activation evidence exists.

## Workstream breakdown

### Workstream WS-1A: implement the coverage seam and gate

- Owner: coder
- Interfaces:
  - Needs: approved M1 plan; call-site audit result.
  - Provides: the corrected seam and schema bump that WS-1B validates and
    WS-2A measures on.
- Expected patches: 1 (Stage-4 seam + schema authority).

### Workstream WS-1B: validate the seam

- Owner: coder
- Interfaces:
  - Needs: WS-1A landed.
  - Provides: the primary proof (targeted re-solve) and the green gates
    M1 exits on.
- Expected patches: 1 (tests + validation artifacts).

### Workstream WS-2A: post-fix corpus A/B re-baseline

- Owner: coder
- Interfaces:
  - Needs: M1 landed (baseline must include both fixed bugs).
  - Provides: the fix-phase quality baseline; the run-to-run noise
    estimate from a small repeat sample, from which the preserved/rescued
    degradation tolerance for M3/M4 is set; the interval #164 debug CSV
    (P12 confirmation: `stop_reason` `hit_neighbor_seed`, zero
    out-of-interval frame rows); the `extrapolated_count` report (claim G
    trigger visibility). Includes Lyra-Wheeling now that stride-2 results
    are trustworthy.
  - Decision enabled: what every M3/M4 trial is judged against.
  - Re-rank trigger: a materially shifted regression landscape vs m4
    pauses M3/M4 planning for a re-rank.
- Expected patches: 1 (report + artifacts).

### Workstream WS-2B: wrong-blob overlay taxonomy

- Owner: coder
- Interfaces:
  - Needs: nothing (existing walker decisions; Check 2 tooling pattern).
  - Provides: per-frame overlays of the accepted blob vs the seed/Hermite
    reference on the 5 selection-leaning mixed passes plus a sample of
    the 17 pure ranking passes; a per-pass classification of what wins
    instead of the torso blob (within-runner limb/trunk fragment vs
    background/spatially-distant blob) with normalized offsets.
  - Decision enabled: the M3 decision-rule branch. Settles the claim C
    position-verification caveat.
  - Re-rank trigger: overlays showing accepted blobs predominantly
    correct re-scopes M3 before any trial.
- Expected patches: 1 (workstream doc + overlay artifacts).

### Workstream WS-2C: candidate-supply census across scale

- Owner: coder
- Interfaces:
  - Needs: nothing (Check 5 method).
  - Provides: blob-count-near-reference across torso heights ~10-35 px
    over more videos/intervals; the merge/fragment crossover estimate and
    multi-candidate prevalence per scale.
  - Decision enabled: the merge-vs-DoG branch of the M3 decision rule.
  - Re-rank trigger: fragmentation rare outside the one known
    small-runner video shifts M3 weighting toward the proximity-term
    branch.
- Expected patches: 1 (workstream doc).

### Workstream WS-2D: ROI discrepancy reconciliation

- Owner: coder
- Interfaces:
  - Needs: nothing (one bounded probe).
  - Provides: a comparison of the production ROI formula against the
    earlier stall-diagnosis probe on Conant 1080-1111, both formulas
    documented; settles whether "24 of 31 frames extract blobs" was a
    wide-ROI artifact.
  - Decision enabled: a clean extraction story for the starvation
    sub-class before M4 design; removes a contradiction between two
    published documents.
  - Re-rank trigger: if Check 2's production-ROI result is the wrong one
    (runner blobs do extract in the tight ROI), claim A's refutation is
    re-examined before M3/M4 proceed.
- Expected patches: 1 (workstream doc).

M3/M4 workstreams are defined in their own plans once M2 evidence lands;
enumerating them now would fabricate detail the evidence has not provided.

## Work packages

### Work package WP-1A: implement post-bootstrap coverage seam

- Owner: coder
- Touch points: `track_runner/walker_bundle.py` (pure helper
  `count_post_bootstrap_accepts(accepts, seed_frame)`; coverage return
  becomes the named `WalkCoverage` dataclass with `accepted_count` and
  `post_bootstrap_accepted` fields; docstrings state both meanings),
  `track_runner/interval_solver.py` (gate reads
  `post_bootstrap_accepted`; stall-definition comments and two walker
  docstrings updated to "zero post-bootstrap accepted frames"),
  `track_runner/tr_schema.py` (13 -> 14; 14 into
  `GEOMETRY_AFFECTING_SCHEMAS`), `docs/TR_SCHEMA_VERSION_HISTORY.md`.
- Depends on: none (after M1 plan approval; first action is re-running
  the call-site audit and recording it).
- Acceptance criteria:
  - Call-site audit recorded as grep output or a table covering every
    direct and monkeypatched use; implementation stops if any consumer
    assumes a bare-integer second return value outside the fallback seam.
  - Gate fires for coverage `accepted_count = 1`,
    `post_bootstrap_accepted = 0`; gate behavior unchanged for all other
    shapes; gate and tests read fields by name, never by position.
  - Walker core untouched.
- Verification commands:
  - `pyflakes track_runner/walker_bundle.py track_runner/interval_solver.py track_runner/tr_schema.py`
  - `pytest tests/ -k "walker or schema"`
- Obvious follow-ons: `docs/CHANGELOG.md` entry; schema history entry;
  fix any import fallout.

### Work package WP-1B: seam tests and primary-proof validation

- Owner: coder
- Touch points: new pure-helper test module (cases from the Check 3
  per-pass table: empty; bootstrap-only; bootstrap+windowed;
  bootstrap-miss windowed-accept; BWD seed at right endpoint; a
  duplicate-frame case asserting each non-seed accept counts exactly
  once); `tests/test_walker_stall_fallback.py` (new bootstrap-only-fires
  case; fakes adapted to the new return shape);
  `tests/test_walker_flag.py` (fake updated); targeted Stage-4 re-solve
  of Conant `seed_1126_1134`; 8-pass harness run.
- Depends on: WP-1A.
- Acceptance criteria:
  - Primary proof: masked FWD pass ships Hermite with
    `walker_fallback_fwd = True`; BWD unchanged.
  - 8-pass harness EQUAL (safety check; the seam itself is proven by the
    pytest gate and the re-solve, since the harness drives `walk_driver`
    and never crosses interval_solver).
  - Full suite green.
- Verification commands:
  - `pytest tests/test_walker_stall_fallback.py tests/test_walker_flag.py`
  - `bash tests/e2e/e2e_blob_walk_baseline.sh`
  - `pytest tests/`
- Obvious follow-ons: changelog entry for the validation evidence; rerun
  any failed gate once its cause is fixed.

### Work package WP-2A: corpus A/B re-baseline

- Owner: coder
- Touch points: `tests/e2e/e2e_walker_ab.py` run + report under
  `docs/active_plans/workstreams/`; repeat-sample noise estimate;
  interval #164 debug CSV; `extrapolated_count` report.
- Depends on: WP-1B (baseline includes both fixes).
- Acceptance criteria: report published with per-pass held-out-seed
  distances, rescued/preserved/regressed table, tolerance estimate, P12
  confirmation columns, `extrapolated_count`.
- Verification commands:
  - `source source_me.sh && python3 tests/e2e/e2e_walker_ab.py` (per its
    own usage; budget caps per pass documented in the report)
  - `pytest tests/test_markdown_links.py -q`
- Obvious follow-ons: changelog entry; remove temp scripts.

### Work package WP-2B: wrong-blob overlay taxonomy

- Owner: coder
- Touch points: `_temp_*.py` overlay probe (Check 2 pattern); overlays
  under `output_smoke/`; workstream doc.
- Depends on: none.
- Acceptance criteria: per-pass wrong-winner classification
  (within-runner fragment vs background/distant) with normalized offsets
  on the 5 selection-leaning passes plus a ranked-pass sample; verdict
  feeds the M3 decision rule.
- Verification commands:
  - `source source_me.sh && python3 _temp_check_overlays.py` (temp;
    removed after handoff)
  - `pytest tests/test_markdown_links.py -q`
- Obvious follow-ons: changelog entry; remove temp scripts.

### Work package WP-2C: candidate-supply census

- Owner: coder
- Touch points: `_temp_*.py` census probe (Check 5 method); workstream
  doc.
- Depends on: none.
- Acceptance criteria: blob-count-near-reference per torso-height bucket
  across ~10-35 px; crossover estimate stated with data.
- Verification commands: as WP-2B.
- Obvious follow-ons: changelog entry; remove temp scripts.

### Work package WP-2D: ROI discrepancy reconciliation

- Owner: coder
- Touch points: `_temp_*.py` bounded probe; workstream doc documenting
  both ROI formulas and the verdict.
- Depends on: none.
- Acceptance criteria: the "24 of 31 frames" discrepancy explained with
  both formulas documented; consequence for claim A stated.
- Verification commands: as WP-2B.
- Obvious follow-ons: changelog entry; remove temp scripts.

M3/M4/M5 work packages are written in their own plans after the M2
evidence lands.

## Acceptance criteria and gates

- Per-patch gate: `pyflakes` clean on touched files; focused `pytest`
  green; one conceptual behavior change per patch -- when a patch touches
  multiple components (code, tests, schema, docs), the plan explains why
  those touches serve the same behavior change.
- Full-suite gate: `pytest tests/` green for changed-code failures;
  failures unrelated to the change are documented and triaged separately
  (per PYTEST_STYLE failure triage) so they inform rather than block.
- Integration gate (M1): the targeted masked-interval re-solve is the
  primary proof; the 8-pass harness EQUAL is a safety check only.
- Regression gate (M3/M4 trials): held-out-seed distance on the WS-2A
  baseline, judged within the WS-2A tolerance (or the categorical
  contingency) on preserved/rescued sets, corroborated by overlays.
  `accepted_fraction` is diagnostic only.
- Manual review gate: every milestone plan and every adoption decision is
  user-approved; design docs (M4) are accepted before trial plans are
  drafted.
- Schema gate: geometry-affecting bump on adoption only; harness trials
  run without a bump.

## Test and verification strategy

- Unit: pure-helper tests (WP-1B) and any new pure functions in M3/M4
  follow PYTEST_STYLE (behavioral asserts, sub-second).
- Seam/integration: `tests/test_walker_stall_fallback.py` and
  `tests/test_walker_flag.py` cover the Stage-4 fallback seam with
  injected coverage shapes -- this is where P10's behavior is proven at
  the pytest level, because the e2e baseline harness drives the walker
  via `walk_driver` and never crosses the interval_solver seam.
- E2E safety: `tests/e2e/e2e_blob_walk_baseline.py` guards walker
  decisions (EQUAL for M1; reviewed diffs for M3/M4 adoptions).
- Quality: `tests/e2e/e2e_walker_ab.py` held-out-seed A/B against the
  WS-2A baseline with its tolerance; overlay corroboration for every
  ranking/anchor adoption.
- Failure semantics: any stop condition in a milestone halts that
  milestone and reports; M3 trial failures sharing a structural cause
  escalate to redesign discussion (judgment call; three same-cause
  failures at the latest).

## Migration and compatibility policy

- Additive rollout: trials run behind A/B harness configuration first;
  default behavior changes only at adoption.
- Backward compatibility: older artifacts (schema <= 13) remain readable;
  per C10, metadata-only changes never invalidate derived artifacts;
  geometry-affecting bumps (M1 adoption: 13 -> 14; M3/M4 adoptions:
  subsequent) invalidate geometry-derived caches knowingly, with the
  tradeoff recorded in `docs/TR_SCHEMA_VERSION_HISTORY.md`.
- Deletion criteria for legacy paths: dead-code retirement (P13) stays
  outside this fix phase until the primary behavior work is stable (see
  Deferred items).
- Rollback strategy: per milestone, restore the previous implementation
  of the named functions and constants (M1: coverage function, gate
  reads, schema constants; M3: touched cost functions or trial config
  off; M4: anchor-update implementation). No data migration in either
  direction; re-solve regenerates artifacts under the active schema.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| Coverage-seam consumer mismatch (a consumer needs total count) | high | call-site audit finds a reader of the old integer semantics | coder (WP-1A) | explicit-both coverage structure; audit recorded before code changes; stop condition halts M1 |
| Anchor-advance reintroduces chained blob state | high | M4 design doc cannot answer checklist items 2-3 | architect-level review at M4 design gate | design rejected at review, not patched; checklist precedes any plan |
| M3 trial scope creep (YAML cleanup, bundled terms) | medium | trial plan touches weights/flags beyond the approved trial | reviewer | configuration boundary rule; bundling rejected at review |
| Re-baseline shifts the regression landscape | medium | WS-2A table diverges materially from m4 | planner | declared re-rank trigger pauses M3/M4 planning |
| Measurement noise masks or fakes trial effects | medium | WS-2A repeat-sample noise comparable to expected effect sizes | coder (WP-2A) | tolerance defined before trials; overlays corroborate every adoption |
| Decode-cost overruns on 4K 120 fps video | low | WS-2A passes exceeding the per-pass budget cap | coder (WP-2A) | budget caps documented in the report; UNDETERMINED recorded honestly (Check 7 precedent) |
| Plan-vs-implementation drift across milestones | medium | implemented scope diverges from the milestone's file list | reviewer | each milestone's plan names files and what-must-not-change; diffs reviewed against it |

## Rollout and release checklist

- [ ] Baseline precondition met (P12 reviewed and accepted as baseline).
- [ ] M1 exit criteria green; schema 14 recorded.
- [ ] M2 artifacts published; tolerance and re-rank triggers evaluated.
- [ ] M3 approach chosen by the decision rule; trial run; adoption
      decision recorded (bump only on adoption).
- [ ] M4 design doc accepted before any M4 trial plan.
- [ ] M5 activation evidence evaluated before any M5 planning.
- [ ] Deferred-item triggers re-checked at each milestone close
      (`extrapolated_count`, identity-jump telemetry).

## Documentation close-out requirements

- Active plan / progress tracker: this roadmap at
  `docs/active_plans/active/blob_walk_v2_fix_phase_roadmap.md` is the
  fix-phase index; each milestone links its own plan when drafted.
- `docs/CHANGELOG.md`: entry per milestone completion and per M2
  artifact batch; owner is the milestone's doer.
- Archive / closure: completed planning docs close out to `docs/archive/`
  per the active-plans convention (P12 plan and validation plan at the
  baseline precondition; each milestone plan at its close). The human
  reviewer handles repository commit workflow.

## Patch plan and reporting format

- Patch 1: Stage-4 seam -- post-bootstrap coverage and fallback gate
  (WP-1A).
- Patch 2: seam tests and primary-proof validation artifacts (WP-1B).
- Patch 3: measurement -- corpus A/B re-baseline report (WP-2A).
- Patch 4: measurement -- wrong-blob overlay taxonomy (WP-2B).
- Patch 5: measurement -- candidate-supply census (WP-2C).
- Patch 6: measurement -- ROI discrepancy reconciliation (WP-2D).
- Patch N (M3/M4/M5): defined by their own plans; reported in the same
  "Patch N: [component] [intent]" form.

## Deferred items

Outside the fix sequence. Each lists the evidence that would activate it.
Shared rule: performance and cleanup items compete with M2/M3 only when
runtime measurably blocks validation work.

- **Acceptance-box widening.** Refuted as a recovery mechanism by claim A
  on the tested stall intervals. Activates only if new overlays show
  runner blobs being box-excluded while extraction is adequate -- which
  would also reopen claim H's soft-scoring replacement as the preferred
  form (replace the hard box with soft scoring rather than enlarging a
  hard box).
- **Skip-cap / Viterbi transition geometry change.** Claim L found zero
  identity jumps; the corridor bounds the hole in practice. Activates if
  identity-jump telemetry (Check 8 method) shows threshold-exceeding
  events -- most plausibly re-checked after M4 changes anchor behavior,
  the scenario the Check 8 caveat names (stale anchor + long skip run).
- **Extrapolation hold-vs-linear (P9).** The branch never executes
  (claim G). Activates when `extrapolated_count` is non-zero in
  production runs -- plausible after M3/M4 raise accepted fractions;
  WS-2A and later A/B runs report the counter so the trigger stays
  visible.
- **Evidence normalization as a standalone fix.** Claim B MIXED with
  direction asymmetry. Lives inside the M3 decision rule (branch 4);
  activates as standalone only on the overlay pattern named there.
- **Dead-code cleanup (P13 `walk_motion_gate.evaluate`).** Superseded
  gate code kept alive by its tests. Activates after the primary behavior
  work (M3, and M4 if entered) is stable, unless the dead code directly
  blocks an approved fix. Ships as its own patch, never as a rider.
- **Cache-bypass narrowing (P17).** The `overrides_in_use` guard forces
  recomputation for the acceptance-box override, which the cache does not
  depend on. Output-neutral performance work; activates only if runtime
  measurably blocks validation (for example, WS-2A wall-clock becomes the
  bottleneck), gated by a decision-equality check like P15's.
- **Pre-pass store threading (P16).** The per-interval residual store is
  built and never consumed by the walker; threading it through
  `walk_one_direction` is a perf API change. Activates with or after P17
  under the same equality gate, when walker observation cost is the
  measured bottleneck.

## Open questions and decisions needed

- M3 approach choice -- decision owner: user, after WS-2B/WS-2C land,
  applying the binding decision rule.
- WS-2A degradation tolerance value -- decision owner: user confirms the
  proposed tolerance from the repeat-sample noise estimate before the
  first M3 trial is judged.
- M4 anchor design boundary -- decision owner: user, via the
  design-safety checklist gate.
- M5 activation -- decision owner: user, on M4 evidence.
