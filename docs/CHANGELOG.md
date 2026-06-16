## 2026-06-16

### Developer Tests and Notes

- **`re-solve.sh` now captures per-video output to log files**: both the solve
  loop and the upgrade loop pipe `2>&1 | tee` into a per-video log in CWD
  (`solve_<stem>.log` and `upgrade_<stem>.log`). `2>&1` folds stderr (Python
  tracebacks) into the capture so failures are reviewable after the batch run,
  while `tee` keeps console output live.

## 2026-06-15

### Behavior or Interface Changes

- **Interval reuse fingerprint made bin-invariant (Patch 1, WS1a)**:
  `interval_fingerprint.build_geometry_tag` no longer takes `bin_factor` and emits
  `schema_v<N>` (the trailing `/bin<B>` segment is gone); `compute_interval_fingerprint`
  drops its `bin_factor` argument. The reuse key now carries only the allow-list inputs:
  seed-pair frame indices, human-authored SOURCE seed box coords, and the
  geometry-affecting schema tag. Stored torso boxes are always unbinned SOURCE-frame, so
  bin is a per-run performance setting and cannot be part of a durable result's identity.
  Consequence: a solve at one bin and a `refine` at another now reuse all unchanged
  intervals instead of pruning the store to empty and tripping the C7 guard. No
  `SCHEMA_VERSION` bump: stored SOURCE coordinate arrays are byte-identical; this is
  cache-key bookkeeping only. The `bin_factor=` keyword was removed from every caller of
  the fingerprint helpers (`solve_queue.plan_interval_work`, `_solve_pre_race_interval`,
  `interval_solver.solve_all_intervals` and its Stage-4 re-key, `solver_workers`) so no
  caller breaks; `bin_factor` stays a live solve-run parameter for processing resolution.

- **Bare `--auto-bin` now resolves the same bin as the no-flag default (Patch 5, WS2b)**:
  bare `--auto-bin` (no value) previously used a stale height-target-480 constant
  (`cli_args.py:41`, yielding `round(source_height / 480)`) instead of the intended
  width-floor selector. The stale constant is replaced with a `-1` sentinel that
  `_resolve_solve_bin_factor` in `track_runner/cli.py` routes through
  `select_default_bin_factor`, producing `floor(source_width / 1440)` -- exactly
  the same bin the no-flag default produces. Explicit `--auto-bin HEIGHT` with a
  positive integer retains its height-based meaning unchanged. Affected files:
  `track_runner/cli_args.py`, `track_runner/cli.py`. Unit test:
  `tests/test_solve_default_bin.py`.

- **Standing reuse-identity rule added to governance docs (Patch 6, WS2c)**:
  `docs/TR_SCHEMA_VERSION_HISTORY.md` gains a "Reuse identity rule" subsection with
  the three-input allow-list, the "stays out of the key" examples list, the
  method-only-changes-use-solve rule, and the require-justification clause.
  `docs/TR_CONFIG_FILES.md` gains a "Reuse key rule" note under
  `torso_box_coords.npz` (SOURCE-frame, unbinned; key describes the persisted
  result, not the runtime method). Stale fingerprint example corrected in
  `docs/TR_CONFIG_FILES.md`. Wording in `docs/COORDINATE_SPACES.md` corrected to
  remove the claim that bin enters the interval reuse key.

- **Identity-warning wording aligned with prepare/fastread contract (Patch 7, WS3a)**:
  `fps` moved from the blocking bucket to the informational bucket in
  `track_runner/tr_video_identity.py` `_BLOCKING_RULES` / `_INFORMATIONAL_RULES`.
  A container remux (.MOV -> .mkv) shifts the display-precision fps value by small
  amounts (for example 119.94 vs 119.916, diff=0.024) without affecting the
  frame-index-to-time mapping. With the prior 0.01 absolute tolerance that difference
  appeared under the "blocking:" header in the identity warning, misleading users into
  thinking the fastread path was problematic. The new tolerance is 1.0 (absolute),
  which absorbs remux precision noise while still surfacing large true frame-rate
  changes (e.g. 30 fps vs 60 fps) in the informational bucket. width, height, and
  frame_count remain in the blocking bucket. `_check_identity_mismatch` in cli.py
  already warns-only and never rejects; this change only improves the label accuracy.
  The live structural validation in `fastread_video.validate_fastread_structural`
  continues to use its own tighter `FPS_REL_TOLERANCE = 1e-3` before authorizing
  fastread decode -- that gate is unchanged.

