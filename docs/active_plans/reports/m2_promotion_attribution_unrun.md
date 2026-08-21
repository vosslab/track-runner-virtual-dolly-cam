# M2 promotion-attribution gate: controlled full-current-corpus receipt

Date: 2026-08-20

## Result

This report now contains a **controlled, newly frozen full-current-corpus
receipt**, not recovered historical evidence. It runs every valid non-pre-race
Stage-3 Hermite interval from all six corpus videos. It supplies the complete
current-input promotion count while preserving the boundary that it cannot prove
today's live corpus bytes are the same bytes used before M2.

The completed v3 receipt is
`/private/tmp/m2-counterfactual-run-v3b-20260820/summary.json`; its frozen input
selection is `/private/tmp/m2-counterfactual-selection-v3-20260820.json`. The
local generator archived base
`283bda5a8dfae628376988895f66d8ab09b3b820`, applies only the explicit source-
hashed M1 C3 seed-truth patch to `track_runner/interval_solver.py`, and runs
`blob_pass=False`. It saves raw FWD, BWD, and blended geometry arrays, then runs
both the base Dice adapter and `trajectory_confidence.interval_agreement()` on
those exact raw paths. That private-video generator was later removed from the
permanent test suite; this frozen receipt remains historical local evidence.

| Receipt property | Actual v3 result |
| --- | --- |
| Videos / intervals | 6 / 3,885, every valid non-pre-race pair |
| Explicit exclusions | 247 persisted `pre_race` rows, retained with reasons |
| Stage / producer | Stage 3 Hermite / archived base plus M1 only |
| Raw and blended geometry | Hashed and shared by both scoring adapters |
| Secondary scores | Velocity, size, motion, and occlusion matched exactly |
| Old Dice promotions | 320 |
| New center-width promotions | 263 |
| Changed tiers | 73 |

All 3,885 rows, including old/new agreement, tiers, promotion flags, secondary
scores, and array identities, are machine-readable in the receipt's
`promotion_table.json`. Every changed tier is retained: 51 `fair -> high`, 16
`low -> fair`, 4 `low -> good`, and 2 `fair -> good`. The runner recomputes each
tier through the same scoring thresholds with only the agreement adapter changed.

The required comparison cannot be reconstructed from the checked-out
artifacts without fabricating a historical baseline. The raw pre-change Dice
implementation is available in `HEAD:track_runner/scoring.py`, and live corpus
and configuration inputs are available through the repository symlinks. What
is missing is a frozen pre-M2 Stage-3 raw-path/score baseline, a manifest that
establishes the historical input bytes and solve settings, and a comparator
that produces the required before/after tier table.

## Required comparison

WP-T3 requires a Stage-3-only, same-input comparison between the old raw-pass
Dice agreement and the current raw-pass torso-normalized center agreement. For
each non-pre-race interval, the record must contain:

| Field | Before arm | After arm |
| --- | --- | --- |
| Corpus video and content identity | Exact same video bytes | Exact same video bytes |
| Seed artifact | Exact same seed JSON bytes | Exact same seed JSON bytes |
| Solve/config inputs | Exact same config and bin factor | Exact same config and bin factor |
| Propagation | Same Stage-3 Hermite raw FWD/BWD paths | Same Stage-3 Hermite raw FWD/BWD paths |
| Agreement | Mean per-frame Dice | Mean `trajectory_confidence` center agreement |
| Tier and promotion | Old tier and `low`/`fair` promotion flag | New tier and `low`/`fair` promotion flag |

The tier-change rows must also retain the unchanged scoring inputs
(`velocity_consistency`, `size_consistency`, motion quality, interval length,
and occlusion treatment) so a reviewer can recompute each old and new tier.

## Audit evidence

### Available code

- `HEAD:track_runner/scoring.py` contains the pre-change
  `_compute_dice_coefficient()` and `compute_agreement()` implementation.
- The current working tree contains
  `track_runner/trajectory_confidence.py`, whose `interval_agreement()` owns
  the replacement mean raw-pass center-distance agreement in torso-width
  units.
- `track_runner/interval_solver.py` still applies the production promotion
  policy only from a Stage-3 score: `PROMOTION_TIERS = {"low", "fair"}`.

That source availability is insufficient: re-running old code against newly
generated paths would not establish a before state unless all non-M2 inputs
are frozen and verified identical.

### Available live inputs

`data/outdoor_corpus.txt` lists six expected relative video paths beneath
`TRACK_VIDEOS/`, including `Jason-3200m-sectionals-IMG_4005.mkv` and
`Conant-4x400-2026_April_15.mkv`. In this checkout, `TRACK_VIDEOS` is a live
symlink to `/Users/vosslab/Documents/TRACK_VIDEOS` and `tr_config` is a live
symlink to `/Users/vosslab/Documents/Track_Runner_Config`.

