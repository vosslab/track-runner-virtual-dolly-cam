## 2026-08-21

### Fixes and Maintenance

- **Repository-owned pytest is organized by pipeline** (`tests/`): source,
  seed, geometry, storage, solver, tracking, crop, output, mode, and UI tests
  now live in matching subdirectories. Top-level generic `test_*.py` names are
  reserved for vendored repository-hygiene checks. Removed the checkout-size
  and package-import-shape checks, which depended on a developer machine or
  private import mechanics rather than durable product behavior.

- **Refine preserves its prior solve on rejection**
  (`track_runner/modes/refine.py`): score/geometry partitioning now stays
  in memory until fingerprint reuse is validated. A refine request that would
  become a full solve fails without discarding the existing torso-coordinate
  artifact.

- **Fast pytest now avoids operational and GUI-lifecycle checks** (`tests/`):
  removed checkout-size, Qt widget/event-loop, private executor, and
  monkeypatch-routing tests that coupled the fast lane to a developer machine
  or implementation internals. Worker budgeting retains compact behavioral
  fit/fail/explicit-override coverage.

- **Current artifact identity and validation are fail-loud**
  (`track_runner/modes/video_artifacts.py`, `track_runner/modes/solve.py`,
  `track_runner/camera_motion_artifact.py`, `track_runner/torso_box_coords_io.py`,
  and `track_runner/state_io.py`): incompatible source geometry now blocks
  consumption; incompatible derived artifacts are rebuilt only through a fresh
  solve. Camera motion requires matching estimator, analysis
  bin, and complete source identity. Trajectory and interval-score readers
  reject incomplete or malformed current records rather than turning them into
  apparent completed work. Seeds remain human truth and require fresh
  annotation for a different video.
  The interval fingerprint now carries the one current schema tag; retired
  schema-version sets, compatibility exports, and their implementation-shape
  tests are removed.

- **Fast tests and current documentation match the maintained product**
  (`tests/` and `docs/`): removed codec round-trips, source-shape inventories,
  and cache-count assertions that coupled the permanent suite to environment
  or implementation layout. Retained tests use generated in-memory data to
  exercise durable contracts. Usage, storage, motion, schema, crop, and CLI
  documentation now describe the current nine-mode workflow, `dolly` default,
  and current-only artifact lifecycle.

- **Current-only seed, config, and diagnostics storage**
  (`track_runner/state_io.py`, `track_runner/tr_config.py`, and
  `track_runner/tr_schema.py`): all owned seed files were already header 3,
  active config files were normalized to header 3, and interval-score files
  were already current nested records. Seed, config, and diagnostics loaders now
  reject retired formats instead of silently converting them. Bin-suffixed
  solved stores, motion NPZ files without `bin_factor`, and heat-map
  `half_window` input now regenerate or fail clearly. Fixture-backed seed
  conversion and the one-trajectory dolly ranking test were removed; portable
  tests retain current schema validation, crop containment, convergence, and
  safe fallback.

- **Interval-score storage validates one current contract**
  (`track_runner/state_io.py`, `track_runner/tr_paths.py`, and consuming
  modes): the public path and APIs consistently use `interval_scores` while
  the established JSON header remains part of the persisted layout. Strict
  reader validation prevents incomplete or stale records from looking current;
  a fresh `solve` writes the sole supported format.

- **Current ownership is explicit and dead race-start code is gone**
  (`track_runner/torso_box_coords_io.py`, `track_runner/modes/`,
  `track_runner/tr_paths.py`, and `track_runner/race_start.py`): modes now use
  the torso-coordinate and prediction owners directly, the duplicate generic
  interval-path API is removed, and the unused velocity-onset detector no
  longer ships. The maintained race-start rule remains the deterministic
  midpoint of the Stage-1 seed transition.

- **Dead convenience aliases are removed** (`track_runner/tr_crop.py`,
  `track_runner/ui/workspace.py`, and `track_runner/seed_color.py`): crop and
  workspace expose only live owners, and seed normalization reads the current
  `processing` configuration instead of an obsolete settings-tree fallback.

- **Setup writes only consumed camera configuration** (`track_runner/tr_config.py`,
  `track_runner/setup_mode.py`, and `track_runner/tr_detection.py`): the YAML
  schema no longer requires an empty detection section or stores unused venue
  metadata. Setup records the motion estimator and discrete zoom levels only;
  YOLO construction uses its fixed application constants directly.

- **Residual observation documentation now states the live contract**
  (`track_runner/residual_motion.py`): raw evidence is keyed by frame and ROI;
  optional acceptance boxes are the sole geometric filter; and candidate
  selection uses integrated magnitude without an invented corridor or stale
  trace fields.

- **Hermite prediction is now pair-local**
  (`track_runner/velocity_model.py`, `track_runner/interval_analytical.py`,
  `track_runner/interval_solver.py`, `track_runner/solve_queue.py`, and
  `track_runner/solver_workers.py`): human torso boxes remain position-and-size
  anchors. Each interval uses the chord between its two endpoint boxes at both
  Hermite ends; no neighboring seed, inferred derivative, or cross-interval
  field enters cached geometry. The manager-era derivative comparison tests
  were removed in favor of two small behavioral checks.

- **Analyze joins the two current solve artifacts explicitly**
  (`track_runner/modes/analyze.py`): solver-context reporting now pairs
  geometry-only NPZ interval records with their matching interval-score JSON
  records before calculating score summaries. A synthetic CLI solve followed by
  analyze exercises the complete stored-artifact path.

- **Pytest imports resolve from the retained source owners**
  (`tests/conftest.py`): the shared bootstrap uses
  `file_utils.get_repo_root()` and gives deterministic precedence to the
  repository root, `track_runner/`, and `common_tools/`. Package-qualified and
  established bare local imports now collect without relying on the removed
  `tools/blob_walk_v2/walk_paths.py` side effect. The import-requirements map
  also recognizes the declared `pytest-qt` package's `pytestqt` module name.

### Removals and Deprecations

- **Completed plans are archived** (`docs/archive/`): the completed
  interaction/trajectory plan and five retired Blob Walk v2 or
  coordinate-space plans no longer appear under `docs/active_plans/active/`.
  Their inbound Markdown links now resolve to the archive locations.

- **Retired Blob Walk cost-model workstream is archived**
  (`docs/archive/blob_walk_v2_cost_model_ab.md`): its historical links now
  resolve from the archive, and it no longer appears as active work.

- **Retired blob-walk diagnostic tests and launcher are removed**
  (`tests/` and the former `run_random_walk.sh`): tests of the deleted manifest
  reporter, M1-D heat investigation, tool path adapter, and 941-line
  plan-era cost-model harness no longer occupy the permanent fast suite. The
  retained Viterbi brute-force optimum and memoization behavior tests import
  the production `track_runner/blob_walk/` modules directly. Current
  architecture and file-structure docs no longer advertise the deleted tool;
  the retained design-history plans now label its commands and paths as
  retired rather than executable guidance. Two one-time implementation checks
  were also removed from the permanent suite: an exact OpenCV-render SHA and a
  walker-bundle dataclass-name scan. Behavioral heat-map and Stage-4 seam tests
  remain.

### Decisions and Failures

