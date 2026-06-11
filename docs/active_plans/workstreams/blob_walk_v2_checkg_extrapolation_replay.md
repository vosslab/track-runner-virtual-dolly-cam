# Blob walk v2 check G: extrapolation replay

Workstream artifact. Offline replay of hold-last vs linear-extension for
the `extrapolated` status, per audit P9 and claim G in the assumption table.
No production code changes. Temp scripts removed before handoff.

Related docs:
- [../audits/blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md) - P6, P9, assumption table
- [../reports/blob_walk_v2_validation_report.md](../reports/blob_walk_v2_validation_report.md) - claim G section

---

## Setup and data sources

Walk debug CSVs gathered from three output directories:

- `output_smoke/check4_walks/` (2 videos x 2 intervals x 2 directions = 8 CSVs)
- `output_smoke/check5_walks/` (same 8 unique combinations)
- `/tmp/check8_walk_output/` (same 8 unique combinations)

Total: 24 CSV files (8 unique combinations replicated across 3 check-set runs).
Videos: `Jason-3200m-sectionals-IMG_4005.mkv` and `Conant-4x400-2026_April_15.mkv`.
Intervals: `seed_564_583`, `seed_602_629`, `seed_1080_1111`, `seed_1296_1327`.

The `seed_564_583` interval has no corridor blobs in any run (all
`soft_miss_no_blob`) so no accepted blob positions exist. That interval
produces no replay scenarios.

Code inspected: `track_runner/blob_walk/walk_status.py` (full),
`track_runner/blob_walk/walk_walker.py` (full).

---

## P6 confirmation: zero extrapolated/interpolated frames in logs

Aggregate status counts across all 24 CSVs:

| Status | Count |
| --- | --- |
| soft_miss_no_blob | 366 |
| accepted | 282 |
| after_walk_terminated | 24 |
| extrapolated | 0 |
| interpolated | 0 |
| soft_miss_no_path | 0 |

Audit P6 is confirmed: `extrapolated` and `interpolated` statuses have zero
occurrences in all available walk debug logs. Reachability analysis follows.

---

## Reachability analysis (P6 mechanism)

From `walk_status.py` lines 88-125:

`extrapolated` requires:
1. Candidates-present for the frame (`not candidates_empty`)
2. A prior accepted frame exists within the window (`prev_accept is not None`)
3. No next accepted frame exists within the window (`next_accept is None`)
4. Consecutive-extrap counter <= `EXTRAP_MAX` = 2

Condition 3 means: the frame is PAST the last accepted frame in the window.
In steady-state emission (`emit_count=1`, emitting the oldest frame at
offset t=0), there is never an accepted frame before t=0, so prev_accept is
always None and control falls to `soft_miss_no_path` or `soft_miss_no_blob`.
This matches audit P6's proof trace.

`extrapolated` is therefore only reachable during the END-OF-WALK FLUSH
(`emit_count=len(buffer)`, `walk_walker.py` lines 1105-1124), when t > 0
frames are emitted. For `extrapolated` to appear in the flush, the final
window must have:
- At least one accepted frame followed by a frame with candidates-present
  but no accepted frame after it.
- This accepted frame must be in the flush window tail.

In the available logs, every interval that has any corridor blobs
(`seed_602_629` FWD/BWD, `seed_1296_1327` FWD/BWD, `seed_1080_1111` BWD)
has accepted frames distributed throughout the interval, so the flush
window typically contains accepted frames in its tail. The condition
(candidates-present AND past-last-accept in window) did not arise in any
of the 24 available CSVs.

`interpolated` requires BOTH a prior and a next accepted frame in the window.
This is structurally impossible during steady-state emission (same P6
argument) and would require an accept-then-skip-then-accept pattern within
the flush window tail.

Both statuses are flush-only. Flush windows hold at most 8 frames
(WALKER_WINDOW_FRAMES - 1 = 8). Maximum extrapolated frames per pass: 2
(EXTRAP_MAX = 2). Maximum interpolated frames per pass: 6 (3 accepted
anchor the interpolation at most). In practice, both are near-zero in
available corpus data.