### Fixes and Maintenance

- **`cli._mode_refine` zero-interval guard improved (Patch 4, WS2a)**:
  `cli._mode_refine` now detects `plan.total_intervals == 0` with a non-empty
  solved store -- meaning the current seeds dropped below 2 usable or the seed
  set no longer matches any stored interval -- and prints an unambiguous
  diagnostic naming the likely cause, then returns cleanly (exit 0) leaving the
  solved store byte-identical. Previously this case fell through to a misleading
  "all 0 intervals already solved" message that gave no hint about the seed
  mismatch. The C7 guard path (work needed, zero reuse) is unchanged and still
  raises. Tests in `tests/test_tr_refine_mode.py`.

- **Legacy bin-tagged store keys migrate on load (Patch 1, WS1a)**:
  `interval_fingerprint.migrate_legacy_fingerprints` is repurposed from a no-op into a
  strip migration that rewrites any key ending `||schema_v<N>/bin<B>` to `||schema_v<N>`
  and returns `(migrated, n_changed)`, so the bin-invariance fix does not force a one-time
  full re-solve of existing `/bin<B>` stores. Collision policy: a single solve run uses
  one bin and writes one key per seed pair, and the store is a fingerprint->result dict
  with no per-entry write-order or timestamp metadata (the manifest in
  `state_io.write_torso_box_coords` is an ordered list keyed only by fingerprint), so two
  stripped keys colliding signals a corrupt store (same seed pair stored at two bins) and
  fails loud rather than silently dropping an entry. Write-back: the migration rewrites
  keys in memory; on a no-work `refine` (`pending_count == 0`) nothing is persisted
  (zero-write contract holds, the cheap in-memory migration repeats next run); on a
  work-needed refine the existing pruned-store write persists the migrated keys and prints
  "migrated N keys" only when the store actually changes. The solve load path
  (`cli._load_prior_results`) persists the migration immediately since solve rewrites the
  store anyway. The dead `bin_factor` bin-resolution block in `cli._mode_refine` (which
  only fed the fingerprint) was removed; `_run_solve` still resolves the run's bin for the
  actual solve.

- **Evidence audit: prepare frame-identity contract confirmed (Patch 7, WS3a)**:
  `fastread_video.py` validates width/height/frame_count exact match and fps within
  1e-3 relative tolerance before authorizing fastread decode (`validate_fastread_structural`).
  `resolve_video_context` keys state to the original (`metadata_identity` always selects
  the original path); only `working_decode` uses the fastread and only after validation.
  The fastread contract holds; the only change needed was reclassifying fps in the
  identity-warning bucket.

### Developer Tests and Notes

- **Geometry-sensitivity regression test added (Patch 2, WS1b)**:
  `tests/test_tr_fingerprint_geometry_sensitivity.py` pins two complementary
  properties of `state_io.interval_fingerprint`: (a) the same seed pair produces a
  stable key across bin values and across runs, and (b) redrawing any box coordinate
  at the same frame index changes the key. This guards against drift toward
  frame-index-only identity, which would reuse a stale interval after a user
  re-annotates a box.

- **Synthetic legacy store reuse proof added (Patch 3, WS1c)**:
  `tests/test_tr_legacy_store_reuse.py` constructs synthetic `/bin4` and `/bin1`
  stores in memory, runs `migrate_legacy_fingerprints`, and asserts
  `reused_count == total_intervals` and `pending_count == 0`; keys at different
  bins become byte-identical after migration. No external video required.

- **Anti-drift tripwire added (Patch 3, WS1d)**:
  `tests/test_fingerprint_anti_drift.py` covers two concerns. Behavioral
  invariance: identical key across representative bin values and solver modes;
  different key when a box coordinate changes or the frame index changes. Shape
  tripwire: the test inspects `build_geometry_tag`'s signature and fails if any
  parameter beyond the approved schema tag is added, routing the author to the
  official-schema-only allow-list in `docs/TR_SCHEMA_VERSION_HISTORY.md`. Modeled
  after the existing schema-version single-source tripwire
  `tests/test_tr_schema_version_single_source.py`.