- **Fixup-plan close-out uses grounded repository evidence**
  (`docs/archive/interaction_shell_and_trajectory_truth.md`): the original
  private-image, shared-tangent, corpus-ranking, machine-profile, and fragile
  lifecycle gates are documented as superseded design or verification choices,
  not missing implementation. Permanent coverage follows the repository's
  deterministic, offline behavior-test policy; image evidence of high school
  runners is excluded from Git.

### Developer Tests and Notes

- `source source_me.sh && python3 -m pytest tests/ -q` passes the full portable suite.
  Focused storage, motion, solver, UI bootstrap, typing, pyflakes, Markdown,
  ASCII, and whitespace checks also pass.

## 2026-08-20

### Behavior or Interface Changes

- **Source-file ownership refactor closes the line-limit maintenance work**
  (`track_runner/` cohesive modules and their focused tests): the user promoted
  the prior source-line debt from a non-goal to active maintainability work.
  Cohesive responsibility extractions separate interval progress/analysis/seed
  anchoring, residual frames/blob traces, walker engine/observer/summary,
  torso-box I/O, crop math/direct/controller, Encode reports/audio/pool control,
  UI heat/edit/status support, mode video/seed support, and walk-report tools.
  They leave every worktree Python file below the exclusive 1,000-line limit;
  a direct `rg`/`wc` scan finds the largest at 994 lines. The structural splits
  do not claim an output or performance change; `camera_motion_artifact` owns
  artifacts/cache only, and Stage 1 camera-motion estimation algorithms are
  unchanged.
  Review found and corrected two real seams: seed eligibility now has one
  canonical approximate rule with a real NPZ identity
  round-trip, and parallel Encode quit terminates and reaps its worker pool.
  The portable suite passes without a collection-count gate. Refreshed Graphify reports 2,306
  nodes, 3,450 edges, and 164 communities, with a 44.6x average token
  reduction benchmark. Jason, private-video, manual, and staging gates were
  not used.

- **Approximate seeds preserve machine trajectory**
  (`track_runner/interval_solver.py` and durable design documentation): an
  approximate seed retains machine geometry with approximate confidence/status;
  it is not an erasure anchor. `not_in_frame` remains the authoritative absence
  status and uses the bracketed `NifSpan` truth-erasure semantics.

- **NIF span now owns both runner absence and crop anchors**
  (`track_runner/off_frame_geometry.py`, `track_runner/interval_solver.py`,
  `track_runner/modes/shared.py`, `track_runner/modes/analyze.py`, and
  `tests/test_off_frame_geometry.py`): the single derived `NifSpan` index set now
  clears authoritative runner trajectory truth across every strict-between
  visible or partial bracket frame while placing edge geometry only in the
  crop trajectory. The legacy local-radius helper and its tests are removed, so
  solve, Analyze, and Encode use the same bracketed absence set. Sparse long,
  short closed, and open-ended spans preserve visible/partial endpoints;
  open-ended spans run through the known last frame. Pre-race NIF validation
  and persisted artifacts remain unchanged.

- **Encode debug overlays preserve pre-race FWD/BWD absence**
  (`track_runner/modes/encode.py` and `track_runner/state_io.py`): persisted
  pre-race intervals deliberately reload with both directional paths as
  `None`. Debug overlays now leave those projection slots blank while retaining
  blended trajectory geometry; malformed one-sided paths fail loudly instead of
  being mistaken for pre-race data. NIF crop-only geometry remains separate
  from runner and projection truth.

- **Analyze now reconstructs the same crop-only NIF edge anchors as Encode**
  (`track_runner/modes/shared.py`, `track_runner/modes/analyze.py`, and
  `track_runner/modes/encode.py`): NIF anchors pass through the authoritative
  `off_frame_geometry` span seam into crop input only, while the tracked
  trajectory stays erased because `not_in_frame` means the runner is absent.
  Analyze therefore reports Encode's output crop policy without treating an
  edge anchor as runner geometry or confidence.

- **Unused experimental crop overrides are removed** (`track_runner/tr_crop.py` and
  `docs/ENCODE_DESIGN.md`): the unreferenced center-lock, fixed-height, and slow-size override
  helpers and their dispatcher accepted no current configuration keys and had no production or test
  caller. The active `direct_center`, `smooth`, and `dolly` crop paths, containment, and NIF handling
  remain unchanged.

- **Superseded seed-derivative experiment**: the temporary
  `seed_tangents.py` field and its generated-derivative tests were removed on
  2026-08-21. Human torso boxes provide anchors, not measured derivatives; the
  current pair-local model is recorded in the newer entry above.

- **Solve now sizes automatic worker pools from declared memory terms**
  (`track_runner/solver_workers.py`, `track_runner/modes/shared.py`,
  `track_runner/modes/solve.py`, and `tests/test_solver_worker_budget.py`): the
  solve path combines current available memory, a measured driver RSS baseline,
  a shape-derived per-worker image budget, and one budget-sized reserve before
  selecting up to the existing half-CPU target. The worker term is derived from
  the two 512 MiB image-store caps, existing rolling-frame/cache caps, and a
  named source-level ledger for simultaneous residual/DoG arrays; it has no
  machine-specific byte threshold. The combined BGR/float32 frame cache now
  enforces its 40-entry cap after every neighbor insertion. Raw per-ROI
  residual/DoG cache entries now evict by accounted byte size instead of
  growing with interval length.
  `--workers` remains an exact explicit override, the reserve is one
  controlled-allocation window rather than a claim about decoder/runtime
  memory, and existing ordinary RSS/cache telemetry remains diagnostic.

- **M7 automatically adopts the offline virtual-dolly crop path**
  (`track_runner/tr_crop.py`, `track_runner/track_runner.config.yaml`,
  `track_runner/modes/analyze.py`, and `track_runner/modes/encode.py`): the
  generated in-memory cases cover containment, convergence, and smooth
  fallback. The shipped configuration selects `dolly` as the default; explicit
  configurations retain their selected mode and non-convergence still falls
  back to `smooth`.

- **Permanent tests use generated in-memory inputs or `tmp_path` only**
  (`tests/TESTS_README.md` and active-plan verification): the retired
  `tests/fixtures/` data directory is not part of the test layout. Repository
  tests do not retain runner imagery or seed artifacts.

- **Target consumes only complete current interval scores**
  (`track_runner/modes/shared.py` and `track_runner/modes/target.py`): target
  no longer filters and displays a partial stale score set. The storage loader
  rejects incompatible or incomplete data, directing the user to a fresh
  solve; target imports its shared mode boundary module-qualified.

- **Seed-schema tests retain behavior rather than storage snapshots**
  (`tests/test_tr_seed_schema_v3.py`): removed field-inventory and byte-equality
  checks. The retained cases cover canonical round-trip identity, `not_in_frame`,
  unknown-field removal, obsolete-header rejection, and approximate confidence.

- **Configuration resolution documents its validated current contract**
  (`track_runner/tr_config.py`): `resolve_config()` returns the selected
  already-validated current configuration; it does not merge or defer
  validation to an unrelated caller.

