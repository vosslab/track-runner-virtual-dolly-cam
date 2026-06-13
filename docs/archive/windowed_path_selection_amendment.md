## CLOSED 2026-05-29

Status: shipped and validated. The windowed path-selection walker landed in
`tools/blob_walk_v2/walk_walker.py` (schema bump v13), the per-frame single-winner
path was removed without a fallback flag, and the SCHEMA_VERSION history entry
was added. The plan closes per WP-3C of `~/.claude/plans/sequential-soaring-hopper.md`.

Primary results. The #85 24-corpus rerun produced FWD accepted_fraction 42.3%
and BWD 41.0% across 48 walks; both clear the amendment Section 10 bars
(FWD >= 34.7%, BWD >= 24.6%). The #95 v2 120-corpus rerun, after the #100
auto-bin scale-factor and source-scale override fix, produced FWD 38.7% and
BWD 39.1% across 61 valid intervals of 120 (5 of 6 videos), again clearing
both bars corpus-wide at a ~5x wall-clock speedup from auto-bin at bin_factor=4.
The full review package lives at `dump_step1/REVIEW_PACKAGE/`.

Known followup (#101). All 20 Lyra-Wheeling intervals in the v2 120-corpus
failed with `degenerate ROI` errors raised by `compute_residual_for_frame`.
The dump-time filter `has_valid_seed_roi` and the render-time ROI clamp disagree
on what counts as off-frame at seed boxes near image boundaries. Tracked as a
separate task; does not affect the closed amendment's primary acceptance bars.

Note (2026-06-12, SCHEMA_VERSION 14): the Viterbi cost model specified in this
amendment was subsequently redesigned. The variance terms (`velocity_consistency_cost`,
`angle_consistency_cost`) shipped as pairwise velocity-delta scoring
(`WEIGHT_SPEED_DELTA` / `WEIGHT_HEADING_DELTA`) rather than window-variance
terms. All six cost weights now live in `track_runner/track_runner.config.yaml`
under the `walker_costs` section, not in `overlay_styles.yaml` as the amendment
proposed. See [docs/TRACK_RUNNER_YAML_CONFIG.md](../TRACK_RUNNER_YAML_CONFIG.md)
for the full key reference.

---

# Windowed path-selection amendment for the blob walker

Status: AMENDMENT PROPOSED. Supersedes per-frame single-winner selection
inside the blob walker. Requires architect approval before code lands.

Parent plan: `~/.claude/plans/sequential-soaring-hopper.md`.
Sibling specs: [blob_walker_velocity_gate_spec_request.md](blob_walker_velocity_gate_spec_request.md) (superseded),
[extraction_rescope_followups.md](extraction_rescope_followups.md).
Closure evidence: `m0_closure_summary.md`.
Audit input: `dump_step1/24corpus/LOW_ACCEPT_ROOT_CAUSE.md`.
Touchpoint scout audit: [window_level_touchpoint_map.md](window_level_touchpoint_map.md).

## API Decision (2026-05-28)

`track_runner/residual_motion.observe_blob_at` signature stays
**UNCHANGED**. It continues to return a single `BlobObservation | None`.
The walker reads the existing `BlobObserverTrace.corridor_blobs` field
as the per-frame candidate list (the geometric-ROI filter has already
been applied during extraction).

Rationale: lower blast radius. The touchpoint scout audit at
[window_level_touchpoint_map.md](window_level_touchpoint_map.md)
identified 4 call sites to `observe_blob_at` (FWD/BWD blob snap in
`track_runner/velocity_model.py`, plus bootstrap and per-step in
`tools/blob_walk_v2/walk_walker.py`) and classified all as
Signature-only impact if the return type stays a single
`BlobObservation`. `corridor_blobs` already exposes the candidate list
the walker needs; reading it instead of mutating the API keeps the
windowing scope contained to `tools/blob_walk_v2/`. The
`velocity_model.py` callers are untouched under this route.

This supersedes any text below that says `observe_blob_at` returns
`list[BlobObservation]`.

## Context

The 2026-05-28 extraction re-scope stripped direction-aware corridor,
along/cross projection, and torso re-anchor from
[residual_motion.py](../../track_runner/residual_motion.py).
`observe_blob_at` still returns one `BlobObservation` whose center is the
single `integrated_mag`-max blob inside the geometric ROI. M0's frame-1
acceptance bar failed on 4 of 6 audit videos; the 24-corpus
`accepted_fraction` settled at 19.7% FWD and 9.6% BWD.

The root cause surfaces in the audit:

- On individual frames, leg/foot blobs and torso blobs compete on
  `integrated_mag`. A single-frame winner is not stable.
- Per-frame oscillation (torso, leg, torso, leg) destroys lock even
  when the torso blob is present every frame.
- The original plan asserts the walker "reasons over local trajectories,
  not isolated frames," but the implementation never moved beyond
  per-frame `max(candidates, key=integrated_mag)`.
- Plan WP-1C status enum (`interpolated`, `extrapolated`) was never
  emitted because the walker has no notion of a selected path.

This amendment redesigns the walker around N-frame windowed candidate
buffers and trajectory path-selection so the path itself, not the per-
frame max, picks the runner blob.

## Objectives

1. Make extraction return every geometric-ROI-passing blob; defer
   winner choice to the walker.
2. Make the walker pick blobs by N-frame trajectory consistency.
3. Emit the full WP-1C status enum from the selected path.
4. Bump `walk_debug_log.SCHEMA_VERSION` per contract C10 and document
   the column meaning change.
5. Pin a measurable 24-corpus acceptance bar and a rollback condition.

## Design philosophy

- Per-frame selection cannot disambiguate torso from limb. Trajectory
  smoothness can. Therefore the unit of decision is the window, not the
  frame.
- Extraction stays appearance-blind and direction-blind (preserves the
  2026-05-28 re-scope and contract C6 / C8).
- The walker owns runner-prior reasoning. Extraction owns image
  reasoning. The seam is a candidate list, not a winner.
- No cross-frame blob state survives past the active window. This
  preserves the "no `last_blob` / no chain memory" rule in
  `docs/TRACK_RUNNER_DESIGN.md` "Anti-pattern: chained blob state".
- One algorithm, one default window, one schema bump. No fallback
  flags; the per-frame path is replaced, not toggled, per plan note
  "do not add backwards-compatibility shims".

## Scope

In scope:

- `tools/blob_walk_v2/walk_walker.py`: sole owner of windowed selection.
  Walker reads `BlobObserverTrace.corridor_blobs` as the per-frame
  candidate list (no API change to extraction).
- `tools/blob_walk_v2/`: walker driver, windowed buffer, path-selection,
  status-enum emission, debug log writer.
- `track_runner/walk_debug_log.py` (or current path-equivalent):
  SCHEMA_VERSION bump and column rewrite.
- `tests/test_blob_walk_v2_*.py`: new fixtures and acceptance tests.

Out of scope (non-goals):

- Any change to `track_runner/residual_motion.py`. `observe_blob_at`
  is UNTOUCHED (see API Decision above).
- Any change to `interval_solver.py`, `velocity_model.py`, or
  `solver_workers.py`. FWD/BWD blob-snap callers continue to consume
  a single `BlobObservation` exactly as today.
- Any change to extraction's image processing (DoG, masks, ROI sizing).
- Any re-introduction of direction-aware filtering, along/cross
  projection, torso re-anchor, appearance cues, jersey color, or
  template matching (C6, C8 hard rules).
- Stage-3/4 promotion logic, residual pre-pass, cache schema.
- Crop, encoder, UI changes.

## Non-goals

- Beat the analytical Hermite propagator on intervals where Hermite
  alone is already correct. The walker exists to add evidence on
  promoted intervals, not to replace propagation.
- Hit 100% accepted_fraction. The honest output is "selected path or
  silence," not "force a blob every frame."
- Make refine cheaper. Windowed selection is a compute increase per
  promoted interval; this amendment accepts that cost.

## Required changes

### 1. Walker reads `trace.corridor_blobs` (no API change)

SUPERSEDED route -- see "API Decision (2026-05-28)" at the top of this
document. `observe_blob_at` keeps its current
`BlobObservation | None` return. The walker obtains the per-frame
candidate list from the existing `BlobObserverTrace.corridor_blobs`
field on the trace returned alongside the observation.

- `corridor_blobs` already holds every blob that passed the geometric
  ROI filter; ordering by `integrated_mag` descending (ties broken by
  `label_id`) is preserved by the existing extractor.
- The walker iterates `trace.corridor_blobs` to populate its rolling
  N-frame candidate buffer. `obs.center_pixel` is no longer the
  walker's selection input; it is ignored inside the walker (callers
  in `velocity_model.py` continue to use it unchanged).
