## 2026-06-10

### Fixes and Maintenance

- Stage-4 walker dispatch now uses the worker pool -- `blob_pass` threads
  through the pool initializer as run-invariant worker context, removing the
  in-process-only special case.

## 2026-06-09

### Behavior or Interface Changes

- The windowed Viterbi walker (`track_runner/blob_walk/`) now solves
  Stage-4-promoted intervals by default. `solve_interval_analytical`,
  `_dispatch_blob_pass`, and `solve_all_intervals` default their walker
  seam on; the Stage-3 dispatch sites (`solve_queue`, `solver_workers`)
  pass `blob_pass=False` explicitly so Stage 3 stays pure Hermite on
  every interval, and the no-reader test/diagnostic paths stay pure
  Hermite. A per-interval, per-pass Hermite fallback fires on walker
  stall (zero accepted frames -> that pass uses its Hermite path), so
  default-on is never worse than Hermite on promoted intervals. The
  fallback reads the walker's own `WalkSummary.accepted_count`, not
  `raw_pred` and not FWD/BWD agreement, preserving Hermite independence.
  The underlying bootstrap-stall root cause remains open; Viterbi weight
  tuning and a promoted-only A/B are follow-up work.
- (Superseded same day) Earlier on 2026-06-09 the default was pure
  Hermite on every promoted interval with the walker opt-in behind the
  now-removed `--walker-stage4` flag; the change above flips that default
  on for promoted intervals.

### Removals and Deprecations

- Removed the experimental `--walker-stage4` CLI flag from solve and refine
  parsers per argparse minimalism; the internal `use_walker` seam (default
  False) in `interval_solver.py` is preserved as the future promotion path.
- Deleted v1 blob-snap layer from `track_runner/velocity_model.py`:
  `_apply_blob_snap`, `_motion_path_ok`, `BLOB_SNAP_ALPHA`,
  `BLOB_SNAP_PATH_SLACK`, `BLOB_SNAP_PATH_PERP_FRACTION`,
  `BLOB_SNAP_VELOCITY_FLOOR`, `BLOB_SNAP_ALPHA_MAX`,
  `BLOB_SNAP_MAX_SHIFT_FRACTION`.
- Deleted `BlobGateTrace` dataclass from `track_runner/blob_trace.py`
  (produced only by the deleted `_apply_blob_snap`; distinct from the
  preserved `BlobObserverTrace` used by the walker).
- Removed `blob_snap_enabled` propagator parameter, `blob_gate` per-frame
  stamp, `blob_coverage_fwd`/`blob_coverage_bwd` coverage diagnostic, and
  `propagated_with_blob_snap` source string.

### Fixes and Maintenance

- Renamed internal solve parameter `use_walker` / `stage4_walker` to `blob_pass`
  across `track_runner/interval_solver.py`, `track_runner/solve_queue.py`,
  `track_runner/solver_workers.py`, `tests/test_walker_flag.py`,
  `tests/test_walker_stall_fallback.py`, and `tests/e2e/e2e_walker_ab.py`.
  The old names read as an experiment toggle; `blob_pass` reflects the true
  role: distinguishing Stage-3 pure-Hermite dispatches (`blob_pass=False`)
  from Stage-4/5 windowed-walker dispatches (`blob_pass=True`). No behavior
  or default change.

- Updated [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md),
  [docs/TR_FWD_BWD_MODEL_METHODOLOGY.md](TR_FWD_BWD_MODEL_METHODOLOGY.md),
  [docs/TR_MOTION_CUE_HEAT_MAP.md](TR_MOTION_CUE_HEAT_MAP.md), and
  [docs/ROADMAP.md](ROADMAP.md) to reflect the current state: walker is the
  default blob pass on promoted intervals (`blob_pass=True`); `--walker-stage4`
  flag removed; `use_walker`/`stage4_walker` seam names replaced with
  `blob_pass`; "experimental" labels removed from ASCII diagrams and prose.
  Also removed v1 live-mechanism descriptions (`_apply_blob_snap` ASCII
  pipeline, three-gate list, `snap_pred` terminology).