- **Fast tests keep direct behavior coverage only**
  (`tests/test_tr_frame_reader.py`, `tests/test_tr_target_mode.py`, and
  `tests/test_trajectory_confidence_consumers.py`): removed overlapping
  resolution examples, an aggregate path-shape test, and a cross-layer
  pseudo-E2E. Smaller owner and consumer tests retain the durable contracts.

- **Run identity is explicit at every artifact write**
  (`track_runner/modes/`, `track_runner/modes/shared.py`, and
  `track_runner/cli.py`): removed the mutable shared video-identity global.
  Mode entry points now receive the current source identity, seed deduplication
  preserves its stored identity, and solve writes to its passed artifact path.
  Removed unsupported-schema deletion helpers, obsolete `VideoReader`
  terminology, and the unused Pillow dependency.

- **Private-video evidence probes are no longer permanent tests** (`tests/e2e/`,
  `track_runner/camera_motion.py`, and `pip_requirements.txt`): the newly added
  M2, M3, M4, M6, M7, and seed-truth harnesses depended on videos and mounted
  artifacts that are not part of the repository, so they and their harness-only
  unit tests were removed. The M4-only lock helper, isolated measurement-motion
  API, and `psutil` dependency were removed with them. Frozen reports, images,
  and `/private/tmp` receipts remain explicitly historical local evidence; they
  are not advertised as reproducible repository tests. Portable deterministic
  production tests remain the permanent validation boundary. The older walker
  baseline, walker A/B, Jason-only ROI, and heat-movie smoke E2Es were removed
  for the same reason; the nominally synthetic heat smoke still depended on
  mutable host scratch state. The portable product regression set passes 113
  tests, and the typing/lint/ASCII/indentation/Markdown/import bundle passes
  1,521 checks. At this historical snapshot, full collection had no dangling
  harness imports and passed 4,461 tests before the later source-ownership
  maintenance work.

- **Dolly crop rasterization preserves the solved center**
  (`track_runner/tr_crop.py` and `tests/test_tr_crop_dolly.py`):
  the integer crop size is now chosen before its integer origin is placed around
  the solved float center. The former independent origin/size rounding could add
  almost 0.75 pixel of center displacement after a valid whole-path solve.
  Portable behavior tests cover center rasterization, containment, bounded
  convergence, and safe fallback; no retained runner media or numeric fixture is
  required. JPEG samples and a complete private corpus are not completion
  requirements.

- **Jason is permanently excluded from prospective corpus work by user direction**
  (`data/outdoor_corpus.txt` and local evidence policy): future walker, M2, M3,
  seed-truth, and M4 selection reads omit
  `Jason-3200m-sectionals-IMG_4005.mkv`. Historical receipts remain historical
  and are not rewritten. The obsolete Jason-only degenerate-ROI reproducer is
  removed because portable unit tests cover that behavior. The five-video M3 planning
  cardinality is 3,252 eligible pairs spanning 61,270 frames. No remaining
  single video supplies M4's three required physical span buckets, so M4
  selection now fails immediately instead of reopening Jason work.

- **The Stage-4 startup pre-pass no longer decodes an unused interval gap**
  (`track_runner/residual_pre_pass.py`): sparse deterministic startup ROIs at
  the two seed ends now read only their merged neighbor ranges, with one seek
  between separated ranges. Residual keys, float32 values, the plain 512 MiB
  LRU bound, adaptive-ROI fallback, and walker decisions are unchanged; a long
  promoted interval no longer receives an extra full decode pass merely to
  populate its two startup neighborhoods.

- **Ordinary pooled solves report worker memory and cache measurements**
  (`track_runner/solver_workers.py`, `track_runner/solve_queue.py`, and
  `track_runner/interval_solver.py`): Stage 3, Stage 4, and Stage 5 pool
  dispatches emit one parseable line with the driver peak before pool creation,
  peak worker RSS, worker-process and interval counts, summed worker interval
  time, and aggregate pre-pass lookups, misses, miss rate, and evictions. The
  measurements stay out of solved-result artifacts and add no dependency or
  private-video test. Automatic worker count is still CPU-based; these ordinary
  solve measurements supply the missing production evidence path but do not
  close M4 or WP-M5 by themselves.

- **The retired local M4 probe selected one plan-valid real video instead of
  requiring the whole corpus**: its canonical selector chose
  the first corpus video with at least three promoted intervals in each physical
  span bucket and freezes exactly those nine rows.
  Requiring representatives from every candidate-bearing video was not an M4
  exit criterion and no longer lets an unrelated source block the measurement.
  After the user-directed permanent Jason exclusion, no remaining video has the
  required bucket capacity, so selection fails loud and no RSS or wall-time
  receipt is claimed.

- **Solve, analyze, encode, and crop share the confidence-owner boundary**
  (`track_runner/trajectory_confidence.py`, `track_runner/interval_solver.py`,
  and `track_runner/tr_crop.py`): one owner function now applies raw FWD/BWD
  confidence to every reconstructed trajectory, including cache-hit solve paths
  whose stored geometry carries no `conf`. Seed anchoring and whole-path dolly
  crop require that value directly instead of inventing `0.5` or maximum
  confidence when the owner step is missing. The reconstruction step is now a
  shared solve/analyze/encode helper, and an inline six-frame persisted-style
  test proves stale blended confidence is replaced with the raw-pass owner
  value at scoring and both crop-mode adapters.

- **C3 repairs fingerprint-matched cached blend endpoints before reuse**
  (`track_runner/interval_solver.py`): a cache hit can contain geometry written
  before endpoint stamping existed even though its fingerprint still matches the
  current human seeds. Solve now restores visible/partial start and end geometry,
  confidence, and seed status before completion callbacks or trajectory stitching;
  fresh Hermite and walker results retain the same pre-persistence stamp. The
  read-only six-video audit
  `/private/tmp/c3-seed-truth-corpus-v2-20260820.json` covers all 4,132 current
  seed pairs and 8,126 visible/partial endpoint references. It found 262 stored
  endpoints differing beyond schema-v10 pixel snapping, repaired 6,085 exact
  float endpoint states in memory, and finished with zero geometry or metadata
  mismatches. The audit did not decode video, run Stage 1, solve intervals, or
  rewrite corpus artifacts.

- **Velocity-model test hygiene is nonbehavioral**
  (`tests/test_tr_velocity_model.py`): adds return annotations to its test
  functions and fixture helper, and corrects two continuation-line leading
  spaces; no test logic or expected values change.

- **M3 grounds synthetic winner selection in the production heat seam**
  (`tests/test_blend_commitment_ground_truth.py`): the WP-T2 ground-truth
  lattice injects only a known residual field and reader geometry, then verifies
  that the real canonical DoG and shared in-box heat evaluator commits the runner
  in both FWD and BWD pass orderings. The former fixed-evidence test is removed.

- **M3 scopes transition feasibility to committed disagreement runs**
  (`track_runner/blend_commitment.py`): the C2 step cap now measures only the
  entry, internal, and exit edges touched by an evidence-backed commitment run.
  Unrelated baseline geometry remains unchanged and does not make that run
  infeasible; the public whole-path maximum remains available for evidence
  reporting. Unavailable-evidence runs retain their explicit baseline outcome.