The config volume contains co-located seed and interval-score artifacts for
the corpus videos. These live inputs are suitable for a newly frozen rerun,
but they do not constitute a historical pre-M2 baseline: there is no recorded
pre-M2 manifest proving the then-current video, seed, config, bin-factor, and
race-start bytes/settings, and there are no saved pre-M2 raw Stage-3 FWD/BWD
paths or scores.

### Missing baseline artifacts and comparator

A follow-up historical-artifact audit found nine archived
`*.track_runner.debug_paths.npz` files under the configuration archive.  They
retain raw FWD/BWD arrays, but record only a fingerprint, frame bounds, and
array index.  They do not retain video, seed, configuration, bin-factor, or
race-start identities; they also lack scores and a before/after comparator.
The aggregate Jason baseline-metrics JSON likewise records counts only, not
raw paths or provenance.  These near-misses cannot establish the same-input
historical before arm required by WP-T3.

The local `tests/e2e/baseline_blob_walk/` directory is not a WP-T3 baseline:

- it covers only four fixed walker intervals, not the Stage-3 promotion corpus;
- its files are ignored local telemetry (`fwd_verdicts.csv`,
  `bwd_verdicts.csv`, and `interval_summary.csv`), rather than versioned
  artifacts;
- its verdict rows do not contain full raw FWD/BWD trajectory boxes or the
  Stage-3 secondary score inputs needed to recompute confidence tiers; and
- it is a walker-output equivalence harness, which is explicitly incompatible
  with using it as a Walker-versus-Hermite or historical promotion baseline.

No existing script was found that emits both old and new agreements and tiers
from one frozen Stage-3 trajectory set. `tests/e2e/e2e_blob_walk_baseline.py`
only writes and compares walker verdict CSVs.

## Reproducible rerun protocol

Run this protocol after a frozen evidence manifest is made from the available
live corpus/config volume. The before arm must be recomputed from the
pre-M2 code revision using those frozen inputs. Do not substitute a walker
snapshot, different seeds, or a freshly edited config for the frozen manifest.

1. Materialize a manifest for every corpus video recording absolute path,
   SHA-256, seed JSON SHA-256, config SHA-256, bin factor, and race-start
   boundary. Copy those exact read-only inputs into a shared evidence location.
2. Create two clean worktrees: one at the commit immediately before the M2
   confidence patch and one at the current M2 candidate. Freeze every other
   output-changing change at the same revision in both arms; specifically run
   Stage 3 with `blob_pass=False` so Stage-4 walker output is not part of the
   comparison.
3. In each worktree, run the same Stage-3 solve over the manifest using the
   shared seed/config inputs. Save the raw FWD and BWD paths, interval score,
   selected bin factor, and input hashes for every interval. Verify the raw
   paths are byte-identical across arms before interpreting metric changes. If
   they differ, stop: this is not an M2-only attribution comparison.
4. For every frozen raw-path pair, compute both metrics with frozen adapters:
   the pre-M2 mean Dice formula and the current
   `trajectory_confidence.interval_agreement()` formula. Apply each arm's
   tier classification with identical non-agreement score inputs, then derive
   promotion from `confidence_tier in {"low", "fair"}`.
5. Write a report under `docs/active_plans/reports/` with aggregate before and
   after tier/promotion counts plus one row for every changed tier. Each row
   must include video, interval endpoints, old/new agreement, fixed secondary
   scores, old/new tier, old/new promotion flag, and a programmatic
   recomputation result. The gate passes only when every changed row's new tier
   follows the new metric and there are zero unexplained rows.

Required Python commands in either worktree must use the repository runtime:

```bash
source source_me.sh && python3 <evidence-comparator> --manifest <frozen-manifest>
source source_me.sh && pytest tests/test_trajectory_confidence.py tests/test_tr_scoring.py
git diff --check
```

The comparator path intentionally remains unspecified because no existing
same-input comparator is present. Its implementation must be reviewed before
it becomes evidence; this report is not a substitute for that tool or run.

## Commands used for this audit

```bash
git log --all --oneline -- track_runner/trajectory_confidence.py track_runner/interval_solver.py track_runner/scoring.py
git show HEAD:track_runner/scoring.py
ls -ld TRACK_VIDEOS tr_config
readlink TRACK_VIDEOS
readlink tr_config
git check-ignore -v tests/e2e/baseline_blob_walk/.../fwd_verdicts.csv
```

## Gate state

The controlled current-input WP-T3 comparison is complete: every valid Stage-3
corpus pair has an old/new promotion row, fixed secondary-score check, and retained
raw-array identity. Historical pre-M2 recovery remains unavailable because no
historical input manifest exists; this receipt must not be described as proof that
current input bytes equal the old production run.