---

## Offline replay: hold vs linear (theoretical + empirical)

### Method

Because no actual extrapolated frames exist in the logs, the replay is
constructed from the last two accepted blob positions (actual `cand_cx`,
`cand_cy` values from the CSV) and the seed-to-neighbor linear
interpolation as the reference baseline. For each qualifying CSV:

1. Extract seed center (from `step=0` row `pred_cx/pred_cy`).
2. Extract neighbor center (from `after_walk_terminated` row `pred_cx/pred_cy`).
3. Take the last two accepted blob rows: positions `(a1_cx, a1_cy)` at
   frame `a1` and `(a2_cx, a2_cy)` at frame `a2`.
4. Compute per-frame velocity from a1 to a2.
5. For tail offsets 1 and 2 frames after `a2`:
   - Hypothetical `extrap_frame = a2_frame + offset`.
   - Reference position: linear interpolation from seed to neighbor at `extrap_frame`.
   - Hold position: `(a2_cx, a2_cy)` (current behavior).
   - Linear position: `(a2_cx + vel_cx * offset, a2_cy + vel_cy * offset)`.
   - Error: Euclidean distance from each to reference, in torso-width units.

Reference caveat: the seed-to-neighbor linear interpolation is the Hermite
baseline, not a measured ground-truth runner position. The actual runner
trajectory diverges from it (see trajectory analysis below). This limits the
comparison to "how each method tracks the Hermite reference line."

### Trajectory vs reference line

The Jason `seed_602_629` FWD accepted trajectory shows consistent errors
of -0.43 to -1.86 px below the reference line (actual runner runs slightly
inside the reference arc). The Conant `seed_1296_1327` FWD trajectory
starts 3-4 px above the reference, crosses it at frame ~1311-1313, then
diverges to -4.6 px below near the neighbor seed. The BWD passes show
larger divergences (up to 50 px for `seed_1296_1327` BWD) because the
reference is computed backwards and the runner has a non-linear trajectory.

This means: hold error and linear error both reflect how far the METHOD
deviates from the Hermite reference line, not from the actual runner.

### Replay results (8 unique CSVs, 10 scenarios)

| Tail offset | n | Hold mean err (TW) | Linear mean err (TW) | Hold wins | Linear wins |
| --- | --- | --- | --- | --- | --- |
| +1 frame | 5 | 1.079 | 1.179 | 5 | 0 |
| +2 frames | 5 | 1.051 | 1.267 | 5 | 0 |

Hold wins in all 10 scenarios. **However, this result is an artifact of the
reference choice**, not evidence that hold is geometrically superior.

Detailed scenarios:

| Interval | Dir | Off | Vel (px/fr) | Hold err (TW) | Lin err (TW) | Winner |
| --- | --- | --- | --- | --- | --- | --- |
| seed_1296_1327 | FWD | +1 | 1.251 | 0.194 | 0.246 | hold |
| seed_1296_1327 | FWD | +2 | 1.251 | 0.161 | 0.273 | hold |
| seed_1296_1327 | BWD | +1 | 0.875 | 2.064 | 2.079 | hold |
| seed_1296_1327 | BWD | +2 | 0.875 | 2.030 | 2.059 | hold |
| seed_1080_1111 | BWD | +1 | 2.628 | 1.398 | 1.525 | hold |
| seed_1080_1111 | BWD | +2 | 2.628 | 1.324 | 1.578 | hold |
| seed_602_629 | FWD | +1 | 0.505 | 1.199 | 1.201 | hold |
| seed_602_629 | FWD | +2 | 0.505 | 1.234 | 1.250 | hold |
| seed_602_629 | BWD | +1 | 2.461 | 0.544 | 0.845 | hold |
| seed_602_629 | BWD | +2 | 2.461 | 0.504 | 1.174 | hold |