- Per-blob `integrated_mag` is the tie-break input for the DP cost;
  the walker does not multiply it into a per-frame gating threshold.

### 2. Walker rolling N-frame candidate buffer

Default window length N = 9 frames. Pinned constant
`WALKER_WINDOW_FRAMES = 9` in the walker module. Rationale:

- 9 frames at 60 fps is 150 ms, shorter than the half-stride period of
  a sprinting runner. Trajectory smoothness assumptions hold.
- Odd length keeps a center frame so the emitted decision per advance
  is at offset `(N-1)//2 = 4` from the buffer head.
- N=9 matches the 2026-05-25 spec-request frame stride pattern
  (0, 1, 2, 4, 8) so the audit cases are inside one window.

The buffer holds candidate lists, not winners. Buffer fills as the
walker advances; entries leave once their decision has been emitted.

### 3. Trajectory path-selection algorithm

Choice: **dynamic programming (Viterbi-style)** over the candidate
lattice.

Justification:

- The lattice is small (median ~5 candidates per frame, N=9 frames,
  worst-case ~3e6 transitions which DP handles in under 10 ms).
- DP gives a globally optimal path under additive costs. Beam search
  would prune branches that re-merge at the torso blob a few frames
  later; that is exactly the leg-vs-torso oscillation case.
