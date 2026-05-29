# Blob refinement fix plan: M0-M2 results (2026-05-23)

Status: IN PROGRESS. Fresh oracle landed; M3 sweep re-run in flight.

This document summarizes the diagnostic and measurement results from the
counterfactual-selected blob-refinement fix plan
(`kind-exploring-cray.md`)
through milestones M0 (instrumentation), M1 (oracle + sweep data
gathering), and M2 (per-hypothesis analyzers). M3 (counterfactual gate
simulator ranking) is in progress and M4 (production fix) is pending.

This rev incorporates the fresh oracle re-run that completed today
(6102 rows across 12 videos at
`output_smoke/blob_refine_fix/2026-05-23/oracle/oracle.csv`). The
previous rev's H1 and H6 verdicts were marked
"producer-bug-pending" because the LOO branch in
`tools/seed_oracle_blob_audit.py` hardcoded
`loo_winner_mask_overlap_frac = 0.0` and
`loo_best_alt_dist_h = None`. That producer bug is fixed and the
verdicts below reflect real, computed values.

## What problem this plan addresses

In production, the blob-refinement layer (`track_runner/velocity_model.py`
`_apply_blob_snap`, coupled with the per-frame observer in
`track_runner/residual_motion.py`) consistently fails to accept motion-cue
blobs that the user visually identifies as obvious in the residual-motion
heat map. Across all 12 tracked videos in `tr_config/`, many post-race
intervals show `blob_accept = 0%/0%` per pass despite a clear blob in the
heat map within the corridor of the predicted runner position.

The predecessor diagnostic plan
([blob_refinement_visual_audit.md](blob_refinement_visual_audit.md)) confirmed on
one Jason interval (2444-2491) that 93% of non-endpoint FWD frames lost
at the proximity gate. The current plan corrects that one-interval, one-
video finding into a corpus-driven counterfactual ranking, with explicit
guardrails to avoid prejudging the fix.

## Method summary

Five candidate policies were defined to address the failure mode without
introducing new gates:

| Policy | Description | Surface |
| --- | --- | --- |
| A | Baseline (current production: `BLOB_SNAP_ALPHA = 0.6` hard gate, cue-conf winner) | none |
| B | Larger proximity gate: `dist <= 1.0 * h` (ALPHA = 1.0) | one constant |
| C | Closest-blob winner: replace cue-conf winner with min-distance corridor blob | one function |
| F | Weighted snap output: `snap = (1-w) * raw_pred + w * blob`, `w = exp(-(dist/h)^2)` | one function |
| G | Cue-confidence reshaping: `score = proximity^2 * (size + integrated_mag)` | one function |
| E | Centroid pull: shift winner centroid by `0.5 * (raw_pred - centroid)` | one function, gated on H1 SUPPORTED |

Policy E joins the M3 evaluation set only when three gates pass: H1
SUPPORTED in >= 6 of 12 videos, AC-2d (TRUST 0% oracle-correctness), and
AC-2e (positive-control oracle-correctness).

The M3 ranking will reject any policy that fails the G-4 gate (six
clauses including cross-video improvement, corpus-wide rescue,
positive-control preservation, Jason poison-pill, and universal oracle-
correctness).

## Measurements landed (M0-M2)

### M0: Instrumentation

The plan required a production trace path so diagnostic tools exercise
the same gate logic as the solver. Three landed:

- `track_runner/blob_trace.py`: `BlobObserverTrace` and `BlobGateTrace`
  dataclasses (existed from predecessor).
- `track_runner/residual_motion.py` `observe_blob_at`: `trace_sink`
  kwarg (existed from predecessor).
- `track_runner/velocity_model.py` `_apply_blob_snap`: `trace_sink`
  kwarg landed this round. Endpoint-inclusive append matches the test
  contract at `tests/test_blob_trace_path.py`.

The visualizer `tools/visualize_blob_gates.py` consumes these traces and
emits `verdicts.csv` per interval with one row per non-endpoint frame
per pass. The `--write-corridor-blob-list` flag adds the
`corridor_blobs_json` column carrying the full per-blob score set
(`cx, cy, area, dist_h, integrated_mag, size_score, proximity_score,
total_score`) so M3 policies G and F can replay scoring without
reimplementing the observer. A follow-up landed today to disambiguate
None gates: the visualizer now emits `"vacuous"` for unevaluated gates
and adds a dedicated `dir_gate_vacuous` column.

