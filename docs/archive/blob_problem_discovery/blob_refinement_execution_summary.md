# Blob refinement diagnostic - execution summary

Manager-level rollup of plan `kind-exploring-cray.md` execution. Companion to the formal audit at [docs/active_plans/audits/blob_refinement_visual_audit.md](../audits/blob_refinement_visual_audit.md).

## Scope of this report

- What ran across M0, M0.5, M0.7, M1, M2, M3, M4 milestones.
- Findings surfaced by each milestone.
- Fixes required mid-execution (rework rounds).
- Recommendations for follow-up plans.

## What ran

| Milestone | Deliverable | Status |
| --- | --- | --- |
| M0 | Production trace path (`trace_sink` kwarg on `observe_blob_at` + `_apply_blob_snap`); `BlobObserverTrace` / `BlobGateTrace` dataclasses; trace pytest suite (4 tests) | DONE |
| M0.5 | Heat-map equivalence audit + pytest (3 tests). Added `out_arrays` kwarg on `compute_heat_map_roi` for diagnostic side-channel | DONE |
| M0.7 | LOO seed-oracle batch audit + hypothesis-test analyzer | DONE (smoke, full corpus pending) |
| M1 | `tools/visualize_blob_gates.py` CLI + 3-panel PNG renderer + parity pytest + AST import-boundary pytest | DONE |
| M2 | `--interval` mode + 8-col contact sheets + `verdicts.csv` (28 cols incl. `lost_at_stage`) + HTML index | DONE |
| M3 | Funnel-stage classifier (`classify_blob_failure_mode.py`) | DONE (smoke) |
| M4 | Audit doc | DONE (PRELIMINARY) |

187 pytests green. No production-code regressions.

## Key findings

### F1. Heat-map equivalence holds (M0.5)

- Verdict: EQUIVALENT on Jason video, 6 frames sampled, max_abs_diff = 0.0.
- Comparison is array-level, not rendered-image, so UI colormap stretch is not a confound.
- Pre-known "validity-mask ordering divergence" suspicion REFUTED by code re-read: both paths apply validity AFTER DoG. Plan corrected.
- Consequence: user visual intuition "the blob is obvious in the motion map" looks at the SAME numerical residual the production gates consume. Gates have access to the right signal.

### F2. TRUST 0% intervals fail at the proximity gate (M2 + M3)

- Interval 2444-2491 FWD pass: 43 / 46 non-endpoint frames (~93%) `lost_at_gate_prox`. 3 / 46 (~7%) `lost_at_gate_path`. Zero `lost_at_observer / raw_blobs / corridor`.
- Mechanism: observer finds blobs, corridor filter keeps them, but the chosen blob is more than `0.6 * h` from `raw_pred`. Production proximity threshold rejects every frame.
- BWD mirrors FWD on this interval. Confirmed by funnel-stage classifier on smoke data.
- Cross-corpus M2/M3 sweep on the other five TRUST 0% intervals + controls pending.

### F3. LOO seed-oracle: H2 REFUTED preliminary; H1/H6 inconclusive on smoke (M0.7)

- Smoke ran with `--limit-per-video 5` on IMG_4005 only. Verdicts:
  - H1 (limb centroid bias): INCONCLUSIVE -- 5 seeds too few to fire the >= 30% threshold.
  - H2 (raw_pred wrong): REFUTED -- 0% of 3 eligible LOO frames showed `dist(raw_pred_loo, seed_torso) > 0.6 * h` AND oracle-centered winner within `0.3 * h`. Preliminary; full corpus pending.
  - H3 (corridor too narrow): INCONCLUSIVE_SCHEMA -- `loo_closest_raw_blob_dist_h` column not yet emitted; M0.7 fix-2 deferred.
  - H6 / H8: smoke too small.
- Frame-level evidence of real LOO: frame 268 shows oracle dist 2.495h vs LOO dist 1.268h, delta 1.227h. Independent observer calls confirmed.
- Per-bin tables (video, scale, race_phase, dist_from_nearest_seed, pass) emit correctly when input has multiple bins.

### F4. Combined decision-tree pointer