- Doc path corrections (blob_walk absorption review): updated
  [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) to point the
  windowed path-selection walker section at its new home
  `track_runner/blob_walk/` (relocated from `tools/blob_walk_v2/`); added
  clarifying note that the walker is wired behind the default-off
  `--walker-stage4` flag; updated the C10 compliance note to reflect that
  the walker schema version now reads `tr_schema.SCHEMA_VERSION`.
- Updated [docs/FILE_STRUCTURE.md](FILE_STRUCTURE.md) to add entries for
  the new `track_runner/blob_walk/` subpackage (all six modules) and
  `track_runner/walker_bundle.py`.
- Repointed dead `tools/blob_walk_v2/core/walk_io.py` and
  `tools/blob_walk_v2/core/walk_walker.py` references in
  [docs/active_plans/decisions/walker_npz_coord_contract.md](active_plans/decisions/walker_npz_coord_contract.md)
  and
  [docs/active_plans/active/typed_coordinate_space_plan.md](active_plans/active/typed_coordinate_space_plan.md)
  to `track_runner/blob_walk/walk_io.py` and
  `track_runner/blob_walk/walk_walker.py`.
- Rotated [docs/CHANGELOG.md](CHANGELOG.md): 1256 lines exceeded the
  ~1000-line threshold; kept day blocks 2026-06-08 and 2026-06-07 in the
  active file; moved 2026-06-03 through 2026-05-03 into new archive
  [docs/CHANGELOG-2026-06a.md](CHANGELOG-2026-06a.md) (named for the most
  recent month in the archived range per REPO_STYLE.md).
- Archived two v1-era analysis docs to `docs/archive/` with a `V1_` prefix
  (`docs/SOLVE_STAGE_FACTORABILITY_NOTE.md` ->
  [docs/archive/V1_SOLVE_STAGE_FACTORABILITY_NOTE.md](archive/V1_SOLVE_STAGE_FACTORABILITY_NOTE.md),
  `docs/BLOB_REDESIGN_REPORT.md` ->
  [docs/archive/V1_BLOB_REDESIGN_REPORT.md](archive/V1_BLOB_REDESIGN_REPORT.md));
  repaired all inbound Markdown links in
  [docs/archive/blob_problem_discovery/blob_refinement_visual_audit.md](archive/blob_problem_discovery/blob_refinement_visual_audit.md)
  and updated backtick path references in
  `docs/active_plans/audits/v1_blob_snap_deletion_blast_radius.md` and
  `docs/CHANGELOG-2026-05a.md`.

## 2026-06-08

### Additions and New Features

- Wrote the consolidated absorption closeout / handoff at
  [docs/active_plans/reports/blob_walk_absorption_closeout.md](active_plans/reports/blob_walk_absorption_closeout.md):
  what shipped (M1-M4), verification, independent review, the corrected A/B
  result and how to read it, design facts, the WP-6 prereqs, and the one human
  decision (commit the default-off increment now; schedule bootstrap-fix +
  Viterbi tuning + promoted-only re-A/B as the next reviewed phase).