### M1A: Oracle audit (LOO seed hypothesis tests)

The oracle audit (`tools/seed_oracle_blob_audit.py`) probes every post-
race seed in the corpus with a leave-one-out (LOO) call: rebuild the
Hermite curves excluding that seed, ask the observer what it would have
chosen, compare to the seed's true torso center. Four of the five
preregistered hypotheses are testable here:

- H1: limb centroid bias (LOO winner's component mask overlaps the seed
  region by >= 50% but its centroid is more than 0.6 * h from the seed
  torso center)
- H2: raw_pred is wrong (LOO raw_pred > 0.6 * h from seed AND oracle-
  centered winner within 0.3 * h)
- H6: wrong winner (a non-winning blob in the LOO corridor is at least
  0.2 * h closer than the winner)
- H8: DoG suppresses (pre-DoG residual at seed torso >= 3 *
  threshold AND post-DoG residual < threshold)

The fresh oracle run produced 6102 rows across the 12-video corpus, of
which 5834 frames had post-race seeds with computable LOO predictions.
Corpus-wide verdicts:

| Hypothesis | Primary verdict | Corpus rate | Per-video max | Notes |
| --- | --- | --- | --- | --- |
| H1 (limb centroid bias) | REFUTED at 20%/30%/40% | 0.0% (1/123 eligible) | Hononega-Orion_600m-IMG_3702 0.2% | 10 videos with zero hits; 123 frames eligible after centroid-overlap filter |
| H2 (raw_pred wrong) | REFUTED at 20%/30%/40% | 2.5% (145/5834) | IMG_3627 32.9%, IMG_3839 26.1% | All other 10 videos under 12% |
| H3 (corridor too narrow) | INCONCLUSIVE_SCHEMA | n/a | n/a | column missing from CSV; not testable here |
| H6 (wrong winner) | INCONCLUSIVE at 20%, REFUTED at 30%/40% | 11.8% (426/3606 multi-blob frames) | IMG_3839 26.1%, IMG_3627 20.0% | Highest signal among tested hypotheses; just below the 20% support bar |
| H8 (DoG suppresses) | REFUTED at 20%/30%/40% | 6.1% (353/5834) | Jason 15.0%, Hononega-Varsity 12.0% | Same pattern as predecessor; no SUPPORTED video |

The H2 REFUTED corpus-wide result remains load-bearing: the raw_pred
predictions feeding the proximity gate are not systematically wrong
overall. The fix lives on the blob side of the gate, not on the
prediction side. This satisfies the plan's WP-1D suspension check
(`output_smoke/blob_refine_fix/2026-05-23/oracle/SUSPENSION_CHECK.md`
records `H_RAW_PRED_CORPUS: REFUTED`, `Overall: PROCEED`, with 1 of 12
videos supporting H2 at the per-video 30% threshold versus the quorum
of 6).

H1 and H6 are no longer producer-bug-pending. The LOO-branch mask-
overlap and second-nearest-blob computations now match the oracle
branch. The verdicts above are computed from real data.

### M1B: Interval sweep (per-frame verdicts)

The sweep (`tools/run_interval_sweep.py` driving
`tools/visualize_blob_gates.py --no-png --write-corridor-blob-list`)
exercises the production gate path on every frame of a stratified
sample. The selection was built by `tools/build_interval_selection.py`
using RNG seed 20260523, drawing 96 intervals from `tr_config/*.
interval_scores.json` across 12 videos: 74 for the ranking sample
(consumed by M2/M3) and 22 for held-out confirmation (consumed by M4).

Because `interval_scores.json` does NOT carry the per-interval
`blob_accept_fwd / blob_accept_bwd` fields that the plan's
TRUST 0% / TRUST high-accept selection rule uses (a documented
limitation tied to the interval-fingerprint coverage gap), buckets at
this stage are stored as `trust_pending` and `weak_fair_pending`. A
post-sweep rebucket step is needed before M3 ranking can apply per-
video preservation gates (G-4 clauses 1, 3, 4). That rebucket step is
not yet built; M2 corpus-wide measurements run on all intervals
regardless of bucket.