- **Bin-invariance test inverted to match the new key (Patch 1, WS1a)**:
  `tests/test_tr_interval_fingerprint_bin.py` previously asserted the M2 stance (bin
  enters the key, a bin change invalidates the fingerprint). It is rewritten to assert the
  restored bin-invariant contract: the same seed pair yields one key, the geometry tag has
  no `/bin` segment, a legacy `||schema_v<N>/bin3` key migrates to the bin-invariant key
  and matches a fresh compute, and re-migrating an already-stripped store reports zero
  changes. `pytest tests/ -k fingerprint` passes (50 selected) and
  `pytest tests/test_pyflakes_code_lint.py` passes (no dead `bin_factor` args).

- **Bucket-pinning test added for fastread/remux scenario (Patch 7)**:
  `tests/test_tr_video_identity.py` gains `test_fastread_remux_bucket_assignments`
  and `test_fps_large_change_is_informational`. The pinning test confirms that
  a remux with changed basename, size_bytes, and small fps display-precision shift
  lands in informational (not blocking) while a true width or frame_count mismatch
  still lands in blocking. The previously-named `test_fps_beyond_tolerance_is_blocking`
  is renamed to `test_fps_large_change_is_informational` to match the new bucket
  assignment; 16 tests pass.

- **Reviewer precision fixes applied (Patch 7, WS3a M3)**:
  (Fix A) Module docstring in `track_runner/tr_video_identity.py` reworded from
  "the video_identity block ... does not gate solve or refine" (ambiguous: could
  imply width/height/frame_count also do not block) to "fps differences in
  video_identity do not gate solve or refine (width, height, and frame_count in
  the blocking bucket still do)." (Fix B) Added
  `test_fps_remux_precision_within_1fps_tolerance_is_clean` which explicitly
  asserts 119.94 vs 119.916 (diff=0.024) produces no fps entry in either bucket;
  updated `test_fps_large_change_is_informational` docstring to clarify that 30
  vs 60 fps is visibly reported in the informational bucket but never blocks; 17
  tests pass.

### Decisions and Failures

- **No `SCHEMA_VERSION` bump for Patches 1-7**: removing `bin_factor` from the
  interval fingerprint key is cache-key bookkeeping only -- the stored SOURCE
  coordinate arrays in `torso_box_coords.npz` are byte-identical regardless of the
  bin used during solve. Aligning the `--auto-bin` selector, the refine guard edge
  case, and the identity-warning wording are all behavioral fixes that leave the
  on-disk artifact format unchanged. No persisted artifact contract changed; no
  C10 approval was sought or required.

### Behavior or Interface Changes

- **Audit follow-up doc corrections**: `docs/modes/SOLVE.md` "Durable upgrade note"
  reworded to accurately scope the bin-keyed cache: only the camera-motion artifact
  (`camera_motion.npz`) keys on `bin_factor`; interval cache entries are bin-invariant
  (load-time migration strips legacy `/bin<B>` suffixes, no full re-solve needed on bin
  change). `docs/USAGE.md` "Spatial binning" note updated with the same distinction.
  Auto-help blocks in `docs/modes/SOLVE.md` and `docs/modes/REFINE.md` regenerated via
  `tools/refresh_mode_docs.py` to replace stale "Bare flag targets 480" text with the
  current `--auto-bin` help string (bare flag routes through width-floor / 1440, same
  as the no-flag default).

### Fixes and Maintenance

- **Audit follow-up code fixes**: `interval_fingerprint.compute_interval_fingerprint`
  now references the module-level `GEOMETRY_TAG` constant instead of re-invoking
  `build_geometry_tag()` on every call (pure function, identical result, avoids
  redundant computation). `tr_video_identity._check_rule` gained the missing
  `#============================================` visual separator that every other
  function in that file carries.

### Developer Tests and Notes

- Audit follow-up: removed duplicate/fragile fingerprint and identity tests flagged
  by the test audit (stable-across-runs duplicate, state_io substring-format
  assertions, a duplicate C7-guard refine test, a duplicate 1080p default-bin case,
  and three structural/required-key video-identity tests); extended the fingerprint
  shape-gate to also pin `compute_interval_fingerprint`'s parameter count. Net fewer
  tests, same contract coverage.

## 2026-06-14

### Additions and New Features

- **`select_default_bin_factor` and `open_analysis_reader` added to
  `common_tools/frame_reader.py` (M2)**: `select_default_bin_factor` computes
  `floor(source_width / TARGET_DEFAULT_WIDTH_PX)` (floor@1440, minimum 1);
  `TARGET_DEFAULT_WIDTH_PX = 1440` is the project-wide code constant. Result:
  4K 3840 -> bin 2 (1920x1080), 2.8K 2880 -> bin 2, 1440p and 1080p -> bin 1.
  `open_analysis_reader` is a shared reader-opener consumed by both production
  solve and the standalone HTML walk tool, eliminating the duplicated open logic.

