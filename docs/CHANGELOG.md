## 2026-06-12

### Additions and New Features

- New `tests/test_walk_cost_model.py`: synthetic-lattice unit tests for the
  pairwise velocity-delta Viterbi cost model. Covers: model-flip (constant-
  velocity runner beats stationary distractor), limb-oscillation (coherent
  center track beats alternating leg blobs), skip-bridge (gap charged once),
  boundary cases (all-skip, two-real-pick, skip-prefix), evidence tie-break
  (stronger mag wins; geometry beats bounded evidence), start-bias (bad high-
  evidence first blob does not anchor the path), structural invariants via
  `compute_path_term_breakdown` (zero delta terms before third real node; skip
  term exact; overspeed zero at limit), and sum-invariant
  (`sum(step_costs) == path_cost`).
- New `tests/test_walker_costs_config.py`: config-plumbing integration tests
  confirming `walker_costs` from the YAML schema reaches `walk_viterbi` module
  globals via `set_cost_weights`, that a partial section fails loud, and that
  the default config carries all required weight keys.
- New `tests/test_walk_io_parity.py`: parity tests for `walk_io.py` after the
  WP-IO-1 scale-back -- reader-geometry fields (`bin_factor`, processed and
  source dimensions), seed-path correctness, and the corrected `load_race_start_frame`
  loud-failure behavior on missing or invalid artifacts.

### Behavior or Interface Changes

- **Viterbi cost-model rewrite (WP-COST-1)** in
  `track_runner/blob_walk/walk_viterbi.py`. Replaces the first-order
  displacement cost (penalized motion, causing the walker to prefer stationary
  distractors over a moving runner -- audit P1-P5 findings) with pairwise
  velocity-delta scoring. New terms `WEIGHT_SPEED_DELTA` and `WEIGHT_HEADING_DELTA`
  penalize acceleration and heading changes between consecutive real
  observations (the DP-compatible form of the window-variance intent: pairwise
  deltas satisfy optimal substructure; a window mean does not). Dead module
  constants `WEIGHT_MAG_VAR` and `WEIGHT_ANGLE_VAR` (stubbed and never wired
  per audit P2) are removed. Evidence is normalized per-frame against the
  frame's strongest candidate, bounded by `WEIGHT_EVIDENCE_NORM`, making it a
  tie-breaker rather than a dominator (fixes P1 evidence-scale bug). The tight
  hard displacement prune is replaced by a soft linear cost plus a quadratic
  overspeed term above the physical envelope; a single generous hard prune at
  `ABSOLUTE_MAX_JUMP_W` is the only gate (fixes P5 always-on bootstrap slack).
  Skip is charged once per skipped frame; geometry bridges gaps via gap-
  normalized velocity (fixes P4 skip-charge semantics). `BOOTSTRAP_UNCERTAINTY_W`
  is no longer read by the DP; rename to `SEED_SEARCH_SLACK_W` recorded as a
  follow-up. Decision: variance-to-pairwise-delta is intentional design, not a
  simplification -- pairwise form is DP-compatible, and the terms couple the
  same physical model at the transition level.
- **walker_costs config section** added to
  `track_runner/track_runner.config.yaml` and validated by `tr_config.py`.
  Shipped defaults: `WEIGHT_DISPLACEMENT = 0.25` (lowered from plan's 1.0 per
  manager resolve -- evidence-forward: displacement as soft cost at this scale
  lets velocity-delta terms and evidence compete on equal footing),
  `WEIGHT_SPEED_DELTA = 1.0`, `WEIGHT_HEADING_DELTA = 0.5`,
  `WEIGHT_OVERSPEED = 4.0`, `WEIGHT_EVIDENCE_NORM = 0.5`, `SKIP_COST = 2.0`.
  Config resolution uses the existing `tr_config.resolve_config` helper (shared
  by `cli.py` and `tools/blob_walk_v2/walk_driver.py`); resolved weights flow
  through the existing frozen `WorkerContext.walker_costs` field and
  `make_pool` initargs. Decision: weights live in `tr_config`-owned YAML, not
  in `walk_io.py` (which is tool-layer glue only -- confirmed by WP-IO-1 audit).
- **Wiring gap fix (spec-review F1)**: `interval_solver._dispatch_blob_pass`
  now passes `walker_costs=getattr(context, "walker_costs", None)` to its
  `make_pool` call. Previously the Stage-4 (primary consumer) pool received
  `None` and fell back to `walk_viterbi` module defaults, ignoring config.
  `solve_queue.py`'s pool call already wired `walker_costs` correctly; the
  `interval_solver` site now matches it.
- **walk_io.py scale-back (WP-IO-1)**: `load_race_start_frame` body replaced --
  it previously re-derived race start as "end_frame of the last pre_race-tier
  interval" from `interval_scores.json` with chained `.get()` defaults that
  silently returned 0 on any shape mismatch. Production authority is
  `state_io.load_diagnostics` with direct key access on
  `data["pre_race_reference"]["race_start_frame"]`; a missing artifact, None
  `pre_race_reference` (legacy file -- re-solve required), or missing key now
  raises `RuntimeError` naming the artifact path (loud failure per
  do-not-hide-bugs-with-defaults). `walk_driver.py` basename-normalization
  mirror deleted; `walk_driver` imports `_normalize_video_basename` from
  `walk_io` (single definition).
- `tr_schema.SCHEMA_VERSION` bumped 13 -> 14. Added 14 to
  `GEOMETRY_AFFECTING_SCHEMAS`. On-disk layout unchanged from v13; v10-v14
  files remain readable. Both `diagnostics` and `torso_box_coords`
  `SUPPORTED_ARTIFACT_SCHEMAS` sets gain 14. Geometry-affecting on Stage-4-
  promoted intervals only; pure-Hermite paths byte-identical to v13. Per
  contract C10 one unified bump covers both geometry-affecting lane changes
  (WP-COST-1 and WP-P10-1). Schema history logged in
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md).

### Fixes and Maintenance

- P10 bootstrap-accept fallback correction (audit M1, plan
  [docs/archive/blob_walk_v2_p10_fix_plan.md](archive/blob_walk_v2_p10_fix_plan.md)).
  The Stage-4 Hermite fallback gate in `track_runner/interval_solver.py`
  previously fired only on zero-accepted-count passes (`accepted_count == 0`).
  A pass whose only accepted frame is the seed frame via bootstrap
  (`accepted_count == 1`, all remaining frames `soft_miss_no_blob`) was not
  gated, producing a path frozen at the seed for all non-seed frames -- strictly
  worse than Hermite. Observed on Conant-4x400-2026_April_15.mkv
  `seed_1126_1134` FWD (8-frame interval, 7 frames frozen, 3.8% incidence in
  the Check 3 sample of 26 passes). Fix: `track_runner/walker_bundle.py` gains
  `WalkCoverage` dataclass (fields: `accepted_count`, `post_seed_accepted`) and
  pure helper `count_post_seed_accepts(accepts, seed_frame)`;
  `walk_bundle_to_path_with_coverage` returns `(path, WalkCoverage)` instead of
  `(path, int)`; the fallback gate reads `coverage.post_seed_accepted == 0` by
  name. Behavior change: bootstrap-only stall passes now fall back to Hermite;
  true-zero-accept stalls and healthy passes are byte-identical. The walker core
  (`track_runner/blob_walk/`) is untouched. Tests: new
  `tests/test_walk_coverage.py` (8 unit tests, all 6 Check 3 case shapes);
  `tests/test_walker_stall_fallback.py` gains
  `test_seed_only_accepted_pass_falls_back_to_hermite` (Conant gate-decision
  unit reproduction); both monkeypatch test modules adapted to `WalkCoverage`
  return. SCHEMA_VERSION bump (13->14) deferred to the integration patch that
  owns `track_runner/tr_schema.py`. Naming: plan used `post_bootstrap_accepted`;
  user decision 2026-06-12 changed to `post_seed_accepted` everywhere.
- **Lazy import micro-fix (spec-review F2)**: `tr_config.py` module-level
  `import blob_walk.walk_viterbi as walk_viterbi` moved to a lazy import inside
  `_validate_walker_costs`. Avoids a potential future circular import since
  `tr_config` loads early in cli startup and `walk_viterbi` imports the full
  `blob_walk` package.
- **RuntimeError micro-fix (spec-review F3)**: `walk_viterbi._evaluate_path_terms`
  now raises `RuntimeError("selected path node not present in window_candidates
  for frame ...")` with frame index and candidate list when `sel_idx` stays
  `None` after the identity scan. Previously the code proceeded to index `None`,
  producing a bare `TypeError` with no diagnostic context.
