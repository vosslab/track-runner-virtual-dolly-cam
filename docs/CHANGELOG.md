## 2026-08-23

### Behavior or Interface Changes

- **Default analysis bin is budgeted by pixel area**
  (`common_tools/frame_reader.py`, `track_runner/modes/shared.py`):
  `MAX_ANALYSIS_PIXELS = 1_036_800` replaces `TARGET_DEFAULT_WIDTH_PX`, and
  `select_default_bin_factor(source_width, source_height)` returns
  `max(1, ceil(sqrt(source_pixels / max_pixels)))`. 4K and 2.8K now bin at 3;
  2.7K, 1440p, and 1080p bin at 2. The former width rule inverted analysis cost
  against source resolution: 1440p stayed at bin 1 and analyzed at 2560x1440,
  larger than 4K at 1920x1080. Area also prices non-16:9 sources correctly.
  Camera-motion artifacts key on analysis bin, so every previously solved video
  needs a fresh solve.
- **Accepted trade-off:** 1080p sources now analyze at 960x540 rather than
  natively. `tests/tracking/test_small_target_bin_recovery.py` measures the cost
  synthetically: blob recovery holds at every bin factor down to a 6x12 source
  target, and centroid error stays flat across bin factors for a given target
  size, being dominated by the two-lobe leave/arrive residual structure rather
  than by binning.
- **Missing solver measurements now fail where they go missing**
  (`track_runner/state_io.py`, `track_runner/review.py`,
  `track_runner/modes/predictions.py`, `track_runner/encode_analysis.py`,
  `track_runner/blob_walk/walk_status.py`): the five numeric interval-score
  fields, `fps`, `intervals`, and blob centroids are read by direct indexing
  instead of substituting `0.0`, `0.5`, `1.0`, `30.0`, or the frame origin.
  `scoring.py` already indexed three of those fields directly, and
  `state_io.py` already raised for an absent `confidence_tier`, so the
  substitutions contradicted the surrounding code. `failure_reasons` and
  `warning_flags` keep `.get` with an inline reason: an empty list is truthful
  when nothing fired. The full suite stayed green, confirming no exercised path
  relied on a fabricated value.

### Fixes and Maintenance

- **README quick-start runs again** (`README.md`): the eight quick-start
  commands passed `-i VIDEO.mp4` while `cli.py` rejects any container other
  than `.mkv`, so a new reader's first command raised. The `--mp4` encode-output
  sentence is accurate and unchanged. Status line advanced to match `VERSION`.
- **Walker facade retired** (`track_runner/blob_walk/`): `walk_walker.py` was a
  delegation-only compatibility facade whose ten of twelve functions forwarded
  to `walk_engine`, `walk_observer`, or `walk_summary`. Its two real bodies
  (`walk_one_direction`, diagnostic-row emission), the window and confidence
  constants, and `conf_from_anchor` moved into `walk_engine.py`, which now calls
  the owning modules directly instead of receiving them as injected callables.
  Import direction was verified acyclic before the move. The six walker tests
  pass with their assertion values unchanged.
- **Seed-truth stamping has a public owner**
  (`track_runner/interval_seed_anchoring.py`): `_stamp_seed_truth` became
  `stamp_seed_truth`, and `modes/encode.py` and `modes/analyze.py` call it
  through its owning module rather than reaching into a private name re-exported
  by `interval_solver.py`. The seven private re-exports are gone; the public
  re-exports remain for a later per-call-site pass.
- **Crop policy defaults have one definition**
  (`track_runner/tr_crop_math.py`): `crop_aspect`, `torso_height_multiple`,
  `crop_torso_anchor`, `crop_containment_radius`, and
  `crop_centered_fit_to_source` were hard-coded in both `tr_crop.py` and
  `tr_crop_direct.py`; both now read the shared constants.
- **Source-container validation is testable**
  (`track_runner/modes/shared.py`): the `.mkv` guard moved out of `cli.main()`
  into `validate_source_container`, keeping its message and remux hint.
- **`tests/output/` is no longer shadowed by an ignore rule** (`.gitignore`):
  the unanchored `output*/` pattern matched the source directory
  `tests/output/`, so new test modules there were silently untracked and
  invisible to the hygiene gates. The pattern is now anchored to `/output*/`,
  which is where generated output actually lands. Un-ignoring immediately
  surfaced a missing parameter annotation the typing gate had never seen.
- **CLI help text describes the current bin rule** (`track_runner/cli_args.py`):
  the `--bin` and `--auto-bin` help strings still described the width-floor
  selector, and `docs/modes/SOLVE.md` and `docs/modes/REFINE.md` embed that text
  in generated blocks, so both documents contradicted their own hand-written
  sections. Regenerated through `tools/refresh_mode_docs.py`.