- **`enumerate_seed_to_seed_intervals` and `SeedToSeedInterval` added to
  `track_runner/race_phases.py` (M3)**: canonical source for seed-to-seed
  interval enumeration, consumed by the standalone HTML walk tool after
  `walk_io.py` was deleted.

- Wired the previously-dead `track_runner/torso_size_stabilizer.py` (median, window 7)
  into the crop size channel (`track_runner/tr_crop.py` `trajectory_to_crop_rects`,
  applied before the size-EMA, both crop modes, crop center untouched per C5) as
  robust SIZE-SPIKE HARDENING. New regression test
  `tests/test_tr_crop_size_stabilizer.py` proves an isolated single-frame torso w/h
  spike is rejected (bounded crop height), a real multi-frame scale ramp is still
  tracked, and crop center cx/cy is byte-unchanged. Constants
  `CROP_SIZE_STABILIZER_METHOD`/`CROP_SIZE_STABILIZER_WINDOW`.

- **Schema-change rule and checklist added to `docs/TR_SCHEMA_VERSION_HISTORY.md`
  (Patch 1)**: a `## When to change SCHEMA_VERSION` section now leads the document
  with an affirmative rule (use `SCHEMA_VERSION` for approved persisted artifact
  contract changes; use `solve` for method-derived stale values; document
  diagnostic telemetry without changing the solver schema), a YES/NO decision
  table from real v10-v14 history, and a numbered `## Checklist before changing
  SCHEMA_VERSION` section that routes each change type to the correct path and
  requires explicit human approval before any `SCHEMA_VERSION` edit. A `## What
  the schema owns` section carries the governing sentence, the two-step
  persistence gate, the schema-owned vs method-owned field split, and the boundary
  classification table so the anti-drift guardrail lives in the doc, not only in
  the plan.

### Behavior or Interface Changes

- **Default solve now bins the walker onto processed capture frames (M2)**:
  no-flag default `bin_factor = select_default_bin_factor(source_width)` (floor@1440).
  `--bin N` stays exact; `--bin 1` is the full-res escape hatch; `--auto-bin N`
  keeps its height-based meaning. The whole solve runs in one coordinate space
  (PROCESSED at bin>1) and converts to SOURCE only at the single storage boundary in
  `track_runner/interval_solver.py`. Shared `open_analysis_reader` used by both
  production solve and the standalone HTML tool.

- **Solve caches were made bin-aware at M2 (later reversed for interval fingerprint)**:
  at M2, `bin_factor` was added to the interval fingerprint
  (`track_runner/interval_fingerprint.py`, `solve_queue.py`) and to the
  camera-motion cache staleness check (`track_runner/camera_motion.py`).
  The camera-motion staleness check still keys on `bin_factor` because stored
  SOURCE dx/dy depend on the analysis bin used. The interval fingerprint bin-awareness
  was reversed by Patch 1 (2026-06-15, WS1a): `bin_factor` is no longer part of the
  interval reuse key; a load-time migration strips `/bin<B>` from existing keys so no
  one-time full re-solve is needed. See 2026-06-15 Behavior or Interface Changes.

- **`SCHEMA_VERSION` rolled back to 10 (Patch 1)**: v11, v12, v13, and v14 are
  rolled back because none of those changes altered the stored solver artifact
  format or any per-video variable. v11 was a residual-stride method change
  (runtime-computed, never persisted); v12 versioned diagnostic CSV telemetry
  columns (not a solver artifact); v13/v14 changed walker DP method and cost
  model (computed values, format unchanged). Current schema is 10 -- the last
  change that actually altered the stored format (float32->uint16 dtype). The
  `GEOMETRY_AFFECTING_SCHEMAS` set is `{3,6,7,8,9,10}`; supported
  `torso_box_coords` is `{10}`; supported `diagnostics` is `{2..10}`. The
  tripwire test in `tests/test_tr_schema_version_single_source.py` pins version
  10 and routes any future change attempt to
  `docs/TR_SCHEMA_VERSION_HISTORY.md#checklist-before-changing-schema_version`.
  Pre-existing v10 artifacts stay readable; run `solve` for current-method values.
  Human approver: user decision 2026-06-14.