- DP state is a single `(frame_offset, candidate_index)` pair. Easy
  to test, easy to reason about, easy to log.
- A "no-candidate" virtual node per frame absorbs missing frames
  without crashing the recursion.

Per-step cost (frame `t` candidate `i` to frame `t+1` candidate `j`):

- `step_displacement_cost`: penalize displacement larger than
  `MAX_W_PER_S * dt`, expressed in torso-width units per
  contract C2. Below the cap: cost scales linearly with displacement
  in torso-width units. Above the cap: cost is `+inf` (edge pruned).
- `velocity_consistency_cost`: variance of step displacements across
  the window; computed only when the path has at least 3 nodes.
- `angle_consistency_cost`: variance of step-vector angles across the
  window; same gating.
- `evidence_bonus`: subtract a small term scaled by `confidence` of
  the chosen candidate so ties break toward higher residual-motion
  strength.

Costs are summed; DP minimizes total. All weights live in
`overlay_styles.yaml` or a sibling YAML so they are tunable without
code edits. No per-frame appearance or color terms (C6).

### 4. Status enum emission

The selected path defines per-frame status:

- `accepted`: frame `t` had a real candidate on the selected path.
- `interpolated`: frame `t` is inside the selected path's frame span
  but the path passed through the virtual "no-candidate" node.
  Position is the linear interpolation between the bracketing accepted
  frames.
- `extrapolated`: frame `t` is past the last accepted frame in the
  current window; position is the linear extension of the last two
  accepted frames, valid for at most `EXTRAP_MAX = 2` frames before
  demoting to `soft_miss_no_path`.
- `soft_miss_no_blob`: extraction returned an empty list.
- `soft_miss_no_path`: extraction returned a non-empty list but no
  edge survived the displacement cap at this frame.

These five statuses are the complete enum. `rejected_max_jump` and
`rejected_direction_reversal` (hard stops) are removed from the
walker; the spec-request "walker never gives up" principle is
preserved because soft misses do not stop the walk.

