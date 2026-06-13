# Blob walk v2 P10 fix plan: bootstrap-accept fallback correction

Narrow fix plan for audit finding P10
([blob_walk_v2_implementation_audit.md](../active_plans/audits/blob_walk_v2_implementation_audit.md)),
validated as an observed failure in
[blob_walk_v2_check3_bootstrap_masking.md](../active_plans/workstreams/blob_walk_v2_check3_bootstrap_masking.md)
(claim J in
[blob_walk_v2_validation_report.md](../active_plans/reports/blob_walk_v2_validation_report.md)).
This is Milestone M1 of the fix-phase roadmap
([blob_walk_v2_fix_phase_roadmap.md](../active_plans/active/blob_walk_v2_fix_phase_roadmap.md),
workstreams WS-1A/WS-1B, work packages WP-1A/WP-1B). Scope is the
Stage-4 coverage seam and fallback gate only; the walker core is not
touched. Status: awaiting user approval. The human reviewer handles all
repository commit workflow.

Baseline precondition (from the roadmap): P12 is implemented and staged;
it becomes the baseline only after human review accepts it. That
acceptance must happen before this milestone's validation numbers are
treated as authoritative.

---

## 1. Observed failure

From the Check 3 workstream doc (26 passes examined across 3 source
videos and 13 intervals; verdict OBSERVED, 1 of 26 passes, 3.8%):

- Conant-4x400-2026_April_15.mkv `seed_1126_1134` FWD (8-frame
  interval): the bootstrap observation at the seed frame was accepted
  (`accepted_count = 1`), all 7 remaining frames were
  `soft_miss_no_blob` (`post_bootstrap_accepted = 0`).
- The Stage-4 Hermite fallback gate is `accepted_count == 0`, so the
  gate was not satisfied and the fallback did not fire.
- The shipped walk path holds the seed-frame position for 7 of 8
  frames -- strictly worse than a pure Hermite interval.
- The BWD pass on the same interval found 3 accepted frames and
  returned normally, so only the FWD pass is masked.

Short intervals are most vulnerable: a single bootstrap hit covers the
whole interval's "accepted" budget while providing no real trajectory
constraint. The masking also has a latent P12 interaction: the
degenerate span-smaller-than-stride case (closed by the P12 crossing
test) relies on the zero-accept fallback for its output, and a
bootstrap accept can mask that fallback too.

## 2. Exact code path

Three loci, observed at current line numbers:

Bootstrap accept --
[track_runner/blob_walk/walk_walker.py](../../track_runner/blob_walk/walk_walker.py),
`_run_bootstrap_step` (lines 919-924): the seed-frame observation
increments the same `accepts` list and `accepted` status count as
windowed-step accepts:

```python
	bootstrap_status = "accepted" if obs is not None else "soft_miss_no_blob"
	if obs is not None:
		accepts.append(frame_f)
		status_counts["accepted"] += 1
```

`WalkSummary.accepted_count` (line 1283) is `status_counts["accepted"]`,
so a bootstrap-only stall reports 1, not 0.

Coverage return --
[track_runner/walker_bundle.py](../../track_runner/walker_bundle.py),
`walk_bundle_to_path_with_coverage` (lines 492-555): returns
`(full_span_path, int(summary.accepted_count))`. The docstring states
the second value is the count of frames marked "accepted"; it does not
distinguish the bootstrap frame.

Fallback gate --
[track_runner/interval_solver.py](../../track_runner/interval_solver.py),
`solve_interval_analytical` (fallback block lines 529-564; gate at
551-552):

```python
		walker_fallback_fwd = (fwd_accepted == 0)
		walker_fallback_bwd = (bwd_accepted == 0)
```

Gap: `accepted_count == 1` with the single accept at the seed frame
(bootstrap only) does not trigger the fallback, although the walk
carries zero post-seed trajectory evidence.

One structural fact makes the fix well-defined: the seed frame can
appear in `accepts` at most once, and only via the bootstrap step.
Windowed steps start at `seed_frame + sign * stride`, and the neighbor
seed frame is never observed (termination check precedes the observe
call, preserved by the P12 fix). So "post-bootstrap accepts" is exactly
"accepts not at the pass's own seed frame".