### Fixes and Maintenance

- **Coordinate-space defect at bin>1 FIXED (M1)**: production solve stored
  PROCESSED coordinates as SOURCE with no conversion at `bin_factor > 1` (latent
  no-op at bin=1, breaking only when binning was enabled). Root cause: production
  fed SOURCE seeds to the PROCESSED walker with no `SeedsView` and wrote walker
  output without PROCESSED->SOURCE conversion. Fixed at a single boundary in
  `track_runner/interval_solver.py`: `_seed_source_to_processed` (input conversion)
  and `_walker_path_processed_to_source` (the one PROCESSED->SOURCE seam, applied
  before the Hermite fallback/blend/score/write so the whole tail is uniform SOURCE).
  Hermite path unchanged. Corrected the misleading `track_runner/walker_bundle.py`
  docstring that claimed no conversion is ever needed. Reference audit:
  `docs/active_plans/audits/coordinate_space_bin_gt1_audit.md`.

- **Solve stale-artifact guard extended to the interval-scores JSON (Patch 3)**:
  the Patch 1 guard cleared only the stale `torso_box_coords.npz`, so the first
  `solve` against a rolled-back config still crashed in `_load_prior_results` ->
  `state_io.load_diagnostics` with "diagnostics file header mismatch ... got 11".
  `interval_scores.json` is a solve OUTPUT, not a required input (absent -> empty
  structure, never raises), but a stale v11-v14 file raised on load. Added
  `state_io.peek_diagnostics_schema` and `_clear_stale_diagnostics_artifact` in
  `track_runner/cli.py`, called in `_mode_solve` beside the torso guard: a stale
  interval-scores file is now treated as absent and regenerated. Scoped to solve
  only; loaders and refine keep rejecting stale artifacts clearly.

- **Stale schema-14 references corrected across docs (Patch 2)**: the rollback
  made several docs wrong. `docs/TR_CONFIG_FILES.md` no longer claims the writer
  emits v11 with `{10,11}` supported; `docs/NEWS.md`, `docs/RELEASE_HISTORY.md`,
  `docs/ROADMAP.md`, and `docs/TR_FWD_BWD_MODEL_METHODOLOGY.md` drop the "schema
  14" attribution from the walker cost-model entries; `docs/TRACK_RUNNER_DESIGN.md`
  C10 bullet now states the walker carries no schema constant. The
  `docs/TR_SCHEMA_VERSION_HISTORY.md` intro and solved-geometry-cache section no
  longer instruct bumping `SCHEMA_VERSION` for observer/solver method changes
  (route to `solve`), the contract reference is corrected from C9 to C10, and a
  supersession note marks older entries as historical. `docs/TROUBLESHOOTING.md`
  gains a "solve/refine rejects a v11-v14 artifact" entry. Planning-tag `P15`
  comments removed from `track_runner/blob_walk/walk_debug_log.py`.

- **Test cleanup (Patch 2)**: removed the misplaced `tests/test_bug_101_degenerate_roi.py`
  (a real-video, slower-than-budget duplicate of the canonical e2e gate
  `tests/e2e/e2e_bug_101_degenerate_roi.py`) and the two scratch rollback-mechanics
  test files. Dropped the fragile `test_history_doc_mentions_current_schema_version`
  doc-substring check; the version pin in `test_schema_version_pinned_to_expected`
  is the governance gate. Added `-> None` to the kept tripwire test and corrected
  its C9->C10 contract references.

- Removed brittle tests per a PYTEST_STYLE.md audit across 5 files: a tunable-constant
  value assertion (`TARGET_DEFAULT_WIDTH_PX == 1440`), `hasattr`/`not hasattr`
  attribute-name assertions, an `inspect.signature` parameter-name assertion,
  tag-format substring pins, an inlined-production-logic test, and bare `len() ==`
  assertions. Behavioral tests retained.

- Rotated `docs/CHANGELOG.md` per the ~1000-line policy in `docs/REPO_STYLE.md`: the two newest day-blocks (`2026-06-14`, `2026-06-13`) stay active; older blocks (`2026-06-12`, `2026-06-11`) were archived to `docs/CHANGELOG-2026-06c.md` via the `devel/rotate_changelog.py` automation (history preserved).

### Removals and Deprecations

