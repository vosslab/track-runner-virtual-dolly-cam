# Blob walk v2 bundle audit (run 2, Opus reviewers)

Status: COMPLETE - six independent Opus reviewers, frozen staged bundle, read-only.

Pre-merge multi-reviewer audit of the staged blob walk v2 bundle (Viterbi
velocity-delta cost-model rewrite, walker_costs config plumbing, P10 seed-only
fallback, walk_io scale-back, SCHEMA_VERSION 13 to 14). This is the second audit
pass; run 1 used the prior model tier. Findings feed the human accept/reject
decision. The bundle was not modified.

## Headline

No code-correctness blocker. Every actionable item is documentation packaging,
test hygiene, or a repo-rule comment cleanup. The cost-model rewrite itself
passes plan, style, legacy, and comment review; the DP-optimality property test
is the strongest single guard and is clean.

## Severity-ranked merged findings

### Blocker

- B1. CHANGELOG 2026-06-12 day block malformed (Docs auditor). The
  `### Developer Tests and Notes` heading appears twice, and the first
  occurrence sits before `### Decisions and Failures` instead of last.
  REPO_STYLE mandates the fixed subsection order with one heading each. Fix:
  merge the two blocks into one section placed last. Mechanical. Lives in the
  frozen staged CHANGELOG, so held pending the freeze lift.

### High

- H1. Planning-scaffolding tags in permanent code comments (Comment auditor).
  Repo rule forbids WS / WP / M tags in code comments. Five hits, all in staged
  additions, each a small reword that keeps the technical content:
  - `track_runner/blob_walk/walk_viterbi.py:71` "WP-COST-1 cost contract" ->
    "cost contract"
  - `track_runner/blob_walk/walk_viterbi.py:75` "WP-COST-1 brief" -> "original
    cost brief"
  - `track_runner/blob_walk/walk_viterbi.py:82` "WS-VAL tuning owns final
    values." -> "Validation tuning owns final values." (or delete)
  - `track_runner/tr_schema.py:64,66` drop "(WP-COST-1)" and "(WP-P10-1)"
    parentheticals
  - `tools/blob_walk_v2/walk_driver.py:187` "(WP-COST-1 file ownership)" ->
    "(single-owner config)"
  Note: M4 / WP-A1 tags flagged by grep in solve_queue.py, cli.py,
  interval_solver.py, walker_bundle.py are PRE-EXISTING lines, not in this
  bundle - out of scope for this audit, worth a separate pass.

- H2. Shipped walker_costs config keys undocumented (Docs auditor). The staged
  `track_runner/track_runner.config.yaml` adds six user-overridable keys; the
  `docs/TRACK_RUNNER_YAML_CONFIG.md` table that documents them is UNSTAGED.
  Merge as-is leaves a shipped config section undocumented. The unstaged doc fix
  is REQUIRED to ride with the bundle.

### Medium

- M1. Four vacuous reader-geometry parity tests (Test auditor),
  `tests/test_walk_io_parity.py:65-126`. Both sides call the identical function;
  cannot fail; do not exercise walk_io or `_worker_init`. Independently confirms
  run 1's vacuous-parity finding. Recommend DELETE. Real reader-geometry parity
  needs a live reader and belongs in tests/e2e/, not pytest.

- M2. Seed-path parity compares only basenames (Test auditor),
  `tests/test_walk_io_parity.py:133-194`. Hand-constructs the expected path and
  compares `.name`, so it pins a naming convention, not walk_io behavior. Fix:
  route through the actual walk_io path-builder or tr_paths, or DELETE.

- M3. Stale weight arithmetic in test docstrings (Test auditor),
  `tests/test_walk_cost_model.py`. RESOLVED by Plan auditor: the live default IS
  `WEIGHT_DISPLACEMENT = 0.25` (walk_viterbi.py:106, config.yaml:24,
  `_active_weights` fallback - all consistent, with a manager-amendment comment
  explaining 1.0 makes the model-flip test unsatisfiable). The test file's
  module docstring line 21 saying `1.0` is the stale line; some inline docstrings
  say 0.25. Assertions are behavioral and robust. Fix: correct the test module
  docstring to 0.25 and strip the numeric margin walkthroughs.