- M4 gate (task #12): rewrote the walker A/B report at
  [docs/active_plans/reports/m4_walker_ab_report.md](active_plans/reports/m4_walker_ab_report.md)
  and saved its raw per-interval data at
  [docs/active_plans/reports/m4_walker_ab_data.csv](active_plans/reports/m4_walker_ab_data.csv).
  Sample-limited run (58 of a 120-interval target evaluated; 62 skipped by a
  per-video decode-time budget, not cherry-picked). Distribution headline:
  success (rescued+preserved) = 21/58 (rescued=6, preserved=15), regressed=35,
  needs_review=2. The walker preserves/rescues on roughly a third of evaluated
  during-race visible intervals (notably the high-drift Conant interior, 3/5
  rescued) and regresses on most of the rest at current Viterbi weights -- a
  broad distribution that supports holding `--walker-stage4` default-OFF, not a
  drop-in win. The report also records that the prior 4-interval result was a
  METRIC artifact (FWD/BWD agreement bias), NOT a selection artifact: the two
  previously-flagged FWD-zero-coverage intervals (Conant seed_1080_1111, Jason
  seed_564_583) are confirmed visible-on-both and during-race, so they were
  legitimately in scope.
- WP-5b (M4): wired real FWD/BWD windowed-walker execution into Stage 4 behind
  a default-off flag. New adapter `walker_bundle.walk_bundle_to_path` bridges a
  `WalkerInputBundle` to the relocated core `blob_walk.walk_walker.walk_one_direction`
  (unpacking the bundle, running one direction with a no-op debug sink so the
  solver path writes no CSV/PNG, and projecting the walker's standalone
  `direction_path` into the full-span, chronological, PROCESSED-pixel aligned
  state list that `blend_paths` / `compute_agreement` already consume). FWD and
  BWD each get their own bundle and their own `walk_one_direction` call
  (contract C9). New flag `--walker-stage4` (`dest=stage4_walker`, default
  False) on the solve and refine parsers threads through
  `cli` -> `solve_all_intervals(stage4_walker=...)` ->
  `_dispatch_blob_pass(use_walker=...)` ->
  `solve_interval_analytical(use_walker=...)`. Default OFF keeps the v1
  `_apply_blob_snap` path byte-identical (e2e_blob_walk_baseline RESULT: PASS,
  full pytest suite green). Added the M4 A/B report at
  [docs/active_plans/reports/m4_walker_ab_report.md](active_plans/reports/m4_walker_ab_report.md)
  and its harness `tests/e2e/e2e_walker_ab.py`; the partial 4-interval A/B
  classified rescued=1, preserved=0, needs_review=0, regressed=3, confirming the
  walker is not yet a default. New tests `tests/test_walker_adapter.py`
  (full-span projection, status->coverage mapping, short-walk padding) and
  `tests/test_walker_flag.py` (OFF takes v1, ON takes walker).
- WP-5a (M4 / WS-E): added the additive Stage 4 walker input bundle seam. New
  module `track_runner/walker_bundle.py` defines the `WalkerInputBundle`
  dataclass (seed, neighbor seed, frame range, direction sign, torso-unit
  scale, and the candidate-lattice source plumbing: reader, scene_transform,
  fps, stride, precomputed_store), a `build_walker_bundles_for_interval`
  builder that emits one FWD and one BWD bundle (each anchored on its own seed,
  contract C9), and an injectable `run_walker_pass(bundle, walker_callable)`
  invocation seam. `track_runner/interval_solver.py` gained
  `run_stage4_walker_seam`, which decides promotion from the Stage-3
  `confidence_tier` BEFORE any walker runs (Stage-3-first), and only then builds
  both bundles and hands them to the injectable walker. The seam lives beside
  the Stage 4 integration point, not inside `track_runner/blob_walk/`, so the
  core walker does not absorb pipeline orchestration. This is additive only: no
  production call site invokes the seam yet, so default solve behavior is
  byte-identical (e2e_blob_walk_baseline RESULT: PASS). The real walker wiring
  is WP-5b. The bundle deliberately omits the Hermite raw_pred path (Hermite
  independence).
- WP-2 (WS-B): created the relocation-equivalence report artifact at
  [docs/active_plans/reports/blob_walk_relocation_equivalence.md](active_plans/reports/blob_walk_relocation_equivalence.md).
  States the equivalence gate (`e2e_blob_walk_baseline.sh`), column comparison
  policy (EXACT for categorical/flag columns including `status`; TOLERANT abs
  <= 0.5 for numeric columns; unclassified falls back to exact), the 8 verdict
  CSVs across 4 intervals / 2 videos compared, and the current result. Result:
  RESULT: PASS -- baseline matches (very-very-close policy), 224 total verdict rows.

### Behavior or Interface Changes

- M4 gate (task #12): rebuilt the walker A/B evaluation harness
  [tests/e2e/e2e_walker_ab.py](../tests/e2e/e2e_walker_ab.py) to fix the prior
  selection + metric artifact. Selection now draws 20 random DURING-RACE
  (left frame > `race_start_frame`, contract C4) VISIBLE-on-all-three seed
  triples per video over the established 6-video corpus
  ([data/outdoor_corpus.txt](../data/outdoor_corpus.txt)) at a fixed
  `--random-seed`, mirroring `walk_util.select_random_visible` and reusing
  `walk_io.load_race_start_frame`. The metric is now an INDEPENDENT accuracy
  proxy: the interior human seed B of each triple is HELD OUT, the merged A->C
  interval is solved Hermite-only (blob off) vs walker-on (blob on), and each
  solved box's center distance to the held-out human seed is measured in
  torso-width units (contract C2). Classification is rescued / preserved /
  regressed / needs_review with preserved counted as success (the walker
  independently matching a good Hermite answer; independence enforced by the
  no-Hermite import gate + WP-5a data-boundary test). This replaces the old
  FWD/BWD-agreement metric, which was structurally biased toward Hermite (its
  two passes mirror one fitted curve; the walker's are independent per C9).
  Evaluation tooling only -- no production solver code changed and the
  `--walker-stage4` default stays OFF. `bash
  tests/e2e/e2e_blob_walk_baseline.sh` RESULT: PASS (224 verdict rows);
  `pytest tests/ -q` 1533 passed.
- WP-5b (M4): `blob_walk.walk_walker.walk_one_direction` now carries the
  per-frame five-value walk `status` on each `direction_path` entry (alongside
  the solved box), so the Stage 4 walker adapter can map it onto the legacy
  `blob_gate` coverage diagnostic without re-reading the debug-log CSV. This is
  an additive dict key on an in-memory structure; the verdict CSV columns and
  the `DebugLogRow` schema are unchanged, so `e2e_blob_walk_baseline` stays
  PASS. No cross-frame state and no Hermite reference are introduced (the
  no-Hermite import gate stays green; the adapter lives on the pipeline side in
  `walker_bundle.py`, not under `track_runner/blob_walk/`).
- WP-4 (M3 / WS-D): folded the relocated verdict-CSV debug log onto the unified
  schema constant. `track_runner/blob_walk/walk_debug_log.py` now defines
  `SCHEMA_VERSION = tr_schema.SCHEMA_VERSION` (imported bare as `import
  tr_schema`) instead of its own standalone `SCHEMA_VERSION = 13`. Now that the
  walker lives inside `track_runner/` beside `tr_schema.py`, two schema
  constants violated contract C10 (one unified `SCHEMA_VERSION`). The exported
  stamp value changes 13 -> 11, but it is metadata only: the constant is never
  written into the verdict CSV, the `HEADER` column tuple (43 columns) is
  unchanged, and `e2e_blob_walk_baseline` compares CSV columns and cells, so no
  CSV output changes. The torso_box_coords writer in `state_io.py` and
  `GEOMETRY_AFFECTING_SCHEMAS` are untouched (already additive per WS1-C).
- WP-3 (M2 / WS-C): confirmed and locked the in-pipeline walker's per-frame
  candidate source path. Extracted the smallest readable gathering helper
  `gather_frame_candidates(obs, trace_sink_holder)` in
  `track_runner/blob_walk/walk_walker.py` from the inline `_build_window_entry`
  extraction; it is the single point that turns one `observe_blob_at` trace
  into the per-frame `corridor_blobs` candidate list. `observe_blob_at` and
  `residual_motion.py` are unchanged (API Decision 2026-05-28). The helper is
  identity-preserving on centroids and yields an empty list on an obs-less or
  blob-less frame, so the gathered sequence stays frame-aligned.
- Recorded the declared candidate coordinate space (PROCESSED full-frame, ROI
  origin already added back) in the gathering helper docstring and added a row
  to `docs/COORDINATE_SPACES.md` distinguishing the trace `corridor_blobs`
  centroids (PROCESSED full-frame) from `observe_blob_at`'s RETURN centroid
  (SOURCE).

### Fixes and Maintenance

- Code-review hardening (behavior-preserving): `walker_bundle.py`
  `_interpolate_missing_frame` now raises a loud
  `RuntimeError("no bracketing frame found for frame N")` before subscripting
  `upper_box` in the final `else` branch. The branch already documents that
  `upper_box` cannot be None (start_frame is always an anchored endpoint); the
  guard converts a genuinely-impossible regression from a cryptic NoneType
  subscript into an explicit failure (loud-failure style, not a fallback
  default). No reachable behavior change.
- Clarified the `--walker-stage4` help text on the solve and refine parsers in
  `cli_args.py` to note it is an experimental A/B switch that runs Stage 4
  single-process (the pool worker does not carry the walker flag). Cosmetic
  help-string only; `dest`, `default`, and `action` are unchanged.
- WP-2 review cleanup: fixed 20 broken markdown links in
  [docs/CHANGELOG.md](CHANGELOG.md) pointing to the dead path
  `tools/blob_walk_v2/core/walk_driver.py`; repointed to the current location
  `tools/blob_walk_v2/walk_driver.py`. Also fixed 2 references in
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md) (`## 12`
  and `## 13` entries) pointing to the dead path
  `tools/blob_walk_v2/core/walk_debug_log.py`; repointed to
  `track_runner/blob_walk/walk_debug_log.py`. Verified with
  `pytest tests/test_markdown_links.py -q` (295 passed).
- WP-2 review cleanup: removed one fragile collection-size assertion from
  `tests/test_blob_walk_v2_candidate_source.py`
  (`assert [len(cands) for cands in sequence] == [2, 1, 3]`). The assertion
  checked a fixture constant, not the function behavior under test; forbidden
  by PYTEST_STYLE. The frame-alignment check directly above it is kept.
- WP-2 review cleanup: updated two stale comments in
  `track_runner/blob_walk/walk_debug_log.py` (module docstring and HEADER
  inline comment) that said "44 columns as of schema v13" / "SCHEMA_VERSION=13".
  Both now describe HEADER as the locked verdict-CSV column tuple (43 columns)
  and point to [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)
  for version history, without hard-coding a stale version stamp.

### Developer Tests and Notes

- M4 gate (task #12): the corrected A/B run was sample-limited by HEVC HDR /
  4k120 random-access decode cost (Jason and Lyra-Wheeling reach frames
  10k-20k+, tens of seconds per triple). A `--per-video-budget` (240 s for the
  recorded run) caps each video's wall time and counts overrun triples as
  `skipped_budget`, so every corpus video contributes its cheap-to-decode
  triples rather than one slow video starving the rest; the evaluated set is an
  unbiased subset of the fixed random sample. Re-running at `--random-seed
  12345` reproduced IMG_3830 and IMG_3823 interval-for-interval (determinism
  check). Verification after the harness/report/changelog edits:
  `bash tests/e2e/e2e_blob_walk_baseline.sh` RESULT: PASS (224 verdict rows,
  walker output unchanged vs baseline); `pytest tests/ -q` 1534 passed;
  `pytest tests/test_markdown_links.py -q` passed.
- WP-5a (M4 / WS-E): added `tests/test_walker_bundle_seam.py`, a deterministic
  data-boundary test using a fake recording walker (no video decode). It asserts
  (positive) the FWD/BWD bundles carry the seed and candidate-lattice source
  (reader, scene_transform, fps, stride) plus the torso-unit scale sufficient to
  walk, and (paired negative) no Hermite raw_pred path is reachable through any
  bundle field. A third test locks Stage-3-first ordering: a non-promoting tier
  never invokes the injectable walker, so walker output cannot influence
  eligibility. `pytest tests/ -k "interval_solver or walker_bundle or no_hermite
  or blob_walk_v2"` 182 passed; `bash tests/e2e/e2e_blob_walk_baseline.sh` PASS
  (default solve unchanged, 224 verdict rows); full `pytest tests/` 1512 passed.
- Added `tests/test_blob_walk_v2_candidate_source.py`: a hand-built-fixture
  behavioral test (no video decode) asserting the gathered candidate sequence
  is frame-aligned, an empty-blob frame and an off-frame soft-miss each yield
  an empty list, and centroids land in the declared PROCESSED full-frame space
  without re-projection to SOURCE. `pytest tests/ -k "walk and candidate"` (4
  passed); `pytest tests/ -k blob_walk_v2` (169 passed, up from 165 by the 4
  new tests); `pytest tests/test_pyflakes_code_lint.py -k walk` (32 passed).
- WP-4 (M3 / WS-D): `pytest tests/ -k "schema"` 33 passed including
  `test_tr_schema_version_single_source.py` (the C10 drift gate that scans
  `track_runner/` and accepts only `tr_schema.SCHEMA_VERSION` re-exports; the
  old `= 13` would have failed it once relocated). `pyflakes
  track_runner/blob_walk/walk_debug_log.py` clean. Recorded the constant fold in
  `docs/TR_SCHEMA_VERSION_HISTORY.md`. `bash
  tests/e2e/e2e_blob_walk_baseline.sh` PASS (baseline matches, 224 verdict rows
  across 4 intervals), confirming the fold changes no CSV output.

## 2026-06-07

### Additions and New Features

- **WP-1: relocated the blob_walk_v2 windowed-walker core into a new
  [track_runner/blob_walk/](../track_runner/blob_walk/) subpackage.** `git mv` moved the six
  core algorithm modules out of `tools/blob_walk_v2/core/` with no behavior change:
  `walk_viterbi.py`, `walk_motion_gate.py`, `walk_status.py`, `walk_walker.py`,
  `walk_debug_log.py`, and `walk_io.py`. Added a minimal
  [track_runner/blob_walk/__init__.py](../track_runner/blob_walk/__init__.py) (docstring only,
  no re-exports). This is the M1/WS-A relocation step of the plan to make the windowed walker
  the in-pipeline blob solver.

### Behavior or Interface Changes

- **WP-1: removed the `walk_paths.setup()` / package-root `sys.path` bootstrap from the
  relocated walker core; core siblings now import via the `blob_walk.*` subpackage.** Inside
  `track_runner/blob_walk/`, core-to-core imports use `import blob_walk.walk_X as walk_X`;
  imports of track_runner siblings (`residual_motion`, `blob_trace`, `scene_coords`,
  `state_io`, `camera_motion`) and `common_tools.*` keep the package-wide bare-name top-level
  convention (these modules import their own siblings by bare name, so a dotted
  `track_runner.X` import is not resolvable). [walk_io.py](../track_runner/blob_walk/walk_io.py)
  now resolves the repo root once via `git rev-parse --show-toplevel`
  (per [docs/REPO_STYLE.md](REPO_STYLE.md)) instead of `walk_paths.setup()`.
- **WP-1: repointed the tool-side driver and render imports to the relocated core.**
  [walk_driver.py](../tools/blob_walk_v2/walk_driver.py) (kept under `tools/` for WP-2;
  see Notes), [make_walk_html_v2.py](../tools/blob_walk_v2/make_walk_html_v2.py),
  [walk_render.py](../tools/blob_walk_v2/render/walk_render.py), and
  [walk_html.py](../tools/blob_walk_v2/render/walk_html.py) now import the moved modules as
  `blob_walk.*`.

### Developer Tests and Notes

- **WP-1: updated every test/e2e import that referenced the old core module paths**
  (`test_blob_walk_v2_debug_log.py`, `test_blob_walk_v2_motion_gate.py`,
  `test_blob_walk_v2_offframe_softmiss.py`, `test_blob_walk_v2_windowed.py`,
  `test_blob_walk_v2_winner_modes.py`, `test_blob_walk_v2_visible_seed_filter.py`,
  `test_m1d_heat_not_computed_detection.py`, `e2e_blob_walk_baseline.py`,
  `e2e_bug_101_degenerate_roi.py`). [test_blob_walk_v2_no_hermite.py](../tests/test_blob_walk_v2_no_hermite.py)
  now scans both `track_runner/blob_walk/` and `tools/blob_walk_v2/` and still asserts no
  `velocity_model`/`interval_solver`/`scoring` import in the walker core.
- **WP-1 verification:** captured the `e2e_blob_walk_baseline` golden snapshot on the pre-move
  tree, then confirmed the post-move walker output is byte-identical (8 verdict CSVs / 224 rows
  PASS). `pytest tests/ -k blob_walk_v2` (165 passed), `pytest -k "blob_walk_v2 and no_hermite"`,
  and `pytest test_pyflakes_code_lint.py -k walk` all green.
- **WP-1 scope note (DONE_WITH_CONCERNS):** `walk_driver.py` was left under
  `tools/blob_walk_v2/core/` and repointed rather than split. Its `run_interval_walk` interleaves
  the solver walk with tool-only tile rendering and heat-movie encoding (it imports `walk_render`
  and `heat_movie_encode`), so cutting it apart would change the e2e baseline call surface
  (`walk_driver.run_interval_walk`) and pull tool modules into the core package. The clean
  solver/tool split of `walk_driver` is deferred to WP-2 (tools repoint + e2e parity), which owns
  that call surface.