- **Deleted `track_runner/blob_walk/walk_io.py` (M3)**: a parallel shim that
  duplicated core loaders and forked the bin policy. The standalone HTML walk tool
  now routes through core owners: `open_analysis_reader` (shared reader-opener),
  `state_io` seeds, `camera_motion` + `scene_coords` scene transform,
  `state_io.load_diagnostics` race-start (fail-loud, no silent zero), and
  `track_runner/race_phases.enumerate_seed_to_seed_intervals`. Tool-only path glue
  moved to `tools/blob_walk_v2/walk_tool_setup.py`. Retired
  `tests/test_select_bin_factor.py`.

- **Removed unused pow2 analysis-bin selector from `common_tools/frame_reader.py`
  (M3)**: `select_bin_factor_for_analysis`, `_next_pow2_ceil`, and
  `TARGET_ANALYSIS_WIDTH_PX` removed; superseded by the floor-based
  `select_default_bin_factor`.

- **Dead `walk_debug_log.SCHEMA_VERSION` export removed (Patch 1)**: the
  `SCHEMA_VERSION = tr_schema.SCHEMA_VERSION` alias in
  `track_runner/blob_walk/walk_debug_log.py` is removed. Nothing read it as a
  value; consumers use `DebugLogWriter`, `DebugLogRow`, and `HEADER`. Removing
  it eliminates the exact mechanism that let v12 ride the solver schema integer
  for a diagnostic CSV change.

- Removed the proven no-op crop post-smooth constants `CROP_POST_SMOOTH_STRENGTH`
  (0.0, position EMA) and `CROP_POST_SMOOTH_MAX_VELOCITY` (0.0, velocity cap) and
  their guarded-off branches from `track_runner/tr_crop.py`. KEPT the load-bearing
  `CROP_POST_SMOOTH_SIZE_STRENGTH` (0.15) size-EMA. Proven byte-identical crop
  rectangles for BOTH crop modes across 6 real solved trajectories (the removed
  legs were dead code). `tests/test_tr_crop_alpha_constants.py` updated.

### Decisions and Failures

- **v11-v14 bumps were avoidable (Patch 1)**: four unnecessary schema bumps
  landed in 30 days because the rule was buried in cache-invalidation jargon.
  The durable fix is the crystal-clear written rule plus the checklist above,
  not a separate method-code fingerprint (which would add a second version
  surface and invite the same drift). The governance tripwire test makes an
  accidental bump impossible to merge unnoticed.

- **No SCHEMA_VERSION bump for bin-aware camera-motion cache keys (M1-M4 spine)**:
  `bin_factor` enters the camera-motion staleness check as a bookkeeping key, not an
  on-disk artifact schema change. Torso boxes persist in SOURCE coordinates regardless
  of bin. `TARGET_DEFAULT_WIDTH_PX` is a code constant, not a per-video config field.
  C10 and C13 respected; no bump applied. Note: at M2, `bin_factor` was also added to
  the interval fingerprint with the same "bookkeeping, no bump" rationale; that
  addition was reversed by Patch 1 (2026-06-15) when it caused solve/refine bin
  divergence -- the interval reuse key is now bin-invariant.

- **Default bin target confirmed as floor@1440 (M2)**: human approved option B
  (4K analyses at 1080p, conservative for small-runner signal) over the more
  aggressive 720p/target-1280 option.

- **UPGRADE NOTE -- first solve after M2 recomputes camera-motion derived artifacts**:
  because `bin_factor` enters the camera-motion cache staleness check, existing bin=1
  camera-motion artifacts are treated as stale and recomputed on first run after
  upgrade. This is a one-time recompute, not an ongoing cost. Note: a similar one-time
  recompute for interval fingerprints was anticipated at M2 but is no longer needed --
  Patch 1 (2026-06-15) removes `bin_factor` from the interval fingerprint entirely and
  provides a load-time migration that strips `/bin<B>` suffixes from existing keys.

- **DESIGN anti-pattern added: parallel tool glue that duplicates core loaders**:
  `docs/TRACK_RUNNER_DESIGN.md` now documents the pattern as a known bad practice
  following the `walk_io.py` deletion (M3). Tool-layer glue must route through
  established core owners; duplicating loaders forks the bin policy and creates
  silent divergence.

- Crop-mode dedup (`smooth` vs `direct_center`) was investigated and SHELVED, not
  executed: both modes kept and the `smooth` default unchanged, per a risk decision
  (encode output change avoided). Comparison evidence:
  `docs/active_plans/decisions/crop_mode_keeper_decision.md`.