- **A local M6 probe combined mixed 4K scrubbing with offscreen FrameView
  rendering**: the retired probe retained separate
  forward-render and QCore mixed-scrub scopes, while the new combined receipt
  requires a visible, non-null initial and final pixmap, a painted final viewport
  pixel, unchanged transform and pan, newest-only final delivery, mailbox
  coalescing, GUI heartbeat, worker-only reader lifetime, and joined teardown.
  The fresh 3840x2160 receipt
  `/private/tmp/track-runner-ui-4k-render-bidir-20260820b.json` passes with 60
  requests (35 forward and 24 backward steps), one decoded intermediate request,
  a 20.740-ms maximum heartbeat gap, and final frame 1116 rendered in place.
  This is automated offscreen QWidget/render/state evidence only, not manual visual or
  user-interaction proof; it does not establish manual visual usability. M6 status remains
  governed solely by its stated exit criteria.

- **M6 declares pytest-qt's import-module alias**
  (`tests/test_import_requirements.py`): the import-requirements gate now maps
  `pytestqt` to the declared `pytest-qt` developer dependency, retaining the
  typed `pytestqt.qtbot.QtBot` annotations in the annotation-session and
  FrameView UI tests.

- **M3 uses project-qualified commitment-policy imports**
  (`track_runner/interval_solver.py`, `tests/test_seed_truth_stamping.py`,
  `tools/blob_walk_v2/walk_driver.py`, `tests/conftest.py`, and
  `tools/blob_walk_v2/walk_paths.py`, and `track_runner/track_runner.py`): all
  M3 commitment-policy consumers now import `track_runner.blend_commitment`;
  the test and walker bootstraps keep the project root ahead of its legacy
  launcher directory, while the legacy launcher supports qualified project
  submodules without loading its CLI/UI graph. This aligns the
  import-requirements gate without changing policy selection.

- **M6 UI tests share one offscreen QApplication harness**
  (`tests/conftest.py`, `tests/test_frame_source.py`, and
  `tests/test_tr_frame_view.py`): collection chooses `offscreen` only when no
  platform was supplied, and the FrameSource signal tests reuse or create a
  `QApplication` rather than an incompatible earlier `QCoreApplication`.
  Focused FrameSource, FrameView, and heat-overlay suites can run together
  without a Qt abort.

- **WP-T5 makes UI predictions explicit SOURCE boxes**
  (`track_runner/modes/shared.py`, `track_runner/ui/base_controller.py`,
  `track_runner/ui/seed_controller.py`, `track_runner/ui/target_controller.py`,
  `track_runner/ui/edit_controller.py`, and `track_runner/seed_editor.py`): the
  persisted torso-box dicts are wrapped at the prediction boundary as
  `common_tools.coord_space.SourceBox` values. Overlay, heat, auto-seed, and
  consensus consumers use the typed geometry; Seed/Target/Edit readers are
  `FrameSource` and callbacks use `collections.abc.Callable` signatures. NPZ storage,
  coordinate conversion, `SCHEMA_VERSION`, and the C10 unified-schema contract are
  unchanged. The reconciled 40-test focused receipt, global pyflakes (240 tests),
  seven exact typing-gate nodes, and staged/unstaged diff checks pass; independent
  final re-review accepted the boundary and its fail-loud `ProcessedBox` guards.

- **T5 prediction assembly is split below the source-file limit**
  (`track_runner/modes/predictions.py` and `track_runner/modes/shared.py`):
  annotation prediction construction now has a UI-independent module while
  `shared.py` retains its existing private compatibility seams for CLI modes.

- **M6 has accepted mixed-direction 4K scrub evidence**
  (`/private/tmp/track-runner-ui-4k-bidir-scrub-20260820.json`): the production
  `FrameSource` received 60 bidirectional/random requests with 59 non-adjacent
  transitions (35 forward, 24 backward). Of 58 intermediate requested frames,
  only frame 1115 was decoded, as directional prefetch; delivery remained
  newest-only through final frame 1116. Reader open, reads, and close stayed on one
  worker thread; it opened and closed once, stopped cleanly, and the 16-ms heartbeat
  peaked at 16.804 ms under the 100-ms limit. This is automated offscreen `QCore`
  evidence only, not a `QWidget` render or manual-visual claim. The separate earlier
  offscreen QWidget render-state receipt remains limited to its recorded 60-forward
  run and does not supply bidirectional coverage. T5 remains accepted and M6 remains
  open for its separate exit criteria.

- **Changelog rotation archives older day blocks**
  (`docs/CHANGELOG-2026-06d.md`): the active changelog keeps its two most
  recent day blocks and remains within the authored-source line limit.

### Fixes and Maintenance

- **Durable documentation now matches current mode, confidence, and crop ownership**
  (`docs/TRACK_RUNNER_V3_SPEC.md`, `docs/TRACK_RUNNER_YAML_CONFIG.md`,
  `docs/CODE_ARCHITECTURE.md`, `docs/FILE_STRUCTURE.md`, and
  `docs/modes/ENCODE.md`): current references identify `modes/` as the
  subcommand-body owner, list the confidence/commitment and UI modules, remove
  unsupported `crop_mode: smart`, and state that `dolly` is the default with a
  bounded `smooth` fallback. They replace the retired LK/confidence-decay/Dice
  winner description with raw FWD/BWD confidence plus evidence-backed
  run-level commitment. `not_in_frame` remains literal absent runner truth;
  any edge anchor is encode-only crop intent, never interpolated tracking
  geometry or persisted runner state.

- **The seven-goal trajectory and interaction plan is now complete through portable evidence**
  ([interaction_shell_and_trajectory_truth.md](archive/interaction_shell_and_trajectory_truth.md)
  and [progress_handoff_2026-08-20.md](active_plans/reports/progress_handoff_2026-08-20.md)):
  M1 accepts C3 cache-reuse restamping; M2/M3/M6 pass the portable audit; M4 accepts bounded
  caches, a source-ledger worker budget, and the pure sizing policy; M5 accepts the generated
  known-derivative shared-tangent decision; M7 selects `dolly` as the crop default; and M8 accepts
  real parser plus `cli.main()` dispatch. These outcomes retain the Stage-1, Jason, private-media,
  manual, network, timing, pixel/byte, and Git-index boundaries. The plan is left unstaged for the
  user's normal workflow. The pre-audit completion snapshot passed 272 focused product tests and
  1,332 hygiene checks. The audit repair pass later completed a 3,979-pass
  health run before the source-ownership maintenance work. The completed
  refactor subsequently cleared the exclusive source-line limit and the
  portable suite passes without a collection-count gate.

- **Whole-working-tree typing maintenance has an independent AST receipt**: 238 Python files have
  zero missing argument/return annotations and zero `typing` imports. This Git-independent check
  does not rely on tracked-file counts and remains separate from the seven product milestones.

- **Import-requirements discovery supports the working tree without staging**: repo-local modules
  and packages are recognized from the working tree while tracked scan scope is retained and
  undeclared external imports are still rejected. Independent review accepts 603 focused checks,
  including symlink and outside-root containment.

- **Markdown-link discovery supports live local targets without weakening boundaries**: Git-scoped
  source collection remains intact, existing untracked local targets are accepted, and external
  file/directory symlink escapes are rejected. Independent review accepts 156 Markdown tests and
  400 typing/pyflakes checks.

