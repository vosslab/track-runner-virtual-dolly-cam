# Blob walk relocation equivalence report

WP-2 / WS-B deliverable. Confirms the blob_walk_v2 core relocation from
`tools/blob_walk_v2/core/` to `track_runner/blob_walk/` produced no behavior
change in walker output.

## Equivalence gate

The gate is `bash tests/e2e/e2e_blob_walk_baseline.sh` (the "very-very-close"
comparison policy defined in `tests/e2e/e2e_blob_walk_baseline.py`).

A golden snapshot of 8 verdict CSVs was captured on the pre-relocation tree.
The post-relocation walker re-runs the same 4 seed-to-seed intervals and the
fresh outputs are compared against the snapshot under the policy below.

## Artifacts compared

8 verdict CSVs across 4 intervals from 2 videos:

| Video | Interval | FWD rows | BWD rows |
| --- | --- | --- | --- |
| Conant-4x400-2026_April_15.mkv | seed_1080_1111 | 32 | 32 |
| Conant-4x400-2026_April_15.mkv | seed_1296_1327 | 32 | 32 |
| Jason-3200m-sectionals-IMG_4005.mkv | seed_564_583 | 20 | 20 |
| Jason-3200m-sectionals-IMG_4005.mkv | seed_602_629 | 28 | 28 |

Total: 224 verdict rows.

## Comparison policy

Columns fall into two classifications:

- EXACT: categorical, identity, and flag columns compared with string equality.
  Includes: `frame_index`, `step`, `direction`, `dt_for_gate`, `winner_mode`,
  `audit_winner_rule`, `obs_corridor_n`, `obs_raw_n`, `candidates_json`,
  `status`, `reject_reason`, `stop_reason`, `roi_anchor_source`,
  `candidates_in_window`.
- TOLERANT: continuous numeric columns compared with abs(diff) <= 0.5.
  Includes: `dt`, `torso_w_px`, `torso_h_px`, `prev_cx/cy`, `pred_cx/cy`,
  `cand_cx/cy`, scene-space positions, velocities, jumps, winner scores,
  `path_cost`, provisional anchor fields.

Any unclassified column falls back to exact comparison so new columns cannot
silently pass without a deliberate classification edit.

The `SCHEMA_VERSION` constant is metadata only and is not written into the
verdict CSV, so the WP-4 constant fold (13 -> 11) produces no column or cell
change.

No IGNORED columns exist in the current policy.

## Result

Run on 2026-06-08 after all WP-1 through WP-4 changes were applied:

```
[compare] PASS Conant-4x400-2026_April_15.mkv/seed_1080_1111/fwd_verdicts.csv rows=32 accepted_fraction=0.000000
[compare] PASS Conant-4x400-2026_April_15.mkv/seed_1080_1111/bwd_verdicts.csv rows=32 accepted_fraction=0.129032
[compare] PASS Conant-4x400-2026_April_15.mkv/seed_1296_1327/fwd_verdicts.csv rows=32 accepted_fraction=0.967742
[compare] PASS Conant-4x400-2026_April_15.mkv/seed_1296_1327/bwd_verdicts.csv rows=32 accepted_fraction=0.741935
[compare] PASS Jason-3200m-sectionals-IMG_4005.mkv/seed_564_583/fwd_verdicts.csv rows=20 accepted_fraction=0.000000
[compare] PASS Jason-3200m-sectionals-IMG_4005.mkv/seed_564_583/bwd_verdicts.csv rows=20 accepted_fraction=0.105263
[compare] PASS Jason-3200m-sectionals-IMG_4005.mkv/seed_602_629/fwd_verdicts.csv rows=28 accepted_fraction=0.888889
[compare] PASS Jason-3200m-sectionals-IMG_4005.mkv/seed_602_629/bwd_verdicts.csv rows=28 accepted_fraction=0.407407
[compare] checked 8 verdict CSVs across 4 intervals
[compare] RESULT: PASS -- baseline matches (very-very-close policy), 224 total verdict rows
```

Relocation is confirmed behavior-equivalent.
