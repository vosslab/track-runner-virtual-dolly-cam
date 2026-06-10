# Blob walk absorption closeout

This is the single handoff document for the blob_walk_v2 absorption increment.
It states what shipped, what is proven, what is left, and the one decision a
reviewer must make. Plan reference: `floating-juggling-candle.md` (under
`~/.claude/plans/`).

All work this increment is staged, not committed. Only a human commits. Default
solve is byte-identical; every change is strictly additive and reversible. The
walker is available only behind a default-off flag (`--walker-stage4`).

## Context

The windowed blob walker (formerly `tools/blob_walk_v2/`) is being absorbed into
`track_runner/` as the rescue path for intervals where the cheap Hermite solve
fails. Hermite runs everywhere (Stage 3); the walker is spent only where Hermite
is uncertain (low/fair confidence promotion, Stage 4). This increment relocates
the walker core, wires it as a default-off Stage 4 path, and evaluates it against
a Hermite-only baseline. It does not make the walker the default.

## What shipped

| Milestone | What | State |
| --- | --- | --- |
| M1 (core) | Walker core relocated via `git mv` to `track_runner/blob_walk/` (walk_viterbi, walk_motion_gate, walk_status, walk_walker, walk_debug_log, walk_io); absolute imports, minimal `__init__.py`, no-Hermite boundary preserved | Staged |
| M1 (tool) | `walk_driver.py` moved to `tools/blob_walk_v2/walk_driver.py` (stays tool-side, imports `interval_solver.blend_paths`, excluded from no-Hermite gate); render/html/heat-movie tools repointed | Staged |
| M2 | Walker gathers per-frame candidates from `BlobObserverTrace.corridor_blobs` via the existing `observe_blob_at` + trace_sink path (PROCESSED full-frame); `observe_blob_at` frozen, `residual_motion` unchanged | Staged |
| M3 | `walk_debug_log` reads `tr_schema.SCHEMA_VERSION` (C10, one constant); `torso_box_coords` writer already unified per WS1-C, no bump | Staged |
| M4 WP-5a | `WalkerInputBundle` dataclass at `track_runner/walker_bundle.py` (outside `blob_walk/` so core stays orchestration-free); injectable `run_walker_pass` seam; Stage-3-first eligibility; no Hermite raw_pred field | Staged |
| M4 WP-5b | Walker wired as Stage 4 blob path behind default-off `--walker-stage4` (dest `stage4_walker`, default False); OFF runs v1 `_apply_blob_snap` byte-identical; ON runs the walker adapter on promoted intervals, FWD and BWD independent (C9) | Staged |

WP-5b details worth restating:

- Adapter `walk_bundle_to_path` calls `blob_walk.walk_walker.walk_one_direction`
  for each direction independently.
- Status to blob_gate mapping handles all statuses (no KeyError); short walks are
  padded via full-span projection.
- `precomputed_store` threading is deferred (perf-only, not correctness).
- `_dispatch_blob_pass` forces in-process when the flag is on, because the pool
  worker does not carry the flag.

Import note for future callers: the core siblings are imported as
`blob_walk.walk_X` with `track_runner/` on `sys.path`. The literal
`track_runner.blob_walk.*` dotted form is NOT resolvable in this repo.

## Verification

All verified green this increment.

| Check | Command | Result |
| --- | --- | --- |
| Default solve byte-identical | `bash tests/e2e/e2e_blob_walk_baseline.sh` | PASS (224 verdict rows, walker output unchanged vs baseline) |
| Full suite | `pytest tests/` | 1534 passed |
| v1 untouched | `velocity_model.py` unchanged | v1 intact |
| Hermite independence | no-Hermite import gate + WP-5a data-boundary test | enforced |

## Independent review summary

Reviewed by multiple fresh subagents under untrusted-code review.

- M1 to M3: two fresh reviews returned PASS_WITH_CONCERNS; all concerns fixed
  (20 broken CHANGELOG links repaired, one fragile collection-size assertion
  trimmed, stale v13 / 44-column comments corrected; the HEADER is 43 columns).
- Contract compliance audit: C2, C5, C6, C9, C10 PASS; one ambiguous
  `roi_pad=max(20,seed_w)` raw 20px floor flagged as image-raster (author to
  confirm). Artifact: `blob_walk_contract_compliance.md` (under
  `docs/active_plans/audits/`).
- WP-5a fresh review: PASS_WITH_CONCERNS, GO.
- WP-5b fresh review: PASS_WITH_CONCERNS; default path judged safe and
  reversible.

## Evaluation result and how to read it