- Funnel: `lost_at_gate_prox` dominant on TRUST 0% (F2).
- Oracle: H2 preliminary REFUTED (F3).
- Therefore the dominant mechanism is likely **H1 (observer centroid bias)** or **H6 (wrong winner)**, NOT H2 (raw_pred quality). The plan's decision tree maps this to a follow-up that investigates centroid bias and cue-confidence weighting in `observe_blob_at` plus the proximity threshold in `_apply_blob_snap`.

## Fixes required during execution

Manager dispatched 5 multi-finding rework rounds; spec-reviewer cycles caught the bugs before audit.

| Patch | Finding caught | Fix |
| --- | --- | --- |
| WP-0B fix | `residual_pre_dog` / `dog_residual` / `validity_mask` undefined on cache HIT path -> NameError on second observer call with trace_sink | Cache extended to hold all three raw arrays; vars initialized; pre-DoG capture moved before DoG call |
| WP-0C follow-up | `types.SimpleNamespace()` had no `observer_trace` attr on observer early-return paths | Seed with `SimpleNamespace(observer_trace=None)` so attribute always exists |
| Style cleanup | Import order; `BlobObserverTrace | None` for skipped frames | Reordered + Optional annotation |
| WP-0.5A rework | Tool reimplemented heat-map path internals (denylist bypass); `metadata_equal` tautological; p99 read unsorted index; smoke override to 1 frame; AST check missing | Call `compute_heat_map_roi` directly via new `out_arrays` side-channel; real runtime metadata captured; `numpy.percentile(diff, 99)`; full --n-per-group honored; AST check moved into tool |
| WP-0.5B rework | Test was tautological (same op applied twice); brittle constant assertions | Runtime capture from both paths; real DoG diff |
| WP-0.7A rework | LOO logic entirely absent: `build_hermite_with_exclusion` defined but never called; LOO observer call missing; `blob_gate` / `dist_to_nearest_other_seed` hardcoded constants; H1 mask overlap placeholder; H8 columns hardcoded None | LOO call wired; both observer calls populate respective CSV columns; mask overlap uses connected-component + ROI-relative coords; H8 cols use ROI-relative coords |
| WP-0.7A fix-2 | `raw_pred_loo_cx/cy` columns never written to row dict; H1 used wrong key `center_pixel` (always [0,0]); H8 used absolute pixel coords against ROI-relative arrays | Columns added; centroid_x/y direct dict access; coord conversion `roi_cx = seed_torso_cx - roi_x1` |
| WP-0.7B fix | `DEFAULT_THRESHOLD` hardcoded `50.0` (production is `10.0`) -> every H8 verdict 5x off; per-bin tables absent (AC violation); video-name exact-match returned no rows; H1 denominator used overlap-filtered subset; H3 missing proximity guard | Import production constant; per-bin tables implemented; `str.contains('IMG_4005')`; denominator all-LOO-winner; H3 proximity gate added (or INCONCLUSIVE_SCHEMA if column missing) |
| WP-1A fix | Executable bit missing; `precompute_interval_residuals` failure silently fell back to legacy (defeated diagnostic validity invariant); `dict.get` defensive defaults on required keys; broad except blocks | `chmod +x`; try/except removed; `seeds_data["seeds"]` direct access; OSError propagates |
| WP-2A fix | Contact sheet thumbnails were all gray placeholders (filename mismatch: looked for `frame_{:05d}` but PNGs are `{PASS}_{:06d}.png`); try/except masking Image.open failures; `blob.get('area', 1.0)` default; stale help text claiming `--interval` raises NotImplementedError | Pass-name + 6-digit filename; try/except removed; `blob['area']` direct; help-text cleared |

Lesson: reviewer-loop caught every silent-fallback / hardcoded-constant / defensive-default pattern. The PYTHON_STYLE rules ("fix the design, not the symptom"; no try/except; no `dict.get` defaults on required keys) directly correspond to bug classes that hit during this work.

## Recommendations for follow-up

### R1. Run the full M0.7 corpus