- Crop zoom bounce is PARTIALLY fixed and the residual is a documented KNOWN
  LIMITATION. Root cause found: `torso_size_stabilizer` was dead code (zero
  importers) and the only active size stabilization was a single size-EMA that
  structurally cannot reject single-frame jitter. The stabilizer is now wired in,
  but honest framing applies: this is size-SPIKE hardening, not a zoom-bounce fix
  -- the visible residual is BROADBAND ~1px breathing near the integer-rounding
  floor and is NOT removed by this change. Broadband-breathing reduction (longer
  window / stronger EMA / crop-height deadband) is a separate future evidence task.
  Diagnostic: `docs/active_plans/audits/zoom_bounce_diagnostic.md`; evidence:
  `docs/active_plans/decisions/size_spike_hardening_evidence.md`.

### Developer Tests and Notes

- The size-spike-hardening crop change is staged AWAITING USER CONFIRMATION
  (output-feel change; human reviews before commit). Full crop suite and the
  coordinate round-trip gate pass.

- **Bounded bin sanity study PASSED on a real 4K source**
  (`docs/active_plans/reports/bin_default_sanity_study.md`): bin1 vs bin2 center
  ratio 1.0025 (not 2x, confirming the storage boundary operates correctly), no
  all-miss intervals, no accepted-fraction drop. Full suite 3108 passed; round-trip
  gate 7 passed; final overall review PASS.

- New `tests/test_solve_storage_source_roundtrip.py` (M1): bin=3 non-pow2,
  non-square box, all-edge+w/h asserts; fails on injected scale and axis-swap
  errors. Reference audit: `docs/active_plans/audits/coordinate_space_bin_gt1_audit.md`.

- New `tests/test_solve_no_crop_coupling.py` (M4): AST-based guard asserting that
  solve/walker modules (`interval_solver`, `solver_workers`, `solve_queue`,
  `walk_viterbi`, `walk_walker`, `walker_bundle`) never import `tr_crop` nor read
  crop-feel config keys. Covers all six modules; fails loud on a missing target.

- Retired `tests/test_select_bin_factor.py` (superseded by the floor-based
  `select_default_bin_factor`; the pow2 logic it tested was removed in M3).
  Deleted `tests/test_walker_costs_config.py` (plumbing removed in 2026-06-13
  M1 walker-costs constantification).

## 2026-06-13

### Behavior or Interface Changes