- **Typing gate repairs annotations without runtime changes** (21 already-changed
  plan files): adds 245 missing annotations and fixes one `typing.Callable` import
  violation across five solver/scoring tests (55), six walker tests (79), three
  encode/refine/target tests (76), and seven tools/production files (35, including
  `Callable`). Independent reviews accepted the annotation-only repair; its exact
  owned typing nodes, focused tests, and pyflakes checks pass.

- **Repository-wide function typing is complete without behavior changes** (69 files):
  the follow-up annotation-only pass repairs the remaining 735 violations reported by
  `tests/test_function_typing.py`, following the earlier targeted repair of 245. The
  typing gate now passes all 241 checks; pyflakes passes 241 checks, indentation passes
  241 checks, and ASCII compliance passes 426 checks. Its pre-audit full-suite snapshot had only
  source-line-limit findings; later audit repairs used their own focused
  receipts. The completed source-ownership refactor subsequently cleared the
  exclusive limit and does not reopen M6 behavior.

### Decisions and Failures

- **The active plan keeps its original goals and scope but removes external and
  arbitrary completion gates**
  ([interaction_shell_and_trajectory_truth.md](archive/interaction_shell_and_trajectory_truth.md)
  and [progress_handoff_2026-08-20.md](active_plans/reports/progress_handoff_2026-08-20.md)):
  milestones now close on repository-owned behavioral/property tests using inline
  inputs, generated transitions, fake readers, deterministic residual fields,
  `tmp_path`. Private videos, historical snapshots,
  manual visual review, user approvals, Git staging, exact pixels/bytes for
  output-changing improvements, and machine-specific timing thresholds are not
  gates. The later source-ownership maintenance work cleared the exclusive
  line limit without reopening a product milestone. Exact equality remains where it is the
  contract, especially C3 seed geometry. M4 worker sizing and M7 default adoption
  have explicit agent-owned decision procedures, so the manager and fresh subagents
  can finish the plan without waiting for the user.

- **The historical M3 v5 receipt is not a completion dependency**
  ([interaction_shell_and_trajectory_truth.md](archive/interaction_shell_and_trajectory_truth.md)
  and [progress_handoff_2026-08-20.md](active_plans/reports/progress_handoff_2026-08-20.md)):
  the rejected schema-1 receipt cannot be replayed and canonical motion artifacts
  are absent, but prospective recovery or Stage-1 recomputation is unnecessary.
  M3 completion now uses constructed known-runner residual cases and transition
  properties through production policy code. Stage 1 remains a non-goal.

- **Unapproved Stage-1 experiment reverted** (`track_runner/camera_motion.py`,
  `track_runner/scene_coords.py`, and `docs/TR_CAMERA_MOTION_METHOD.md`): the
  affine recurrence, terminal-tail policy, cache-label change, and rebuild
  harness were outside the active plan's explicit camera-motion boundary and
  have been removed. The six experimental canonical motion artifacts were
  moved recoverably, not byte-restored, to
  `/private/tmp/track-runner-reverted-stage1-artifacts-20260820/`:
  `Conant-4x400-2026_April_15.track_runner.camera_motion.npz`,
  `IMG_3823.track_runner.camera_motion.npz`,
  `IMG_3830.track_runner.camera_motion.npz`,
  `Jason-3200m-sectionals-IMG_4005.track_runner.camera_motion.npz`,
  `Lyra-Hersey-800m-IMG_3882.track_runner.camera_motion.npz`, and
  `Lyra-Wheeling-IMG_3912.track_runner.camera_motion.npz`. Their canonical
  paths are absent, so the restored model recomputes them on demand. No M3
  evidence was accepted from the experiment.

- **Interruption handoff records the autonomous restart path**
  ([progress_handoff_2026-08-20.md](active_plans/reports/progress_handoff_2026-08-20.md)):
  the handoff separates implementation state from optional historical diagnostics,
  identifies M4 worker sizing and M7 rule-based adoption as the highest-impact
  remaining work, and records the Stage-1, Jason, private-video, no-staging, and
  no-human-gate boundaries.

- **M3 full-corpus receipt is rejected, not accepted evidence**
  (`/private/tmp/m3-corpus-v5-20260820.json` and
  [interaction_shell_and_trajectory_truth.md](archive/interaction_shell_and_trajectory_truth.md)):
  the audited run selected all 3,885 valid non-pre-race intervals from the six-video
  `data/outdoor_corpus.txt` corpus and retained 247 pre-race exclusions. It reports
  3,795 completed outcomes, 90 infeasible outcomes, and zero unavailable or error
  outcomes. The infeasible outcomes reject the receipt, so no committed-run overlays
  were rendered, filed, or accepted. Its missing provenance is repaired only in a
  new, unrun runner and cannot be backfilled into this receipt. The bin-2
  evidence-runner coordinate conversion is repaired, while the dominant Lyra
  discrete camera-scale defect remains external to M3. This receipt stays rejected
  historical diagnostic material and is not needed by the portable M3 gate.

## 2026-08-19

### Behavior or Interface Changes

- **M6 frame scrubbing coalesces queued decode requests**
  (`track_runner/ui/frame_source.py` and `tests/test_frame_source.py`): a
  thread-safe latest-request mailbox retains only the newest requested frame
  before worker decode, preserving request-id delivery filtering, directional
  cache/prefetch behavior, reader-only worker ownership, and heat-overlay stale
  guards. Prefetch atomically chooses either a pending newest request or its
  directional candidate, so a request present at the selection point wins without
  an intermediate read. Closing discards the mailbox before queuing worker shutdown, so a
  superseded scrub burst cannot delay session teardown.

- **M6 documentation now matches the asynchronous UI boundary**
  (`track_runner/ui/base_controller.py`, `track_runner/ui/workspace.py`, and
  `docs/archive/interaction_shell_and_trajectory_truth.md`): annotation
  controller documentation identifies `FrameSource`, not direct `FrameReader`
  access; heat status is a persistent non-modal label; and Patch 9 names WP-U5
  through WP-U7 as accepted. The typed prediction boundary was outside that Patch 9
  documentation receipt and was accepted later. No runtime behavior or work-package
  status changed in this entry.

- **M3 encode developer overlays now expose pass-commitment metadata**
  (`track_runner/modes/encode.py`, `track_runner/encoder.py`): disagreement
  frames carry the run-level commitment direction and linear transition alpha
  into the ephemeral debug payload and render `commit:fwd|bwd <percent>` (or
  `commit:unavailable`). Review-tier overlays and persisted trajectory schema
  remain unchanged.

- **M2 now has a complete controlled current-input scoring receipt, without a
  historical-recovery claim** (`tests/e2e/e2e_m2_promotion_attribution.py` and
  `/private/tmp/m2-counterfactual-run-v3b-20260820/summary.json`): the opt-in
  runner archives `283bda5`, applies only the source-hashed C3/M1
  truth-stamping patch, then runs all 3,885 valid non-pre-race Stage-3 Hermite
  intervals (`blob_pass=False`) from all six corpus videos; 247 persisted
  pre-race rows are retained in an explicit exclusion ledger. It saves
  FWD/BWD/blended geometry arrays and hashes, then applies base Dice and the
  staged `trajectory_confidence` owner to those exact paths. Secondary inputs
  match in both arms. Dice promotes 320 intervals, center-width agreement
  promotes 263, and all 73 tier changes are recorded for recomputation. This
  is a controlled current-input counterfactual, not evidence that the current
  inputs reproduce historical pre-M2 artifacts.