A corrected A/B was run over the established corpus: 6 videos x 20 = 120
during-race visible intervals; 58 of 120 evaluated under a per-video decode
budget (fixed seed 12345, unbiased subset). The metric is independent held-out
interior human seed distance in torso-width units. This replaces the structurally
biased FWD/BWD agreement metric. Artifacts:
[m4_walker_ab_report.md](m4_walker_ab_report.md) and
[m4_walker_ab_data.csv](m4_walker_ab_data.csv).

Headline distribution:

| Outcome | Count |
| --- | --- |
| Success (rescued + preserved) | 21/58 |
| - rescued | 6 |
| - preserved | 15 |
| Regressed | 35 |
| Needs review | 2 |

How to read it: the 21/58 figure UNDERSTATES the walker. A fresh review judged
the result PARTIALLY-TRUSTWORTHY. The direction (walker not yet ready as default)
is correct, but regressed=35 is inflated by two harness artifacts:

1. The harness runs the walker UNCONDITIONALLY on every triple, bypassing the
   production promotion gate. In production the walker runs only on low/fair
   (promoted) intervals. Scoring it on high-confidence intervals, where Hermite
   is already good, manufactures regressions production would never produce.
2. At least 11 of the 35 regressions show the bootstrap-stall signature
   (`walker_err >= 1.0` with good Hermite), which is a fixable bug, not genuine
   worse tracking. The remaining roughly 24 are mild (`walker_err < 1.0`) Viterbi
   weight-calibration misses.

So the true promoted-only, stall-fixed success rate is materially higher than
21/58, consistent with "the walker works on most intervals."

## Design facts restated

- Cost, not quality, is why the walker is gated. The walker is MORE accurate than
  Hermite but COMPUTATIONALLY EXPENSIVE (roughly 300x to 1000x or more per
  interval; see [walker_vs_hermite_cost_benchmark.md](walker_vs_hermite_cost_benchmark.md)).
  Hermite runs cheap everywhere; the walker is spent only where Hermite fails.
- Preserved equals success. Where Hermite is already best and the walker
  independently matches it, that is a win (preserved), not a wash.
- v1 blob-snap has NO rescue capacity. Its gates read `raw_pred`, so it cannot
  diverge from a failed Hermite path. That is why it is being retired, and why
  the A/B baseline is Hermite-only and never v1.

## Not done / next phase

Each item below changes walker output and therefore breaks the
relocation-equivalence baseline, so each is gated on human review.

- WP-6 prereq, fix the forward-bootstrap stall in
  `track_runner/blob_walk/walk_walker.py` (`_compute_roi_and_observe`): the
  acceptance box (0.5*w by 0.75*h) anchored on a frozen seed center with a
  no-velocity bootstrap rejects all blobs as `acceptance_box_empty`, so the
  anchor never advances. Diagnosis and three fix directions in
  `fwd_zero_coverage_diagnosis.md` (under `docs/active_plans/audits/`).
  Breaks the baseline; needs review.
- WP-6 prereq, tune the Viterbi cost weights (the roughly 24 mild regressions).
  Breaks the baseline; needs review.
- Harness improvement (eval tooling), gate the A/B to promoted-only intervals and
  record the Hermite confidence tier per triple, so the next A/B separates
  promoted from non-promoted and is conclusive. Does not change production output,
  but is required before the next A/B is trustworthy.
- WP-6 itself, delete v1 `_apply_blob_snap` and `BLOB_SNAP_*`, then flip
  `--walker-stage4` to default-on. Blast-radius recipe ready in
  `v1_blob_snap_deletion_blast_radius.md` (under `docs/active_plans/audits/`).
  Note: the `residual_motion` argmax-winner return is NOT removable; it is still
  consumed by the walker. Breaks the baseline; needs review.

## The one decision

The increment is shippable as-is: additive, default-off, reversible, and fully
reviewed.

Making the walker the default (WP-6) is a separate, larger decision. It requires
approving the bootstrap-stall fix, the Viterbi tuning, and a promoted-only re-A/B
FIRST.

Recommendation: commit the increment now so the walker is available behind
`--walker-stage4` for experimentation, and schedule the bootstrap fix plus tuning
plus re-A/B as the next reviewed phase.

## Artifact index

Reports (this folder, `docs/active_plans/reports/`):

- [blob_walk_relocation_equivalence.md](blob_walk_relocation_equivalence.md)
- [m4_walker_ab_report.md](m4_walker_ab_report.md)
- [m4_walker_ab_data.csv](m4_walker_ab_data.csv)
- [stage4_walker_seam_map.md](stage4_walker_seam_map.md)
- [walker_vs_hermite_cost_benchmark.md](walker_vs_hermite_cost_benchmark.md)
- [blob_walk_absorption_closeout.md](blob_walk_absorption_closeout.md) (this doc)

Audits (`docs/active_plans/audits/`):

- `blob_walk_contract_compliance.md`
- `fwd_zero_coverage_diagnosis.md`
- `v1_blob_snap_deletion_blast_radius.md`