**Why hold appears better:** The runner's last-accepted position (a2) is close
to the reference line at the extrapolated frame. The linear extension
extrapolates AWAY from the neighbor seed, while the reference line converges
toward it. Both methods are far from the reference (errors 0.16-2.06 TW),
but hold lands closer simply because the reference curves toward the neighbor
while the linear extension continues past it.

This is not a meaningful test of hold vs linear as position predictors.
The comparison is contaminated by the reference line's curvature toward
the neighbor seed.

### Synthetic parametric scenarios (uniform-motion reference)

For the synthetic case where the runner follows the reference line exactly
(constant-velocity motion from seed to neighbor), linear extrapolation
achieves zero error and hold accumulates 0.22-0.42 TW per frame:

| Tail offset | n | Hold mean err (TW) | Linear mean err (TW) | Hold wins | Linear wins |
| --- | --- | --- | --- | --- | --- |
| +1 frame | 40 | 0.220 | 0.000 | 0 | 40 |
| +2 frames | 19 | 0.420 | 0.000 | 0 | 19 |

In the constant-velocity case linear is strictly better. This is the
expected theoretical result: hold error equals accumulated displacement,
linear error is zero.

---

## Why the comparison is indeterminate with available data

Three factors limit the conclusion:

1. **Zero affected frames in all logs.** No `extrapolated` frames exist in
   24 available CSVs. The code path under test is never executed in
   practice with the current accepted fraction and corpus intervals.

2. **Reference contamination.** The seed-to-neighbor linear interpolation is
   not ground-truth runner position. The actual trajectory diverges from it
   by 1-50 px across an interval. Using it as the reference measures
   "which method tracks the Hermite line better," not "which method is
   closer to the runner."

3. **Small effect surface.** Even when reachable, `extrapolated` affects at
   most 2 frames per pass per interval (EXTRAP_MAX = 2). At the observed
   velocity magnitudes (0.5-2.6 px/frame) and torso widths (6.5-11.0 px),
   hold error is 0.05-0.40 TW per frame. For 2 frames maximum, the total
   positional degradation from hold vs linear is under 0.8 TW cumulative,
   smaller than the typical inter-frame Hermite residual.

---

## Verdict: claim G

**UNDETERMINED.**

- P9 is confirmed: the code uses HOLD at lines 114-116 of `walk_status.py`
  ("Simple hold: use last accepted position") while the spec says linear
  extension of the last two accepted positions.
- The spec deviation is real, but the affected surface is zero in all
  available logs.
- Empirical comparison with available data is contaminated by the reference
  line curvature and does not cleanly measure hold vs linear as position
  estimators.
- Theoretical analysis (uniform motion) shows linear BETTER, as expected.
- Practical significance is negligible at current accepted fractions: the
  `extrapolated` branch never fires. Any improvement from the fix would
  require (a) the accepted fraction to increase enough for some intervals
  to have flush-tail frames with candidates-present past the last accept,
  and (b) those frames to land in the final blended path rather than being
  masked by the Hermite fallback.

The verdict could be narrowed to LINEAR BETTER (or NO MEANINGFUL DIFFERENCE)
if a corpus with actual `extrapolated` frames becomes available and a
ground-truth labeling or dual-seed reference is used. With current data,
UNDETERMINED is the correct call.

---

## Spec correction status (P9)

The spec deviation (hold instead of linear) is a confirmed code-docs
mismatch but has no measurable impact at current accepted fractions.
Recommended disposition: low-priority fix, deferred until
`extrapolated_count` becomes non-zero in production runs. Fix requires
changing `walk_status.py` lines 114-116 to store the last two accepted
positions and extrapolate linearly. The interpolated case (lines 101-109)
is already implemented correctly (true linear interpolation between
bracketing accepts).

---

## Verification commands

```
source source_me.sh && python3 tests/check_ascii_compliance.py -i docs/active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md
git add docs/active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md
pytest tests/test_markdown_links.py -q
```