### 5. Bootstrap mode

First `N-1` frames have no full window. Chosen behavior:

- **Wait for window to fill before any emission.** No partial-window
  selection, no per-frame fallback.
- During fill, the walker advances internally but emits nothing to
  the debug log or the trace consumer.
- Once the window is full, the walker emits the first decision and
  then continues to emit one decision per advance.

Rationale: partial-window selection silently re-introduces per-frame
bias on the most important frames (those near the seed). Frame-by-
frame fallback is exactly the design this amendment replaces. The
small latency cost is acceptable; the seed itself anchors the first
`N-1` frames without walker output.

End-of-interval bookend: the walker flushes the final window by
forcing emission of the remaining `(N-1)//2` decisions when the
neighbor seed is reached.

### 6. Per-walk quality metrics

Recompute under the windowed model:

- `accepted_fraction = count(accepted) / (count(accepted) +
  count(interpolated) + count(extrapolated) + count(soft_miss_*))`.
  Interpolated and extrapolated frames do NOT count toward
  `accepted`. They count toward the denominator. This keeps the
  metric honest about extraction coverage; interpolation is not
  evidence.
- `longest_no_accept_streak`: longest run of non-`accepted` statuses,
  inclusive of `interpolated` and `extrapolated`.
- `fwd_bwd_agreement`: unchanged definition (geometric distance
  between the two pass paths in torso-width units), but computed
  only on frames where both passes emit `accepted` or
  `interpolated`. Frames where either pass is `soft_miss_*` are
  excluded.
- New: `interpolated_fraction` and `extrapolated_fraction` reported
  alongside `accepted_fraction` so the consumer sees coverage shape.

### 7. Schema impact

`track_runner/walk_debug_log.py` `SCHEMA_VERSION` bumps from current
value to next integer (C10 unified version). Column changes:

- `status`: existing column, but value set changes (see status enum
  above). Old values `rejected_max_jump`, `rejected_direction_reversal`
  removed; new values `interpolated`, `extrapolated`, `soft_miss_no_path`
  added.
- `chosen_blob_index`: meaning changes from "per-frame
  `integrated_mag` argmax" to "candidate index on the DP-selected
  path." New column comment required.
- `candidates_in_window` (new): integer, number of non-empty
  candidate lists in the window that produced this decision.
- `path_total_cost` (new): float, sum of DP costs across the window.
- `path_step_cost` (new): float, DP cost contribution of this frame's
  edge.
- `window_head_frame` (new): integer source frame index of the
  window head when this decision was emitted.
- `corridor_blobs_count`: column removed; superseded by
  `candidates_in_window` and the per-frame ROI candidate count
  already logged in the trace.

History entry added to `docs/TR_SCHEMA_VERSION_HISTORY.md` describing
the bump.

### 8. Test plan

New pytest fixtures under `tests/test_blob_walk_v2_windowed.py`:

- `test_window_picks_torso_over_leg_oscillation`: synthetic 9-frame
  interval where the torso blob is present every frame but the leg
  blob has higher `integrated_mag` on frames 2, 4, 6. Per-frame
  selection picks leg three times; windowed DP picks torso every
  time. This is the named leg-vs-torso fixture.
- `test_window_emits_interpolated_for_single_frame_gap`: candidate
  list empty on the center frame; bracketing frames have one
  candidate each. Windowed DP path crosses the gap; status is
  `interpolated`.
- `test_window_emits_extrapolated_past_last_accept`: last two frames
  of an interval have empty candidate lists; first three are
  accepted. Statuses are `accepted x3`, `extrapolated x2`.
- `test_displacement_cap_in_torso_units`: candidate two torso-widths
  away from the predicted center is pruned regardless of
  `integrated_mag`.
- `test_walker_reads_corridor_blobs_length`: assert that the walker's
  per-frame candidate buffer length equals
  `len(trace.corridor_blobs)` for that frame, and that each candidate
  is consumed (none silently dropped). Replaces the previous
  `observe_blob_at` signature test -- the API is unchanged, so the
  test target is the walker's trace-access path, not the extractor.