- **walker_costs supply chain closed (post-review F1/F2)**:
  `solve_all_intervals` gains `walker_costs: dict = None` parameter (optional-
  by-design for diagnostic callers; production supply via cli). `cli._run_solve`
  passes `cfg["walker_costs"]` (direct key access; default config always carries
  the section) into `solve_kwargs`, which flows into `ExecutionContext` and then
  to `make_pool -> _worker_init -> set_cost_weights`. Three `getattr` defensive
  fallbacks on always-populated `ExecutionContext` fields (`bin_factor`,
  `video_frame_count`, `walker_costs`) replaced with direct attribute access
  (do-not-hide-bugs-with-defaults cleanup, N1). Tool chain: `process_video` in
  `tools/blob_walk_v2/make_walk_html_v2.py` now calls
  `walk_driver.apply_walker_costs_for_video` once per video before any walks
  run; the function was previously defined but never called, making YAML weights
  a no-op in the tool path. Full chain confirmed: cli -> solve_all_intervals ->
  ExecutionContext -> make_pool -> _worker_init -> set_cost_weights AND
  walk_driver -> apply_walker_costs_for_video -> set_cost_weights.

- **WS-2B overlay review** of the two flagged A/B regressions (see
  [docs/active_plans/workstreams/blob_walk_v2_ws2b_overlay_review.md](active_plans/workstreams/blob_walk_v2_ws2b_overlay_review.md)).
  Lyra-Hersey [840,945]: regression mechanism is per-frame blob centroid cy
  bias (~9px / 0.65 tw) at frame 892 where the runner passes a vertical pole
  (partial occlusion reduces mag to 7023 vs ~20k baseline); one corridor blob
  throughout, no ranking failure, cost model not implicated.
  Jason [12408,12596]: complete signal absence (187/188 FWD frames and 176/188
  BWD frames are soft_miss_no_blob); runner is in a dense indoor-meet pack
  where residual-motion extraction cannot isolate the target from crowd motion;
  corridor is empty, no candidates to rank, cost model not implicated.
  Both cases confirm neither regression is caused by the pairwise velocity-delta
  cost rewrite. Tile output at `corpus_walk/<video>/seed_<L>_<R>/`.

### Decisions and Failures

