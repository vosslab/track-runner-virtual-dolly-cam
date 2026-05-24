# Blob refinement fix plan: M0-M2 results (2026-05-23)

Status: IN PROGRESS. Fresh oracle landed; M3 sweep re-run in flight.

This document summarizes the diagnostic and measurement results from the
counterfactual-selected blob-refinement fix plan
([kind-exploring-cray.md](../../../../.claude/plans/kind-exploring-cray.md))
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
([archive/...](../audits/blob_refinement_visual_audit.md)) confirmed on
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
| H_DIST_SHAPE_THRESHOLD vs H_DIST_SHAPE_BLOB | **SUPPORTED THRESHOLD** | mode bin [0.6, 0.7) over 3884 prox-rejected frames | Support for policy B (loosen ALPHA from 0.6 to 1.0). Winners cluster right at the cutoff. |
| H_ALT_PASSES (alternate-blob rescue) | REFUTED | 5.5% rescue rate (214/3884) | Policy C is weak as a standalone fix. Closest-blob picks only rescue a small fraction; the cue-conf winner is usually the right object. |
| H_CENTROID_BIAS (with policy-E gates) | INCONCLUSIVE (degraded) | computed corpus-wide; AC-2d/2e blocked; H1 corpus REFUTED | `policy_e_eligible: false`. Tier columns missing in oracle.csv. AC-2d (TRUST 0% oracle-correctness) and AC-2e (positive-control oracle-correctness) report BLOCKED. Policy E auto-drops from M3 unless rebucket lands. |

The distance-shape verdict is the load-bearing M2 result. The mode bin
sitting at [0.6, 0.7) means most proximity rejections happen just past
the current `0.6 * h` threshold. Loosening to `1.0 * h` (policy B)
would capture the bulk of the distribution without changing the
geometry-of-rejection: the winners are not pathological, they just
exceed the cutoff by a small margin.

## Leading candidate fix

The candidate fix prior to the fresh oracle was policy B, anchored on
the M2 distance-shape mode in [0.6, 0.7) and on H_ALT_PASSES REFUTED.
The fresh oracle keeps the cited evidence intact: H_DIST_SHAPE_THRESHOLD
SUPPORTED, H_ALT_PASSES REFUTED, and H2 REFUTED corpus-wide. Policy B
remains the smallest-surface fix matching the distance shape.

What the fresh oracle changes is the strength of the "raw_pred is
right" anchor. Two videos (IMG_3627 at 32.9% and IMG_3839 at 26.1%)
sit above the 20% per-video threshold for H2, with IMG_3627 above the
30% threshold individually even though the corpus is REFUTED and the
suspension quorum (6 of 12) does not trigger. The M3 counterfactual
simulator must therefore answer whether policy B genuinely rescues
trust_0 intervals on this corpus, or whether it would over-snap on the
two videos where raw_pred itself is wrong. The simulator's bisection
job is to discriminate B's behavior on those two videos from its
behavior on the other ten.

Remaining policy summary:

- The dominant mechanism on this corpus is proximity-threshold-too-
  tight. Centroid bias (H1) and DoG suppression (H8) are both REFUTED
  at every threshold tested.
- Policy B (one-constant change, `BLOB_SNAP_ALPHA: 0.6 -> 1.0`) is the
  smallest-surface fix matching the M2 distance shape.
- Policy F (weighted snap) and policy G (cue-conf reshaping) remain in
  the M3 evaluation set. The fresh-oracle H6 result (11.8% wrong-winner
  rate, the most prominent signal among tested hypotheses) gives G a
  stronger prior than it had under the previous rev. See the working
  hypothesis below.
- Policy C is REFUTED for being the standalone fix (5.5% rescue), but
  it may still rank if combined evidence shifts in the M3 simulator.
- Policy E auto-drops because H1 is REFUTED corpus-wide
  (`h1_supported_corpus: false`, `policy_e_eligible: false` in
  `analyses/policy_e_gates.json`).

No fix has been committed to production code. The plan explicitly
forbids landing a fix before M3 RANKING.md picks one based on G-4 gate
results.

## Working hypothesis after fresh oracle

With no hypothesis SUPPORTED at any threshold in the fresh corpus, the
original audit's pointer toward "H1 or H6 dominant" is REFUTED. The
signal closest to support is H6 wrong-winner at 11.8% corpus-wide,
below the 20% threshold but the most prominent of the four tested.
This nudges the policy ranking prior toward:

1. **Policy G** (cue-confidence reshape, winner selection) -- weighted
   by the H6 corpus rate even though H6 itself is REFUTED. If a non-
   trivial fraction of corridor winners are the wrong blob, reshaping
   the cue-confidence score is the lever that fixes selection without
   touching ALPHA.
2. **Policy B** (loosen ALPHA from 0.6 to 1.0) -- still backed by the
   M2 distance-shape mode at [0.6, 0.7). Smallest surface, but does not
   address the wrong-winner signal at all.
3. **Policy F** (weighted snap) -- a softer-landing alternative to B
   that decays with distance. Less aggressive than B at small distances,
   more permissive at large ones.

This is a working hypothesis, not a conclusion. The M3 counterfactual
simulator is the arbiter. Recording the ranking shift here so the
simulator's policy-prior section can cite an evidence-grounded
starting order rather than re-deriving it.

## Pending work

| Item | Blocker | ETA |
| --- | --- | --- |
| Oracle re-run with H1/H6 producer fix | DONE | n/a |
| Rebucket selection.csv from sweep verdicts (compute blob_accept_fwd/_bwd per interval, classify TRUST 0% / TRUST high-accept) | small tool, not built | one focused task |
| H_CENTROID_BIAS AC-2d/2e | rebucket | minutes after rebucket |
| Counterfactual sim instrumentation re-fix | DONE (vacuous gate column landed) | n/a |
| Ranking-sample sweep re-run with new verdicts.csv schema | in flight | running |
| M3 counterfactual sim real implementation (policies B, C, F, G; E auto-dropped) | sweep re-run + rebucket | 30-60 min after both |
| WP-3A real impl: per-policy replay, G-4 gate, RANKING.md | M3 sim | 30-60 min |
| WP-4A production fix | M3 RANKING.md winner | 15 min |
| WP-4B production-fix reviewer (read-only) | WP-4A | 5 min |
| WP-4C validation re-render (ranking-sample + held-out passes) | WP-4A | ~20 min |
| WP-4D full pytest | WP-4A | 5 min |
| WP-5A-C audit doc FINAL + changelog + plan archive | M4 done | 15 min |

## Caveats

1. Bucket labels in `per_video_selection.csv` are `trust_pending` /
   `weak_fair_pending` placeholders. A rebucket step using the sweep's
   `gate == accepted` counts per interval is needed before per-video
   G-4 preservation gates can fire.
2. The sweep wrapper had a transient command-line bug (passed `-c
   tr_config` to a visualizer that doesn't accept `-c`); fixed and the
   sweep re-ran clean. Sweep output paths have a nested-dir oddity
   (`interval_X_Y/interval_X_Y/verdicts.csv`) from the wrapper double-
   naming; downstream glob `**/verdicts.csv` handles it but it should
   be cleaned before WP-4C.
3. The `interval_fingerprint` coverage gap
   ([memory](../../../../.claude/projects/-Users-vosslab-nsh-track-runner-virtual-dolly-cam/memory/fingerprint-coverage-gap-blob-policies.md))
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