- **Prepare mode live ffmpeg progress** (`track_runner/fastread_video.py`): the
  transcode step now streams ffmpeg's native progress stats line (`frame=...
  fps=... time=... speed=...`) live to the terminal, overwriting a single line
  via carriage return. Replaced the fake heartbeat line
  `[ ... ] ffmpeg running, elapsed MM:SS` with real per-second progress from
  ffmpeg stderr. The success summary (`ffmpeg summary:`) filters out progress
  stats lines so it shows only ffmpeg's encode-completion lines.
  Verbose mode (`-v`) still streams every stderr segment on its own line.

- **Prepare mode always rebuilds fast-read** (`track_runner/fastread_video.py`,
  `track_runner/cli.py`): prepare now always deletes any existing fast-read
  video and rebuilds from scratch. The validate-and-skip resume path was removed
  because it crashed with a `cv2 decode failure` on partial or stale fast-read
  files. The `force` parameter on `create_fastread_video`, the
  `skipped_transcode` parameter on `_print_status_summary`, and the
  `skipped_transcode` summary branch were removed. `_mode_prepare` in cli.py
  no longer passes `force` to `create_fastread_video`, and the now-dead
  `-f`/`--force` flag was removed from the prepare subparser in `cli_args.py`.
  Secondary audit fixes
  applied: stdlib import block reordered to shortest-name-first then
  alphabetical (`os, shutil, logging, subprocess, collections, dataclasses`);
  bare `collections.deque` annotations tightened to `collections.deque[str]`;
  two self-evident comments trimmed from `_stream_ffmpeg_stderr`.

- **Solve KeyError crash fixed** (`track_runner/cli.py`): `solve` and
  `solve --full` crashed with `KeyError: 'walker_costs'` on any per-video
  config predating the `walker_costs` section. Root cause was a direct
  `cfg["walker_costs"]` read at cli.py:893. Durable fix: the entire
  config-to-worker supply chain for walker costs was removed (M1);
  Viterbi cost weights are now fixed constants in
  `track_runner/blob_walk/walk_viterbi.py` with no per-video config read.

### Fixes and Maintenance

- **Fast-read smoke read skips the exact last frame** (`track_runner/fastread_video.py`):
  `_smoke_read_fastread` now probes frames 0 / mid / `frame_count - 2` instead of
  the final frame. cv2 random-access seek to the exact last frame fails on a
  healthy file (terminal-seek imprecision) and crashed post-transcode validation
  with `cv2 decode failure at frame N`; the production pipeline never random-seeks
  the last frame, and truncation is still caught by the exact `frame_count` match.

### Removals and Deprecations

- **Removed walker_costs config plumbing (M1)**: deleted the `walker_costs`
  section from `track_runner/track_runner.config.yaml`, the
  `_validate_walker_costs` validator from `tr_config.py`, and the
  config-to-worker supply chain (`cli.py` -> `solve_kwargs` ->
  `ExecutionContext.walker_costs` -> `make_pool` initargs ->
  `walk_viterbi.set_cost_weights` -> `_COST_WEIGHT_OVERRIDES`). The
  six Viterbi cost constants (`WEIGHT_DISPLACEMENT`, `WEIGHT_SPEED_DELTA`,
  `WEIGHT_HEADING_DELTA`, `WEIGHT_OVERSPEED`, `WEIGHT_EVIDENCE_NORM`,
  `SKIP_COST`) are now the sole source of truth in `walk_viterbi.py`.

- **Removed detection threshold config keys (M2)**: `detection.confidence_threshold`
  (0.25) and `detection.nms_threshold` (0.45) removed from
  `track_runner.config.yaml`. Both are now fixed constants in `tr_detection.py`.
  The dead `detection.model` key (`yolov8n`) was also removed; no production
  code read it.

- **Removed crop direct-center smoothing keys (M2)**: `processing.crop_post_smooth_strength`,
  `processing.crop_post_smooth_size_strength`, and
  `processing.crop_post_smooth_max_velocity` removed from `track_runner.config.yaml`.
  All three are now fixed constants in `tr_crop.py` with identical effective
  values. `processing.crop_min_size` was already absent from the default config
  (removed 2026-05-02); stale doc references were corrected.

- **Removed heartbeat scaffolding** (`track_runner/fastread_video.py`): deleted
  `HEARTBEAT_INTERVAL_S` constant, `_format_elapsed` helper, and
  `_collect_stderr_lines` helper; removed the `import time` that existed only to
  support them. ffmpeg stderr is now read incrementally via `os.read` in chunks,
  split on `\r` and `\n`, into a `collections.deque(maxlen=64)` tail buffer used
  for the error tail and success summary.

### Decisions and Failures

- **Walker costs, detection thresholds, crop alphas are fixed constants
  (human-approved 2026-06-13)**: walker Viterbi cost weights, detection
  `confidence_threshold` / `nms_threshold`, and crop direct-center smoothing
  alphas (`crop_post_smooth_strength`, `crop_post_smooth_size_strength`,
  `crop_post_smooth_max_velocity`) are too obscure for per-video user config.
  They are now fixed constants in their respective modules
  (`walk_viterbi.py`, `tr_detection.py`, `tr_crop.py`). The prior
  `docs/TRACK_RUNNER_DESIGN.md` statement that walker weights could be "tuned
  without code edits" was unapproved doc drift, not a human-approved decision;
  correcting it restores the intended design.

- **Crop smooth path was investigated and kept**: `crop_mode == "smooth"`,
  `CropController`, `smooth_crop_trajectory`, and the five smooth-only config
  knobs (`crop_smoothing_attack`, `crop_smoothing_release`, `crop_max_velocity`,
  `crop_velocity_scale`, `crop_displacement_alpha`) were audited for
  reachability. The path is reachable: `crop_mode` defaults to `"smooth"` in
  the default config, the `CropController` branch executes on that default, and
  several tests exercise it. The smooth path was not removed.

- **Config-key removals do not trigger a SCHEMA_VERSION bump**: `SCHEMA_VERSION`
  governs on-disk solver artifacts (diagnostics JSON, `torso_box_coords.npz`,
  and the geometry fingerprint cache key), not the YAML config schema. Removing
  config keys does not change any on-disk artifact layout. No bump was applied.
  This decision is recorded in `docs/TR_SCHEMA_VERSION_HISTORY.md` under the
  2026-06-13 entry.