- Re-run `tools/seed_oracle_blob_audit.py` on all 12 seeded videos with `--limit-per-video` removed (or set to 5000+).
- Re-run `tools/test_blob_hypotheses.py` on the resulting CSV.
- Decide H1, H6, H8 verdicts on real data, not 5-frame smoke.
- Wall-clock estimate: ~3.5s per 5 seeds on Jason; full Jason ~3000 seeds = ~40 min single-process, ~10 min at `--workers 4`. Twelve videos at similar density: 2-4 hours.

### R2. Cross-corpus M2/M3 sweep

- Run the 8 named TRUST 0% / control intervals from IMG_4005 + 33 cross-corpus TRUST 0% intervals (3 per other 11 videos).
- Build per-video CLASSIFICATION.md.
- Look for: is `lost_at_gate_prox` universally dominant on TRUST 0%, or video-dependent?

### R3. After R1 + R2 land, write the proximity-gate mechanism plan

- Target: `track_runner/velocity_model.py` `_apply_blob_snap` proximity gate (`BLOB_SNAP_ALPHA = 0.6`) AND `track_runner/residual_motion.py` `observe_blob_at` centroid computation.
- Hypothesis to test: blob centroid sits on a swinging limb rather than torso center, causing systematic offset > 0.6 * h.
- Candidate fixes (do NOT prejudge):
  - Replace centroid-of-mask with weighted centroid biased toward seed-box center.
  - Continuous proximity weighting (`BLOB_REDESIGN_REPORT` proposal) -- re-evaluate with R1 / R2 evidence.
  - Cue-confidence reweighting toward distance.
- This plan should be a separate document; the current plan is a diagnostic only.

### R4. WP-0.7A residual cleanups (low priority)

- `loo_winner_mask_overlap_frac` is always 0.0 in the CSV. Implement so the LOO half has H1 measurement too (currently only oracle-side has it). Not blocking hypothesis verdicts.
- NIF seeds: stream to CSV with `is_nif=True` rather than silently dropping. Required for M2 negative-control coverage.
- `precomputed_store=None` (legacy reader path) is the current default. M0.5 verdict EQUIVALENT confirms this is numerically safe on Jason. Switch to `prepass` once `residual_pre_pass.precompute_interval_residuals` is wired into the oracle audit.
- `closest_raw_blob_dist_h` column for H3 proximity guard.

### R5. `.gitignore` hygiene

- Add `__pycache__/` (or `**/__pycache__/`) to `.gitignore`. Currently `tools/__pycache__/` appears as untracked noise. Won't be committed but adds noise to `git status`.

### R6. Plan archive

- Once human commits, `git mv ~/.claude/plans/kind-exploring-cray.md docs/archive/blob_refinement_visual_audit_plan.md`.

## Process notes

- Reviewer-first discipline (spec-reviewer per workpackage) caught roughly 30 bugs that would otherwise have landed silently. The cost (one reviewer subagent per coder subagent) is well worth the bug surface it caught.
- Smoke-first execution policy worked: every tool was demonstrated end-to-end on real data before the manager marked it done. Smoke uncovered the cache-hit bug, the H8 5x threshold bug, the contact sheet filename mismatch.
- The plan's "load-bearing invariant" (diagnostic image is valid evidence only if it comes from the exact solver path) was directly enforced by the AST import-boundary pytest. The visualize tool cannot drift into reimplementation without breaking the test.
- The "Fix the design, not the symptom" core philosophy from `docs/REPO_STYLE.md` was load-bearing during fix rounds; every silent fallback removed produced a clearer error and a clearer next action.

## Status snapshot

| Item | State |
| --- | --- |
| All 16 work packages | DONE |
| pytest suite (187 tests) | GREEN |
| Heat-map equivalence | EQUIVALENT (Jason video, 6 frames) |
| Funnel-stage finding (2444-2491 FWD) | 43/46 lost_at_gate_prox |
| Oracle verdicts | PRELIMINARY (5-seed smoke only) |
| Full corpus run | PENDING |
| Cross-corpus M2/M3 | PENDING |
| Follow-up fix plan | NOT WRITTEN (R3 target named) |
| `docs/CHANGELOG.md` 2026-05-23 | UPDATED |
| Human commit | PENDING |