## 3. Call-site audit (required pre-implementation check)

Recorded per the roadmap's mandatory audit. Re-run at implementation
time and re-recorded in the implementation changelog entry. Grep
performed 2026-06-10 over all `*.py` in the repo for
`walk_bundle_to_path_with_coverage`:

| Location | Kind of use | Field/shape it needs |
| --- | --- | --- |
| `track_runner/walker_bundle.py:492` | definition | becomes `(path, WalkCoverage)` |
| `track_runner/interval_solver.py:532-536` | production caller (the only one) | unpacks `(path, int)`; the int feeds only the gate at lines 551-552 |
| `track_runner/interval_solver.py:551-552` | gate read of the unpacked int | needs `post_bootstrap_accepted` |
| `tests/test_walker_stall_fallback.py:84,115` | monkeypatched fake returning `(path, int)` | fakes adapt to the named type |
| `tests/test_walker_flag.py:80` | monkeypatched fake returning `(path, 1)` | fake adapts to the named type |
| `tests/test_walker_stall_fallback.py:11`, `tests/test_walker_flag.py:7` | docstring prose mentions | wording updated with the fakes |

Findings, verified against current code:

- The only production caller is `solve_interval_analytical`; the
  unpacked integers `fwd_accepted` / `bwd_accepted` are read exactly
  once each, at the gate. No telemetry, debug CSV, or result-dict
  consumer reads the bare integer (`grep` for `fwd_accepted` /
  `bwd_accepted` finds only lines 532, 535, 551, 552).
- No consumer assumes a bare-integer second return value outside the
  known fallback seam. The audit-triggered stop condition is NOT met;
  implementation may proceed once this plan is approved.
- This matches the prior session's audit recorded in the roadmap
  (production caller in `interval_solver.py`; monkeypatched in
  `tests/test_walker_stall_fallback.py` and `tests/test_walker_flag.py`).

## 4. Minimal fix

Two invariants govern this fix and override any conflicting phrasing
elsewhere in this doc:

- **The fallback gate must measure post-seed trajectory evidence.** A
  pass whose only accepted frame is its own seed frame has the same
  evidentiary content as a zero-accept pass and must fall back to
  Hermite (the "never worse than Hermite" guarantee on promoted
  intervals).
- **The walker core is untouched.** Bootstrap status semantics,
  `WalkSummary.accepted_count`, `accepted_fraction`, debug CSV columns
  and values, selection, Viterbi costs, acceptance box, and emission
  all keep their current meaning. This fix corrects the seam that
  interprets coverage, not the walker that produces it.

### Coverage-seam semantics (the core of the fix)

The coverage return becomes a named type instead of a bare integer, so
both quantities are explicit rather than silently changing the meaning
of one int:

- `walker_bundle.py` gains a small dataclass `WalkCoverage` (matching
  the project's existing `WalkSummary` dataclass style) with two named
  fields:
  - `accepted_count`: total accepted frames, unchanged meaning,
    preserved for any telemetry or future consumer;
  - `post_bootstrap_accepted`: accepted frames excluding the pass's
    own seed frame, computed from `WalkSummary.accepts` and the
    bundle's seed frame via a pure helper
    `count_post_bootstrap_accepts(accepts, seed_frame)`.
- `walk_bundle_to_path_with_coverage` returns
  `(full_span_path, WalkCoverage(...))`; its docstring states both
  meanings.
- The fallback gate in `solve_interval_analytical` reads
  `coverage.post_bootstrap_accepted` **by name**, so the new meaning
  cannot be misread at the call site:

```python
		walker_fallback_fwd = (fwd_coverage.post_bootstrap_accepted == 0)
		walker_fallback_bwd = (bwd_coverage.post_bootstrap_accepted == 0)
```

- **Tests assert field names, never tuple positions** -- positional
  access is exactly the ambiguity this design removes. (Per the
  roadmap, an equivalent named form may be substituted only with a
  recorded reason; this plan proposes the dataclass as specified.)

The seed frame used by `count_post_bootstrap_accepts` is the pass's own
anchor seed (`bundle.seed["frame_index"]`): the left seed for FWD, the
right seed for BWD. Because the seed frame appears in `accepts` at most
once and only via bootstrap (section 2), the helper is a simple count
of accepts not equal to the seed frame.

This explicit-both design is chosen over repointing the existing
integer because the seam then documents itself and total-count
consumers keep working.

### Expected behavior change

- Bootstrap-only passes (accepted_count = 1, sole accept at the seed
  frame) now fire the per-pass Hermite fallback.
- Every pass with at least one post-bootstrap accept is byte-identical.
- True zero-accept passes are unchanged (fallback fired before, fires
  now).

### Files and functions that change

- `track_runner/walker_bundle.py`: `WalkCoverage` dataclass; pure
  helper `count_post_bootstrap_accepts(accepts, seed_frame)`; return
  shape and docstring of `walk_bundle_to_path_with_coverage`.
- `track_runner/interval_solver.py`: the two gate reads; the
  stall-definition comments and two walker docstrings updated to
  "zero post-bootstrap accepted frames".
- `track_runner/tr_schema.py`: `SCHEMA_VERSION` 13 -> 14; 14 added to
  `GEOMETRY_AFFECTING_SCHEMAS`.
- `docs/TR_SCHEMA_VERSION_HISTORY.md`: history entry (see section 6).
- Tests: new pure-helper test module;
  `tests/test_walker_stall_fallback.py` (fakes adapted, new
  bootstrap-only case); `tests/test_walker_flag.py` (fake adapted).

### What must stay unchanged

- Walker selection, statuses, and bootstrap status semantics
  (the bootstrap frame still reports `accepted` when observed).
- `WalkSummary.accepted_count` and `accepted_fraction` definitions.
- Debug CSV columns and values.
- Viterbi costs, acceptance box, emission, window buffer.
- C9 pass independence: the fallback remains per-pass output selection
  after both producers run; it reads each pass's own coverage, never
  raw_pred and never FWD/BWD agreement.

### Explicit non-goals

This milestone corrects the existing fallback predicate. Specifically
NOT included:

- No second fallback mechanism and no interval-specific special case.
- No new hard gates anywhere (roadmap principle: keep the gate count
  falling).
- Nothing from M3 (ranking quality), M4 (anchor advance), or M5
  (emission redesign).
- Nothing from the parked items: acceptance-box widening, skip-cap or
  Viterbi transition geometry, extrapolation hold-vs-linear (P9),
  evidence normalization, dead-code cleanup (P13), cache-bypass
  narrowing (P17), pre-pass store threading (P16).

## 5. Validation cases (in order of evidentiary weight)

1. **Targeted Stage-4 re-solve of Conant `seed_1126_1134` (primary
   proof).** Re-solve the masked interval after the fix. Expected:
   `walker_fallback_fwd = True` with Hermite FWD geometry shipped, and
   an unchanged BWD pass (3 accepted frames, walker path retained,
   `walker_fallback_bwd = False`). Any non-masked pass changing output
   in this re-solve is a stop condition.
2. **Fallback-seam pytest gate.**
   `tests/test_walker_stall_fallback.py` gains a bootstrap-only case:
   injected coverage `accepted_count = 1`,
   `post_bootstrap_accepted = 0` must fire the fallback. The existing
   zero-accept and accepted-pass cases keep their behavior with fakes
   adapted to the named type. `tests/test_walker_flag.py` fake updated
   (nonzero post-bootstrap coverage so it still isolates the OFF-vs-ON
   branch, not the fallback). This pytest seam is where P10's behavior
   is proven at test level, because the e2e harness never crosses the
   interval_solver seam (see case 4).
3. **Pure-helper unit tests.** New test module for
   `count_post_bootstrap_accepts`, with case shapes drawn from the
   Check 3 per-pass table:
   - empty accepts (Conant `seed_1080_1111` FWD shape: total 0,
     post-bootstrap 0);
   - bootstrap-only (Conant `seed_1126_1134` FWD shape: total 1,
     post-bootstrap 0 -- the masked case);
   - bootstrap + windowed accepts (Conant `seed_1296_1327` FWD shape:
     total 30, post-bootstrap 29);
   - bootstrap miss with windowed accept (Conant `seed_1134_1142` FWD
     shape: total 1, post-bootstrap 1 -- same total as the masked case,
     opposite gate outcome);
   - BWD pass with the seed at the right endpoint (Conant
     `seed_1126_1134` BWD shape: total 3, bootstrap miss,
     post-bootstrap 3);
   - a duplicate-frame case asserting each non-seed accept counts
     exactly once.
   Tests assert the named fields, never tuple positions.
4. **8-pass harness EQUAL (safety check only).** Rerun
   `tests/e2e/e2e_blob_walk_baseline.py`. Expected: EQUAL on all 8
   passes, because the harness drives the walker via `walk_driver` and
   never crosses the interval_solver seam -- it can prove the walker
   core is untouched, but it cannot exercise the fallback gate. That is
   why the targeted re-solve (case 1) and the pytest seam (case 2)
   carry the evidentiary weight and the harness is a safety check.
5. **Static and full-suite gates.** `pyflakes` clean on
   `track_runner/walker_bundle.py`, `track_runner/interval_solver.py`,
   `track_runner/tr_schema.py`; focused
   `pytest tests/ -k "walker or schema"`; full `pytest tests/` green.

## 6. Risk and rollback

- **Scope:** two production files at the Stage-4 seam plus the schema
  authority and tests. The walker core
  (`track_runner/blob_walk/`) is untouched.
- **Behavior change surface:** geometry changes only on
  bootstrap-only-masked walker passes (3.8% of sampled passes; 1 of 26
  in the Check 3 sample). All other passes are byte-identical. This is
  a correctness fix: corpus quality movement is expected to be small at
  that incidence and is not the justification.
- **SCHEMA_VERSION: bump -- yes, 13 -> 14, geometry-affecting.** The
  fix changes shipped geometry on masked passes (frozen-at-seed walker
  output replaced by Hermite). Per contract C10 there is one unified
  `SCHEMA_VERSION`; record the bump in
  [TR_SCHEMA_VERSION_HISTORY.md](../TR_SCHEMA_VERSION_HISTORY.md)
  annotated as "geometry-affecting only for bootstrap-only-masked
  walker passes; byte-identical for all other passes", and add 14 to
  `GEOMETRY_AFFECTING_SCHEMAS`. Honest tradeoff, same as the P12 bump:
  the bump invalidates geometry-derived caches on unaffected videos
  too, a re-solve cost paid for nothing there. The alternative (no
  bump) risks silently mixing frozen-at-seed pre-fix geometry with
  post-fix Hermite geometry on masked intervals -- exactly the mismatch
  C10 exists to prevent. Bump wins.
- **Rollback path:** restore the previous implementation of
  `walk_bundle_to_path_with_coverage`, the fallback-gate reads in
  `solve_interval_analytical`, and the schema constants
  (`SCHEMA_VERSION`, `GEOMETRY_AFFECTING_SCHEMAS`); remove the new test
  module. No data migration in either direction; re-solve regenerates
  artifacts under the active schema.

## 7. Stop conditions

Implementation halts and reports if any of these occurs:

- Any walker-decision diff in the 8-pass harness (the walker core must
  be untouched).
- Any non-masked pass changing output in the targeted re-solve.
- The re-run call-site audit finds a consumer that needs the total
  count where this plan assumed the post-bootstrap count (section 3
  found none; the audit is re-run at implementation time).

## 8. Out of scope

M3/M4/M5 work and all parked items (section 4 non-goals list) are
separate, unapproved follow-ups; nothing from any of them is included
in this plan. Obvious follow-ons at implementation time -- the
`docs/CHANGELOG.md` entry, the `docs/TR_SCHEMA_VERSION_HISTORY.md`
entry, and rerunning any failed gate after its cause is fixed -- belong
to the implementation milestone, not to this plan draft.