The ranking sweep finished in 22 minutes wall-clock at `--workers 1`,
74 of 74 intervals succeeded, producing 75 `verdicts.csv` files (the
extra one is from a single-interval Jason smoke test). Each verdicts
file carries roughly 200-400 non-endpoint frames per pass, with the
`gate` column (skipped / absent / accepted / rejected) and the
`lost_at_stage` column (corridor / gate_prox / gate_path / gate_dir /
observer / accepted) so the M2 analyzers can filter precisely on
proximity-gate rejections. With the new vacuous-gate accounting, M3
counterfactual replay can distinguish "gate did not fire because no
blob was available" from "gate fired and rejected".

### M2: Per-hypothesis analyzers on the sweep verdicts

Three corpus-wide analyses ran on the 75 `verdicts.csv` files. All
report from the proximity-rejected subset (`lost_at_stage ==
gate_prox`):

| Test | Verdict | Corpus rate | Policy implication |
| --- | --- | --- | --- |
| H_DIST_SHAPE_THRESHOLD vs H_DIST_SHAPE_BLOB | **SUPPORTED THRESHOLD** | mode bin `fingerprint-coverage-gap-blob-policies.md`)
   means a production policy fix to `compute_cue_confidence` or
   `BLOB_SNAP_ALPHA` will NOT invalidate the per-user refine cache.
   Users will not see the fix until they delete their cache. This is a
   separate production-design issue tracked outside the current plan.
4. None of the changes have been committed; all edits are staged for
   user review. Two new memory files document locked rules (never
   delete solved interval data; the fingerprint gap) that future
   sessions should respect.
5. IMG_3627 (32.9% H2 hit rate, 79 eligible frames) and IMG_3839
   (27.3% on 22 frames; the 26.1% headline rate is over the 23-frame
   superset) both sit above per-video H2 sensitivity at 20% even
   though the corpus is REFUTED. The small-n on IMG_3839 limits how
   much weight that video should carry, but IMG_3627 deserves a focused
   look in M3.

## Artifacts

All artifacts under `output_smoke/blob_refine_fix/2026-05-23/`:

- `oracle/oracle.csv` (fresh re-run: 6102 rows; supersedes earlier runs)
- `oracle/oracle_jason.csv` (633 rows, Jason solo)
- `oracle/oracle_12videos.csv` (6102 rows; matches the fresh `oracle.csv`)
- `oracle/HYPOTHESIS_TESTS.md` (fresh; reflects H1/H6 real values)
- `oracle/HYPOTHESIS_TESTS.json` (machine-readable sidecar)
- `oracle/SUSPENSION_CHECK.md` (PROCEED, 1 of 12 H2 SUPPORTED)
- `m2_sweep/per_video_selection.csv` (96 rows, RNG seed 20260523)
- `m2_sweep/<video>/interval_*/verdicts.csv` (75 files; re-running with
  vacuous-gate schema)
- `m2_sweep/sweep_master_v2.log` (per-interval timing + exit codes)
- `analyses/distance_histogram.md` (272K, full per-video + per-interval
  histograms with ASCII bars)
- `analyses/alternate_blob.md` (per-video + corpus rescue rates)
- `analyses/centroid_bias.md` (degraded mode; H1 REFUTED corpus-wide)
- `analyses/policy_e_gates.json` (`policy_e_eligible: false`,
  `h1_supported_corpus: false`)
- `analyses/h1_h6_anomaly.md` (root-cause forensics; documents the
  producer bug resolved this round)

New / modified tools (all uncommitted):

- `tools/seed_oracle_blob_audit.py` (NIF source filter + LOO mask-
  overlap + best-alt-dist fix landed this round)
- `tools/test_blob_hypotheses.py` (JSON output schema rewrite to
  `{corpus, per_video}` per plan spec)
- `tools/blob_distance_histogram.py` (new)
- `tools/test_alternate_blob_winner.py` (new)
- `tools/test_centroid_bias.py` (new; degraded-mode fallback)
- `tools/blob_fix_suspension_check.py` (new)
- `tools/counterfactual_gate_sim.py` (skeleton; policy A replay only)
- `tools/per_video_report.py` (new; not yet run on real corpus)
- `tools/build_interval_selection.py` (new)
- `tools/run_interval_sweep.py` (new)
- `tools/visualize_blob_gates.py` (vacuous-gate emission +
  `dir_gate_vacuous` column landed this round)
- `track_runner/velocity_model.py` (`_apply_blob_snap` `trace_sink`
  kwarg)
- Pytest coverage: 33 new fixture-driven tests across the new tools,
  all green. Repo-wide pytest not re-run since the fixture additions;
  no production code touched beyond the trace_sink wiring.