- M4. Unstaged doc-truth fixes required to ride (Docs auditor). `docs/ROADMAP.md`
  and `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md` still describe the pre-bundle world
  (old "zero accepted frames" fallback gate, A/B + weight tuning listed as
  remaining work). Staged DESIGN.md already flips both. Doc set self-contradicts
  if these do not ride along.

### Low

- L1. ~10 dead `hasattr` guards (Test auditor), `test_walk_cost_model.py`. The
  guarded API now exists; the guards turn a missing-API regression into a silent
  pass. Delete the guards.
- L2. One non-discriminating evidence test (Test auditor),
  `test_walk_cost_model.py:494-526` `test_higher_mag_wins_identical_geometry` -
  passes on old and new model by its own docstring. DELETE; covered structurally
  elsewhere.
- L3. Missing all-skip-terminal coverage (Test auditor). No test that a fully
  pruned window resolves to all-None with cost N*SKIP_COST. One small synthetic
  lattice closes it.
- L4. Three DEFERRED-OK dead-code chains (Legacy auditor), all PRE-EXISTING, not
  bundle-introduced, explicitly parked for the SEED_SEARCH_SLACK_W follow-up
  cleanup patch: walk_motion_gate `evaluate()` / `MotionGateResult` /
  `bootstrap_search_radius_w()` / `BOOTSTRAP_UNCERTAINTY_W` dead chain (now zero
  live readers); `BOOTSTRAP_N`; `mode_disagreement_count=0` cosmetic field; HTML
  legend entries (out of staged set).
- L5. Two style non-fix notes (Style auditor): `_active_weights` rebuilds the
  defaults dict per call on the test-only path; cross-module private
  `_normalize_video_basename` call is the deliberate single-source choice.

## Clean (no findings)

- Plan: all WP acceptance criteria grep-verified; no scope creep; no non-goal
  files touched; contracts C2/C8/C9/C10 honored; gates green (103 focused, 190
  pyflakes).
- Style: no blocker/high/medium; diff actively improves do-not-hide-bugs
  (removed getattr fallbacks, direct-key config validation, loud race-start).
- Legacy: no REMOVE-NOW; WEIGHT_MAG_VAR/ANGLE_VAR gone repo-wide; basename mirror
  deleted; all compute_path_* helpers reachable; pyflakes clean.
- Comment: every DP cost term has an intent comment; boundary rules and
  gap-normalization explained; audit-P2 design choice recorded; set_cost_weights
  write-once cache justified; Google docstrings + #==== separators present; zero
  non-ASCII.
- Docs (staged): DESIGN.md cost terms correct/complete; schema-history v14 row
  correct and matches tr_schema.py; staged CHANGELOG links resolve.

## Could not verify (human-review gate)

- WP-VAL-1 A/B numerical correctness and the --hermite-only byte-identity check.
  Verifying recorded numbers requires re-running the corpus tool, which the
  frozen/read-only constraint forbids. The plan classifies A/B thresholds as
  human-review release evidence, not an automatic gate. Corpus-120 control run
  (separate, 100 intervals, 5 videos) shows zero regression: FWD +0.8, BWD +1.0
  totals, max per-video delta +1.2, all hit_neighbor_seed.

## Fix batch (apply when freeze lifts)

Mechanical, low-risk, no design judgment:
1. B1: reorder + merge the CHANGELOG 2026-06-12 Developer Tests and Notes block.
2. H1: five scaffolding-tag rewords.
3. H2 + M4: stage the four unstaged doc-truth fixes (TRACK_RUNNER_YAML_CONFIG.md,
   ROADMAP.md, TR_FWD_BWD_MODEL_METHODOLOGY.md,
   archive/windowed_path_selection_amendment.md) so they ride with the bundle.
4. M1 + L2: delete four vacuous reader-geometry parity tests + one
   non-discriminating evidence test.
5. M3 + L1: correct the test module docstring to 0.25, strip stale margin math,
   delete the dead hasattr guards.

Design-judgment / optional (leave for the user):
- M2 seed-path parity rework vs delete.
- L3 add the all-skip-terminal test.
- L4 the SEED_SEARCH_SLACK_W follow-up cleanup patch (separate, planned).
