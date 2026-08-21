# Blob walk v2 P12 fix plan: stride termination overrun

> **Status: historical.** The separate `blob_walk_v2` diagnostic product was
> removed on 2026-08-21. This plan records an earlier investigation and is not
> awaiting approval or part of the current production walker workflow.

Narrow fix plan for audit finding P12
([blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md)),
validated as an observed failure in
[blob_walk_v2_check0_stride_overrun.md](../workstreams/blob_walk_v2_check0_stride_overrun.md).
Scope was the termination condition only.

---

## 1. Observed failure

From the Check 0 workstream doc (Lyra-Wheeling-IMG_3912.mkv, 119.94 fps,
stride 2; see the FPS and stride table there):

- Total intervals: 309. Post-race intervals with odd span: 148.
  Post-race odd-span intervals in the Stage-4 promoted tier (low/fair): 1.
- The one promoted odd-span interval is **interval #164**: left seed frame
  16588, right seed frame 16591, span 3, `confidence_tier = fair`,
  phase post_race.
- FWD pass from 16588 at stride 2: step 1 visits 16590 (16590 != 16591, no
  termination); step 2 computes 16592, overshooting the neighbor seed. The
  equality check never fires. With `max_steps_guard = 4`, the walker runs
  steps 1-4, visiting frames 16590, 16592, 16594, 16596 -- a confirmed
  overrun of 3 frames into the next interval.
- BWD pass from 16591: step 1 visits 16589; step 2 computes 16587,
  overshooting the neighbor seed at 16588; walks 2 frames into the
  previous interval.
- Verdict in the workstream doc: "P12 OBSERVED FAILURE: YES." Confirmed by
  code analysis (no walk debug CSVs existed for this video).

This also violates contract C5/C6 interval independence in spirit: a walk
observes frames belonging to adjacent intervals.

## 2. Exact code path

File: [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py),
function `_run_windowed_steps`. Current line numbers (the audit's 1012/1019/1027
references are stale after later edits):

Stepping (lines 1027-1028 and 1035):

```python
	step = 1
	dt = stride
	...
	while True:
		frame_f = seed_frame + sign * step * dt
```

Termination (lines 1042-1050):

```python
		# Termination: hit neighbor seed.
		if frame_f == neighbor_seed_frame:
			stop_reason = "hit_neighbor_seed"
			break

		# Termination: loop-bound guard.
		if step > max_steps_guard:
			stop_reason = "loop_guard"
			break
```

`step` increments at line 1100. The guard is
`max_steps_guard = abs(neighbor_seed_frame - seed_frame) + 1` (line 1030),
counted in STEPS, so at stride 2 the overrun can reach roughly
`stride * span` frames before the guard fires.

Stride origin: `residual_motion.resolve_stride` (residual_motion.py line 442),
`stride = max(1, round(fps / 60.0))`. 30 and 60 fps give stride 1;
119.94 fps gives stride 2.

Note the ordering inside the loop: the termination check runs BEFORE the
observe call. The neighbor seed frame itself is therefore never observed
today, at any stride. Any fix must preserve that property.

## 3. Minimal fix

Two invariants govern this fix and override any conflicting phrasing elsewhere
in this doc:

- **Walks must not observe frames outside the seed-bounded interval.** A walk
  from `seed_frame` toward `neighbor_seed_frame` may only observe frames
  strictly between the two seeds. It must never observe a frame belonging to an
  adjacent interval.
- **Seed endpoints remain anchors and are not observed.** Both `seed_frame` and
  `neighbor_seed_frame` are hard anchors per contract C3. Neither endpoint is
  ever observed by the walk; the termination check runs before the observe call
  so the neighbor seed frame is reached but never measured.

Replace the equality termination with a directional crossing test that clamps
the terminal frame to the neighbor seed:

```python
		# Termination: reached or passed neighbor seed in direction of travel.
		if sign * (frame_f - neighbor_seed_frame) >= 0:
			frame_f = neighbor_seed_frame
			stop_reason = "hit_neighbor_seed"
			break
```

### Final-step semantics decision

Two candidate semantics were considered:

1. **Shorten the final step to land on the seed.** When the next stride step
   would overshoot, set `frame_f = neighbor_seed_frame` instead.
2. **Terminate on crossing without touching the seed frame.** Break as soon
   as the computed frame reaches or passes the seed.

These converge to the same code. Because the termination check precedes the
observe call, "landing on the seed" and "terminating on crossing with
`frame_f` clamped to the seed" both break out of the loop before any
observation of the seed frame -- exactly matching today's stride-1 behavior,
where equality fires and the seed frame is never observed (seeds are hard
anchors per C3, not observations). **Recommended: the crossing test with
clamp, as shown above.** Reasons:

- It preserves the "walk terminates AT the neighbor seed" semantics: the
  returned `frame_f` and diagnostic rows report the seed frame, not an
  overshot frame.
- It is byte-identical to the equality test at stride 1: when
  `frame_f == neighbor_seed_frame`, the condition `>= 0` fires and the clamp
  is a no-op.
