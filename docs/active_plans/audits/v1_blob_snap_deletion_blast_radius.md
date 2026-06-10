# V1 blob-snap deletion blast radius

Read-only audit mapping the full blast radius of deleting blob-snap v1 for
WP-6. The deletion itself is held for external review; this document only
maps what must be deleted, what must be preserved, which tests and docs must
change, and the exact grep commands WP-6 should run to confirm zero remaining
references.

All line numbers are as of the audit (branch `main`, 2026-06-08). WP-6 must
re-grep before editing.

## Scope and the single load-bearing finding

"Blob-snap v1" is the per-frame three-gate snap layer in
`track_runner/velocity_model.py`: `_apply_blob_snap`, its helper
`_motion_path_ok`, and the `BLOB_SNAP_*` constants. It is the OLD blob
consumer that the windowed walker (`track_runner/blob_walk/`) replaces.

Load-bearing finding for WP-6: the argmax-winner return of
`residual_motion.observe_blob_at` is NOT cleanly removable. The walker still
reads it (the `obs is None` sentinel and `obs.confidence` for one diagnostic
CSV column), and the trace fields `winner_blob` / `winner_score` are the same
`best_blob` / `best_score` the walker reads for debug columns. See the verdict
section below.

## Delete list (safe to remove once v1 is gone)

Symbols whose only producer or consumer is `_apply_blob_snap`. After the
deletions below, run the verification recipe; pyflakes should be clean.

### In track_runner/velocity_model.py

- `_apply_blob_snap` (def at line 607). The whole function.
- `_motion_path_ok` (def at line 551). Helper called only from
  `_apply_blob_snap` (line 865); no other caller anywhere in the repo.
- `BLOB_SNAP_ALPHA` (line 529) -- read only at lines 752, 839, 918 (all
  inside `_apply_blob_snap`).
- `BLOB_SNAP_PATH_SLACK` (line 531) -- read only at line 867.
- `BLOB_SNAP_PATH_PERP_FRACTION` (line 534) -- read only at line 867.
- `BLOB_SNAP_VELOCITY_FLOOR` (line 538) -- read at lines 845, 868 (gate) and
  passed into `_motion_path_ok`; both call sites die with the function.
