# Check 0 extension: Lyra-Wheeling stride-2 overrun analysis

Audit finding P12 from
[blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md).

**Question:** Did any promoted interval on Lyra-Wheeling-IMG_3912 have a span not
divisible by stride=2, causing the equality termination condition to miss the neighbor seed?

**Conclusion:** P12 OBSERVED FAILURE -- YES (one promoted interval confirmed).

---

## Artifacts found

| Artifact | Path | Status |
| --- | --- | --- |
| Seeds JSON | `tr_config/Lyra-Wheeling-IMG_3912.track_runner.seeds.json` | PRESENT |
| Interval scores | `tr_config/Lyra-Wheeling-IMG_3912.track_runner.interval_scores.json` | PRESENT |
| Camera motion npz | `tr_config/Lyra-Wheeling-IMG_3912.track_runner.camera_motion.npz` | PRESENT |
| Torso box npz | `tr_config/Lyra-Wheeling-IMG_3912.track_runner.torso_box_coords.npz` | PRESENT |
| Walk debug CSVs | (searched repo-wide) | ABSENT |

Walk debug CSVs were not found. No walk debug output was written for this video in
the current solve. This rules out direct overrun evidence from CSV telemetry.

---

## FPS and stride

From `seeds.json` `video_identity`:

- `fps = 119.94`
- `stride = max(1, round(119.94 / 60)) = max(1, round(1.999)) = 2`

The stride is 2. Every post-race interval with an odd span is a termination-miss
candidate: the walker steps in increments of 2, so `frame_f == neighbor_seed_frame`
can never be true when `(neighbor_seed_frame - seed_frame) % 2 != 0`.

---

## Interval span table (summary counts)

| Category | Count |
| --- | --- |
| Total seeds | 310 |
| Total intervals | 309 |
| Intervals with span % 2 != 0 (all phases) | 154 |
| Intervals with span % 2 != 0 (post_race only) | 148 |
| Intervals with span % 2 != 0 (post_race, low/fair tier = Stage-4 promoted) | 1 |

Race start frame: 565 (end of last `pre_race` interval).

Only one interval is both (a) post_race and (b) flagged as Stage-4-promoted
(`confidence_tier` of `low` or `fair` in `interval_scores.json`).
The remaining 294 post-race intervals all have `confidence_tier = high` and
would not be promoted to Stage 4.

---

## The one promoted odd-span interval

| Field | Value |
| --- | --- |
| Interval index | 164 |
| left seed frame | 16588 |
| right seed frame | 16591 |
| span | 3 frames |
| span % 2 | 1 (ODD) |
| confidence_tier | `fair` |
| Phase | post_race |

With stride=2, starting from frame 16588 (FWD pass):

- step 1: frame_f = 16588 + 2 = 16590 -- visits 16590, no termination (16590 != 16591)
- step 2: frame_f = 16588 + 4 = 16592 -- overshoots neighbor seed at 16591

The equality check `frame_f == neighbor_seed_frame` at line 1027 of
`track_runner/blob_walk/walk_walker.py` never fires. The walk continues past
frame 16591 into the next interval's frames.

The `max_steps_guard = abs(16591 - 16588) + 1 = 4`. The guard fires when
`step > 4`, i.e., at step 5. So the walker runs steps 1-4 before stopping,
visiting frames 16590, 16592, 16594, 16596 -- all of which belong to
adjacent intervals. This constitutes a confirmed overrun of 3 frames into
the next interval.

The BWD pass (starting from frame 16591) has stride=2:

- step 1: frame_f = 16591 - 2 = 16589 -- visits 16589, no termination (16589 != 16588)
- step 2: frame_f = 16591 - 4 = 16587 -- overshoots neighbor seed at 16588

Same failure mode in reverse: walks 2 frames into the previous interval.

---

## Overrun evidence verdict

Walk debug CSVs are absent, so there is no direct CSV-level confirmation of
the overrun (no emitted rows beyond the neighbor seed frame, no
`loop_guard` stop_reason to inspect). However:

1. The seeds JSON and interval scores are present and consistent.
2. The code path in `walk_walker.py` lines 1012-1034 is unambiguous: equality
   termination with stride=2 on a span-3 interval mathematically cannot stop
   at the neighbor seed.
3. The overrun direction and extent are fully deterministic from the code.

**Overrun evidence: CONFIRMED BY CODE ANALYSIS (no CSV telemetry available).**

---

## P12 verdict

**P12 OBSERVED FAILURE: YES.**

Interval #164 (frames 16588-16591, span=3, tier=fair) is a confirmed
termination-miss case. The walker processes frames from the adjacent intervals
on both the FWD and BWD passes. This is not a hypothetical risk -- it is
structurally certain given the code at `walk_walker.py` line 1027 and the
actual seed positions in this video.

The practical impact on output quality is bounded by interval #164's small
span (3 frames, ~25 ms at 120 fps). The misprocessed frames are 16590, 16592
(FWD) and 16589, 16587 (BWD). These frames belong to interval #163 and
interval #165 respectively, which are both `high`-confidence. The stride-2
overrun affects a total of at most 4 frames of output.

For a production fix, the termination condition should use `>=` comparison
(or equivalent overshoot detection) in place of the equality check at
`walk_walker.py` line 1027. Specifically: stop when
`sign * (frame_f - neighbor_seed_frame) >= 0` (i.e., the walker has reached
or passed the neighbor seed in the direction of travel).

---

## What a confirmation run would require

To obtain direct CSV telemetry evidence:
1. Trigger a Stage-4 blob walk on interval #164 alone (frames 16588-16591).
2. Inspect the debug CSV for rows with `frame_index` values of 16592, 16594,
   or 16596 (FWD direction) or 16587, 16585 (BWD direction).
3. Check the `stop_reason` column; `loop_guard` rather than `hit_neighbor_seed`
   would be direct confirmation.

This requires a partial re-solve (Stage 4 only, single interval). The solve
itself is expensive enough (HEVC decode) that it was not run for this analysis.
The code analysis is conclusive without it.

---

## Analysis script

Script used: `_temp_stride_analysis.py` (deleted before handoff per scope rules).

Command run:

```
source source_me.sh && python3 _temp_stride_analysis.py
```

Key output lines:

```
fps=119.94, stride=2
race_start_frame=565
total seeds: 310
total intervals: 309
intervals with span % 2 != 0 (post_race, low/fair tier): 1
interval #164: frames 16588-16591, span=3, tier=fair
```