- **Spec module tables match disk** (`docs/TRACK_RUNNER_V3_SPEC.md`): the UI and
  shared-utility tables still listed `actions.py` and `tools_common.py`, and the
  shared-utility table also carried `emwy_yaml_writer.py`, which the repository
  does not contain. The dependency graph no longer routes `ui.workspace` through
  the removed module.
- **Zero-caller re-exports dropped** (`track_runner/interval_solver.py`):
  `BlockBarColumn`, `TaskETAColumn`, `measure_canonical_blend_boxes`, and
  `stitch_trajectories` were reachable only through the facade name and had no
  callers there. `PROMOTION_TIERS` was not dead as it first appeared: the
  module reads it in its own promotion check, so that read now goes to
  `interval_analytical` directly and the alias is gone. The remaining public
  re-exports all have external callers.
- **Observer entry builder calls its own helpers**
  (`track_runner/blob_walk/walk_observer.py`): `build_window_entry` kept
  `gather_candidates` and `lighten_trace_fn` as injected parameters from the
  facade era, and its single caller always passed that module's own functions.
  It now calls them directly, matching the seams removed elsewhere in this
  change.
- **Documentation matches the code** (`docs/`): the bin table was republished to
  `COORDINATE_SPACES.md`, `TRACK_RUNNER_DESIGN.md`, `TROUBLESHOOTING.md`,
  `TRACK_RUNNER_V3_SPEC.md`, `modes/SOLVE.md`, and the bin report;
  `CODE_ARCHITECTURE.md` and `FILE_STRUCTURE.md` describe all seven walker
  modules; `cli.py` lists `setup` and `analyze`; `walk_motion_gate.py` names its
  two real importers rather than a module that never existed.

### Removals and Deprecations

- **Three unimported modules removed:** `track_runner/regime_policies.py`,
  `track_runner/ui/actions.py`, and `common_tools/tools_common.py` had zero
  importers across production, tools, tests, and devel.

### Decisions and Failures

- **Area budget chosen over a lower width target.** Analysis work scales with
  pixel count, so budgeting area keeps the rule correct for non-16:9 sources,
  and a single constant remains the retune lever.
- **End-to-end coverage stays out of scope.** Real footage cannot be committed,
  and a generated clip standing in for a whole solve would assert that the
  solver reproduces its own output. Synthetic input is used only at component
  level, where the measured property is independent of solver output.

### Developer Tests and Notes

- **New coverage:** `tests/source/test_residual_pre_pass.py` (store hit, miss,
  ROI keying, byte-bounded eviction), `tests/modes/test_mode_entry_guards.py`
  (container validation), `tests/output/test_required_score_fields.py` (one
  failure test per converted module), `tests/tracking/test_walk_status_blob_contract.py`
  (malformed blob), `tests/crop/test_crop_default_policy.py` (empty-config crop
  geometry), and `tests/tracking/test_small_target_bin_recovery.py`.
- **Count assertions resolved:** walker-adapter span counts now derive from the
  interval bounds, the analyze-report series length asserts against the frame
  count it must match, the crop-rect count derives from `total_frames`, and the
  dolly pin-weight comprehension moved into a named helper.
- **3,964 tests passed** after the full sequence. The hygiene suites enumerate
  through `git ls-files`, so their per-file parametrization counts the newly
  added files only once those files are staged.
- **Bin-recovery harness sweeps sizes and bin factors.** The published recovery
  table is now the output of `measure_recovery` in
  `tests/tracking/test_small_target_bin_recovery.py`, which parametrizes four
  target sizes against bin factors 1-4, so the report and the gate cannot
  drift. Widening the sweep falsified the harness's first tolerance: a
  one-target-width bound fails at 6x12, because the residual of a displaced
  rectangle has leave and arrive lobes whose combined centroid error scales
  with (target width + displacement) / 2. The bound now states that structure.
- **Six-reviewer audit applied.** An independent review pass over the staged
  change produced the corrections recorded above under "Fixes and Maintenance":
  the `.gitignore` anchoring, the CLI help text, the `TRACK_RUNNER_V3_SPEC.md`
  module tables, and the `walk_observer.build_window_entry` seam. One added
  test, an assertion that recovered blob area shrinks monotonically with bin
  factor, was deleted as an arithmetic consequence of `INTER_AREA` downsampling
  rather than a property of blob extraction.
- **Changelog rotated:** this entry pushed the active file past the 1000-line
  trigger, so `devel/rotate_changelog.py` moved 2026-08-21, 2026-08-20, and
  2026-08-19 into `docs/CHANGELOG-2026-08a.md`, keeping the two newest day
  blocks in place.

## 2026-08-22

### Fixes and Maintenance

- **Schema-15 audit removed obsolete seams:** fixed debug-sidecar key drift, removed unreachable
  helpers/callback injection, archived the plan and five records, and retained length exceptions.

### Developer Tests and Notes

- **Six audits found one runtime seam:** fixed; no affine or fixture drift; 3,885 tests passed.
