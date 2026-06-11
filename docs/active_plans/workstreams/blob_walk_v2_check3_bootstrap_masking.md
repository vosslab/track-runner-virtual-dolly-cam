# Check 3: bootstrap-accept masking (Claim J)

**Verdict: OBSERVED (1 of 26 passes, 3.8%)**

## Claim J

The walker bootstrap step marks the seed frame as `accepted` when the blob
observation is not None. The Stage-4 Hermite fallback fires only when
`accepted_count == 0`. Therefore: a walk that finds a blob at the seed frame
but stalls for all remaining frames ships a path with `accepted_count == 1`,
skips the fallback, and returns a result frozen at the seed position for most
frames -- strictly worse than pure Hermite.

## Evidence sources

Three walkthroughs run via `_temp_check3_walk_more.py` and `_temp_check3_walk_extra.py`
(both temp scripts, now deleted). Verdict CSVs written to `/tmp/`. No
production code was modified.

- `/tmp/check3_walk_output` -- 4 baseline intervals (Conant, Jason)
- `/tmp/check3_walk_more` -- 6 Conant + 1 Jason additional intervals
- `/tmp/check3_walk_extra` -- 2 Lyra-Wheeling intervals

26 passes examined across 3 source videos and 13 intervals.

## Summary counts

| Category | Count | Pct |
| --- | --- | --- |
| Total passes | 26 | 100% |
| Normal (accepted >= 2, fallback not fired) | 21 | 80.8% |
| Bootstrap-only masked (accepted=1, only seed, fallback skipped) | 1 | 3.8% |
| Zero-accept (fallback FIRED) | 4 | 15.4% |

## Code locus

`track_runner/blob_walk/walk_walker.py` bootstrap block:

```python
bootstrap_status = "accepted" if obs is not None else "soft_miss_no_blob"
if obs is not None:
    accepts.append(frame_f)
    status_counts["accepted"] += 1
```

`track_runner/interval_solver.py` fallback gate:

```python
walker_fallback_fwd = (fwd_accepted == 0)
walker_fallback_bwd = (bwd_accepted == 0)
```

Gap: `accepted_count == 1` (bootstrap only) does not trigger fallback.

## Masked instance

Conant-4x400-2026_April_15.mkv `seed_1126_1134` FWD (8-frame interval):
- `accepted_count = 1`, bootstrap frame accepted, `post_bootstrap_accepted = 0`
- Remaining 7 frames: all `soft_miss_no_blob`
- Hermite fallback did not fire; shipped path is frozen at seed position
- The BWD pass for the same interval found 3 accepted frames (normal)

## Per-pass table

```
Video                              Interval       Dir  AccCount  BsAcc  PostBs  Masked  Fallback
--------------------------------------------------------------------------------------------------
Conant-4x400-2026_April_15.mkv    seed_1080_1111  fwd         0 False       0   False   True
Conant-4x400-2026_April_15.mkv    seed_1080_1111  bwd         4 True        3   False   False
Conant-4x400-2026_April_15.mkv    seed_1296_1327  fwd        30 True       29   False   False
Conant-4x400-2026_April_15.mkv    seed_1296_1327  bwd        23 True       22   False   False
Jason-3200m-sectionals-IMG_4005   seed_564_583    fwd         0 False       0   False   True
Jason-3200m-sectionals-IMG_4005   seed_564_583    bwd         2 True        1   False   False
Jason-3200m-sectionals-IMG_4005   seed_602_629    fwd        24 True       23   False   False
Jason-3200m-sectionals-IMG_4005   seed_602_629    bwd        11 True       10   False   False
Conant-4x400-2026_April_15.mkv    seed_1111_1126  fwd         2 True        1   False   False
Conant-4x400-2026_April_15.mkv    seed_1111_1126  bwd         4 True        3   False   False
Conant-4x400-2026_April_15.mkv    seed_1126_1134  fwd         1 True        0   TRUE    False
Conant-4x400-2026_April_15.mkv    seed_1126_1134  bwd         3 False       3   False   False
Conant-4x400-2026_April_15.mkv    seed_1134_1142  fwd         1 False       1   False   False
Conant-4x400-2026_April_15.mkv    seed_1134_1142  bwd         6 True        5   False   False
Conant-4x400-2026_April_15.mkv    seed_1142_1157  fwd         6 True        5   False   False
Conant-4x400-2026_April_15.mkv    seed_1142_1157  bwd         9 True        8   False   False
Conant-4x400-2026_April_15.mkv    seed_1157_1173  fwd         9 True        8   False   False
Conant-4x400-2026_April_15.mkv    seed_1157_1173  bwd        11 True       10   False   False
Conant-4x400-2026_April_15.mkv    seed_1173_1235  fwd        58 True       57   False   False
Conant-4x400-2026_April_15.mkv    seed_1173_1235  bwd        61 True       60   False   False
Jason-3200m-sectionals-IMG_4005   seed_348_450    fwd         2 False       2   False   False
Jason-3200m-sectionals-IMG_4005   seed_348_450    bwd        18 True       17   False   False
Lyra-Wheeling-IMG_3912.mkv        seed_660_707    fwd        16 False      16   False   False
Lyra-Wheeling-IMG_3912.mkv        seed_660_707    bwd         1 False       1   False   False
Lyra-Wheeling-IMG_3912.mkv        seed_707_754    fwd         0 False       0   False   True
Lyra-Wheeling-IMG_3912.mkv        seed_707_754    bwd         0 False       0   False   True
```

## Interpretation

Claim J is confirmed: the bootstrap-accept masking scenario occurs in real
data. The gap is real but low-incidence at 3.8% in this sample (1 of 26).

The 8-frame Conant `seed_1126_1134` FWD pass is the clearest case: blob
extraction failed on all post-seed frames, the bootstrap blob was the sole
accepted frame, and the fallback gate `accepted_count == 0` was never
satisfied. The shipped walk path holds the seed-frame position for all
non-accepted frames and is strictly worse than a Hermite-only interval.

Short intervals (8 frames) are more vulnerable: a single bootstrap hit
covers the whole interval's "accepted" budget while providing no real
trajectory constraint.

## Fix direction

Extend the Hermite fallback condition from `accepted_count == 0` to
`post_bootstrap_accepted == 0` (i.e., no non-seed accepted frames). This
ensures that a bootstrap-only walk -- one where the walk effectively stalled
after the seed observation -- is treated the same as a zero-accept walk and
falls back to Hermite. The fix is a one-line change in `interval_solver.py`.

Alternatively, exclude the bootstrap frame from the `accepted_count` that
gates the fallback. Either approach eliminates the masking scenario without
affecting passes where the walk genuinely found non-seed accepted frames.
