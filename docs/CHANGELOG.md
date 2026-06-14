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

## 2026-06-12

### Additions and New Features

- **Fast-read video Patch 5 (WP-P5 benchmark tool)**: new
  `tools/benchmark_fastread_video.py` measures scattered-seek median/p95,
  sequential ms/frame, and file sizes for an original 4K HEVC video vs its
  `.fastread.mkv` counterpart. Opens a fresh `FrameReader` per video to
  isolate seek costs; uses a fixed RNG seed so both videos sample the same
  frame set. Reports the scatter-seek speedup ratio; exits non-zero if the
  fast-read median is less than 3x faster than the original (the gate from
  the WP-P5 acceptance criteria). `--report-only` (`-r`) prints numbers
  without triggering the gate. Encode time is not re-measured (transcode is
  prepare's job); the script prints a note instead. Constants
  `N_SCATTER_SEEKS=40`, `SEQ_RUN_LENGTH=60`, `SEQ_START_OFFSET=200` are
  module-level with inline comments. Script has shebang + executable bit;
  lives in `tools/` (not `tests/`) so it is never collected by pytest.
  Lint-clean: `pytest tests/test_pyflakes_code_lint.py` 192 passed;
  `pytest tests/test_shebangs.py` 367 passed.

- **Fast-read video Patch 4b (working-mode routing + analyze provenance)**:
  completed the second half of WP-P4. The interactive working modes now decode
  from `video_context.working_decode.path` (the fast-read working video when
  present and valid, otherwise the original). `seeding.collect_seeds` and
  `seeding.collect_seeds_at_frames` and `seed_editor.edit_seeds` renamed their
  `video_path` parameter to `decode_video_path` and probe/open the
  FrameReader on that decode path; the UI controllers already receive an
  already-opened reader and never re-derive a path, so no controller change was
  needed. `cli._mode_seed`, `_mode_edit`, `_mode_target`, `_mode_setup`, and
  `_mode_analyze` gained a `video_context` parameter (threaded from the single
  `resolve_video_context` call in `main()`); each prints the source/decode
  banner via `fastread_video.print_video_routing_banner`, and the decode calls
  pass `video_context.working_decode.path`. Setup still writes config/state off
  the original: `config_path` is keyed off the original and the banner only
  reports what would decode; `run_setup` was not given the decode path. Analyze
  reports now carry two new fields named exactly `canonical_source` (original
  basename) and `decode_source` (fast-read basename, or the literal `original`
  when no fast-read is in use) across all three surfaces: the console report
  (`encode_analysis.format_analysis_report`), the YAML report
  (`encode_analysis.write_analysis_yaml`), and the HTML report
  (`analyze_report.write_analyze_report` -> new "Video source" section). State
  and identity paths remain keyed off the original everywhere; the fast-read
  video is never recorded as the configured source. New behavioral tests in
  `tests/test_fastread_video.py` cover: seed/config artifact paths still produce
  original-stem names under a valid fast-read VideoContext, analyze label
  derivation (fast-read vs no-fast-read), and the YAML + HTML reports carrying
  `canonical_source`/`decode_source` (monkeypatched, no real video/ffmpeg).
- **AGENTS.md refresh**: rewrote from 13-line stub to 103-line operational pointer
  file; added sections for testing, project overview, modes and workflow (with all
  nine mode quick-reference links), track runner design and contract summary, blob
  walk, config, schema versioning, common agent tasks, git workflow, and developer
  reference; added `docs/modes/PREPARE.md` link for the new prepare fast-read-video
  step. Philosophy/style prose replaced by cross-references into docs/*.md per REPO_STYLE.

- **README.md refresh**: updated first paragraph (pure prose, under 250 chars, no
  repo name, purpose + user + distinctive detail); added `prepare` mode to quick-start
  workflow diagram and command block with optional-but-recommended note for 4K HEVC;
  added `docs/modes/PREPARE.md` link under the "Run it" docs section.

- **Fast-read video Patch 4a (WP-P4a)**: original-vs-fast-read selection
  centralized in one resolver and routed through the solve-side modes. New
  frozen dataclasses `fastread_video.VideoSelection` (`path`, `role`,
  `using_fastread`, `reason`) and `fastread_video.VideoContext`
  (`original_video_path`, `working_decode`, `final_encode`,
  `metadata_identity`). New `fastread_video.resolve_video_context(
  original_video_path)` computes the deterministic fast-read path; absent ->
  `working_decode` is the original (reason `no_fastread_original`); present ->
  validates EXACTLY ONCE via `validate_fastread_structural` (invalid raises with
  the existing path+check+remedy message); valid -> `working_decode` is the
  fast-read (reason `valid_fastread`). `final_encode` and `metadata_identity`
  always select the original (reasons `final_encode_original`,
  `metadata_identity_original`). `track_runner/cli.py` `main()` resolves the
  context once after the `prepare` early-return and before mode dispatch, and
  threads it into `_mode_solve`, `_mode_refine`, and `_mode_encode`. Solve/refine
  decode from `working_decode.path`: `_run_solve` gains a `decode_video_path`
  parameter feeding the Stage-1 camera-motion FrameReader, the Stage-3
  FrameReader, and the worker-pool video path; camera-motion artifact path and
  identity continue to key off the original (`args.input_file`). Worker decode
  path flows through unchanged plumbing with no per-worker re-validation (a valid
  `VideoContext` is the authorization). Encode always reads `final_encode.path`
  (the original). New `fastread_video.print_video_routing_banner` logs
  `source video:` and `decode video:` lines at the start of each routed mode.
  Dual-path naming: `interval_solver.solve_all_intervals` and `_dispatch_blob_pass`
  rename `video_path` -> `decode_video_path`; `solver_workers._worker_init`
  renames `video_path` -> `decode_video_path`; `encoder._encode_segment` and
  `encoder.encode_cropped_video_parallel` rename `video_path` ->
  `original_video_path`. Deferred WP-P3 resolver field-tests added to
  `tests/test_fastread_video.py` (absent/valid/invalid cases, exactly-once
  validation, encode/identity always original). WP-P4b (setup/seed/edit/target
  UI + analyze source fields + path-leakage validation) is a separate task.

- **prepare mode docs**: New `docs/modes/PREPARE.md` documents the `prepare`
  subcommand: purpose (faster OpenCV frame reads on 4K HEVC sources), invocation
  and flags, what the fast-read video is and where it lives, the role policy
  (working modes decode fast-read when valid, encode uses original, state keyed
  to original), structural validation and loud-fail-on-invalid behavior,
  idempotency (`--force` recreates), rollback (delete the file), and the
  frame-identity contract (same geometry/frame-count/timing, not same pixels).
  `docs/MODES.md` updated: `prepare` added to the canonical workflow diagram and
  mode reference table as step 0 (optional). `docs/USAGE.md` updated: `prepare`
  added to the subcommand list, typical workflow section rewritten to show
  `prepare` as an optional-but-recommended first step for 4K HEVC sources with
  role-policy and selection-semantics summaries.

- **Fast-read video Patch 2 (WP-P2)**: `prepare` CLI subcommand and creation
  function. New `fastread_video.create_fastread_video(original_video_path,
  fastread_path, force, verbose)` transcodes via the fixed baseline command
  (`-map 0:v:0 -an -vf "hqdn3d,format=yuv420p" -c:v libx264 -preset veryfast
  -crf 23 -g 30`); output at the deterministic path from `tr_paths.fastread_video_path`.
  Implements M1 idempotency: existing valid fast-read skips transcode; existing
  invalid fast-read raises with remedy; `--force` recreates unconditionally. Progress
  printed at coarse steps (`[  0%]` .. `[100%]`); heartbeat line every 30 seconds
  while ffmpeg is running; success prints final 5-10 non-empty stderr lines; failure
  prints last 30-60 stderr lines and deletes partial output. `--verbose` streams
  full ffmpeg command + stderr. End-of-run status summary: fast-read path, geometry,
  timestamp-alignment fallback note, next-action lines. `prepare` dispatches before
  config/data-path setup so it does not require `setup` to have been run first.
  Registered in `track_runner/cli_args.py` with `-f/--force` and `-v/--verbose`.
  Dispatched via new `_mode_prepare` arm in `track_runner/cli.py`. Also removes the
  dead `if frame is None:` guard in `_smoke_read_fastread` (WP-P1 review item):
  `read_frame` raises on failure per its docstring and never returns None.

- **Fast-read video Patch 1 (WP-P1)**: deterministic path helper plus live
  structural validation for the upcoming `prepare` mode (fastread_video_prepare
  plan). New `track_runner.tr_paths.fastread_video_path(original_video_path)`
  returns `<original stem>.fastread.mkv` beside the source and is the only
  source of that path (filename is the registration -- no sidecar, no stored
  bookkeeping per contract C13). New module `track_runner/fastread_video.py`
  with `validate_fastread_structural(original_video_path, fastread_path)`:
  probes BOTH files fresh via `common_tools.probe_video.probe_video` and
  compares live-probed geometry/timing only (width/height/frame_count exact,
  fps within probe-precision relative tolerance, duration within a small
  absolute tolerance), runs a best-effort first/middle/last timestamp alignment
  that falls back to the frame-count + duration invariant (the probe primitive
  exposes no per-frame timestamps; the fallback is logged and surfaced), and
  smoke-reads frames 0 / mid / last via `common_tools.frame_reader.FrameReader`.
  Any failed check raises `RuntimeError` naming the fast-read path, the failed
  check, and the remedy ("re-run prepare --force, or delete the fast-read video
  to use the original"). Returns a frozen `FastreadValidation` carrying the
  timestamp-alignment fallback note so WP-P2's status summary can report it. No
  CLI subcommand, no `resolve_video_context`/`VideoContext` yet (WP-P2/WP-P4).

- New `tests/test_walk_cost_model.py`: synthetic-lattice unit tests for the
  pairwise velocity-delta Viterbi cost model. Covers: model-flip (constant-
  velocity runner beats stationary distractor), limb-oscillation (coherent
  center track beats alternating leg blobs), skip-bridge (gap charged once),
  boundary cases (all-skip, two-real-pick, skip-prefix), evidence tie-break
  (stronger mag wins; geometry beats bounded evidence), start-bias (bad high-
  evidence first blob does not anchor the path), structural invariants via
  `compute_path_term_breakdown` (zero delta terms before third real node; skip
  term exact; overspeed zero at limit), and sum-invariant
  (`sum(step_costs) == path_cost`).
- New `tests/test_walker_costs_config.py`: config-plumbing integration tests
  confirming `walker_costs` from the YAML schema reaches `walk_viterbi` module
  globals via `set_cost_weights`, that a partial section fails loud, and that
  the default config carries all required weight keys.
- New `tests/test_walk_io_parity.py`: parity tests for `walk_io.py` after the
  WP-IO-1 scale-back -- reader-geometry fields (`bin_factor`, processed and
  source dimensions), seed-path correctness, and the corrected `load_race_start_frame`
  loud-failure behavior on missing or invalid artifacts.

### Behavior or Interface Changes

- **Viterbi cost-model rewrite (WP-COST-1)** in
  `track_runner/blob_walk/walk_viterbi.py`. Replaces the first-order
  displacement cost (penalized motion, causing the walker to prefer stationary
  distractors over a moving runner -- audit P1-P5 findings) with pairwise
  velocity-delta scoring. New terms `WEIGHT_SPEED_DELTA` and `WEIGHT_HEADING_DELTA`
  penalize acceleration and heading changes between consecutive real
  observations (the DP-compatible form of the window-variance intent: pairwise
  deltas satisfy optimal substructure; a window mean does not). Dead module
  constants `WEIGHT_MAG_VAR` and `WEIGHT_ANGLE_VAR` (stubbed and never wired
  per audit P2) are removed. Evidence is normalized per-frame against the
  frame's strongest candidate, bounded by `WEIGHT_EVIDENCE_NORM`, making it a
  tie-breaker rather than a dominator (fixes P1 evidence-scale bug). The tight
  hard displacement prune is replaced by a soft linear cost plus a quadratic
  overspeed term above the physical envelope; a single generous hard prune at
  `ABSOLUTE_MAX_JUMP_W` is the only gate (fixes P5 always-on bootstrap slack).
  Skip is charged once per skipped frame; geometry bridges gaps via gap-
  normalized velocity (fixes P4 skip-charge semantics). `BOOTSTRAP_UNCERTAINTY_W`
  is no longer read by the DP; rename to `SEED_SEARCH_SLACK_W` recorded as a
  follow-up. Decision: variance-to-pairwise-delta is intentional design, not a
  simplification -- pairwise form is DP-compatible, and the terms couple the
  same physical model at the transition level.
- **walker_costs config section** added to
  `track_runner/track_runner.config.yaml` and validated by `tr_config.py`.
  Shipped defaults: `WEIGHT_DISPLACEMENT = 0.25` (lowered from plan's 1.0 per
  manager resolve -- evidence-forward: displacement as soft cost at this scale
  lets velocity-delta terms and evidence compete on equal footing),
  `WEIGHT_SPEED_DELTA = 1.0`, `WEIGHT_HEADING_DELTA = 0.5`,
  `WEIGHT_OVERSPEED = 4.0`, `WEIGHT_EVIDENCE_NORM = 0.5`, `SKIP_COST = 2.0`.
  Config resolution uses the existing `tr_config.resolve_config` helper (shared
  by `cli.py` and `tools/blob_walk_v2/walk_driver.py`); resolved weights flow
  through the existing frozen `WorkerContext.walker_costs` field and
  `make_pool` initargs. Decision: weights live in `tr_config`-owned YAML, not
  in `walk_io.py` (which is tool-layer glue only -- confirmed by WP-IO-1 audit).
- **Wiring gap fix (spec-review F1)**: `interval_solver._dispatch_blob_pass`
  now passes `walker_costs=getattr(context, "walker_costs", None)` to its
  `make_pool` call. Previously the Stage-4 (primary consumer) pool received
  `None` and fell back to `walk_viterbi` module defaults, ignoring config.
  `solve_queue.py`'s pool call already wired `walker_costs` correctly; the
  `interval_solver` site now matches it.
- **walk_io.py scale-back (WP-IO-1)**: `load_race_start_frame` body replaced --
  it previously re-derived race start as "end_frame of the last pre_race-tier
  interval" from `interval_scores.json` with chained `.get()` defaults that
  silently returned 0 on any shape mismatch. Production authority is
  `state_io.load_diagnostics` with direct key access on
  `data["pre_race_reference"]["race_start_frame"]`; a missing artifact, None
  `pre_race_reference` (legacy file -- re-solve required), or missing key now
  raises `RuntimeError` naming the artifact path (loud failure per
  do-not-hide-bugs-with-defaults). `walk_driver.py` basename-normalization
  mirror deleted; `walk_driver` imports `_normalize_video_basename` from
  `walk_io` (single definition).
- `tr_schema.SCHEMA_VERSION` bumped 13 -> 14. Added 14 to
  `GEOMETRY_AFFECTING_SCHEMAS`. On-disk layout unchanged from v13; v10-v14
  files remain readable. Both `diagnostics` and `torso_box_coords`
  `SUPPORTED_ARTIFACT_SCHEMAS` sets gain 14. Geometry-affecting on Stage-4-
  promoted intervals only; pure-Hermite paths byte-identical to v13. Per
  contract C10 one unified bump covers both geometry-affecting lane changes
  (WP-COST-1 and WP-P10-1). Schema history logged in
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md).

### Fixes and Maintenance

- **Fast-read video Patch 4a-fix (state-path leak)**: WP-P4a set
  `ExecutionContext.video_path` to the resolved decode path (fast-read when
  valid), and `solve_queue` keyed the race-start contact-sheet ARTIFACT path
  off `context.video_path`. With a valid fast-read in use the artifact picked
  up the `.fastread` stem instead of the original stem, violating the plan
  invariant "all Track Runner artifacts keyed to the ORIGINAL video" and
  contract C13. Fix is the design fix, not a patch-over: `ExecutionContext`
  gains an explicit `original_video_path` field alongside `video_path`
  (`video_path` now means the DECODE path, used only for frame decoding;
  `original_video_path` is used for every artifact-output-name decision).
  `interval_solver.solve_all_intervals` gains an `original_video_path`
  parameter (defaults to `decode_video_path` for single-path diagnostic
  callers) and threads it into the context. `cli._run_solve` threads
  `args.input_file` into `solve_kwargs["original_video_path"]`. The contact
  sheet artifact path in `solve_queue` (`solve_queue.py:~656`,
  `tr_paths.default_race_start_contact_sheet_path`) now reads
  `context.original_video_path`; the renderer call and the worker-pool
  `make_pool(video_path=...)` decode path stay on `context.video_path`. Audit
  of `solve_queue` found no sibling artifact/identity leaks (the contact sheet
  was the only `tr_paths`/output-name call keyed off `context.video_path`).
  `race_start_contact_sheet.render_race_start_contact_sheet` gains an optional
  `decode_source` marker appended to the title strip when the decode video
  differs from the original (debug-artifact provenance). New regression test
  `tests/test_fastread_video.py::test_contact_sheet_path_keyed_off_original_not_decode`
  proves the artifact path uses the original stem given a fast-read decode
  path; `tests/test_walker_costs_config.py` updated for the new required field.

- `docs/INSTALL.md` refreshed: fixed stale `VIDEO.mp4` example to `VIDEO.mkv`
  (the tool requires MKV source); added `prepare` pointer for 4K HEVC sources
  in the "First run" section; added "Verify install" section with a one-command
  check (`python track_runner/track_runner.py --help`).
- `docs/USAGE.md` refreshed: corrected stale `-d`/`--debug` description in the
  global options table (was "Enable debug video output with tracking overlays";
  now matches actual CLI: verbose diagnostic output for developers, does not
  affect rendered overlays in encoded video).
- **Docs audit fixes**: removed stale M2-merge hedge from `docs/modes/PREPARE.md`
  and `docs/USAGE.md` (working-mode fast-read routing has landed); corrected
  `docs/modes/PREPARE.md` frame-identity section (no tonemapping filter; 8-bit
  pixel-format conversion only). `tools/refresh_mode_docs.py` gained `prepare`
  in MODES list; `docs/modes/PREPARE.md` gained AUTO HELP markers and was
  refreshed with live CLI output. CHANGELOG 2026-06-12 day block reordered to
  canonical single-occurrence section order (duplicate headings merged).
- Code style and dual-path rename cleanup (see audit fixes).
- Removed planning-scaffolding milestone tags (WP-P#, M#) from permanent code comments and
  docstrings; rephrased to plain descriptions.
- `benchmark_fastread_video.py` raises `RuntimeError` instead of `sys.exit` on gate failure
  (PYTHON_STYLE).
- Reordered `fastread_video.py` stdlib imports shortest-first; collapsed a duplicate function
  separator before `cli._mode_prepare`.
- Renamed the worker-pool decode-path identifier to `decode_video_path` across
  `solver_workers.make_pool`, `solve_queue.ExecutionContext`, and `interval_solver`,
  distinguishing it from `original_video_path` (artifact/identity path, contract C13).
- Hardened `fastread` tests: timestamp-note check made behavioral (non-empty string, not
  substring pin), reason asserts reference module constants instead of string literals, YAML
  and HTML analyze asserts compare against input variables, deleted tests of an in-file helper
  (`_decode_label_from_context` and two callers).

- Review-fix cleanup (WP-P2): replace `getattr(args, "force/verbose", False)` with `args.force` / `args.verbose` in `track_runner/cli.py` (`prepare` subparser always sets defaults); tighten `_build_ffmpeg_transcode_cmd` and `_collect_stderr_lines` return annotations to `list[str]` in `track_runner/fastread_video.py`; replace collection-size assert (`len(...) > 0`) with behavioral substring assert (`"frame-count" in ...`) in `tests/test_fastread_video.py`.

- P10 bootstrap-accept fallback correction (audit M1, plan
  [docs/archive/blob_walk_v2_p10_fix_plan.md](archive/blob_walk_v2_p10_fix_plan.md)).
  The Stage-4 Hermite fallback gate in `track_runner/interval_solver.py`
  previously fired only on zero-accepted-count passes (`accepted_count == 0`).
  A pass whose only accepted frame is the seed frame via bootstrap
  (`accepted_count == 1`, all remaining frames `soft_miss_no_blob`) was not
  gated, producing a path frozen at the seed for all non-seed frames -- strictly
  worse than Hermite. Observed on Conant-4x400-2026_April_15.mkv
  `seed_1126_1134` FWD (8-frame interval, 7 frames frozen, 3.8% incidence in
  the Check 3 sample of 26 passes). Fix: `track_runner/walker_bundle.py` gains
  `WalkCoverage` dataclass (fields: `accepted_count`, `post_seed_accepted`) and
  pure helper `count_post_seed_accepts(accepts, seed_frame)`;
  `walk_bundle_to_path_with_coverage` returns `(path, WalkCoverage)` instead of
  `(path, int)`; the fallback gate reads `coverage.post_seed_accepted == 0` by
  name. Behavior change: bootstrap-only stall passes now fall back to Hermite;
  true-zero-accept stalls and healthy passes are byte-identical. The walker core
  (`track_runner/blob_walk/`) is untouched. Tests: new
  `tests/test_walk_coverage.py` (8 unit tests, all 6 Check 3 case shapes);
  `tests/test_walker_stall_fallback.py` gains
  `test_seed_only_accepted_pass_falls_back_to_hermite` (Conant gate-decision
  unit reproduction); both monkeypatch test modules adapted to `WalkCoverage`
  return. SCHEMA_VERSION bump (13->14) deferred to the integration patch that
  owns `track_runner/tr_schema.py`. Naming: plan used `post_bootstrap_accepted`;
  user decision 2026-06-12 changed to `post_seed_accepted` everywhere.
- **Lazy import micro-fix (spec-review F2)**: `tr_config.py` module-level
  `import blob_walk.walk_viterbi as walk_viterbi` moved to a lazy import inside
  `_validate_walker_costs`. Avoids a potential future circular import since
  `tr_config` loads early in cli startup and `walk_viterbi` imports the full
  `blob_walk` package.
- **RuntimeError micro-fix (spec-review F3)**: `walk_viterbi._evaluate_path_terms`
  now raises `RuntimeError("selected path node not present in window_candidates
  for frame ...")` with frame index and candidate list when `sel_idx` stays
  `None` after the identity scan. Previously the code proceeded to index `None`,
  producing a bare `TypeError` with no diagnostic context.
- **walker_costs supply chain closed (post-review F1/F2)**:
  `solve_all_intervals` gains `walker_costs: dict = None` parameter (optional-
  by-design for diagnostic callers; production supply via cli). `cli._run_solve`
  passes `cfg["walker_costs"]` (direct key access; default config always carries
  the section) into `solve_kwargs`, which flows into `ExecutionContext` and then
  to `make_pool -> _worker_init -> set_cost_weights`. Three `getattr` defensive
  fallbacks on always-populated `ExecutionContext` fields (`bin_factor`,
  `video_frame_count`, `walker_costs`) replaced with direct attribute access
  (do-not-hide-bugs-with-defaults cleanup, N1). Tool chain: `process_video` in
  `tools/blob_walk_v2/make_walk_html_v2.py` now calls
  `walk_driver.apply_walker_costs_for_video` once per video before any walks
  run; the function was previously defined but never called, making YAML weights
  a no-op in the tool path. Full chain confirmed: cli -> solve_all_intervals ->
  ExecutionContext -> make_pool -> _worker_init -> set_cost_weights AND
  walk_driver -> apply_walker_costs_for_video -> set_cost_weights.

- **WS-2B overlay review** of the two flagged A/B regressions (see
  [docs/active_plans/workstreams/blob_walk_v2_ws2b_overlay_review.md](active_plans/workstreams/blob_walk_v2_ws2b_overlay_review.md)).
  Lyra-Hersey [840,945]: regression mechanism is per-frame blob centroid cy
  bias (~9px / 0.65 tw) at frame 892 where the runner passes a vertical pole
  (partial occlusion reduces mag to 7023 vs ~20k baseline); one corridor blob
  throughout, no ranking failure, cost model not implicated.
  Jason [12408,12596]: complete signal absence (187/188 FWD frames and 176/188
  BWD frames are soft_miss_no_blob); runner is in a dense indoor-meet pack
  where residual-motion extraction cannot isolate the target from crowd motion;
  corridor is empty, no candidates to rank, cost model not implicated.
  Both cases confirm neither regression is caused by the pairwise velocity-delta
  cost rewrite. Tile output at `corpus_walk/<video>/seed_<L>_<R>/`.

### Decisions and Failures

- `WEIGHT_DISPLACEMENT = 0.25` (lowered from plan's 1.0): manager resolved
  evidence-forward after WP-COST-1 landed. At 1.0, displacement cost dominated
  evidence for slow-moving runners in pre-race intervals; 0.25 lets the
  velocity-delta terms and normalized evidence compete on honest scale.
- Variance-to-pairwise-delta design: the plan's window-variance intent is
  implemented as pairwise velocity deltas (penalize acceleration, not deviation
  from a window mean) because the pairwise form is additive and satisfies
  optimal substructure. A window mean requires global rollback and is not
  DP-compatible. Code comment cites audit P2.
- `SEED_SEARCH_SLACK_W` rename deferred: the legacy `BOOTSTRAP_UNCERTAINTY_W`
  constant exists in `walk_motion_gate.py` but is no longer read by the DP
  (the DP reads no bootstrap slack after WP-COST-1). The rename to
  `SEED_SEARCH_SLACK_W` is recorded as a follow-up; it does not expand this
  patch's scope.
- `walker_costs` config residence confirmed in `tr_config.py` (not in
  `walk_io.py`): the WP-IO-1 audit confirmed `walk_io.py` is tool-layer glue
  that delegates to established owners; adding config parsing there would
  re-implement the pipeline boundary the existing `tr_config/resolve_config`
  path already provides.
- Walker-vs-Hermite interpretation rule recorded (user-confirmed, well
  documented): the walker is the trusted, more-accurate solver; Hermite is the
  cheap floor, gated to Stage-4-promoted intervals by CPU cost, not by quality.
  Held-out-single-seed error (`e2e_walker_ab`) is therefore NOT a
  walker-vs-Hermite quality ranking: it under-samples the interval (one frame)
  and is biased toward Hermite on smooth motion, where a small `hermite_err`
  means the held-out frame was easy, not that Hermite tracked well. Durable rule
  added to
  [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) ("Interpreting
  walker-vs-Hermite and held-out-seed error"). The held-out expansion's
  "regressed 11/13" headline is reframed accordingly; its valid signal is
  absolute multi-torso walker outliers, not the Hermite comparison.

### Developer Tests and Notes

- Add `tests/test_walk_viterbi_brute_force.py`: exhaustive brute-force
  enumeration vs DP optimality check for `walk_viterbi.select_path` across five
  lattice shapes (dense, sparse/empty-frame, +inf-edge, near-stationary, zero-
  mag evidence); 39 fixed-seed parametrized cases, all passing (WP-COST-2
  extension).
- `tests/test_walker_costs_config.py` extended with
  `test_execution_context_carries_walker_costs_to_make_pool`: asserts that the
  `make_pool` boundary receives the `walker_costs` dict from `ExecutionContext`
  (mock-captured, no video needed), closing the F1 hole class.
- **WP-VAL-1 release evidence** (frozen 34-pass + 5-video corpus A/B, 2026-06-12):
  shipped default weights re-solved all 34 check7 classified passes (22 effective-
  ranking + 10 mixed + 2 starvation) with the new pairwise Viterbi cost model.
  Ranking-class passes FWD 66.7-100%, mostly >= 83%. 5-video corpus subset: IMG_3830
  FWD 82.0% / BWD 88.5% vs before 83.6% / 88.5% (delta -1.6 pp / 0.0 pp, no
  regression). Lyra-Hersey partial 16/20 FWD 78.0%. Identity spot check across
  18 intervals: PASS, no cross-athlete captures. Tuning evaluation: 4 weight configs
  (including mandatory evidence-forward config with WEIGHT_EVIDENCE_NORM = 1.0)
  all produced identical results on the fast subset -- default weights are the
  winning config. `e2e_blob_walk_baseline`: exit code 0, golden snapshot passed.
  `e2e_walker_ab`: IMG_3830 preserved=2, regressed=3; IMG_3823 preserved=2,
  regressed=3 (Jason in progress). Artifact:
  [docs/active_plans/workstreams/blob_walk_v2_cost_model_ab.md](active_plans/workstreams/blob_walk_v2_cost_model_ab.md).
  Roadmap: P1/P2/P3/P4/P5 and P10 marked fixed in
  [docs/active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md).
  **Completion (2026-06-12)**: all 5 corpus videos complete (20/20 intervals each); no
  regression on any video (max delta -2.1 pp Conant FWD/BWD vs -2.1 pp baseline shift);
  identity check extended to 39 intervals, PASS; e2e_walker_ab 4/6 complete (Lyra-Hersey
  preserved=4/regressed=1, Conant rescued=3/preserved=2; Jason and Lyra-Wheeling pending
  due to concurrent process log collision). Release-review summary (governance package for
  human accept/reject) inserted at top of
  [docs/active_plans/workstreams/blob_walk_v2_cost_model_ab.md](active_plans/workstreams/blob_walk_v2_cost_model_ab.md).
- Held-out error expansion
  [docs/active_plans/workstreams/blob_walk_v2_heldout_expansion.md](active_plans/workstreams/blob_walk_v2_heldout_expansion.md):
  13 mid-span held-out triples (spans 15-45, 4 videos), blended-path error.
  Raw numbers walker median 1.105 vs Hermite 0.100 torso-widths; reframed per
  the walker-vs-Hermite interpretation rule above (the instrument is
  Hermite-biased on easy frames, so this is not a quality ranking). Valid
  signal: absolute walker outliers of 2-3 torso-widths (IMG_3830 [1288,1308],
  IMG_3823 [2316,2337]) flagged for eyes-on tile review. Every row drove the
  walker; the P10 fallback fired on zero rows.
- Corpus-120 schema-14 control run
  [docs/active_plans/workstreams/blob_walk_v2_corpus120_schema14.md](active_plans/workstreams/blob_walk_v2_corpus120_schema14.md):
  same 100 intervals across 5 videos as the schema-13 baseline (Lyra-Wheeling
  skipped, 6 h decode). FWD 59.7% vs 58.9% (+0.8), BWD 60.8% vs 59.8% (+1.0);
  every video and direction at or above baseline, max delta +1.2; all 100
  intervals stop on `hit_neighbor_seed`. Accepted-fraction is a coverage metric,
  not positional accuracy.
- Weight-sensitivity sweep (SIDE QUEST)
  [docs/active_plans/workstreams/blob_walk_v2_weight_sensitivity.md](active_plans/workstreams/blob_walk_v2_weight_sensitivity.md):
  6 extreme configs on the 24-pass subset. SKIP_COST is the only load-bearing
  weight (halving it drops accepted fraction 9.8 pp); delta and evidence terms
  near-inert on short high-confidence intervals; multi-candidate fraction 82%.
  Explains why the original 4-config tuning was flat -- 2x nudges were
  insufficient to flip rankings.
- Second pre-merge audit (six independent reviewers, read-only on the frozen
  bundle): no code-correctness blocker. Findings are packaging and hygiene --
  the CHANGELOG ordering blocker in this 2026-06-12 block (now fixed: the two
  `Developer Tests and Notes` sub-blocks merged into one placed last), five
  planning-tag rewords in code comments, four unstaged doc-truth fixes required
  to ride with the bundle, and fragile-test pruning. Artifact:
  [docs/active_plans/audits/blob_walk_v2_bundle_audit_run2.md](active_plans/audits/blob_walk_v2_bundle_audit_run2.md).

- New artifact
  [docs/active_plans/reports/blob_walk_v2_starvation_characterization.md](active_plans/reports/blob_walk_v2_starvation_characterization.md):
  read-only characterization of the 12 starvation-class passes (7 pure starvation
  + 5 starvation-leaning mixed from check7). Per-pass table with baseline empty
  fractions, post-rewrite accepted fractions, and seed-cold status. Key findings:
  (a) cost-model rewrite did not touch starvation (structurally upstream of
  Viterbi -- empty lattice before the cost model runs; all 4 weight configs
  produced identical results on starvation passes); (b) physical condition is
  small apparent runner size (torso ~11 px proc), where DoG resolves limb-level
  blobs or falls below detection threshold (claim D crossover 11-30 px, plus
  drift-stall sub-type for Conant); (c) frequency: 10.8% of all corpus
  interval-directions show seed-cold symptoms (corpus120 proxy), 35% of the m4
  regressed bucket has starvation as primary or significant factor (check7).
  Jason is the dominant contributor at 35% of its interval-directions. No fix
  proposals; mechanism, frequency, and impact only.

- Short-span interval frequency study completed. Measured span distribution
  across all 12 corpus videos via `_temp_span_study.py` (deleted after run).
  Corpus: 6274 intervals, 170372 frames. Short-span (1-13 frames): 57.3% of
  intervals by count but only 8.8% of frames by weight. Among those, only 3.4%
  are Stage-4-promoted (low/fair tier), yielding ~0.3% of corpus frames in
  short-span Stage-4 intervals -- the Viterbi cost-model degenerate bucket is
  tiny by frame-weighted impact. Distribution is bimodal: four densely-seeded
  videos (IMG_3830, IMG_3823, Hononega-Orion_600m, Hononega-Varsity_4x400m)
  dominate short-span counts; five coarsely-seeded videos have almost none.
  Artifact:
  [docs/active_plans/reports/blob_walk_v2_short_span_frequency.md](active_plans/reports/blob_walk_v2_short_span_frequency.md).
- **Fast-read video Patch 3 (WP-P3 M1 unit tests)**: new
  `tests/test_fastread_video.py` (15 tests, 0.11 s). Covers structural
  validation pass/fail shapes via synthetic probe dicts and a fake
  `FrameReader` (no real ffmpeg, no real video): matching probe pair returns a
  `FastreadValidation`; width/height/frame_count mismatch, out-of-tolerance
  duration (5 s gap), and out-of-tolerance fps (120 vs 60) each raise
  `RuntimeError`; error messages name the fast-read path and include a remedy
  hint. Path-namespace tests: `fastread_video_path` ends with
  `<stem>.fastread.mkv` and sits beside the source; `default_seeds_path`,
  `default_config_path`, and `default_intervals_path` use the original-video
  stem (not the fastread name). Idempotency decision helper not yet exposed
  outside WP-P2's prepare flow -- idempotency tests deferred to WP-P4 (noted
  in handoff). `resolve_video_context`/`VideoContext`/`VideoSelection` not
  yet built -- those resolver field-tests deferred to WP-P4 per manager
  resequencing.

- Docset audit 2026-06-12: created `docs/NEWS.md`, `docs/RELATED_PROJECTS.md`,
  `docs/RELEASE_HISTORY.md`; updated `docs/TROUBLESHOOTING.md` with `prepare`
  mitigation and cross-reference to `docs/modes/PREPARE.md`; updated
  `docs/ROADMAP.md` to note fast-read M2 pending state.
- Architecture docs refresh: `docs/CODE_ARCHITECTURE.md` and
  `docs/FILE_STRUCTURE.md` updated to reflect current repo state. Pipeline
  diagram and narrative extended with `prepare` as step 0. `fastread_video.py`
  documented in the module map under "Crop, encode, and analysis" with
  `FastreadValidation`, `create_fastread_video`, and `validate_fastread_structural`.
  `cli_args.py` entry updated to list `prepare` subcommand. `FILE_STRUCTURE.md`:
  added `fastread_video.py` line in `track_runner/`, added `docs/modes/PREPARE.md`,
  added `corpus_walk/` entry, removed stale `pip_extras.txt` line, corrected
  `TR_FWD_BWD_MODEL_METHODOLOGY.md` and `TR_MOTION_CUE_HEAT_MAP.md` filenames,
  removed `RESIDUAL_MOTION_OBSERVATIONS.md` from active docs tree (it lives in
  `docs/archive/`), refreshed stale test listings in the unit/integration section,
  expanded `devel/` and `tools/` sections to match actual file inventory.
- Changelog rotation: older day blocks 2026-06-10 through 2026-06-07 archived
  to `docs/CHANGELOG-2026-06b.md` per the rotation policy (keeps two newest
  day blocks in active changelog).

## 2026-06-11

### Additions and New Features

- Created `docs/TROUBLESHOOTING.md` (first entry: walker / corpus runs on 4K HEVC sources
  take hours). Documents cause (HEVC random-access seek cost ~450-550 ms vs 6-14 ms
  sequential per [common_tools/README.md](../common_tools/README.md) strategy table),
  notes auto-bin does not reduce seek cost (binning is post-decode), links the access-pattern
  fix roadmap (P16/P17) parked in
  [active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md),
  and confirms runs are slow but not broken.

- Added runtime notice in `tools/blob_walk_v2/make_walk_html_v2.py` `process_video`:
  when source width >= 3840 or source height >= 2160 (derived from
  `reader.geometry.source_width` / `reader.geometry.source_height`), emits one
  `logger.info` line noting the random-access seek cost (~0.5 s single-process,
  multi-second under parallel load) and pointing at `docs/TROUBLESHOOTING.md`.
  The same warning now also fires at initial metadata parse in
  `common_tools/probe_video.py`, covering every production probe consumer.
  No behavior change; log only. Post-review polish: both messages point at
  `docs/TROUBLESHOOTING.md` rather than roadmap item tags; the probe comment
  states the measured mechanism (forward decode from the nearest preceding
  keyframe in long-GOP HEVC -- the corpus test files carry no B-frames per
  the `common_tools/README.md` probe) instead of a B-frame claim;
  `probe_video.py` gains a module-level logger, a docstring `Warns:` section,
  and a single-f-string message.

- Drafted the M1 fix plan
  [docs/archive/blob_walk_v2_p10_fix_plan.md](archive/blob_walk_v2_p10_fix_plan.md)
  (P10 bootstrap-accept fallback masking; implemented 2026-06-12, archived) from
  the roadmap's M1 section, including the recorded call-site audit of
  `walk_bundle_to_path_with_coverage` (single production consumer at the
  `interval_solver` fallback gate; two monkeypatching test modules; the
  audit-triggered stop condition is not met). No production code changed.

### Fixes and Maintenance

- Validation report clarity edits (documentation only, no code change): (1) Claim
  C section now states percentages are of 34 classified passes, explaining the 49%
  vs 50% discrepancy with the source artifact header. (2) Claim B section breaks
  pooled 44 multi-candidate frames into FWD 26 (ev_match 3/26=11.5%,
  disp_match 25/26=96.2%) vs BWD 18 (ev_match 16/18=88.9%) to expose the
  direction asymmetry obscured by the pooled 43.2% figure. (3) Fixed false
  preamble "No implementation details are provided" in the fixes section --
  P12 and P10 entries contain exact fix directions verbatim from workstream
  artifacts; replaced with accurate wording noting they are candidate directions
  requiring user-approved plans. (4) Added user-directed design-orientation note
  (2026-06-11): goal is better tracking with LESS gating; prefer removing or
  softening wrong guards over adding new ones; claim-A caution does not endorse
  keeping hard exclusion long-term; replacing hard exclusion with soft scoring
  (claim H direction) remains design-aligned once candidate supply is understood.

- Cost telemetry unified in `track_runner/blob_walk/walk_viterbi.py`: `compute_path_cost`
  now delegates to `compute_path_step_costs` and returns `sum(step_costs)`, making the
  sum invariant (sum(steps) == total) structural rather than comment-only. Required blob
  keys (`integrated_mag`, `centroid_x`, `centroid_y`) in `compute_path_step_costs` now
  accessed directly per do-not-hide-bugs-with-defaults (was `.get(key, 0.0)`); skip nodes
  (None) retain explicit handling. Defensive index guard `path_step_costs[k] if k < len(...)`
  removed from `walk_walker.py` stamping; replaced with direct `path_step_costs[k]` since
  `compute_path_step_costs` returns one entry per path node by construction. Decision logic
  (`select_path`, `transition_cost`) unchanged; 8-pass equality harness all PASS.

- Blob walker stride>1 termination overrun fixed (audit P12, observed on 120fps
  source interval frames 16588-16591). Equality test `frame_f == neighbor_seed_frame`
  replaced by directional crossing test with clamp in new pure helper
  `_neighbor_reached` (`track_runner/blob_walk/walk_walker.py`); seed endpoints
  remain anchors and are never observed. SCHEMA_VERSION bumped 12->13;
  geometry-affecting for stride>1 (>=~90fps) sources only, byte-identical at
  stride 1. Validation: 7 new unit tests in `tests/test_walk_neighbor_reached.py`
  (including interval #164 FWD/BWD exact sequences); 8-pass stride-1 harness EQUAL
  on all passes. Two caveats: (a) stride-1 preservation rests on analytic predicate
  equivalence proof plus unit tests plus run-to-run determinism -- no true pre/post
  empirical snapshot exists because the stored reference walks postdate the fix;
  (b) the real 120fps interval #164 (Lyra-Wheeling, 4K HEVC) was not re-solved
  post-fix due to expensive decode cost; unit tests cover the exact frame arithmetic
  for that interval. Full pytest suite 1578 passed. Plan:
  [blob_walk_v2_p12_fix_plan.md](active_plans/active/blob_walk_v2_p12_fix_plan.md).

### Developer Tests and Notes

- Claim G offline replay completed (check G workstream). Zero `extrapolated`
  or `interpolated` frames found in 24 walk debug CSVs (366 `soft_miss_no_blob`,
  282 `accepted`, 24 `after_walk_terminated`). Audit P6 confirmed: both statuses
  are flush-only. P9 spec deviation confirmed: `walk_status.py` lines 114-116
  implement HOLD instead of linear extension for `extrapolated` status. Empirical
  comparison with available data is indeterminate (reference-line curvature
  contaminates the replay). Synthetic uniform-motion analysis supports LINEAR
  BETTER (59/59 scenarios). Verdict: UNDETERMINED -- practical impact zero at
  current accepted fractions; fix deferred until `extrapolated_count` becomes
  non-zero. Artifact:
  [docs/active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md](active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md).
  Validation report claim G updated from STILL UNKNOWN to UNDETERMINED.

- Corpus-120 walk run completed (post-P12, SCHEMA_VERSION 13, rng_seed=None fresh
  sample): 6 videos x 20 random visible-both intervals. All 120 intervals completed
  with stop_reason `hit_neighbor_seed`; all 6 manifest PASS checks passed. Per-video
  FWD/BWD accepted_fraction (pooled over interval lengths): IMG_3830 83.6%/88.5%,
  IMG_3823 55.8%/54.5%, Jason 40.2%/39.0%, Lyra-Hersey 82.7%/78.0%, Conant
  67.9%/76.2%, Lyra-Wheeling 58.0%/62.4%. Corpus: 58.5% FWD / 61.1% BWD. The
  elevation vs L3 reference (38.7%/39.1% on 120-corpus) is primarily sampling
  variation from the fresh-sample selection; a fixed-seed A/B (WS-2A) is required
  before the number is authoritative. Lyra-Wheeling (stride 2, 120 fps): 13 of 20
  sampled intervals have an odd half-span (prime P12 trigger candidates); all report
  `hit_neighbor_seed`, consistent with the termination fix. No walk debug CSVs are
  written by this tool path; per-frame P12 verification remains in unit tests. Elapsed
  times: IMG_3830 0:28, IMG_3823 0:20, Jason 52:22, Lyra-Hersey 25:48, Conant 31:19,
  Lyra-Wheeling 6:11:13. Artifact:
  [active_plans/workstreams/blob_walk_v2_corpus120_run_2026_06_10.md](active_plans/workstreams/blob_walk_v2_corpus120_run_2026_06_10.md).

- Blob walk v2 validation complete: all 9 checks (Check 0 through Check 8) executed
  and synthesized. Final synthesis report:
  [active_plans/reports/blob_walk_v2_validation_report.md](active_plans/reports/blob_walk_v2_validation_report.md).
  Claim verdicts:
  - A REFUTED -- rejected blobs on stall intervals are background athletes, not the runner;
    widening the acceptance box would not recover runner signal.
  - B MIXED -- evidence term dominates per-node cost but window-level DP accumulates
    spatial momentum that attenuates the evidence signal on FWD passes (Jason FWD:
    0/23 ev_match, 22/23 disp_match); BWD passes behave differently due to fewer
    competing blobs.
  - C RANKING-DOMINANT -- of 35 m4 A/B regressed intervals, 17 ranking-driven,
    10 mixed, 7 starvation-driven, 1 pending (Lyra-Wheeling 120 fps excluded per K).
  - D REFUTED (conditional) -- limb merging is size-dependent: large runners (Conant,
    30 px torso) merge to one blob; small runners (Jason, 11 px torso) show 4-6
    distinct limb-level blobs per frame.
  - E OBSERVED -- within-body vertical centroid jitter on Jason (11 px torso): Viterbi
    selection alternates between lower-body (ncy ~ -0.4) and upper-body (ncy ~ +0.2)
    clusters; alternation rate 0.26 flips/step; 85% of steps stay within 0.10 torso
    heights.
  - F CONFIRMED (structural) -- anchor is always 9+ frames stale in steady state
    (window depth = WALKER_WINDOW_FRAMES = 9); quality impact conditional on image-space
    drift per standing constraint P8.
  - G UNDETERMINED -- extrapolation-vs-hold-last comparison not exercised in
    sample (label updated same-day from STILL UNKNOWN by the check G replay;
    see the claim G entry above).
  - I CONDITIONALLY CONFIRMED -- anchor staleness is present at every rejection; two
    distinct stall sub-mechanisms: (a) image-space drift (Conant, 2.35 TW over 31 frames),
    where anchor-advance would help; (b) near-stationary runner (Jason) where L4 centroid
    offset + P11 acceptance-box exclusion is operative and anchor-advance alone would not
    cure it.
  - J OBSERVED -- bootstrap-accept masking of the pure-stall Hermite fallback occurs in
    1 of 26 passes (3.8%); the bootstrap frame counts as an accepted frame and prevents
    the zero-accepted-count fallback from firing even when the remaining frames are all
    misses.
  - K OBSERVED FAILURE -- stride-2 stepping/termination bug (audit P12) is live on
    interval 16588-16591 of Lyra-Wheeling-IMG_3912.mkv (120 fps, stride 2); walker
    results on that video are suspect until the stepping fix lands.
  - L NOT EXERCISED in sample -- pooled P99 accepted-to-accepted displacement 0.578 W;
    zero events exceed the identity-jump threshold (0.878 W); structural hole in Viterbi
    (no cap on skip-to-blob transitions) confirmed real but not manifesting in this
    8-pass sample.
  - H INFORMED, NOT PROVEN -- gate redesign direction (replace hard exclusion with soft
    scoring) is design-aligned with the user-directed orientation (better quality, less
    gating); no trial yet.
  Design orientation (user-directed 2026-06-11): goal is better tracking with less
  gating; prefer removing or softening wrong guards over adding new ones. Every fix
  still requires its own user-approved plan.
  Check 0 (stride-2 overrun, K) and Check 3 (bootstrap-accept masking, J) were
  completed during the validation but not individually logged in this changelog;
  their artifacts are at
  [active_plans/workstreams/blob_walk_v2_check0_stride_overrun.md](active_plans/workstreams/blob_walk_v2_check0_stride_overrun.md)
  and
  [active_plans/workstreams/blob_walk_v2_check3_bootstrap_masking.md](active_plans/workstreams/blob_walk_v2_check3_bootstrap_masking.md).

- Validation closeout additions (documentation only): (1) Report restructured
  with three explicit verdict groups -- PROVEN, REFUTED (see A and D), STILL
  UNKNOWN (see B, G, H, L) -- as a grouped table above the per-claim sections.
  (2) Claim G confirmed UNDETERMINED: `extrapolated` status never executes in
  current corpus; code path dead at current accepted fractions; spec deviation
  (P9 HOLD vs linear) deferred; see
  [active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md](active_plans/workstreams/blob_walk_v2_checkg_extrapolation_replay.md).
  (3) Claim C sharpened with Check 7 addendum 65%/35% effective ranking/starvation
  split; mixed-bucket diagnosis done (accept_on_nonempty >= 0.88 on all 10 mixed
  passes; wrong-blob-wins inferred, not position-verified). (4) Open items updated:
  Lyra-Wheeling 35th interval UNDETERMINED (timeout), mixed-bucket diagnosis marked
  complete with caveat, Claim G open item updated from pending to measured-but-
  undetermined. Stray walk process (PID 10595, _temp_check7b_lyra_wheeling.py,
  67h CPU) confirmed dead after pkill.
