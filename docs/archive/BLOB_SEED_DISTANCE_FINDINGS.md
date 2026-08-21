# Blob acceptance vs distance to nearest seed

Diagnostic findings from `tools/check_interval_blob_funnel.py --gate-trace`
on `TRACK_VIDEOS/Hononega-Orion_600m-IMG_3702.mkv`, run 2026-04-24.

## Question

`tools/diagnose_residual_motion.py` shows the residual-motion blob detector
finds the runner reliably on user seed frames (Gate 1 PASS: 4/4 within 1x
torso height, median distance 43.1 px). The matching question for the
solver: at what distance N (in frames) from the nearest seed endpoint does
that signal collapse, and into which failure mode?

## Method

Ran `--gate-trace -S -N 30` on the Orion clip (30 random post-race
intervals, 622 traced frames across FWD+BWD passes). For each traced
frame, computed `min(frame - start_seed, end_seed - frame)` and binned
the 5-bucket classification (AGREE / REFINE / MISSED / FAR / ABSENT)
across the bins `1, 2, 3-5, 6-10, 11-20, 21+`.

## Result

```
dist    n   AGREE REFINE MISSED FAR ABSENT  good%
1     108     27     5     39   36    1     30%
2      86     26     2     34   23    1     33%
3-5   162     52     8     54   45    3     37%
6-10  106     25    22     41   18    0     44%
11-20  94     44    11     19   13    7     59%
21+    66     50     1     13    2    0     77%
```

`good%` = `(AGREE + REFINE) / n`. `good` is what we want: either the blob
already sat on the Hermite prediction (AGREE) or the gates accepted a
meaningful refinement (REFINE).

The trend is the inverse of the hypothesis. The blob signal is **worst
adjacent to seeds and best in the interval interior**. MISSED + FAR
dominate the near-seed bins (`dist=1..5`, ~60% of frames) and collapse
toward `dist >= 21` (~20% of frames).

## Interpretation

Two non-exclusive mechanisms can explain the near-seed hostile regime:

1. **Residual motion is limbs, not torso.** The detector responds to
   pixel motion, which on a runner is dominated by swinging arms and
   legs. The blob centroid lands on a limb. Near a seed the Hermite
   prediction anchors to the user-annotated torso center, so the
   limb-to-torso offset alone can push `dist / h` above 0.3 (REFINE
   threshold) or 1.0 (FAR threshold). Far from a seed the Hermite
   prediction has integrated several frames of motion and is itself
   centered closer to the limb cloud, reducing the apparent offset.

2. **Path gate tangent is ill-defined at seed boundaries.** The motion-
   path gate uses a chord tangent `raw_pred[t+1] - raw_pred[t-1]`. Near
   a Hermite endpoint that chord is dominated by the cubic curve's
   corner geometry, not the runner's instantaneous direction. Path
   rejection spikes exactly where the tangent is least trustworthy.

## Caveats

- Sample is 30 intervals from one video. The per-bin counts at `21+`
  (n=66) come from a small number of long intervals; one well-tracking
  long interval can dominate the bin.
- Pre-race intervals are excluded by the funnel tool, so this is a
  post-race-only result.
- `MISSED` lumps all three gates together. Splitting into
  `MISSED-prox / MISSED-dir / MISSED-path` per bin would separate the
  two hypotheses above. The data already exists in the per-frame trace;
  only the aggregation needs extending.

## Next steps

- Larger sample (N >= 100 intervals, multiple videos) before acting.
- Split MISSED by failed gate inside the distance table to identify
  whether path-gate-near-seed (hypothesis 2) or limb-offset-everywhere
  (hypothesis 1) is the dominant failure mode.
- If hypothesis 1: the fix is in the observation layer (corridor /
  centroid logic), not the gates. The gates are correctly rejecting
  blobs that genuinely sit on a limb.
- If hypothesis 2: the fix is in the path gate. Candidate: skip the
  path gate (or widen its slack) for frames within `K=2` frames of an
  interval endpoint, where the chord tangent is unreliable.