- `WEIGHT_DISPLACEMENT = 0.25` (lowered from plan's 1.0): manager resolved
  evidence-forward after WP-COST-1 landed. At 1.0, displacement cost dominated
  evidence for slow-moving runners in pre-race intervals; 0.25 lets the
  velocity-delta terms and normalized evidence compete on honest scale.
- Variance-to-pairwise-delta design: the plan's window-variance intent is
  implemented as pairwise velocity deltas (penalize acceleration, not deviation
  from a window mean) because the pairwise form is additive and satisfies
  optimal substructure. A window mean requires global rollback and is not
  DP-compatible. Code comment cites audit P2.
- `SEED_SEARCH_SLACK_W` rename deferred: the legacy `BOOTSTRAP_UNCERTAINTY_W`
  constant exists in `walk_motion_gate.py` but is no longer read by the DP
  (the DP reads no bootstrap slack after WP-COST-1). The rename to
  `SEED_SEARCH_SLACK_W` is recorded as a follow-up; it does not expand this
  patch's scope.
- `walker_costs` config residence confirmed in `tr_config.py` (not in
  `walk_io.py`): the WP-IO-1 audit confirmed `walk_io.py` is tool-layer glue
  that delegates to established owners; adding config parsing there would
  re-implement the pipeline boundary the existing `tr_config/resolve_config`
  path already provides.
- Walker-vs-Hermite interpretation rule recorded (user-confirmed, well
  documented): the walker is the trusted, more-accurate solver; Hermite is the
  cheap floor, gated to Stage-4-promoted intervals by CPU cost, not by quality.
  Held-out-single-seed error (`e2e_walker_ab`) is therefore NOT a
  walker-vs-Hermite quality ranking: it under-samples the interval (one frame)
  and is biased toward Hermite on smooth motion, where a small `hermite_err`
  means the held-out frame was easy, not that Hermite tracked well. Durable rule
  added to
  [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) ("Interpreting
  walker-vs-Hermite and held-out-seed error"). The held-out expansion's
  "regressed 11/13" headline is reframed accordingly; its valid signal is
  absolute multi-torso walker outliers, not the Hermite comparison.

### Developer Tests and Notes

- Add `tests/test_walk_viterbi_brute_force.py`: exhaustive brute-force
  enumeration vs DP optimality check for `walk_viterbi.select_path` across five
  lattice shapes (dense, sparse/empty-frame, +inf-edge, near-stationary, zero-
  mag evidence); 39 fixed-seed parametrized cases, all passing (WP-COST-2
  extension).
- `tests/test_walker_costs_config.py` extended with
  `test_execution_context_carries_walker_costs_to_make_pool`: asserts that the
  `make_pool` boundary receives the `walker_costs` dict from `ExecutionContext`
  (mock-captured, no video needed), closing the F1 hole class.
- **WP-VAL-1 release evidence** (frozen 34-pass + 5-video corpus A/B, 2026-06-12):
  shipped default weights re-solved all 34 check7 classified passes (22 effective-
  ranking + 10 mixed + 2 starvation) with the new pairwise Viterbi cost model.
  Ranking-class passes FWD 66.7-100%, mostly >= 83%. 5-video corpus subset: IMG_3830
  FWD 82.0% / BWD 88.5% vs before 83.6% / 88.5% (delta -1.6 pp / 0.0 pp, no
  regression). Lyra-Hersey partial 16/20 FWD 78.0%. Identity spot check across
  18 intervals: PASS, no cross-athlete captures. Tuning evaluation: 4 weight configs
  (including mandatory evidence-forward config with WEIGHT_EVIDENCE_NORM = 1.0)
  all produced identical results on the fast subset -- default weights are the
  winning config. `e2e_blob_walk_baseline`: exit code 0, golden snapshot passed.
  `e2e_walker_ab`: IMG_3830 preserved=2, regressed=3; IMG_3823 preserved=2,
  regressed=3 (Jason in progress). Artifact:
  [docs/active_plans/workstreams/blob_walk_v2_cost_model_ab.md](active_plans/workstreams/blob_walk_v2_cost_model_ab.md).
  Roadmap: P1/P2/P3/P4/P5 and P10 marked fixed in
  [docs/active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md).
  **Completion (2026-06-12)**: all 5 corpus videos complete (20/20 intervals each); no
  regression on any video (max delta -2.1 pp Conant FWD/BWD vs -2.1 pp baseline shift);
  identity check extended to 39 intervals, PASS; e2e_walker_ab 4/6 complete (Lyra-Hersey
  preserved=4/regressed=1, Conant rescued=3/preserved=2; Jason and Lyra-Wheeling pending
  due to concurrent process log collision). Release-review summary (governance package for
  human accept/reject) inserted at top of
  [docs/active_plans/workstreams/blob_walk_v2_cost_model_ab.md](active_plans/workstreams/blob_walk_v2_cost_model_ab.md).
- Held-out error expansion
  [docs/active_plans/workstreams/blob_walk_v2_heldout_expansion.md](active_plans/workstreams/blob_walk_v2_heldout_expansion.md):
  13 mid-span held-out triples (spans 15-45, 4 videos), blended-path error.
  Raw numbers walker median 1.105 vs Hermite 0.100 torso-widths; reframed per
  the walker-vs-Hermite interpretation rule above (the instrument is
  Hermite-biased on easy frames, so this is not a quality ranking). Valid
  signal: absolute walker outliers of 2-3 torso-widths (IMG_3830 [1288,1308],
  IMG_3823 [2316,2337]) flagged for eyes-on tile review. Every row drove the
  walker; the P10 fallback fired on zero rows.
- Corpus-120 schema-14 control run
  [docs/active_plans/workstreams/blob_walk_v2_corpus120_schema14.md](active_plans/workstreams/blob_walk_v2_corpus120_schema14.md):
  same 100 intervals across 5 videos as the schema-13 baseline (Lyra-Wheeling
  skipped, 6 h decode). FWD 59.7% vs 58.9% (+0.8), BWD 60.8% vs 59.8% (+1.0);
  every video and direction at or above baseline, max delta +1.2; all 100
  intervals stop on `hit_neighbor_seed`. Accepted-fraction is a coverage metric,
  not positional accuracy.
- Weight-sensitivity sweep (SIDE QUEST)
  [docs/active_plans/workstreams/blob_walk_v2_weight_sensitivity.md](active_plans/workstreams/blob_walk_v2_weight_sensitivity.md):
  6 extreme configs on the 24-pass subset. SKIP_COST is the only load-bearing
  weight (halving it drops accepted fraction 9.8 pp); delta and evidence terms
  near-inert on short high-confidence intervals; multi-candidate fraction 82%.
  Explains why the original 4-config tuning was flat -- 2x nudges were
  insufficient to flip rankings.
- Second pre-merge audit (six independent reviewers, read-only on the frozen
  bundle): no code-correctness blocker. Findings are packaging and hygiene --
  the CHANGELOG ordering blocker in this 2026-06-12 block (now fixed: the two
  `Developer Tests and Notes` sub-blocks merged into one placed last), five
  planning-tag rewords in code comments, four unstaged doc-truth fixes required
  to ride with the bundle, and fragile-test pruning. Artifact:
  [docs/active_plans/audits/blob_walk_v2_bundle_audit_run2.md](active_plans/audits/blob_walk_v2_bundle_audit_run2.md).

- New artifact
  [docs/active_plans/reports/blob_walk_v2_starvation_characterization.md](active_plans/reports/blob_walk_v2_starvation_characterization.md):
  read-only characterization of the 12 starvation-class passes (7 pure starvation
  + 5 starvation-leaning mixed from check7). Per-pass table with baseline empty
  fractions, post-rewrite accepted fractions, and seed-cold status. Key findings:
  (a) cost-model rewrite did not touch starvation (structurally upstream of
  Viterbi -- empty lattice before the cost model runs; all 4 weight configs
  produced identical results on starvation passes); (b) physical condition is
  small apparent runner size (torso ~11 px proc), where DoG resolves limb-level
  blobs or falls below detection threshold (claim D crossover 11-30 px, plus
  drift-stall sub-type for Conant); (c) frequency: 10.8% of all corpus
  interval-directions show seed-cold symptoms (corpus120 proxy), 35% of the m4
  regressed bucket has starvation as primary or significant factor (check7).
  Jason is the dominant contributor at 35% of its interval-directions. No fix
  proposals; mechanism, frequency, and impact only.

- Short-span interval frequency study completed. Measured span distribution
  across all 12 corpus videos via `_temp_span_study.py` (deleted after run).
  Corpus: 6274 intervals, 170372 frames. Short-span (1-13 frames): 57.3% of
  intervals by count but only 8.8% of frames by weight. Among those, only 3.4%
  are Stage-4-promoted (low/fair tier), yielding ~0.3% of corpus frames in
  short-span Stage-4 intervals -- the Viterbi cost-model degenerate bucket is
  tiny by frame-weighted impact. Distribution is bimodal: four densely-seeded
  videos (IMG_3830, IMG_3823, Hononega-Orion_600m, Hononega-Varsity_4x400m)
  dominate short-span counts; five coarsely-seeded videos have almost none.
  Artifact:
  [docs/active_plans/reports/blob_walk_v2_short_span_frequency.md](active_plans/reports/blob_walk_v2_short_span_frequency.md).

## 2026-06-11

### Additions and New Features

- Created `docs/TROUBLESHOOTING.md` (first entry: walker / corpus runs on 4K HEVC sources
  take hours). Documents cause (HEVC random-access seek cost ~450-550 ms vs 6-14 ms
  sequential per [common_tools/README.md](../common_tools/README.md) strategy table),
  notes auto-bin does not reduce seek cost (binning is post-decode), links the access-pattern
  fix roadmap (P16/P17) parked in
  [active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md),
  and confirms runs are slow but not broken.

- Added runtime notice in `tools/blob_walk_v2/make_walk_html_v2.py` `process_video`:
  when source width >= 3840 or source height >= 2160 (derived from
  `reader.geometry.source_width` / `reader.geometry.source_height`), emits one
  `logger.info` line noting the random-access seek cost (~0.5 s single-process,
  multi-second under parallel load) and pointing at `docs/TROUBLESHOOTING.md`.
  The same warning now also fires at initial metadata parse in
  `common_tools/probe_video.py`, covering every production probe consumer.
  No behavior change; log only. Post-review polish: both messages point at
  `docs/TROUBLESHOOTING.md` rather than roadmap item tags; the probe comment
  states the measured mechanism (forward decode from the nearest preceding
  keyframe in long-GOP HEVC -- the corpus test files carry no B-frames per
  the `common_tools/README.md` probe) instead of a B-frame claim;
  `probe_video.py` gains a module-level logger, a docstring `Warns:` section,
  and a single-f-string message.

- Drafted the M1 fix plan
  [docs/archive/blob_walk_v2_p10_fix_plan.md](archive/blob_walk_v2_p10_fix_plan.md)
  (P10 bootstrap-accept fallback masking; implemented 2026-06-12, archived) from
  the roadmap's M1 section, including the recorded call-site audit of
  `walk_bundle_to_path_with_coverage` (single production consumer at the
  `interval_solver` fallback gate; two monkeypatching test modules; the
  audit-triggered stop condition is not met). No production code changed.

### Fixes and Maintenance

- Validation report clarity edits (documentation only, no code change): (1) Claim
  C section now states percentages are of 34 classified passes, explaining the 49%
  vs 50% discrepancy with the source artifact header. (2) Claim B section breaks
  pooled 44 multi-candidate frames into FWD 26 (ev_match 3/26=11.5%,
  disp_match 25/26=96.2%) vs BWD 18 (ev_match 16/18=88.9%) to expose the
  direction asymmetry obscured by the pooled 43.2% figure. (3) Fixed false
  preamble "No implementation details are provided" in the fixes section --
  P12 and P10 entries contain exact fix directions verbatim from workstream
  artifacts; replaced with accurate wording noting they are candidate directions
  requiring user-approved plans. (4) Added user-directed design-orientation note
  (2026-06-11): goal is better tracking with LESS gating; prefer removing or
  softening wrong guards over adding new ones; claim-A caution does not endorse
  keeping hard exclusion long-term; replacing hard exclusion with soft scoring
  (claim H direction) remains design-aligned once candidate supply is understood.

- Cost telemetry unified in `track_runner/blob_walk/walk_viterbi.py`: `compute_path_cost`
  now delegates to `compute_path_step_costs` and returns `sum(step_costs)`, making the
  sum invariant (sum(steps) == total) structural rather than comment-only. Required blob
  keys (`integrated_mag`, `centroid_x`, `centroid_y`) in `compute_path_step_costs` now
  accessed directly per do-not-hide-bugs-with-defaults (was `.get(key, 0.0)`); skip nodes
  (None) retain explicit handling. Defensive index guard `path_step_costs[k] if k < len(...)`
  removed from `walk_walker.py` stamping; replaced with direct `path_step_costs[k]` since
  `compute_path_step_costs` returns one entry per path node by construction. Decision logic
  (`select_path`, `transition_cost`) unchanged; 8-pass equality harness all PASS.

- Blob walker stride>1 termination overrun fixed (audit P12, observed on 120fps
  source interval frames 16588-16591). Equality test `frame_f == neighbor_seed_frame`
  replaced by directional crossing test with clamp in new pure helper
  `_neighbor_reached` (`track_runner/blob_walk/walk_walker.py`); seed endpoints
  remain anchors and are never observed. SCHEMA_VERSION bumped 12->13;
  geometry-affecting for stride>1 (>=~90fps) sources only, byte-identical at
  stride 1. Validation: 7 new unit tests in `tests/test_walk_neighbor_reached.py`
  (including interval #164 FWD/BWD exact sequences); 8-pass stride-1 harness EQUAL
  on all passes. Two caveats: (a) stride-1 preservation rests on analytic predicate
  equivalence proof plus unit tests plus run-to-run determinism -- no true pre/post
  empirical snapshot exists because the stored reference walks postdate the fix;
  (b) the real 120fps interval #164 (Lyra-Wheeling, 4K HEVC) was not re-solved
  post-fix due to expensive decode cost; unit tests cover the exact frame arithmetic
  for that interval. Full pytest suite 1578 passed. Plan:
  [blob_walk_v2_p12_fix_plan.md](active_plans/active/blob_walk_v2_p12_fix_plan.md).

### Developer Tests and Notes

- Claim G offline replay completed (check G workstream). Zero `extrapolated`
  or `interpolated` frames found in 24 walk debug CSVs (366 `soft_miss_no_blob`,
  282 `accepted`, 24 `after_walk_terminated`). Audit P6 confirmed: both statuses
  are flush-only. P9 spec deviation confirmed: `walk_status.py` lines 114-116
  implement HOLD instead of linear extension for `extrapolated` status. Empirical
  comparison with available data is indeterminate (reference-line curvature
  contaminates the replay). Synthetic uniform-motion analysis supports LINEAR
  BETTER (59/59 scenarios). Verdict: UNDETERMINED -- practical impact zero at
  current accepted fractions; fix deferred until `extrapolated_count` becomes
  non-zero. Artifact:
  [docs/active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md](active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md).
  Validation report claim G updated from STILL UNKNOWN to UNDETERMINED.

- Corpus-120 walk run completed (post-P12, SCHEMA_VERSION 13, rng_seed=None fresh
  sample): 6 videos x 20 random visible-both intervals. All 120 intervals completed
  with stop_reason `hit_neighbor_seed`; all 6 manifest PASS checks passed. Per-video
  FWD/BWD accepted_fraction (pooled over interval lengths): IMG_3830 83.6%/88.5%,
  IMG_3823 55.8%/54.5%, Jason 40.2%/39.0%, Lyra-Hersey 82.7%/78.0%, Conant
  67.9%/76.2%, Lyra-Wheeling 58.0%/62.4%. Corpus: 58.5% FWD / 61.1% BWD. The
  elevation vs L3 reference (38.7%/39.1% on 120-corpus) is primarily sampling
  variation from the fresh-sample selection; a fixed-seed A/B (WS-2A) is required
  before the number is authoritative. Lyra-Wheeling (stride 2, 120 fps): 13 of 20
  sampled intervals have an odd half-span (prime P12 trigger candidates); all report
  `hit_neighbor_seed`, consistent with the termination fix. No walk debug CSVs are
  written by this tool path; per-frame P12 verification remains in unit tests. Elapsed
  times: IMG_3830 0:28, IMG_3823 0:20, Jason 52:22, Lyra-Hersey 25:48, Conant 31:19,
  Lyra-Wheeling 6:11:13. Artifact:
  [active_plans/workstreams/blob_walk_v2_corpus120_run_2026_06_10.md](active_plans/workstreams/blob_walk_v2_corpus120_run_2026_06_10.md).

- Blob walk v2 validation complete: all 9 checks (Check 0 through Check 8) executed
  and synthesized. Final synthesis report:
  [active_plans/reports/blob_walk_v2_validation_report.md](active_plans/reports/blob_walk_v2_validation_report.md).
  Claim verdicts:
  - A REFUTED -- rejected blobs on stall intervals are background athletes, not the runner;
    widening the acceptance box would not recover runner signal.
  - B MIXED -- evidence term dominates per-node cost but window-level DP accumulates
    spatial momentum that attenuates the evidence signal on FWD passes (Jason FWD:
    0/23 ev_match, 22/23 disp_match); BWD passes behave differently due to fewer
    competing blobs.
  - C RANKING-DOMINANT -- of 35 m4 A/B regressed intervals, 17 ranking-driven,
    10 mixed, 7 starvation-driven, 1 pending (Lyra-Wheeling 120 fps excluded per K).
  - D REFUTED (conditional) -- limb merging is size-dependent: large runners (Conant,
    30 px torso) merge to one blob; small runners (Jason, 11 px torso) show 4-6
    distinct limb-level blobs per frame.
  - E OBSERVED -- within-body vertical centroid jitter on Jason (11 px torso): Viterbi
    selection alternates between lower-body (ncy ~ -0.4) and upper-body (ncy ~ +0.2)
    clusters; alternation rate 0.26 flips/step; 85% of steps stay within 0.10 torso
    heights.
  - F CONFIRMED (structural) -- anchor is always 9+ frames stale in steady state
    (window depth = WALKER_WINDOW_FRAMES = 9); quality impact conditional on image-space
    drift per standing constraint P8.
  - G UNDETERMINED -- extrapolation-vs-hold-last comparison not exercised in
    sample (label updated same-day from STILL UNKNOWN by the check G replay;
    see the claim G entry above).
  - I CONDITIONALLY CONFIRMED -- anchor staleness is present at every rejection; two
    distinct stall sub-mechanisms: (a) image-space drift (Conant, 2.35 TW over 31 frames),
    where anchor-advance would help; (b) near-stationary runner (Jason) where L4 centroid
    offset + P11 acceptance-box exclusion is operative and anchor-advance alone would not
    cure it.
  - J OBSERVED -- bootstrap-accept masking of the pure-stall Hermite fallback occurs in
    1 of 26 passes (3.8%); the bootstrap frame counts as an accepted frame and prevents
    the zero-accepted-count fallback from firing even when the remaining frames are all
    misses.
  - K OBSERVED FAILURE -- stride-2 stepping/termination bug (audit P12) is live on
    interval 16588-16591 of Lyra-Wheeling-IMG_3912.mkv (120 fps, stride 2); walker
    results on that video are suspect until the stepping fix lands.
  - L NOT EXERCISED in sample -- pooled P99 accepted-to-accepted displacement 0.578 W;
    zero events exceed the identity-jump threshold (0.878 W); structural hole in Viterbi
    (no cap on skip-to-blob transitions) confirmed real but not manifesting in this
    8-pass sample.
  - H INFORMED, NOT PROVEN -- gate redesign direction (replace hard exclusion with soft
    scoring) is design-aligned with the user-directed orientation (better quality, less
    gating); no trial yet.
  Design orientation (user-directed 2026-06-11): goal is better tracking with less
  gating; prefer removing or softening wrong guards over adding new ones. Every fix
  still requires its own user-approved plan.
  Check 0 (stride-2 overrun, K) and Check 3 (bootstrap-accept masking, J) were
  completed during the validation but not individually logged in this changelog;
  their artifacts are at
  [active_plans/workstreams/blob_walk_v2_check0_stride_overrun.md](active_plans/workstreams/blob_walk_v2_check0_stride_overrun.md)
  and
  [active_plans/workstreams/blob_walk_v2_check3_bootstrap_masking.md](active_plans/workstreams/blob_walk_v2_check3_bootstrap_masking.md).

- Validation closeout additions (documentation only): (1) Report restructured
  with three explicit verdict groups -- PROVEN, REFUTED (see A and D), STILL
  UNKNOWN (see B, G, H, L) -- as a grouped table above the per-claim sections.
  (2) Claim G confirmed UNDETERMINED: `extrapolated` status never executes in
  current corpus; code path dead at current accepted fractions; spec deviation
  (P9 HOLD vs linear) deferred; see
  [active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md](active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md).
  (3) Claim C sharpened with Check 7 addendum 65%/35% effective ranking/starvation
  split; mixed-bucket diagnosis done (accept_on_nonempty >= 0.88 on all 10 mixed
  passes; wrong-blob-wins inferred, not position-verified). (4) Open items updated:
  Lyra-Wheeling 35th interval UNDETERMINED (timeout), mixed-bucket diagnosis marked
  complete with caveat, Claim G open item updated from pending to measured-but-
  undetermined. Stray walk process (PID 10595, _temp_check7b_lyra_wheeling.py,
  67h CPU) confirmed dead after pkill.

## 2026-06-10

### Additions and New Features

- Published blob walk v2 fix-phase roadmap at
  [active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md)
  as the fix-phase index. Evidence basis: implementation audit, validation
  report, and Check 0-8 / Check G workstream docs. The roadmap orders five
  milestones -- M1 P10 fallback correction, M2 re-baseline and ranking
  evidence as four parallel workstreams, M3 ranking-quality trial bound to
  an evidence-keyed decision rule, M4 anchor-advance design phase behind a
  safety checklist, M5 conditional emission redesign -- with acceptance of
  the P12 stride termination fix as a baseline precondition, and parks
  contraindicated items (acceptance-box widening, standalone evidence
  normalization, skip-cap, extrapolation, P13/P16/P17) with their
  activation evidence.

### Fixes and Maintenance

- Stage-4 walker dispatch now uses the worker pool -- `blob_pass` threads
  through the pool initializer as run-invariant worker context, removing the
  in-process-only special case.
- Validation Check 1 / audit P15: made the walk debug CSV `path_cost` column
  truthful. It was documented as a per-frame Viterbi contribution but stamped
  the whole-window total on every row. `path_cost` is now documented as the
  whole-window total (its actual value, unchanged), and two new telemetry
  columns are added per spec section 7: `path_step_cost` (per-frame node cost
  contribution = local node cost + transition into it, via the new pure
  `walk_viterbi.compute_path_step_costs` helper) and `window_head_frame`
  (source frame index of the window head at decision time). Telemetry only:
  field-wise decision equality (selected path, statuses, positions, accepted
  counts, fallback signal) was confirmed exact on the two diagnosed stall
  intervals (Conant 1080-1111 FWD, Jason 564-583 FWD) plus steady-state
  intervals via the `e2e_blob_walk_baseline` golden. The
  `compute_path_step_costs` helper reads the already-selected path only; the
  Viterbi DP selection (backpointers, argmin, costs) is unchanged. Unified
  `SCHEMA_VERSION` bumped 11 -> 12 (metadata-only; not geometry-affecting); see
  [TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md). Workstream
  artifact:
  [active_plans/workstreams/blob_walk_v2_check1_p15_fix.md](active_plans/workstreams/blob_walk_v2_check1_p15_fix.md).

### Developer Tests and Notes

- Check 7 completion addendum: (1) Lyra-Wheeling 754-981 (span 227, 4K 120fps,
  stride 2) UNDETERMINED -- FWD walk exceeded 45 min budget cap before completing;
  stride-2 termination bug present on this video. Verdict unaffected: ranking still
  largest group (17 vs 7 starvation vs 10 mixed). (2) Mixed-bucket per-pass
  diagnosis: 10 mixed passes re-walked with in-memory per-frame status capture.
  Key finding: accept_on_nonempty >= 0.88 across all 10 passes -- when candidates
  are present the walker accepts one; regression is wrong-blob-wins, not path
  rejection. Sub-class: starvation-leaning=5, selection-leaning=5. Effective
  ranking-driven fraction (including selection-leaning mixed) is 22/34 = 65%.
  Effective starvation fraction (including starvation-leaning mixed) is 12/34 = 35%.
  Workstream doc updated:
  [docs/active_plans/workstreams/blob_walk_v2_check7_regressed_split.md](active_plans/workstreams/blob_walk_v2_check7_regressed_split.md).

- Check 7 (claim C, regressed-bucket split): re-ran the 35 m4 A/B regressed
  intervals via `walk_one_direction` with a null log to capture
  `WalkSummary.soft_miss_no_blob_count` per pass. Result: ranking-driven 17,
  mixed 10, starvation-driven 7, pending 1 (Lyra-Wheeling, 4K 120fps). Verdict:
  RANKING-DOMINANT. `soft_miss_no_path` is near zero across all passes; wrong-blob-
  wins is the failure mode, not displacement-cap rejection. Cost trial (Check 6
  / claim B) addresses 2-3x more regressions than the box trial (Check 2 / claim A).
  Artifact:
  [active_plans/workstreams/blob_walk_v2_check7_regressed_split.md](active_plans/workstreams/blob_walk_v2_check7_regressed_split.md).

- Published read-only implementation audit of the blob_walk v2 walker at
  [active_plans/audits/blob_walk_v2_implementation_audit.md](active_plans/audits/blob_walk_v2_implementation_audit.md).
- Validation Check 2 (claim A): rendered rejected-blob overlays on the two
  diagnosed stall intervals (Conant 1080-1111 FWD, Jason 564-583 FWD) and
  measured per-blob distance from the frozen-anchor seed reference. Claim A
  ("rejected blobs are the runner's blobs") is REFUTED: Jason's 195 rejected
  blobs have median distance 5.97 torso-widths from the runner (0.5% within
  1W, 5.1% within 2W); Conant's tight ROI yields near-noise residual on 30/31
  frames. Blobs in wider search areas are from other athletes 7-24W away.
  Widening the acceptance box would not recover runner signal. Root cause of
  the stall is below-threshold runner signal (Jason: 3 px wide torso) and
  background domination, not a misplaced acceptance box. Artifact:
  [active_plans/workstreams/blob_walk_v2_check2_rejected_overlays.md](active_plans/workstreams/blob_walk_v2_check2_rejected_overlays.md).
  PNG overlays under `output_smoke/blob_walk_v2_check2/`.
  Findings are separated into proven / likely / assumption tiers; no behavior
  changes result from this audit alone.
  - **Proven findings:** Viterbi evidence term uses raw `integrated_mag` instead
    of the spec's normalized confidence; spec'd velocity-variance and
    angle-variance consistency cost terms are defined but never implemented;
    walker emits the oldest window frame instead of the spec's center frame,
    making `interpolated`/`extrapolated` statuses structurally unreachable in
    steady state; candidate lists are pre-filtered by the acceptance box around
    an anchor stale by the window depth; bootstrap observation counts as an
    accept and can mask the pure-stall Hermite fallback; walk debug log
    `path_cost` column stamps the whole-window total while documented as a
    per-frame contribution; latent stride>1 stepping/termination bug for
    >=90 fps sources.
  - **Audited clean:** coordinate handling, FWD/BWD temporal symmetry, pool
    integration.
  - All behavior changes are blocked behind the report's assumption table.
- Corpus FPS probe (audit claim K): `data/outdoor_corpus.txt` videos run at
  30/30/60/60/60/120 fps. `Lyra-Wheeling-IMG_3912.mkv` is 120 fps (stride 2),
  so the stride>1 stepping/termination bug (audit P12) is LIVE on 1 of 6
  corpus videos, not latent. Walker results on that video are suspect until
  the stepping fix lands.
- Added [active_plans/active/blob_walk_v2_validation_plan.md](active_plans/active/blob_walk_v2_validation_plan.md):
  ordered smallest-first checks for the audit's assumption table (P15
  telemetry truthfulness first as the only code change, then rejected-blob
  overlays, bootstrap-accept masking counts, anchor-lag telemetry,
  normalized-cy trace, per-term cost telemetry, regressed-bucket split,
  identity-jump count). No walker behavior trials until gating claims are
  proven and each trial is separately approved.
- Check 4: anchor-lag telemetry (claims F and I) complete. Walk debug CSVs
  from 4 baseline intervals (Conant bootstrap/steady-state, Jason early/steady-state)
  used to measure anchor_age_at_observation via the new `window_head_frame` column.
  Key findings:
  - Steady-state anchor age is exactly 9 frames (= WALKER_WINDOW_FRAMES), confirming
    audit P6+P8 predictions. All rejections in all 8 passes have anchor_age >= 7 frames.
  - Claim F CONFIRMED (structural): anchor is always 9+ frames stale in steady state;
    quality impact conditional on image-space drift per standing constraint P8.
  - Claim I CONDITIONALLY CONFIRMED: anchor staleness is present at every rejection.
    Two stall sub-mechanisms found: (a) Conant 1080-1111 FWD -- runner image-space drift
    reaches 2.35 TW over 31 frames (24/31 frames outside acceptance half-width); anchor-
    advance would help. (b) Jason 564-583 FWD -- runner is near-stationary in image space
    (max drift 0.53 TW), so L4 centroid offset + P11 acceptance-box exclusion is the
    operative mechanism; anchor-advance alone would NOT cure this case. No
    production code changes. Workstream artifact:
    [active_plans/workstreams/blob_walk_v2_check4_anchor_lag.md](active_plans/workstreams/blob_walk_v2_check4_anchor_lag.md).
- Check 6: per-term cost telemetry (claim B). Measured whether evidence
  (WEIGHT_EVIDENCE * integrated_mag) or displacement dominates Viterbi path
  selection on real corpus data. Key numbers from 87 accepted frames across 5
  usable passes (4 baseline intervals, 7 passes attempted, 3 excluded for zero
  accepted or missing CSV):
  - Static dominance confirmed: evidence cost median -559 vs displacement cost
    median 0.000 (ratio infinite; displacement is near-zero on most accepted
    frames; max displacement 2.0 W only on bootstrapped pairs).
  - Dynamic: on 44 multi-candidate distinct frames, selected == max-evidence
    43.2% pooled (FWD 11.5%, BWD 88.9%); selected == min-displacement 93.2%
    pooled (FWD 96.2%, BWD 88.9%).
  - Claim B verdict: MIXED. Evidence dominates the per-node cost but not the
    path-choice outcome. The window-level DP accumulates transition costs over 9
    frames; by decision time, accumulated spatial momentum dominates and the path
    follows the min-displacement candidate rather than the max-evidence candidate
    on FWD passes (Jason FWD: 0/23 ev_match, 22/23 disp_match). BWD passes
    behave differently (10/10 ev_match) likely because fewer competing blobs and
    larger mag ratios leave displacement and evidence co-aligned. Duplicate-blob
    issue in Conant candidates_json collapses Conant FWD to 3 genuine
    multi-candidate frames (all trivially co-aligned). No production code changes.
    Artifact:
    [active_plans/workstreams/blob_walk_v2_check6_per_term_cost.md](active_plans/workstreams/blob_walk_v2_check6_per_term_cost.md).
- Check 8: identity-jump count (claim L). Measured per-step accepted-to-accepted
  displacement in torso-width units across 8 passes (4 baseline intervals, 2 videos).
  Pooled P99 = 0.578 W; identity-jump threshold (P99 + 0.3 W) = 0.878 W.
  Zero events exceed the threshold (max observed = 0.614 W). Seven skip-bridging
  steps exist (accepted frames that cross a skip gap), with max displacement
  0.231 W -- well below the threshold and well within the ~0.80 W corridor
  radius. Claim L verdict: NOT EXERCISED in sample. The structural hole
  (no displacement cap on skip-to-blob Viterbi transitions) is real but does
  not manifest in this 8-pass sample; the corridor filter acts as a soft outer
  bound even without the Viterbi cap. Bootstrap-stall intervals produce zero
  accepted frames and zero steps, so worst-case skip runs are not represented
  in step terms. No production code changes. Artifact:
  [active_plans/workstreams/blob_walk_v2_check8_identity_jumps.md](active_plans/workstreams/blob_walk_v2_check8_identity_jumps.md).
- Check 5: normalized-cy trace (claims D and E). Measured per-frame
  normalized vertical blob position ncy = (cand_cy - pred_cy) / torso_h
  for walker-selected blobs across 4 baseline intervals (Conant bootstrap and
  steady-state, Jason early and steady-state; Lyra-Wheeling excluded, P12
  live bug). 88 accepted frames with ncy across 6 non-empty pass directions.
  Claim D (limbs merged into one broad blob): REFUTED for small runners,
  SUPPORTED for large runners. Conant (30px torso): 97-100% of frames have
  exactly 1 blob near reference -- DoG merges the runner. Jason (11px torso):
  every frame with candidates has 4-6 distinct blobs within 1 torso-width;
  limb-level separation is observed. Limb merging is not universal; it
  depends on apparent runner size. Claim E (within-body vertical centroid
  jitter): OBSERVED on Jason. Jason/seed_602_629/FWD has 4-6 competing blobs
  spanning the full torso vertical extent; Viterbi selection jumps between
  lower-body (ncy ~ -0.4) and upper-body (ncy ~ +0.2) clusters, with a
  max single-step |delta ncy| = 0.384 torso heights. Global alternation rate
  is 0.26 flips/step; 85% of steps stay within 0.10 torso heights. Conant
  shows mild slow drift (no sharp jitter), consistent with one merged blob
  tracking center-of-mass through a stride. No production code changes.
  Artifact:
  [active_plans/workstreams/blob_walk_v2_check5_normalized_cy.md](active_plans/workstreams/blob_walk_v2_check5_normalized_cy.md).

## 2026-06-09

### Behavior or Interface Changes

- The windowed Viterbi walker (`track_runner/blob_walk/`) now solves
  Stage-4-promoted intervals by default. `solve_interval_analytical`,
  `_dispatch_blob_pass`, and `solve_all_intervals` default their walker
  seam on; the Stage-3 dispatch sites (`solve_queue`, `solver_workers`)
  pass `blob_pass=False` explicitly so Stage 3 stays pure Hermite on
  every interval, and the no-reader test/diagnostic paths stay pure
  Hermite. A per-interval, per-pass Hermite fallback fires on walker
  stall (zero accepted frames -> that pass uses its Hermite path), so
  default-on is never worse than Hermite on promoted intervals. The
  fallback reads the walker's own `WalkSummary.accepted_count`, not
  `raw_pred` and not FWD/BWD agreement, preserving Hermite independence.
  The underlying bootstrap-stall root cause remains open; Viterbi weight
  tuning and a promoted-only A/B are follow-up work.
- (Superseded same day) Earlier on 2026-06-09 the default was pure
  Hermite on every promoted interval with the walker opt-in behind the
  now-removed `--walker-stage4` flag; the change above flips that default
  on for promoted intervals.

### Removals and Deprecations

- Removed the experimental `--walker-stage4` CLI flag from solve and refine
  parsers per argparse minimalism; the internal `use_walker` seam (default
  False) in `interval_solver.py` is preserved as the future promotion path.
- Deleted v1 blob-snap layer from `track_runner/velocity_model.py`:
  `_apply_blob_snap`, `_motion_path_ok`, `BLOB_SNAP_ALPHA`,
  `BLOB_SNAP_PATH_SLACK`, `BLOB_SNAP_PATH_PERP_FRACTION`,
  `BLOB_SNAP_VELOCITY_FLOOR`, `BLOB_SNAP_ALPHA_MAX`,
  `BLOB_SNAP_MAX_SHIFT_FRACTION`.
- Deleted `BlobGateTrace` dataclass from `track_runner/blob_trace.py`
  (produced only by the deleted `_apply_blob_snap`; distinct from the
  preserved `BlobObserverTrace` used by the walker).
- Removed `blob_snap_enabled` propagator parameter, `blob_gate` per-frame
  stamp, `blob_coverage_fwd`/`blob_coverage_bwd` coverage diagnostic, and
  `propagated_with_blob_snap` source string.

### Fixes and Maintenance

- Renamed internal solve parameter `use_walker` / `stage4_walker` to `blob_pass`
  across `track_runner/interval_solver.py`, `track_runner/solve_queue.py`,
  `track_runner/solver_workers.py`, `tests/test_walker_flag.py`,
  `tests/test_walker_stall_fallback.py`, and `tests/e2e/e2e_walker_ab.py`.
  The old names read as an experiment toggle; `blob_pass` reflects the true
  role: distinguishing Stage-3 pure-Hermite dispatches (`blob_pass=False`)
  from Stage-4/5 windowed-walker dispatches (`blob_pass=True`). No behavior
  or default change.

- Updated [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md),
  [docs/TR_FWD_BWD_MODEL_METHODOLOGY.md](TR_FWD_BWD_MODEL_METHODOLOGY.md),
  [docs/TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md), and
  [docs/ROADMAP.md](ROADMAP.md) to reflect the current state: walker is the
  default blob pass on promoted intervals (`blob_pass=True`); `--walker-stage4`
  flag removed; `use_walker`/`stage4_walker` seam names replaced with
  `blob_pass`; "experimental" labels removed from ASCII diagrams and prose.
  Also removed v1 live-mechanism descriptions (`_apply_blob_snap` ASCII
  pipeline, three-gate list, `snap_pred` terminology).

- Doc path corrections (blob_walk absorption review): updated
  [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) to point the
  windowed path-selection walker section at its new home
  `track_runner/blob_walk/` (relocated from `tools/blob_walk_v2/`); added
  clarifying note that the walker is wired behind the default-off
  `--walker-stage4` flag; updated the C10 compliance note to reflect that
  the walker schema version now reads `tr_schema.SCHEMA_VERSION`.
- Updated [docs/FILE_STRUCTURE.md](FILE_STRUCTURE.md) to add entries for
  the new `track_runner/blob_walk/` subpackage (all six modules) and
  `track_runner/walker_bundle.py`.
- Repointed dead `tools/blob_walk_v2/core/walk_io.py` and
  `tools/blob_walk_v2/core/walk_walker.py` references in
  [docs/active_plans/decisions/walker_npz_coord_contract.md](active_plans/decisions/walker_npz_coord_contract.md)
  and
  [docs/active_plans/active/typed_coordinate_space_plan.md](active_plans/active/typed_coordinate_space_plan.md)
  to `track_runner/blob_walk/walk_io.py` and
  `track_runner/blob_walk/walk_walker.py`.
- Rotated [docs/CHANGELOG.md](CHANGELOG.md): 1256 lines exceeded the
  ~1000-line threshold; kept day blocks 2026-06-08 and 2026-06-07 in the
  active file; moved 2026-06-03 through 2026-05-03 into new archive
  [docs/CHANGELOG-2026-06a.md](CHANGELOG-2026-06a.md) (named for the most
  recent month in the archived range per REPO_STYLE.md).
- Archived two v1-era analysis docs to `docs/archive/` with a `V1_` prefix
  (`docs/SOLVE_STAGE_FACTORABILITY_NOTE.md` ->
  [docs/archive/V1_SOLVE_STAGE_FACTORABILITY_NOTE.md](archive/V1_SOLVE_STAGE_FACTORABILITY_NOTE.md),
  `docs/BLOB_REDESIGN_REPORT.md` ->
  [docs/archive/V1_BLOB_REDESIGN_REPORT.md](archive/V1_BLOB_REDESIGN_REPORT.md));
  repaired all inbound Markdown links in
  [docs/archive/blob_problem_discovery/blob_refinement_visual_audit.md](archive/blob_problem_discovery/blob_refinement_visual_audit.md)
  and updated backtick path references in
  `docs/active_plans/audits/v1_blob_snap_deletion_blast_radius.md` and
  `docs/CHANGELOG-2026-05a.md`.

## 2026-06-08

### Additions and New Features

- Wrote the consolidated absorption closeout / handoff at
  [docs/active_plans/reports/blob_walk_absorption_closeout.md](active_plans/reports/blob_walk_absorption_closeout.md):
  what shipped (M1-M4), verification, independent review, the corrected A/B
  result and how to read it, design facts, the WP-6 prereqs, and the one human
  decision (commit the default-off increment now; schedule bootstrap-fix +
  Viterbi tuning + promoted-only re-A/B as the next reviewed phase).
- M4 gate (task #12): rewrote the walker A/B report at
  [docs/active_plans/reports/m4_walker_ab_report.md](active_plans/reports/m4_walker_ab_report.md)
  and saved its raw per-interval data at
  [docs/active_plans/reports/m4_walker_ab_data.csv](active_plans/reports/m4_walker_ab_data.csv).
  Sample-limited run (58 of a 120-interval target evaluated; 62 skipped by a
  per-video decode-time budget, not cherry-picked). Distribution headline:
  success (rescued+preserved) = 21/58 (rescued=6, preserved=15), regressed=35,
  needs_review=2. The walker preserves/rescues on roughly a third of evaluated
  during-race visible intervals (notably the high-drift Conant interior, 3/5
  rescued) and regresses on most of the rest at current Viterbi weights -- a
  broad distribution that supports holding `--walker-stage4` default-OFF, not a
  drop-in win. The report also records that the prior 4-interval result was a
  METRIC artifact (FWD/BWD agreement bias), NOT a selection artifact: the two
  previously-flagged FWD-zero-coverage intervals (Conant seed_1080_1111, Jason
  seed_564_583) are confirmed visible-on-both and during-race, so they were
  legitimately in scope.
- WP-5b (M4): wired real FWD/BWD windowed-walker execution into Stage 4 behind
  a default-off flag. New adapter `walker_bundle.walk_bundle_to_path` bridges a
  `WalkerInputBundle` to the relocated core `blob_walk.walk_walker.walk_one_direction`
  (unpacking the bundle, running one direction with a no-op debug sink so the
  solver path writes no CSV/PNG, and projecting the walker's standalone
  `direction_path` into the full-span, chronological, PROCESSED-pixel aligned
  state list that `blend_paths` / `compute_agreement` already consume). FWD and
  BWD each get their own bundle and their own `walk_one_direction` call
  (contract C9). New flag `--walker-stage4` (`dest=stage4_walker`, default
  False) on the solve and refine parsers threads through
  `cli` -> `solve_all_intervals(stage4_walker=...)` ->
  `_dispatch_blob_pass(use_walker=...)` ->
  `solve_interval_analytical(use_walker=...)`. Default OFF keeps the v1
  `_apply_blob_snap` path byte-identical (e2e_blob_walk_baseline RESULT: PASS,
  full pytest suite green). Added the M4 A/B report at
  [docs/active_plans/reports/m4_walker_ab_report.md](active_plans/reports/m4_walker_ab_report.md)
  and its harness `tests/e2e/e2e_walker_ab.py`; the partial 4-interval A/B
  classified rescued=1, preserved=0, needs_review=0, regressed=3, confirming the
  walker is not yet a default. New tests `tests/test_walker_adapter.py`
  (full-span projection, status->coverage mapping, short-walk padding) and
  `tests/test_walker_flag.py` (OFF takes v1, ON takes walker).
- WP-5a (M4 / WS-E): added the additive Stage 4 walker input bundle seam. New
  module `track_runner/walker_bundle.py` defines the `WalkerInputBundle`
  dataclass (seed, neighbor seed, frame range, direction sign, torso-unit
  scale, and the candidate-lattice source plumbing: reader, scene_transform,
  fps, stride, precomputed_store), a `build_walker_bundles_for_interval`
  builder that emits one FWD and one BWD bundle (each anchored on its own seed,
  contract C9), and an injectable `run_walker_pass(bundle, walker_callable)`
  invocation seam. `track_runner/interval_solver.py` gained
  `run_stage4_walker_seam`, which decides promotion from the Stage-3
  `confidence_tier` BEFORE any walker runs (Stage-3-first), and only then builds
  both bundles and hands them to the injectable walker. The seam lives beside
  the Stage 4 integration point, not inside `track_runner/blob_walk/`, so the
  core walker does not absorb pipeline orchestration. This is additive only: no
  production call site invokes the seam yet, so default solve behavior is
  byte-identical (e2e_blob_walk_baseline RESULT: PASS). The real walker wiring
  is WP-5b. The bundle deliberately omits the Hermite raw_pred path (Hermite
  independence).
- WP-2 (WS-B): created the relocation-equivalence report artifact at
  [docs/active_plans/reports/blob_walk_relocation_equivalence.md](active_plans/reports/blob_walk_relocation_equivalence.md).
  States the equivalence gate (`e2e_blob_walk_baseline.sh`), column comparison
  policy (EXACT for categorical/flag columns including `status`; TOLERANT abs
  <= 0.5 for numeric columns; unclassified falls back to exact), the 8 verdict
  CSVs across 4 intervals / 2 videos compared, and the current result. Result:
  RESULT: PASS -- baseline matches (very-very-close policy), 224 total verdict rows.

### Behavior or Interface Changes

- M4 gate (task #12): rebuilt the walker A/B evaluation harness
  [tests/e2e/e2e_walker_ab.py](../tests/e2e/e2e_walker_ab.py) to fix the prior
  selection + metric artifact. Selection now draws 20 random DURING-RACE
  (left frame > `race_start_frame`, contract C4) VISIBLE-on-all-three seed
  triples per video over the established 6-video corpus
  ([data/outdoor_corpus.txt](../data/outdoor_corpus.txt)) at a fixed
  `--random-seed`, mirroring `walk_util.select_random_visible` and reusing
  `walk_io.load_race_start_frame`. The metric is now an INDEPENDENT accuracy
  proxy: the interior human seed B of each triple is HELD OUT, the merged A->C
  interval is solved Hermite-only (blob off) vs walker-on (blob on), and each
  solved box's center distance to the held-out human seed is measured in
  torso-width units (contract C2). Classification is rescued / preserved /
  regressed / needs_review with preserved counted as success (the walker
  independently matching a good Hermite answer; independence enforced by the
  no-Hermite import gate + WP-5a data-boundary test). This replaces the old
  FWD/BWD-agreement metric, which was structurally biased toward Hermite (its
  two passes mirror one fitted curve; the walker's are independent per C9).
  Evaluation tooling only -- no production solver code changed and the
  `--walker-stage4` default stays OFF. `bash
  tests/e2e/e2e_blob_walk_baseline.sh` RESULT: PASS (224 verdict rows);
  `pytest tests/ -q` 1533 passed.
- WP-5b (M4): `blob_walk.walk_walker.walk_one_direction` now carries the
  per-frame five-value walk `status` on each `direction_path` entry (alongside
  the solved box), so the Stage 4 walker adapter can map it onto the legacy
  `blob_gate` coverage diagnostic without re-reading the debug-log CSV. This is
  an additive dict key on an in-memory structure; the verdict CSV columns and
  the `DebugLogRow` schema are unchanged, so `e2e_blob_walk_baseline` stays
  PASS. No cross-frame state and no Hermite reference are introduced (the
  no-Hermite import gate stays green; the adapter lives on the pipeline side in
  `walker_bundle.py`, not under `track_runner/blob_walk/`).
- WP-4 (M3 / WS-D): folded the relocated verdict-CSV debug log onto the unified
  schema constant. `track_runner/blob_walk/walk_debug_log.py` now defines
  `SCHEMA_VERSION = tr_schema.SCHEMA_VERSION` (imported bare as `import
  tr_schema`) instead of its own standalone `SCHEMA_VERSION = 13`. Now that the
  walker lives inside `track_runner/` beside `tr_schema.py`, two schema
  constants violated contract C10 (one unified `SCHEMA_VERSION`). The exported
  stamp value changes 13 -> 11, but it is metadata only: the constant is never
  written into the verdict CSV, the `HEADER` column tuple (43 columns) is
  unchanged, and `e2e_blob_walk_baseline` compares CSV columns and cells, so no
  CSV output changes. The torso_box_coords writer in `state_io.py` and
  `GEOMETRY_AFFECTING_SCHEMAS` are untouched (already additive per WS1-C).
- WP-3 (M2 / WS-C): confirmed and locked the in-pipeline walker's per-frame
  candidate source path. Extracted the smallest readable gathering helper
  `gather_frame_candidates(obs, trace_sink_holder)` in
  `track_runner/blob_walk/walk_walker.py` from the inline `_build_window_entry`
  extraction; it is the single point that turns one `observe_blob_at` trace
  into the per-frame `corridor_blobs` candidate list. `observe_blob_at` and
  `residual_motion.py` are unchanged (API Decision 2026-05-28). The helper is
  identity-preserving on centroids and yields an empty list on an obs-less or
  blob-less frame, so the gathered sequence stays frame-aligned.
- Recorded the declared candidate coordinate space (PROCESSED full-frame, ROI
  origin already added back) in the gathering helper docstring and added a row
  to `docs/COORDINATE_SPACES.md` distinguishing the trace `corridor_blobs`
  centroids (PROCESSED full-frame) from `observe_blob_at`'s RETURN centroid
  (SOURCE).

### Fixes and Maintenance

- Code-review hardening (behavior-preserving): `walker_bundle.py`
  `_interpolate_missing_frame` now raises a loud
  `RuntimeError("no bracketing frame found for frame N")` before subscripting
  `upper_box` in the final `else` branch. The branch already documents that
  `upper_box` cannot be None (start_frame is always an anchored endpoint); the
  guard converts a genuinely-impossible regression from a cryptic NoneType
  subscript into an explicit failure (loud-failure style, not a fallback
  default). No reachable behavior change.
- Clarified the `--walker-stage4` help text on the solve and refine parsers in
  `cli_args.py` to note it is an experimental A/B switch that runs Stage 4
  single-process (the pool worker does not carry the walker flag). Cosmetic
  help-string only; `dest`, `default`, and `action` are unchanged.
- WP-2 review cleanup: fixed 20 broken markdown links in
  [docs/CHANGELOG.md](CHANGELOG.md) pointing to the dead path
  `tools/blob_walk_v2/core/walk_driver.py`; repointed to the current location
  `tools/blob_walk_v2/walk_driver.py`. Also fixed 2 references in
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md) (`## 12`
  and `## 13` entries) pointing to the dead path
  `tools/blob_walk_v2/core/walk_debug_log.py`; repointed to
  `track_runner/blob_walk/walk_debug_log.py`. Verified with
  `pytest tests/test_markdown_links.py -q` (295 passed).
- WP-2 review cleanup: removed one fragile collection-size assertion from
  `tests/test_blob_walk_v2_candidate_source.py`
  (`assert [len(cands) for cands in sequence] == [2, 1, 3]`). The assertion
  checked a fixture constant, not the function behavior under test; forbidden
  by PYTEST_STYLE. The frame-alignment check directly above it is kept.
- WP-2 review cleanup: updated two stale comments in
  `track_runner/blob_walk/walk_debug_log.py` (module docstring and HEADER
  inline comment) that said "44 columns as of schema v13" / "SCHEMA_VERSION=13".
  Both now describe HEADER as the locked verdict-CSV column tuple (43 columns)
  and point to [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)
  for version history, without hard-coding a stale version stamp.

### Developer Tests and Notes

- M4 gate (task #12): the corrected A/B run was sample-limited by HEVC HDR /
  4k120 random-access decode cost (Jason and Lyra-Wheeling reach frames
  10k-20k+, tens of seconds per triple). A `--per-video-budget` (240 s for the
  recorded run) caps each video's wall time and counts overrun triples as
  `skipped_budget`, so every corpus video contributes its cheap-to-decode
  triples rather than one slow video starving the rest; the evaluated set is an
  unbiased subset of the fixed random sample. Re-running at `--random-seed
  12345` reproduced IMG_3830 and IMG_3823 interval-for-interval (determinism
  check). Verification after the harness/report/changelog edits:
  `bash tests/e2e/e2e_blob_walk_baseline.sh` RESULT: PASS (224 verdict rows,
  walker output unchanged vs baseline); `pytest tests/ -q` 1534 passed;
  `pytest tests/test_markdown_links.py -q` passed.
- WP-5a (M4 / WS-E): added `tests/test_walker_bundle_seam.py`, a deterministic
  data-boundary test using a fake recording walker (no video decode). It asserts
  (positive) the FWD/BWD bundles carry the seed and candidate-lattice source
  (reader, scene_transform, fps, stride) plus the torso-unit scale sufficient to
  walk, and (paired negative) no Hermite raw_pred path is reachable through any
  bundle field. A third test locks Stage-3-first ordering: a non-promoting tier
  never invokes the injectable walker, so walker output cannot influence
  eligibility. `pytest tests/ -k "interval_solver or walker_bundle or no_hermite
  or blob_walk_v2"` 182 passed; `bash tests/e2e/e2e_blob_walk_baseline.sh` PASS
  (default solve unchanged, 224 verdict rows); full `pytest tests/` 1512 passed.
- Added `tests/test_blob_walk_v2_candidate_source.py`: a hand-built-fixture
  behavioral test (no video decode) asserting the gathered candidate sequence
  is frame-aligned, an empty-blob frame and an off-frame soft-miss each yield
  an empty list, and centroids land in the declared PROCESSED full-frame space
  without re-projection to SOURCE. `pytest tests/ -k "walk and candidate"` (4
  passed); `pytest tests/ -k blob_walk_v2` (169 passed, up from 165 by the 4
  new tests); `pytest tests/test_pyflakes_code_lint.py -k walk` (32 passed).
- WP-4 (M3 / WS-D): `pytest tests/ -k "schema"` 33 passed including
  `test_tr_schema_version_single_source.py` (the C10 drift gate that scans
  `track_runner/` and accepts only `tr_schema.SCHEMA_VERSION` re-exports; the
  old `= 13` would have failed it once relocated). `pyflakes
  track_runner/blob_walk/walk_debug_log.py` clean. Recorded the constant fold in
  `docs/TR_SCHEMA_VERSION_HISTORY.md`. `bash
  tests/e2e/e2e_blob_walk_baseline.sh` PASS (baseline matches, 224 verdict rows
  across 4 intervals), confirming the fold changes no CSV output.

## 2026-06-07

### Additions and New Features

- **WP-1: relocated the blob_walk_v2 windowed-walker core into a new
  [track_runner/blob_walk/](../track_runner/blob_walk/) subpackage.** `git mv` moved the six
  core algorithm modules out of `tools/blob_walk_v2/core/` with no behavior change:
  `walk_viterbi.py`, `walk_motion_gate.py`, `walk_status.py`, `walk_walker.py`,
  `walk_debug_log.py`, and `walk_io.py`. Added a minimal
  [track_runner/blob_walk/__init__.py](../track_runner/blob_walk/__init__.py) (docstring only,
  no re-exports). This is the M1/WS-A relocation step of the plan to make the windowed walker
  the in-pipeline blob solver.

### Behavior or Interface Changes

- **WP-1: removed the `walk_paths.setup()` / package-root `sys.path` bootstrap from the
  relocated walker core; core siblings now import via the `blob_walk.*` subpackage.** Inside
  `track_runner/blob_walk/`, core-to-core imports use `import blob_walk.walk_X as walk_X`;
  imports of track_runner siblings (`residual_motion`, `blob_trace`, `scene_coords`,
  `state_io`, `camera_motion`) and `common_tools.*` keep the package-wide bare-name top-level
  convention (these modules import their own siblings by bare name, so a dotted
  `track_runner.X` import is not resolvable). [walk_io.py](../track_runner/blob_walk/walk_io.py)
  now resolves the repo root once via `git rev-parse --show-toplevel`
  (per [docs/REPO_STYLE.md](REPO_STYLE.md)) instead of `walk_paths.setup()`.
- **WP-1: repointed the tool-side driver and render imports to the relocated core.**
  [walk_driver.py](../tools/blob_walk_v2/walk_driver.py) (kept under `tools/` for WP-2;
  see Notes), [make_walk_html_v2.py](../tools/blob_walk_v2/make_walk_html_v2.py),
  [walk_render.py](../tools/blob_walk_v2/render/walk_render.py), and
  [walk_html.py](../tools/blob_walk_v2/render/walk_html.py) now import the moved modules as
  `blob_walk.*`.

### Developer Tests and Notes

- **WP-1: updated every test/e2e import that referenced the old core module paths**
  (`test_blob_walk_v2_debug_log.py`, `test_blob_walk_v2_motion_gate.py`,
  `test_blob_walk_v2_offframe_softmiss.py`, `test_blob_walk_v2_windowed.py`,
  `test_blob_walk_v2_winner_modes.py`, `test_blob_walk_v2_visible_seed_filter.py`,
  `test_m1d_heat_not_computed_detection.py`, `e2e_blob_walk_baseline.py`,
  `e2e_bug_101_degenerate_roi.py`). [test_blob_walk_v2_no_hermite.py](../tests/test_blob_walk_v2_no_hermite.py)
  now scans both `track_runner/blob_walk/` and `tools/blob_walk_v2/` and still asserts no
  `velocity_model`/`interval_solver`/`scoring` import in the walker core.
- **WP-1 verification:** captured the `e2e_blob_walk_baseline` golden snapshot on the pre-move
  tree, then confirmed the post-move walker output is byte-identical (8 verdict CSVs / 224 rows
  PASS). `pytest tests/ -k blob_walk_v2` (165 passed), `pytest -k "blob_walk_v2 and no_hermite"`,
  and `pytest test_pyflakes_code_lint.py -k walk` all green.
- **WP-1 scope note (DONE_WITH_CONCERNS):** `walk_driver.py` was left under
  `tools/blob_walk_v2/core/` and repointed rather than split. Its `run_interval_walk` interleaves
  the solver walk with tool-only tile rendering and heat-movie encoding (it imports `walk_render`
  and `heat_movie_encode`), so cutting it apart would change the e2e baseline call surface
  (`walk_driver.run_interval_walk`) and pull tool modules into the core package. The clean
  solver/tool split of `walk_driver` is deferred to WP-2 (tools repoint + e2e parity), which owns
  that call surface.