- **M3 replaces the private Dice output selector with run-level commitment**
  (`track_runner/interval_solver.py`, `track_runner/blend_commitment.py`, and
  `tests/test_tr_blend_policy_boundary.py`): `blend_paths()` delegates to the
  commitment policy, whose disagreement definition is exclusively
  `trajectory_confidence.frame_disagrees()`. The old private Dice threshold,
  coefficient, and confidence-winner branch are deleted. The boundary test proves
  that geometric overlap cannot select the output or change the owner-defined
  scoring agreement.

- **M3 production-field pilot and committed-run overlays are filed**
  ([m3_blend_commitment_pilot.md](active_plans/reports/m3_blend_commitment_pilot.md)
  and `docs/active_plans/reports/m3_blend_commitment_overlays/`): the frozen
  three-interval receipt has three FWD commitments, one tie, and no unavailable,
  error, or infeasible outcome. Its maximum center step falls from 0.9883623025 to
  0.8052584366 torso widths, while canonical heat rises from 27795.469809 to
  30117.390607; it uses 421 decode reads in 60.339451 seconds. Twenty-four exact
  receipt-derived PNG overlays are filed without re-running selection or tracking.
  Direction, alpha, and evidence status are live-only review metadata; NPZ reload
  intentionally retains geometry only. This is a bounded pilot, not M3 full-corpus
  exit evidence; the full runner and transition-band sweep remain open.

- **Patch 10 WP-D3 records the negative same-input dolly smoothness sweep**
  (`docs/active_plans/decisions/dolly_crop_assets/m7_dolly_smoothness_sweep.json`):
  the exact IMG_3823 lambda sweep from 0 through 2048 retains each candidate's
  source-fit, rounded-containment, convergence, lag, p95 acceleration, and
  crop-height metrics. Default lambda 20 converges with zero x/y lag but p95
  acceleration 0.150231 TW/frame^2 exceeds direct-center's 0.139754. The best
  containment-valid candidates, lambda 16 and 48, reach 0.143740 but still lose.
  An un-staged channel-specific pin trial did not meet the gate; it has no retained
  raw receipt, so it is rejected and not used as evidence. An exact QP/nonlinear
  solver would exceed the approved banded pin-and-resolve scope and requires a
  new user-approved work package. M7 remains open; neither this evidence nor the
  existing one-clip receipt changes the crop default.
  This historical solver-parameter result predates and is superseded on the
  retained trajectory by the size-first rasterization repair recorded above.

- **Patch 10 WP-D2 settles the bounded containment fixed point and WP-D3 records
  a scoped live converged receipt**
  (`tests/e2e/e2e_dolly_crop_evidence.py`,
  `tests/test_dolly_crop_evidence_metrics.py`, and
  `docs/active_plans/decisions/dolly_crop_assets/`): the opt-in runner now
  reconstructs IMG_3823 from its matching source video, solved artifact, seeds,
  and config, then runs `smooth`, `direct_center`, and `dolly` with only the
  in-memory crop-mode override changed. It writes raw rectangles, cross-correlation
  lag, torso-width-normalized p95 acceleration, crop-height step distributions,
  exact DollyCropReport provenance, trace plots, and fifteen real `cv2` crop
  samples. The 2026-08-20 one-clip result has direct-center lag 0/0 and p95
  acceleration 0.139754 TW/frame^2; the dolly fixed point converges at the
  smallest demonstrated cap of 10 passes (`converged=true`,
  `fallback_used=false`) after center-containment pins propagate. The exact
  in-process IMG_3823 cap sweep records fallback at caps 8 and 9 and convergence
  at cap 10. This is not the complete original corpus, is a one-clip result only,
  changes no crop default, and makes no default recommendation.

- **M6 has an opt-in 4K frame-scrub responsiveness receipt runner and narrow
  execution receipt**
  (`tests/e2e/e2e_ui_4k_scrub.py`): a real fast-read MKV supplied on the command
  line drives production `FrameSource` with rapid timer requests and a 16-ms Qt
  heartbeat, without constructing a widget or changing application behavior. It
  writes an explicit JSON receipt below `/private/tmp` containing reader open/read/
  close thread traces, newest-only delivery evidence, heartbeat criteria, and decode
  thread shutdown status. The 2026-08-20 receipt
  `/private/tmp/track-runner-ui-4k-scrub-20260820.json` uses the valid 3840x2160
  Lyra-Wheeling fast-read source: 60 forward requests yielded only final frame
  16647, while open/read/close remained worker-thread-only and the 16-ms heartbeat
  peaked at 17.695 ms below its 100-ms limit. The decode thread stopped cleanly.
  This is an offscreen `QCore` measurement, not a `QWidget` render or manual visual
  proof; it does not establish bidirectional/random scrub responsiveness or request
  coalescing.

- **M6 now also has a real 4K offscreen QWidget render-state receipt**
  (`tests/e2e/e2e_ui_4k_scrub.py --render`): the 2026-08-20 receipt
  `/private/tmp/track-runner-ui-4k-render-20260820f.json` exercises the real
  3840x2160 Lyra-Wheeling fast-read video through `FrameSource` and `FrameView`.
  The newest-only final frame is 16647; all reader activity remains on the worker
  thread; the maximum Qt heartbeat is 18.056 ms, within the 100-ms limit; and the
  actual `FrameView` viewport contains non-background RGBA `[242, 243, 236, 255]`.
  The view retains transform 1.5 and pan `[1151.333, 1266.667]`; render timing is
  117.598 ms to first render and 229.106 ms to final render. This is automated
  offscreen QWidget/render/state evidence only, not manual visual or user-interaction
  proof, bidirectional/random scrub coverage, or general coverage proof. M6 remains
  open for those separate exit criteria.

- **M6 headless heat-overlay tests now initialize Qt consistently**
  (`tests/test_tr_heat_map_overlay.py`): test collection sets the default
  `QT_QPA_PLATFORM=offscreen` before importing PySide6, while preserving any
  explicit CI platform selection.

- **M4 controlled fast-read rebuild confirms the bin-2 decoder gate is not
  stale-artifact caused**: an isolated production Conant rebuild under
  `/private/tmp/m4-fastread-decode-v2-20260820` completed
  `create_fastread_video` and then failed its own
  `validate_fastread_structural` tail decode at advertised frame 14463. It
  created no v2 decode manifest and no motion, pool, or bin measurement evidence.
  This isolates an OpenCV/probe frame-count-tail incompatibility; the temporary
  receipt is `/private/tmp/m4-prepare-decode-v2.log`. Bin 1 remains the only
  measured receipt. M4 and dependent M5 worker sizing remain open, with no
  substitution, truncation, padding, or inferred bin-2 result.