- `test_no_cross_frame_blob_state`: assert that `corridor_blobs`
  entries are not stored on the walker outside the active window.
- `test_bootstrap_emits_after_window_fill`: first emission index is
  `N-1`, not 0.
- `test_status_enum_is_complete`: every emitted row has status in
  the five-value enum; no `rejected_*` values appear.

Regression: existing `tests/test_blob_walk_v2_winner_modes.py`,
`tests/test_blob_walk_v2_no_hermite.py`,
`tests/test_blob_walk_v2_motion_gate.py` rewritten or deleted; the
"single winner" semantics they assert no longer exist.

### 9. Migration path

The single-winner path is **removed**, not flagged. Rationale:

- The parent plan explicitly forbids backwards-compatibility shims.
- A flag-gated fallback would let regressions hide behind "use the
  old path."
- The schema bump is a hard boundary; tools that read the old debug
  log must be updated, not bridged.

Concretely:

- `observe_blob_at` is unchanged (see API Decision). Walker stops
  consulting `obs.center_pixel` for selection and instead consumes
  `trace.corridor_blobs`.
- Walker variants that branched on "winner mode" collapse into one
  variant.
- Tools under `tools/` that already consume `trace.corridor_blobs`
  are unaffected; tools that previously read `obs.center_pixel` as
  the per-frame winner are updated to walk the candidate list.

### 10. Acceptance criteria

24-corpus rerun pinned bars (X = 15 percentage points):

- Aggregate `accepted_fraction` FWD must rise from 19.7% baseline to
  >= 34.7%.
- Aggregate `accepted_fraction` BWD must rise from 9.6% baseline to
  >= 24.6%.
- `longest_no_accept_streak` median must drop by >= 30%.
- The 4 audit videos that failed M0 frame-1 acceptance must pass
  frame-1 acceptance under windowed selection.
- No interval's FWD/BWD agreement (in torso-width units) may regress
  by more than 0.5 torso-widths against the M0 baseline.

Failure-rollback condition: if any one of the four bars above is
missed on the 24-corpus rerun, the amendment is rejected and the
implementation branch is dropped without merge. Partial credit is
not accepted; the per-frame baseline is not worse than partial
windowed gains, so a half-landed redesign would only add complexity
without payoff.

## Risks

- **DP cost weight tuning sensitivity.** Mitigation: weights live in
  YAML; the test fixtures exercise the leg-vs-torso case so weight
  drift is caught in CI.
- **Latency from bootstrap wait.** Mitigation: small (N-1 = 8 frames
  at 60 fps ~ 130 ms); the seed already anchors that region.
- **Window length wrong for very long-stride sequences.** Mitigation:
  N is pinned but tunable via the same YAML; out-of-scope sequences
  are flagged as a follow-up, not absorbed into the amendment.
- **Schema bump breaks downstream tooling.** Mitigation: enumerate
  every `walk_debug_log` consumer under `tools/` in the
  implementation PR and update each in lockstep.

## Open questions

None blocking. Weight values for DP cost terms are tuned during
implementation against the leg-vs-torso fixture; they are not pinned
in the amendment because pinning them now would constrain tuning
without evidence.

## Cross-references

- Contract clauses honored: C1 (seeds are truth), C2 (torso-units),
  C5 (boundary imprecision), C6 (intervals independent), C8
  (no appearance cues), C10 (unified SCHEMA_VERSION), C11
  (all-frame torso boxes), C12 (minimal per-frame data).
- Philosophy: `docs/TRACK_RUNNER_DESIGN.md` "Anti-pattern: chained
  blob state" -- no `last_blob`, no `*_chain_*` state survives past
  the active window.
- Spec request (superseded): [blob_walker_velocity_gate_spec_request.md](blob_walker_velocity_gate_spec_request.md)
  "walker never gives up" principle is honored by demoting all hard
  stops to soft misses.
- Extraction re-scope follow-ups:
  [extraction_rescope_followups.md](extraction_rescope_followups.md).
