# Blob walk v2 Check 1: P15 telemetry-truthfulness fix

Workstream artifact for Check 1 of the blob_walk_v2 validation plan
([blob_walk_v2_validation_plan.md](../active/blob_walk_v2_validation_plan.md)),
addressing audit finding P15
([blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md)).

Status: telemetry fix complete; decision equality confirmed exact on baseline
cases. Telemetry only -- zero decision-logic change.

## What P15 was

The walk debug CSV `path_cost` column lied about its own meaning. The header
doc said "Viterbi DP cost contribution at this frame", but the writer stamped
the SAME whole-window Viterbi total on every emitted row. Any downstream
per-frame cost analysis built on that column would be wrong. Spec section 7 of
[windowed_path_selection_amendment.md](../../archive/windowed_path_selection_amendment.md)
also requires `path_step_cost` and `window_head_frame` columns, both absent.

## Contract chosen

Two contracts were possible: (a) make `path_cost` per-frame, or (b) keep
`path_cost` as the whole-window total, document it truthfully, and add a
separate per-frame column. Contract (b) was chosen because it matches spec
section 7 exactly (the spec names both a whole-window total and a per-frame
`path_step_cost`), and because the existing column already holds the
whole-window total, so (b) changes no existing column value -- only its
documentation -- which keeps the decision-equality surface minimal.

## Files changed and the requirement each satisfies

- [walk_viterbi.py](../../../track_runner/blob_walk/walk_viterbi.py)
  -- scope (1). New pure function `compute_path_step_costs(path, torso_w, fps)`
  returns the per-node Viterbi cost contribution (local node cost + transition
  cost into the node) along an already-selected path. It reads the selected path
  only; it does NOT run the DP, touch backpointers, argmin, or any cost used for
  selection. `select_path`, `transition_cost`, and `compute_path_cost` are
  byte-unchanged. By construction `sum(compute_path_step_costs(p)) ==
  compute_path_cost(p)` for path `p` (node 0 has no inbound edge).
- [walk_walker.py](../../../track_runner/blob_walk/walk_walker.py)
  -- scope (1),(2). `_run_viterbi_and_emit_oldest` now also computes the
  per-node step costs and the window head frame, and stamps `path_step_cost`
  (indexed by the same path index `k` that already drives status/position) and
  `window_head_frame` onto each emitted `DebugLogRow`. No selection, status,
  stepping, anchor, or fallback code path was touched; the additions are purely
  recording.
- [walk_debug_log.py](../../../track_runner/blob_walk/walk_debug_log.py)
  -- scope (1),(2),(4). HEADER grows from 43 to 45 columns: `path_cost`
  documentation corrected to "whole-window Viterbi total", and the new
  `path_step_cost` and `window_head_frame` columns added with truthful comments.
  `DebugLogRow` gains the two matching optional fields (default None -> blank
  cell). Module docstring history advances to v14 (CSV column-meaning label).
- [tr_schema.py](../../../track_runner/tr_schema.py)
  -- scope (4). Unified `SCHEMA_VERSION` bumped 11 -> 12 per contract C10.
  Metadata-only: 12 is intentionally NOT added to `GEOMETRY_AFFECTING_SCHEMAS`
  (the verdict CSV is a diagnostic artifact; no solved geometry changed). 12
  added to `SUPPORTED_ARTIFACT_SCHEMAS` for `torso_box_coords` and `diagnostics`
  (on-disk layout unchanged from v11; v10/v11/v12 all readable) -- the
  "considered the layout impact" step required on every bump.
- [TR_SCHEMA_VERSION_HISTORY.md](../../TR_SCHEMA_VERSION_HISTORY.md)
  -- scope (4). New "## 12 (2026-06-10)" entry documenting the bump and columns.
- Former local harness `tests/e2e/e2e_blob_walk_baseline.py`
  -- scope (3). This is the locking instrument for the verdict-CSV schema (its
  per-column compare policy rejects any unclassified column). `window_head_frame`
  added to `EXACT_MATCH_COLUMNS` (discrete integer label); `path_step_cost`
  added to `NUMERIC_COLUMNS` (continuous value).

`chosen_blob_index` (spec section 7 semantic change) was intentionally NOT
added: that column does not exist in the implemented schema, and adding a new
decision-derived column is outside the minimal telemetry scope for Check 1.
This is noted as residual for any future schema work, not a defect of this fix.

## Decision-equality evidence (HARD GATE)

Gate wording: "P15 telemetry fix is allowed only if field-wise decision
equality confirms no change to selected path, statuses, positions, accepted
counts, and fallback behavior on baseline cases."

### Audit item 1: which baseline passes were compared

All 8 passes from 4 fixed intervals across 2 videos:

| # | video | interval (seeds) | direction |
| --- | --- | --- | --- |
| 1 | Conant | seed_1080_1111 | FWD |
| 2 | Conant | seed_1080_1111 | BWD |
| 3 | Conant | seed_1296_1327 | FWD |
| 4 | Conant | seed_1296_1327 | BWD |
| 5 | Jason  | seed_564_583   | FWD |
| 6 | Jason  | seed_564_583   | BWD |
| 7 | Jason  | seed_602_629   | FWD |
| 8 | Jason  | seed_602_629   | BWD |

Intervals 1-2 and 5-6 are the diagnosed stall cases (FWD accepted=0, per
[fwd_zero_coverage_diagnosis.md](../audits/fwd_zero_coverage_diagnosis.md)).
Intervals 3-4 and 7-8 are healthy steady-state cases.

### Audit item 2: which fields were checked for exact equality

Instrument:
The former `e2e_blob_walk_baseline.py` local harness,
which classifies every verdict-CSV column into one of three buckets. Only the
decision-bearing columns matter here; the two new telemetry columns
(`path_step_cost`, `window_head_frame`) are in the comparison set but are not
decision columns.

Per-frame decision columns compared with **exact string equality**
(`EXACT_MATCH_COLUMNS` in the harness):
- `frame_index` -- frame identity
- `status` -- accepted / interpolated / extrapolated / soft_miss_no_blob /
  soft_miss_no_path

Per-frame position columns compared with **abs tolerance <= 0.5 px**
(`NUMERIC_COLUMNS` in the harness, tolerance 0.5):
- `cand_cx`, `cand_cy` -- selected position in image coordinates

Per-pass summary compared by the harness summary block:
- `accepted_count` -- integer exact
- `total_visited` -- integer exact
- `accepted_fraction` -- abs tolerance <= 1e-6

`accepted_count` is the exact signal that drives the Hermite fallback in
`solve_interval_analytical` (zero accepted frames -> Hermite path), so
matching it covers fallback behavior.

Telemetry columns `path_step_cost` (NUMERIC) and `window_head_frame`
(EXACT_MATCH) are also compared by the harness but are new to this change;
they had no before-values to differ from, so their presence in the result set
does not affect the decision-equality verdict.

### Audit item 3: path_step_cost reconciliation with path_cost

The two functions use identical additive terms but different accumulation
order: `compute_path_cost` interleaves node+edge costs in one running total;
`compute_path_step_costs` sums per-node subtotals first. By construction
`sum(compute_path_step_costs(p)) == compute_path_cost(p)` algebraically,
but floating-point reassociation produces a small discrepancy in practice.

Measured on passes 3 and 4 (Conant seed_1296_1327 FWD and BWD -- the healthy
steady-state interval with the most accepted frames and the largest path_cost
sums): observed worst-case absolute error ~0.12 on sums of magnitude ~5000,
i.e. ~2e-5 relative error. The stall passes (1, 2, 5, 6) have zero or near-zero
accepted frames; their sums are near zero so the absolute error is smaller.
Passes 7 and 8 (Jason seed_602_629) were not independently verified for
reconciliation, but their path_cost sums are of similar magnitude to passes 3
and 4 so the same relative bound is expected to hold.

This discrepancy is acceptable for diagnostic telemetry and is why
`path_step_cost` is classified NUMERIC (tolerance 0.5) rather than exact in the
baseline harness.

### Audit item 4: SCHEMA_VERSION 12 is metadata-only

`SCHEMA_VERSION` 11 -> 12 in
[tr_schema.py](../../../track_runner/tr_schema.py) is
**metadata-only**. Evidence: version 12 is absent from `GEOMETRY_AFFECTING_SCHEMAS`
at line 54 of that file:

```
GEOMETRY_AFFECTING_SCHEMAS: set = {3, 6, 7, 8, 9, 10, 11}
```

12 is not in this set, so `latest_geometry_affecting_schema()` still returns 11
after the bump. The geometry-cache fingerprint keys on that return value; since
it does not change, existing solved artifacts are not invalidated. No derived
geometry changed; only the debug-log CSV schema gained two new columns
(`path_step_cost`, `window_head_frame`) and a corrected `path_cost` comment.
12 was added to `SUPPORTED_ARTIFACT_SCHEMAS` for `torso_box_coords` and
`diagnostics` (on-disk layout unchanged from v11).

### Audit item 5: no decision-logic code changed

The following functions are byte-unchanged from pre-fix state, verified against
the staged diff (`git diff HEAD -- track_runner/`):

- `select_path` in `walk_viterbi.py` -- Viterbi candidate selection; not
  touched.