- **M4 measurement motion preparation preflights terminal decode**
  (`tests/e2e/e2e_prepass_memory_measure.py` and
  `tests/test_m4_measurement_runner.py`): before Stage 1 begins for a selected
  video, `prepare-motion` now reads the final frame advertised by the probe
  through the exact measurement `FrameReader` and bin factor. An unavailable
  terminal frame raises a precise error naming the source path, advertised
  frame count, final index, and bin factor. The measurement runner neither
  truncates nor pads the input; production `FrameReader` behavior and corpus
  contents are unchanged.

- **M4 bin-qualified motion preparation rejects concurrent duplicate runs**
  (`tests/e2e/e2e_prepass_memory_measure.py` and
  `tests/test_m4_measurement_runner.py`): `prepare-motion` now owns an atomic
  lock scoped to its explicit motion-output root and bin factor. A second
  invocation for the same destination fails with the lock path instead of
  starting another Stage-1 pool; distinct bins remain independent. The lock
  records its owner and is removed on both successful and failing preparation.
  A stale record is reclaimed only if it is absent or malformed, or identifies a
  conclusively dead PID on this host; live or indeterminate owners are never
  reclaimed. It affects only measurement artifacts and never writes canonical
  `tr_config`.

- **Patch 6 WP-N1 tangent support window implemented; measurement pending**
  (`track_runner/velocity_model.py`, `track_runner/interval_solver.py`, and
  `tests/test_tr_velocity_model.py`): directional position and log-size tangent
  estimates now take the video fps and select no more than four directional seeds
  within the initial `TANGENT_SUPPORT_WINDOW_S = 2.0` second window. The interval
  solver threads fps into the curve fit. Deterministic tests cover backward and
  forward distant-support exclusion plus the regression, finite-difference, and
  zero-slope degradation ladder. The 2.0-second choice is documented but is not a
  corpus-proven sweep result. All six corpus videos exist locally, but the required
  Hermite-before versus Hermite-after held-out comparison was not run. A Git-frozen
  pre-WP-N1 `velocity_model.py` revision exists, but no frozen post-M1--M4/pre-N1
  checkpoint or valid same-algorithm, same-input held-out comparator exists. The
  walker A/B harness is not a valid substitute because it compares different
  algorithm classes. M5 remains incomplete pending that evidence.

- **Patch 6 WP-N4 raw-prediction propagators unified**
  (`track_runner/velocity_model.py`, `track_runner/interval_solver.py`,
  `track_runner/residual_pre_pass.py`, `track_runner/blob_walk/walk_walker.py`,
  and focused solver/velocity tests): one direction-parameterized raw-prediction
  builder now supplies FWD and BWD Hermite paths, with confidence-decay constants
  defined once. The residual prepass and test doubles use that shared seam. The
  previously documented unused `frame_indices` estimator parameters and image-data
  propagator parameters are absent from the live interfaces and call sites. A fixed
  seed/camera-motion fixture checks the exact pre-collapse IEEE-754 FWD and BWD
  byte streams by SHA-256 digest; focused velocity and walker tests passed.

- **Patch 2 WP-T3 and WP-T4 confidence ownership accepted**
  (`track_runner/trajectory_confidence.py`, `track_runner/interval_solver.py`,
  `track_runner/scoring.py`, `track_runner/cli.py`, `track_runner/analyze_report.py`,
  `track_runner/encode_analysis.py`, `track_runner/regime_classifier.py`,
  `track_runner/review.py`, and `track_runner/tr_crop.py`):
  `trajectory_confidence` solely owns raw FWD/BWD center agreement, normalized by
  mean torso width. Solver and scoring use that owner; blended-output confidence no
  longer passes through distance decay; the duplicate stored/reporting Dice and
  confidence-classify mechanisms are gone. Analyze and encode consumers are migrated
  and report this value as center agreement. The [promotion-attribution evidence record](active_plans/reports/m2_promotion_attribution_unrun.md)
  now has a complete controlled current-input v3 corpus receipt: 3,885 valid
  non-pre-race Stage-3 intervals, 247 explicit pre-race exclusions, 320/263
  Dice/owner promotions, and 73 reproducible tier changes. It remains unable to
  recover a historical pre-M2 baseline because no historical input manifest exists;
  the current-input receipt must not be described as historical recovery. M2's
  controlled attribution gate is closed, while M3 and M5 retain their own open
  exit criteria.

- **Patch 7 WP-U1 and WP-U2 annotation-session foundation accepted**
  (`track_runner/ui/session.py`, `track_runner/ui/workspace.py`,
  `track_runner/seeding.py`, `track_runner/seed_editor.py`,
  `track_runner/ui/edit_controller.py`, `track_runner/seed_color.py`, and
  `track_runner/ui/seed_controller.py`): the process-lifetime
  `AnnotationSession` owns controller lifecycle, video context, reader, seed
  store, and prediction store. Seed, Target, and Edit now swap controllers in
  one process. Edit add-seed return and pending-deletion preservation route
  through that session, and the obsolete workspace construction-order guards
  are removed. `build_seed_dict` is now the public factory with an explicit
  status; its module-scope import and consolidated status resolution replace
  the deferred import and duplicate Seed-controller branches, while Edit
  callers use the public API. Focused verification:
  `pytest tests/test_annotation_session.py` (6 passed).
  This accepts only WP-U1 and WP-U2; WP-U3 through WP-U7 remain pending, so M6
  is not complete.

- **Patch 8 WP-U3 and WP-U4 off-thread frame and heat access accepted**
  (`track_runner/ui/frame_source.py`, `track_runner/ui/session.py`,
  `track_runner/ui/seed_controller.py`, `track_runner/ui/edit_controller.py`,
  `track_runner/ui/target_controller.py`, `track_runner/ui/heat_map_overlay.py`,
  `track_runner/ui/workspace.py`, and `track_runner/ui/frame_view.py`):
  `FrameSource` is the only UI-side reader and keeps all reader access on its worker
  thread. Seed, Edit, and Target issue asynchronous cache requests and discard stale
  results; session metadata bridges their requests to the source. Heat computation
  is queued with the same current-result guard. The blocking heat busy dialog and
  frame-view disable gate are removed, and Target now reports its own mode label.
  This accepts only WP-U3 and WP-U4; WP-U5 through WP-U7 and the typed prediction
  boundary were outside this receipt, so M6 is not complete.

- **Patch 9 WP-U5 through WP-U7 annotation usability accepted**
  (`track_runner/ui/keymap.py`, `track_runner/ui/base_controller.py`,
  `track_runner/ui/seed_controller.py`, `track_runner/ui/edit_controller.py`,
  `track_runner/ui/target_controller.py`, `track_runner/ui/frame_view.py`,
  `track_runner/ui/status_presenter.py`, `track_runner/ui/workspace.py`,
  `tools/refresh_mode_docs.py`, and `docs/TRACK_RUNNER_KEYBINDINGS.md`): one
  declarative table now drives exact key/modifier dispatch, current-mode hints, F1
  help, and the generated keybinding reference; the generator has a check-only
  drift gate. `FrameView` wraps native BGR data, retains its pixmap item, and leaves
  the scene rect intact on same-size frame updates, preserving a pan across frame
  advances. Annotation actions route feedback through the persistent GUI status
  presentation rather than stdout-only messages. This accepts WP-U5, WP-U6, and
  WP-U7 only. The typed prediction boundary was outside this receipt; M6 remains
  pending its stated exit criteria.