- `BLOB_SNAP_ALPHA_MAX` (line 541) -- read only at line 894.
- `BLOB_SNAP_MAX_SHIFT_FRACTION` (line 544) -- read only at line 887.
- The stale fingerprint comment block at lines 526-528 ("baked into the
  solver fingerprint ... any numeric change invalidates the refine cache").
  This is FALSE today (see the fingerprint note below) and should go with the
  constants.
- Module imports that go dead after the function is gone: `import types`
  (line 20), `import blob_trace` (line 26), `import residual_motion`
  (line 27), and the `coord_space` references at lines 792-793, 829. Each of
  these is used ONLY inside `_apply_blob_snap` in this file. Confirm with
  pyflakes after deletion and remove the now-unused imports.

### In track_runner/blob_trace.py

- `BlobGateTrace` dataclass (line 37). Produced only by `_apply_blob_snap`
  (lines 740, 906) and consumed nowhere else in repo or tests. Fully dead
  once `_apply_blob_snap` is removed. (Distinct from `BlobObserverTrace`,
  which the walker uses -- preserve that one.)

### Diagnostic cascade (see "needs human decision")

`_apply_blob_snap` is the only producer of the per-frame `blob_gate` stamp
and the `propagated_with_blob_snap` source string. The coverage diagnostic
chain that reads `blob_gate` becomes dead too, but it spans modules beyond
the prompt's named symbols, so it is listed under "needs human decision"
rather than as an automatic delete.

## Preserve list (deletion must NOT touch)

- `velocity_model._compute_raw_pred_forward` and `_compute_raw_pred_backward`
  -- the frozen Hermite `raw_pred` builders. Still called by the surviving
  Hermite path and by `interval_solver.py` (lines 544-545) for the walker
  pre-pass, and referenced in `residual_pre_pass.py` and
  `track_runner/blob_walk/walk_walker.py`. KEEP.
- `velocity_model.propagate_forward_analytical` /
  `propagate_backward_analytical` -- KEEP as functions. They are the public
  propagator entry points called by `interval_solver.py` (lines 561, 568),
  `scoring.py`, `solver_workers.py`, and four test files. WP-6 removes their
  v1 internals (the `_apply_blob_snap` call), not the functions. The
  `blob_snap_enabled` parameter is part of this seam -- see "needs human
  decision".
- ALL of `residual_motion.observe_blob_at` -- the measurement pipeline (ROI,
  DoG residual, blob extraction, corridor filter, `corridor_blobs` trace) is
  the SHARED engine the walker depends on. Preserve the function, its
  signature (the frozen API per the 2026-05-28 API Decision), the
  `precomputed_store` parameter, and the `trace_sink` path that populates
  `BlobObserverTrace.corridor_blobs`, `raw_blobs`, `winner_blob`,
  `winner_score`.
- `residual_motion.BlobObservation` (class, line 913) and the argmax-winner
  return path (`best_blob = max(..., key=integrated_mag)` line 1363,
  `best_score` line 1366, the `BlobObservation(...)` construction lines
  1387-1392). PRESERVE -- still has a live consumer in the walker (verdict
  below).
- `blob_trace.BlobObserverTrace` -- the walker's trace type. Only
  `BlobGateTrace` is dead; `BlobObserverTrace` survives.
- `interval_solver.select_promoted_intervals` and `PROMOTION_TIERS` --
  Stage-4 promotion machinery, independent of v1 snap internals. KEEP.

## Verdict: argmax-winner return is NOT removable (live consumer)

The plan asked whether `observe_blob_at`'s argmax-winner `BlobObservation`
return can be removed/demoted now that the walker reads `corridor_blobs`
instead of the winner. Answer: NO, it has a live consumer.

Two production callers of `observe_blob_at` read the return value:

1. `velocity_model._apply_blob_snap` (v1) -- reads `observation.center_pixel`
   (line 832) and `observation.confidence` (line 894). This consumer is being
   DELETED at WP-6, so it does not by itself keep the winner alive.
2. `track_runner/blob_walk/walk_walker.py` (the v2 walker, SURVIVES) -- still
   reads the return value:
   - `gather_frame_candidates` uses `obs` as a soft-miss sentinel: `if obs is
     None: return []` (line 727). Candidates then come from
     `trace.corridor_blobs`, NOT from the winner. So the walker needs the
     return to be non-`None` when a blob exists.
   - `_build_window_entry` reads `obs.confidence` (line 771) into the
     `obs_confidence` debug field, which flows to the `walk_debug_log` CSV
     column `obs_confidence` (walk_debug_log.py lines 88, 189; walk_walker.py
     line 461). Diagnostic only -- it does not feed the Viterbi path
     selection -- but it is a real read of the `BlobObservation` return.
   - The walker also reads `trace.winner_blob` / `trace.winner_score`
     (walk_walker.py lines 185-186, 788-791), which are populated from the
     same `best_blob` / `best_score` computed at lines 1363-1366. So even the
     trace-only path keeps `best_blob` alive.

Conclusion for WP-6: keep `best_blob = max(...)`, `best_score`, the
`BlobObservation` construction, and the return. They are diagnostic for the
walker, not tracking-decision inputs, but they are live. A future cleanup
could demote `obs.confidence` to a trace-only field and let the walker stop
reading the `BlobObservation` entirely, at which point the return could be
narrowed -- but that is a separate change, not part of v1 deletion, and is
listed under "needs human decision".

## Needs human decision

These are real consequences of the deletion that exceed the prompt's named
symbols. WP-6 should get an explicit call on each rather than the audit
guessing.

1. `blob_snap_enabled` parameter survival. It threads through
   `interval_solver.solve_interval_analytical` (line 472), `solver_workers.py`
   (lines 145-160), `solve_queue.py` (line 784), and the two propagator
   functions, and four test files pass it. With v1 gone, the parameter no
   longer selects "snap vs no-snap". WP-6 must decide: remove it entirely (and
   collapse the Stage-3 Hermite / Stage-4 blob distinction onto the walker),
   or repurpose `True` to mean "run the walker". This is the Stage-4 walker
   seam, owned by WP-5b, not a pure deletion.
2. The `blob_gate` coverage diagnostic cascade. `_apply_blob_snap` is the only
   producer of `blob_gate`. When it is gone, `interval_solver._coverage_from_track`
   (line 90), `_stamp_blob_coverage` (line 125, called line 610), and the
   resulting `blob_coverage_fwd` / `blob_coverage_bwd` score fields (read by
   `solve_queue.py` lines 355-356 and described in `review.py` lines 172-173)
   all lose their input. Decide whether to delete this chain or re-source it
   from walker output.
3. The `propagated_with_blob_snap` source string (velocity_model.py line 933).
   No test asserts on it, but any downstream tool that branches on
   `state["source"]` would change behavior. WP-6 should grep tools/ before
   removing it.
4. Demoting `obs.confidence` to trace-only (see verdict). Optional follow-on,
   not required for v1 deletion.

## Tests to update list

No test asserts on a specific `BLOB_SNAP_*` numeric value or on the
`propagated_with_blob_snap` source string (verified by grep), so the
constants are not value-pinned. The tests below reference v1 symbols or the
`blob_snap_enabled` parameter and will need updating or deletion at WP-6,
depending on the decision-1 outcome above.

- `tests/test_tr_velocity_model.py`:
  - `test_propagator_skips_blob_observer_when_disabled` (line 487) -- pins
    that `blob_snap_enabled=False` never calls `observe_blob_at`. Tied
    directly to v1 toggle semantics. Update or delete.
  - `test_propagator_hermite_only_matches_no_reader_path` (line 505) -- pins
    that `blob_snap_enabled=True` with `reader=None` equals the Hermite-only
    path. Tied to v1 toggle semantics. Update or delete.
  - The Hermite tests at lines 157-161, 208-212, 271-272, 294-295 pass
    `blob_snap_enabled=False` only to reach the pure-Hermite path. They
    survive in substance but the keyword argument changes if decision 1
    removes the parameter.
- `tests/test_tr_stage4_parity.py`:
  - `test_stage_4_parity_hermite_then_blob_vs_direct_blob` (line 44) -- drives
    `solve_interval_analytical(..., blob_snap_enabled=True, reader=None)`.
    Update for the new seam. The other three tests in this file
    (`select_promoted_intervals*`, `promotion_tiers*`) are Stage-4 promotion
    tests and survive unchanged.
- `tests/test_tr_solve_mode.py` -- lines 66, 90, 133, 141 pass
  `blob_snap_enabled`. Survive in substance; keyword changes with decision 1.
- `tests/test_tr_solver_integration.py` -- line 108 passes
  `blob_snap_enabled=False`. Same: survives in substance, keyword tracks
  decision 1.

The `observe_blob_at` / `BlobObservation` contract tests
(`test_blob_observation_contract.py`, `test_observe_blob_at_processed_contract.py`,
`test_tr_residual_motion_bin.py`, `test_tr_residual_motion_window.py`, and
the walker tests) exercise the PRESERVED measurement API and must keep
passing unchanged. Do not touch them for v1 deletion.

## Docs to update list

- `docs/TRACK_RUNNER_DESIGN.md` -- lines 119-122 describe v1: "consults
  `observe_blob_at` at each non-endpoint frame and blends the blob into the
  predicted center when three local gates all pass: proximity, direction, and
  temporal smoothness." Lines 141-145 reference `snap_pred`. Update to the
  walker model. The windowed-walker section (lines 147+) already describes
  the replacement and stays.
- `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md` -- the strongest v1 description:
  the `_apply_blob_snap` ASCII pipeline (lines 159, 162, 170, 173), the
  three-gate list (lines 199-228), the `snap_pred` terminology throughout,
  and the direct `velocity_model.py _apply_blob_snap` link (line 241).
  Rewrite the blob-application half for the walker; the FWD/BWD independence
  and `raw_pred` halves survive.
- `docs/TR_MOTION_CUE_HEAT_MAP.md` -- the measurement-pipeline half (ROI,
  DoG, blob extraction, `integrated_mag` sort, `observe_blob_at` call flow,
  lines 96-294) describes the PRESERVED engine and largely stays. The
  consumer half that names the "three local gates ... `snap_pred[t]`
  blending" (lines 328, 335-339) describes v1 and must be updated to the
  walker.
- Secondary doc mentions to sweep at WP-6 (lower priority, mostly historical
  or structural): `docs/CODE_ARCHITECTURE.md`, `docs/FILE_STRUCTURE.md`,
  `docs/TRACK_RUNNER_V3_SPEC.md`, `docs/archive/V1_SOLVE_STAGE_FACTORABILITY_NOTE.md`,
  `docs/TR_CONFIG_FILES.md`, `docs/ROADMAP.md`, `docs/TR_SCHEMA_VERSION_HISTORY.md`,
  `docs/archive/V1_BLOB_REDESIGN_REPORT.md`, `docs/BLOB_SEED_DISTANCE_FINDINGS.md`.
  Archived docs under `docs/archive/` are historical and should NOT be edited.
- `docs/CHANGELOG.md` -- add a WP-6 entry per repo policy when the deletion
  lands.

## Fingerprint note (correction)

The comment at `velocity_model.py` lines 526-528 claims the `BLOB_SNAP_*`
constants are "baked into the solver fingerprint ... any numeric change
invalidates the refine cache." This is FALSE in the current code.
`interval_fingerprint.build_geometry_tag` (lines 47-68) explicitly states:
"Tuning blob-snap constants or changing which propagator ran does not change
the tag -- only a real geometry-affecting schema bump does." The fingerprint
hashes seed geometry plus the geometry-affecting schema set, not these
constant values. Therefore deleting the constants has NO cache-invalidation
effect by itself. Any cache invalidation WP-6 wants is a separate
`SCHEMA_VERSION` / `GEOMETRY_AFFECTING_SCHEMAS` decision under contract C10.

## Verification recipe (run before deleting)

WP-6 should run these exact greps and confirm the expected post-deletion
state. Run from repo root.

1. Confirm `_apply_blob_snap` has no caller outside the two propagators in
   velocity_model.py (expect only the def + the two internal calls):

   `grep -rn "_apply_blob_snap" --include=*.py .`

2. Confirm `_motion_path_ok` has no caller outside `_apply_blob_snap` (expect
   only the def + line 865):

   `grep -rn "_motion_path_ok" --include=*.py .`

3. Confirm every `BLOB_SNAP_*` reference is inside velocity_model.py (expect
   zero hits in any other file):

   `grep -rn "BLOB_SNAP" --include=*.py . | grep -v "velocity_model.py:"`

4. Confirm `BlobGateTrace` has no producer or consumer outside
   `_apply_blob_snap` (expect only blob_trace.py def + velocity_model.py
   docstring/creation lines):

   `grep -rn "BlobGateTrace" --include=*.py .`

5. Confirm the walker still reads only `obs is None` and `obs.confidence` from
   the `BlobObservation` return (no new winner dependency snuck in):

   `grep -rn "obs\.\|\.center_pixel\|\.confidence" --include=*.py track_runner/blob_walk/walk_walker.py`

6. After the edits, run pyflakes on the touched files to catch now-unused
   imports (`types`, `blob_trace`, `residual_motion`, `coord_space`):

   `pyflakes track_runner/velocity_model.py track_runner/blob_trace.py`

7. After the edits, confirm zero remaining references to the deleted symbols
   across the repo (expect empty output):

   `grep -rn "_apply_blob_snap\|_motion_path_ok\|BLOB_SNAP_\|BlobGateTrace\|propagated_with_blob_snap" --include=*.py track_runner/ tools/ devel/`

8. Run the affected focused tests (after updating them per decision 1):

   `pytest tests/test_tr_velocity_model.py tests/test_tr_stage4_parity.py tests/test_tr_solve_mode.py tests/test_tr_solver_integration.py`

## Summary counts

- Symbols safe to delete: 11 (`_apply_blob_snap`, `_motion_path_ok`, six
  `BLOB_SNAP_*` constants, `BlobGateTrace`, plus the stale fingerprint comment
  block and up to four now-dead imports in velocity_model.py).
- Live consumers of the argmax-winner return: 1 (the v2 walker, via the
  `obs is None` sentinel + `obs.confidence` debug field + the
  `winner_blob`/`winner_score` trace fields). Verdict: NOT removable now.
- Tests to update: 4 files (2 v1-pinning tests to rewrite/delete, the rest
  keyword-tracking).
- Docs to update: 3 primary design docs + a CHANGELOG entry + ~9 secondary
  sweeps.
- Needs human decision: 4 items (blob_snap_enabled parameter fate, blob_gate
  coverage cascade, source-string removal, optional obs.confidence demotion).