- BWD symmetry is automatic: with `sign = -1`, the test reads
  `-(frame_f - neighbor_seed_frame) >= 0`, i.e. fires when
  `frame_f <= neighbor_seed_frame`, the correct crossing test for a
  descending walk.
- It also closes the degenerate case `span < stride` (e.g. span 1 at
  stride 2): the very first computed frame crosses, the walk terminates
  immediately with zero observed frames, and the existing pure-stall Hermite
  fallback covers output.

After the fix, walks never observe frames outside
`[min(seed_frame, neighbor_seed_frame), max(seed_frame, neighbor_seed_frame)]`,
exclusive of the endpoints.

### Functions that change

- `_run_windowed_steps` in `walk_walker.py`: the termination block only.
- Preferred: extract a small pure helper in `walk_walker.py`, e.g.

```python
#============================================
def _neighbor_reached(frame_f: int, neighbor_seed_frame: int, sign: int) -> bool:
	"""Return True when the walk has reached or passed the neighbor seed."""
	reached = sign * (frame_f - neighbor_seed_frame) >= 0
	return reached
```

  called from the termination block, so the predicate is unit-testable
  without a reader or residual cache.

### Explicit non-goals

No other logic changes. Specifically NOT touched:

- scoring / Viterbi costs / weights
- acceptance-box or ROI construction
- pure-stall Hermite fallback
- emission, window buffer, or flush logic
- loop guard formula (it remains a backstop; the crossing test now fires
  first in all well-formed intervals)
- no new gates, no stride changes in `resolve_stride`
- Check 7 (ranking quality) and Claim G (extrapolation) are separate,
  unapproved follow-ups and are deliberately NOT absorbed here.

## 4. Validation cases (30 / 60 / 120 fps)

1. **Stride-1 decision equality (30 and 60 fps, stride = 1).** Rerun the
   8-pass harness `tests/e2e/e2e_blob_walk_baseline.py`. Expected:
   byte-identical decisions, because the crossing test differs from the
   equality test only at stride > 1.
2. **Stride-2 even-span interval.** Synthetic or real even-span interval at
   stride 2: the computed frame lands exactly on the seed, the crossing test
   fires with the clamp a no-op, `stop_reason = "hit_neighbor_seed"`.
   Unchanged from before.
3. **Stride-2 odd-span: interval #164 (frames 16588-16591).** Expected
   visited (observed) frame sequences after the fix:
   - FWD from 16588: observes 16590 only; step 2 computes 16592, crossing
     fires, `frame_f` clamps to 16591, terminates with
     `stop_reason = "hit_neighbor_seed"`. Frame 16591 (the seed) is NOT
     observed, matching stride-1 semantics; frame 16589 is skipped by the
     stride by design. FWD final `frame_f` == 16591.
   - BWD from 16591: observes 16589 only; step 2 computes 16587, crossing
     fires, `frame_f` clamps to 16588, terminates with
     `stop_reason = "hit_neighbor_seed"`. BWD final `frame_f` == 16588.
   - No visited frame falls outside [16588, 16591].
   Confirmation per the workstream doc: a Stage-4 single-interval re-solve
   with debug CSV; the `stop_reason` column must read `hit_neighbor_seed`,
   never `loop_guard`, and no row may carry `frame_index` 16592/16594/16596
   (FWD) or 16587/16585 (BWD).
4. **Unit tests.** Add a pytest module under `tests/` for the extracted
   `_neighbor_reached` helper: stride-1 equality cases (FWD and BWD),
   stride-2 overshoot cases (FWD and BWD), not-yet-reached cases, and the
   span-smaller-than-stride immediate-termination case. Pure function, no
   fixtures needed.

Invariant restated: walks must never observe frames outside the closed
interval bounded by `seed_frame` and `neighbor_seed_frame`.

## 5. Risk and rollback

- **Scope:** single file (`walk_walker.py`) plus one new test module.
  Rollback is reverting one termination block.
- **Behavior change surface:** stride-1 sources (30/60 fps) are byte
  identical. Only stride > 1 sources change, and only on intervals whose
  span is not divisible by stride; on those the change is strictly a
  correctness fix (stop at the seed instead of overrunning into adjacent
  intervals).
- **SCHEMA_VERSION: bump -- yes.** The fix changes walk outputs (per-frame
  statuses, accepted positions, paths) on stride > 1 sources: that is
  geometry-affecting for derived artifacts. Per contract C10 there is one
  unified `SCHEMA_VERSION`, and a bump records that artifacts written after
  the fix follow a new contract; record the bump in
  [TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md)
  annotated as "geometry-affecting for stride > 1 (>= ~90 fps) sources
  only; byte-identical at stride 1". Honest tradeoff: the bump invalidates
  geometry-derived caches on 30/60 fps videos too, where output is
  unchanged -- a re-solve cost paid for nothing on those videos. The
  alternative (no bump) risks silently mixing pre-fix overrun geometry with
  post-fix geometry on 120 fps videos, which is exactly the mismatch C10
  exists to prevent. Bump wins.

## 6. Out of scope

Check 7 (ranking quality) and Claim G (extrapolation behavior) are separate,
unapproved follow-ups; nothing from either is included in this plan.