- **Patch 1 seed truth is restamped after blending**
  (`track_runner/interval_solver.py`): `visible` and `partial` human seed boxes
  now overwrite blended or fallback geometry exactly (`cx`, `cy`, `w`, and `h`),
  alongside their confidence and seed status. `approximate` and `obstructed`
  seeds retain confidence-only treatment, and `not_in_frame` erasure is
  unchanged. Analyze and encode callers now use the same seed-truth restamp.
  Focused verification: `pytest tests/test_seed_truth_stamping.py` (1 passed).
  The corpus seed-artifact sweep remains open because the required artifacts are
  unavailable. This historical patch record predates the later portable
  source-ownership completion receipt.

- **Patch 4 WP-M1 pre-pass storage is byte-bounded**
  (`track_runner/residual_pre_pass.py`): rolling full-frame gray data now stays
  `uint8`; pre-pass results use a plain LRU bounded to 512 MiB by stored bytes.
  Eviction replacement accounting handles overwritten keys, an oversize result
  is not retained, and a cache miss uses the legacy reader fallback. Walker-active
  prepass caches now contain the exact deterministic FWD/BWD bootstrap and
  initial-window ROIs; cached float32 residuals are lossless and preserve legacy
  values. Adaptive decision-dependent walker ROIs intentionally miss and use the
  legacy fallback, so the cache counters now measure real consumer lookup/miss
  behavior.
  The earlier 11-row bin-1 receipt remains valid historical partial evidence.
  Future same-input bin-1/bin-2 runs require one eligible non-Jason video rather
  than representatives from the whole corpus. None currently qualifies. The
  startup pre-pass read schedule for the historical 11 rows falls from 1,034 to
  331 reads (703 avoided, 68.0%) without changing residual or walker semantics;
  no replacement real wall-time or RSS run has been claimed.
  The [memory-budget measurement receipt](active_plans/reports/m4_prepass_memory_budget_report.md)
  records an actual 11/11 bin-1 corpus run, including its 127401984 B
  zero-worker parent baseline, 1395294208 B maximum worker RSS, per-bucket
  cache/wall values, and a bin-1-only seven-worker calculation with 2 GiB
  headroom. The earlier bin-2 missing-artifact cause is superseded by an isolated
  measurement-only Stage-1 path that never writes canonical `tr_config`. Current
  permanent Jason exclusion retires the attempted Jason-only manifest. There is
  still no replacement bin-2 pool, output, or inferred figure. M4's memory-budget
  exit and the dependent M5 worker-sizing exit remain open.

- **Patch 11 WP-O1 and WP-O2 CLI organization and dead-code cleanup implemented
  and independently reviewed; milestone exit pending M6**
  (`track_runner/cli.py`, `track_runner/modes/`, `track_runner/scoring.py`,
  `track_runner/interval_solver.py`, `track_runner/review.py`, and
  `track_runner/velocity_model.py`): each CLI mode body now lives in its own
  `track_runner.modes` module while `cli.py` remains the argument-wiring and
  dispatch shell; the argparse help surface is unchanged. The never-produced
  `occlusion_risk` chain, the unused confidence classifier, and propagator
  arguments left unused by the shared raw-prediction work are removed.

- **Patch 10 WP-D1 and WP-D2 offline dolly implementation accepted; WP-D3
  evidence UNRUN** (`track_runner/dolly_path.py`, `track_runner/tr_crop.py`,
  `track_runner/modes/analyze.py`, and `track_runner/modes/encode.py`): the
  pure whole-path solver minimizes weighted tracking and acceleration costs for
  center position and log crop size. `crop_mode: dolly` is explicit opt-in;
  the shipped config default remains `direct_center`, while an omitted
  `crop_mode` key falls back to `smooth` for backward compatibility. Containment
  uses bounded pin-and-re-solve with
  a reported smooth fallback, and Analyze/Encode retain per-clip dolly
  provenance. The [WP-D3 evidence record](active_plans/decisions/dolly_crop_evidence.md)
  has zero evaluated clips: the required source corpus, matching solved inputs,
  configuration, harness, and comparable dolly assets are absent. It makes no
  crop-default recommendation. M7 is not complete until valid same-clip,
  three-mode evidence exists; only then is a user decision on the default
  appropriate.

### Developer Tests and Notes

- **Patch 4 WP-M1 focused cache evidence passed**: tests cover replacement
  accounting, oversize-result handling, the legacy-reader fallback, and cache
  lookup/miss instrumentation. Walker-active cache entries are the deterministic
  FWD/BWD bootstrap and initial-window ROIs; decision-dependent walker ROIs use
  the legacy fallback by design. Cached float32 residuals remain lossless. The
  [WP-M2 memory-budget report](active_plans/reports/m4_prepass_memory_budget_report.md)
  records the completed 11/11 bin-1 measurement and its remaining bin-2 decoder
  gate. The earlier missing-artifact cause is superseded by a measurement-only
  isolated Stage-1 path; canonical `tr_config` remains untouched. The all-video
  selection is now superseded, so Conant and Lyra-Hersey no longer block M4.
  Jason alone covers all three span buckets, but its terminal preflight still
  rejects advertised final frame 36043. No replacement bin-2 pool, output, RSS,
  cache-counter, wall-time, or worker-sizing result exists. M4 and dependent M5
  remain open.

- **Patch 8 focused verification passed**: `pytest tests/test_frame_source.py`,
  `pytest tests/test_tr_seed_controller.py`, `pytest tests/test_tr_target_controller.py`,
  and `pytest tests/test_tr_frame_view.py` passed, as did the focused pyflakes
  check. The Qt heat test suite aborts during host startup, so it supplies no test
  result. Manual 4K scrub-responsiveness verification remains unperformed.

- **Patch 9 focused verification passed**: keymap lookup, generated-document drift,
  BGR display, persistent pixmap, pan-margin, and GUI-feedback tests passed, and
  `tools/refresh_mode_docs.py --check-keybindings` reported the generated reference
  current. The frame-view and status-presenter Qt modules have separate-pass receipts;
  the combined Qt run aborts during application startup and is not a combined-suite
  receipt. The standalone heat-overlay suite also aborts during host Qt application
  startup, so it supplies no receipt. No 4K before/after display timing or manual
  pan-across-frame observation is recorded.

- **Patch 11 focused verification passed**: `tools/dump_cli_help.py` produced no
  help-surface diff across the extraction, and
  `pytest tests/test_pyflakes_code_lint.py` passed. Independent review confirmed
  the removed classifier and `occlusion_risk` chain have no live callers.

- **Patch 10 focused verification passed**: `pytest tests/test_dolly_path.py`
  validates the pure banded solver, including a nonlinear dense-reference
  comparison, scale behavior, zero-smoothness anchors, and `not_in_frame`
  handling. `pytest tests/test_tr_crop_dolly.py` validates containment,
  bounded fallback, explicit dolly provenance, and unchanged default/explicit
  `smooth` behavior. These receipts do not substitute for WP-D3 corpus evidence.