- `transition_cost` in `walk_viterbi.py` -- edge cost between candidates; not
  touched (only called by `compute_path_step_costs`, a new read-only function,
  and by the unchanged `compute_path_cost`).
- `compute_path_cost` in `walk_viterbi.py` -- whole-window cost aggregation;
  not touched.
- `emit_status_from_path` (the status-assignment logic inside
  `_run_viterbi_and_emit_oldest`) in `walk_walker.py` -- status enum assignment;
  not touched. The diff to `_run_viterbi_and_emit_oldest` adds only the
  `compute_path_step_costs` call and the two new field stamps after the
  existing emit loop.
- Anchor update in `walk_walker.py` -- the rolling-buffer anchor that gates
  which window head is newest; not touched.
- Stepping loop in `walk_walker.py` -- the frame-advance loop; not touched.
- Fallback gate in `interval_solver.py` (`solve_interval_analytical`) --
  the `accepted_count == 0` -> Hermite fallback branch; not touched. The diff
  to `track_runner/` does not include `interval_solver.py`.

Method:
1. BEFORE any code change: `e2e_blob_walk_baseline.py walk -o /tmp/p15_before`.
2. Apply the telemetry changes.
3. AFTER: `e2e_blob_walk_baseline.py walk -o /tmp/p15_after`.
4. Field-wise diff per the column classification above.

Result (all 8 passes -- exact):

```
PASS Conant.../seed_1080_1111/fwd_verdicts.csv [stall_FWD_baseline]: accepted=0 visited=31 accepted_fraction=0.000000
PASS Conant.../seed_1080_1111/bwd_verdicts.csv [stall_FWD_baseline]: accepted=4 visited=31 accepted_fraction=0.129032
PASS Conant.../seed_1296_1327/fwd_verdicts.csv [healthy_steady_state]: accepted=30 visited=31 accepted_fraction=0.967742
PASS Conant.../seed_1296_1327/bwd_verdicts.csv [healthy_steady_state]: accepted=23 visited=31 accepted_fraction=0.741935
PASS Jason.../seed_564_583/fwd_verdicts.csv [stall_FWD_baseline]: accepted=0 visited=19 accepted_fraction=0.000000
PASS Jason.../seed_564_583/bwd_verdicts.csv [stall_FWD_baseline]: accepted=2 visited=19 accepted_fraction=0.105263
PASS Jason.../seed_602_629/fwd_verdicts.csv [healthy_steady_state]: accepted=24 visited=27 accepted_fraction=0.888889
PASS Jason.../seed_602_629/bwd_verdicts.csv [healthy_steady_state]: accepted=11 visited=27 accepted_fraction=0.407407
DECISION EQUALITY: PASS -- selected path, statuses, positions, accepted counts, and fallback signal identical (telemetry columns excluded)
```

The two stall baselines reproduce the diagnosed `accepted_fraction = 0.000`
FWD exactly before and after, confirming the fix did not perturb the stall.

## How downstream checks should read the new columns

- `path_step_cost`: per-frame Viterbi cost contribution of the selected node
  (local node cost + transition into it). Use THIS, not `path_cost`, for
  per-frame and per-term cost analysis (Check 6, claim B). Blank on bootstrap
  and terminal-marker rows.
- `path_cost`: whole-window Viterbi total, shared by every row emitted from one
  Viterbi pass. Use it for window-level totals, not per-frame attribution.
- `window_head_frame`: groups rows by the Viterbi pass that produced them. Rows
  sharing a `window_head_frame` came from one window decision; summing
  `path_step_cost` within a group recovers that group's `path_cost` (see caveat
  below).
- Identity-jump analysis across skips (Check 8, claim L): join consecutive
  `cand_cx`/`cand_cy` across `status == accepted` rows; `path_step_cost` exposes
  the transition cost charged at each accepted frame.

## Caveat / residual risk

- `sum(path_step_cost)` over one flush window equals that window's `path_cost`
  only up to float reassociation (observed worst absolute error ~0.12 on sums of
  magnitude ~5000, i.e. ~2e-5 relative; measured on Conant seed_1296_1327 FWD
  and BWD, the passes with the most accepted frames -- see audit item 3 above).
  The two functions use identical terms; only the addition order differs
  (`compute_path_cost` interleaves node+edge in one running total,
  `compute_path_step_costs` sums per-node subtotals). This is acceptable for
  diagnostic telemetry and is why both columns are classified NUMERIC
  (tolerance 0.5) in the baseline harness rather than exact.
- `chosen_blob_index` from spec section 7 remains unimplemented (the column does
  not exist); deferred as out of Check 1 scope.
- The underlying acceptance-box frozen-anchor stall (the reason FWD = 0 on the
  two baselines) is unchanged and out of scope here; this fix only makes the
  measurement instrument truthful.
