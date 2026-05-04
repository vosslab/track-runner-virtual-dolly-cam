# File structure

```
track-runner-virtual-dolly-cam/
|-- README.md                      project overview and quick start
|-- AGENTS.md                      AI agent instructions
|-- CLAUDE.md                      Claude Code bootstrap (loads AGENTS and styles)
|-- VERSION                        CalVer version
|-- LICENSE.LGPL_v3                LGPL v3 license for code
|-- LICENSE.CC_BY_4_0              CC BY 4.0 license for non-code content
|-- source_me.sh                   shell environment bootstrap
|-- re-solve.sh                    convenience wrapper for re-solving
|-- pip_requirements.txt           runtime Python dependencies
|-- pip_requirements-dev.txt       developer Python dependencies
|-- pip_extras.txt                 optional extras
|-- Brewfile                       Homebrew system packages
|-- tr_config                      symlink into per-video config store
|
|-- track_runner/                  main application package
|   |-- track_runner.py            entry point shim
|   |-- cli.py                     subcommand dispatch and orchestration
|   |-- cli_args.py                argparse configuration
|   |-- setup_mode.py              interactive camera configuration
|   |-- seeding.py                 seed frame collection
|   |-- seed_editor.py             seed editing logic
|   |-- seed_color.py              jersey color extraction helpers
|   |-- box_utils.py               bounding box utilities
|   |-- camera_motion.py           phase-correlation motion estimators
|   |-- scene_coords.py            pixel-to-scene coordinate transform
|   |-- velocity_model.py          Hermite FWD/BWD propagation
|   |-- residual_motion.py         per-frame residual cue pipeline
|   |-- residual_pre_pass.py       per-worker per-interval sequential
|   |                              residual pre-pass (Stage 4 inner step)
|   |-- residual_heat_map.py       residual heat-map generation
|   |-- interval_solver.py         analytical interval solver
|   |-- interval_fingerprint.py    per-interval cache-key fingerprinting
|   |-- solve_queue.py             solve and refine driver
|   |-- solver_workers.py          worker-process state
|   |-- scoring.py                 interval scoring (sole owner)
|   |-- review.py                  interval quality assessment
|   |-- race_phases.py             race phase classification
|   |-- regime_classifier.py       motion regime classification
|   |-- regime_policies.py         per-regime parameter policies
|   |-- tr_crop.py                 adaptive crop trajectory
|   |-- encoder.py                 ffmpeg encoding
|   |-- encode_analysis.py         post-encode quality analysis
|   |-- analyze_report.py          HTML diagnostic report (analyze --plot)
|   |-- tr_detection.py            YOLOv8n person detection (optional)
|   |-- state_io.py                seed / geometry / scores / motion I/O
|   |-- tr_config.py               YAML config parsing and migration
|   |-- tr_paths.py                per-video state and output paths
|   |-- tr_video_identity.py       video identity hashing
|   |-- overlay_config.py          overlay style loader
|   |-- video_io.py                VideoReader and frame utilities
|   |-- key_input.py               keyboard input handling
|   |-- track_runner.config.yaml   default runtime config
|   |-- overlay_styles.yaml        overlay style definitions
|   |
|   +-- ui/                        PySide6 GUI modules
|       |-- app_shell.py           main window and theme
|       |-- base_controller.py     annotation controller base
|       |-- seed_controller.py     seed placement
|       |-- edit_controller.py     seed editing
|       |-- target_controller.py   weak-interval targeting
|       |-- frame_view.py          frame display widget
|       |-- workspace.py           multi-frame workspace
|       |-- overlay_items.py       visual overlays
|       |-- heat_map_overlay.py    residual heat-map overlay
|       |-- status_presenter.py    status bar display
|       |-- zoom_controls.py       zoom interface
|       |-- actions.py             UI action handlers
|       `-- theme.py               dark/light theme definitions
|
|-- common_tools/                  shared utility modules
|   |-- frame_filters.py           frame filtering
|   |-- frame_reader.py            frame reading
|   `-- tools_common.py            shared tool helpers
|
|-- data/                          non-Python build-time assets
|   `-- js/
|       `-- analyze_report_renderer.js  vanilla-JS canvas renderer for
|                                        analyze --plot HTML reports
|
|-- tools/                         analysis and batch scripts
|   |-- analyze_crop_path_stability.py
|   |-- analyze_track_runner_json.py
|   |-- assess_pixel_zoom.py
|   |-- batch_encode_experiment.py
|   |-- batch_smart_experiment.py
|   |-- benchmark_solver_gates.py
|   |-- diagnose_residual_motion.py
|   |-- inspect_score_distribution.py
|   `-- refresh_mode_docs.py          regenerate `--help` blocks in docs/modes/
|
|-- tests/                         pytest suite
|   |-- conftest.py                pytest configuration
|   |-- git_file_utils.py          shared test helper
|   |-- check_ascii_compliance.py  single-file ASCII checker
|   |-- fix_ascii_compliance.py    single-file ASCII fixer
|   |-- fix_whitespace.py          single-file whitespace fixer
|   |-- fixtures/                  shared fixture data
|   |
|   |-- repo-wide gates
|   |   |-- test_pyflakes_code_lint.py
|   |   |-- test_ascii_compliance.py
|   |   |-- test_bandit_security.py
|   |   |-- test_indentation.py
|   |   |-- test_whitespace.py
|   |   |-- test_shebangs.py
|   |   |-- test_init_files.py
|   |   |-- test_import_dot.py
|   |   |-- test_import_star.py
|   |   `-- test_import_requirements.py
|   |
|   `-- unit and integration
|       |-- test_blob_snap.py
|       |-- test_camera_motion.py
|       |-- test_cli_args_encode.py
|       |-- test_heat_map_overlay_smoke.py
|       |-- test_interval_fingerprint.py
|       |-- test_race_phases.py
|       |-- test_residual_heat_map.py
|       |-- test_tr_residual_pre_pass.py
|       |-- test_tr_debug_blob_flag.py
|       |-- test_tr_debug_blob_instrumentation.py
|       |-- test_review.py
|       |-- test_scene_coords.py
|       |-- test_scoring.py
|       |-- test_seed_controller.py
|       |-- test_seed_schema_v3.py
|       |-- test_solve_queue.py
|       |-- test_solver_integration.py
|       |-- test_solver_parallelism.py
|       |-- test_tr_analyze_report.py
|       |-- test_tr_config_migration.py
|       |-- test_tr_detection.py
|       |-- test_velocity_model.py
|       `-- analyze_report_fixtures.py    shared fixtures for analyze_report tests
|
|-- devel/                         developer tooling
|   |-- commit_changelog.py        changelog commit automation
|   `-- submit_to_pypi.py          PyPI submission tool
|
|-- TRACK_VIDEOS/                  sample / working videos (gitignored content)
|
`-- docs/                          documentation
    |-- CHANGELOG.md               chronological change history
    |-- AUTHORS.md                 maintainer information
    |-- INSTALL.md                 setup and dependencies
    |-- USAGE.md                   workflow narrative, global options, configuration
    |-- MODES.md                   per-mode (subcommand) index pointing to docs/modes/
    |-- modes/                     per-mode reference pages with auto-stamped --help
    |   |-- SETUP.md
    |   |-- SEED.md
    |   |-- SOLVE.md
    |   |-- TARGET.md
    |   |-- REFINE.md
    |   |-- EDIT.md
    |   |-- ENCODE.md
    |   `-- ANALYZE.md
    |-- CODE_ARCHITECTURE.md       pipeline and module overview
    |-- FILE_STRUCTURE.md          this file
    |-- TRACK_RUNNER_CONTRACT.md   hard invariants (authoritative)
    |-- TRACK_RUNNER_DESIGN.md     design philosophy
    |-- TRACK_RUNNER_V3_SPEC.md    technical specification
    |-- TRACK_RUNNER_HISTORY.md    evolution history
    |-- TRACK_RUNNER_KEYBINDINGS.md    annotation UI keybindings
    |-- TRACK_RUNNER_YAML_CONFIG.md    YAML config reference
    |-- TRACK_RUNNER_ANALYZE_AND_ENCODE.md   analyze and encode guide
    |-- TR_CONFIG_FILES.md         per-video state file reference
    |-- FWD_BWD_MODEL_METHODOLOGY.md   coupled FWD/BWD model mechanics
    |-- MOTION_CUE_HEAT_MAP.md     heat-map mechanism technical doc
    |-- RESIDUAL_MOTION_OBSERVATIONS.md   per-frame measurement summary
    |-- ROADMAP.md                 planned work
    |-- TODO.md                    backlog scratchpad
    |-- CLAUDE_HOOK_USAGE_GUIDE.md permissions-hook reference
    |-- PYTHON_STYLE.md            Python style conventions
    |-- REPO_STYLE.md              repository conventions
    |-- MARKDOWN_STYLE.md          Markdown style conventions
    |-- video-object-deep-research-report.md   background research
    |-- active_plans/              in-flight implementation plans
    `-- archive/                   historical design documents and plans
```

## Generated artifacts

- Per-video config YAML produced by `setup`. Uses the path pattern
  documented in [docs/TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) and
  generated by [track_runner/tr_paths.py](../track_runner/tr_paths.py).
- Per-video state files (seeds JSON, geometry NPZ, interval-scores JSON,
  diagnostics JSON, camera-motion NPZ) live under the per-video
  `tr_config` store. See
  [docs/TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).
- Race-start confirmation contact sheet PNG:
  `<video>.track_runner.race_start_check.png`, produced by `solve` /
  `refine` Stage 2 whenever `race_start_frame` is detected. Sidecar path
  computed by `default_race_start_contact_sheet_path` in
  [track_runner/tr_paths.py](../track_runner/tr_paths.py).
- Debug paths NPZ: `<video>.track_runner.debug_paths.npz`, written only
  by `solve --debug-paths`. `refine` intentionally does not touch this
  sidecar, so a partial re-solve cannot clobber a complete one.
- Encoded output videos land in the per-video output directory computed
  by [track_runner/tr_paths.py](../track_runner/tr_paths.py).
- Smoke-test output reuses a stable `output_smoke/` directory per
  [docs/REPO_STYLE.md](REPO_STYLE.md).

## Where to add new work

- New solver or scoring module: `track_runner/`, with a matching test under
  `tests/`.
- New shared helper used by both `track_runner/` and `tools/`:
  `common_tools/`.
- New batch or analysis script: `tools/`.
- New user-facing doc: `docs/` with SCREAMING_SNAKE_CASE filename.
- New implementation plan: `docs/active_plans/` while in flight, move to
  `docs/archive/` when superseded.

## Documentation map

- Root: [README.md](../README.md), [AGENTS.md](../AGENTS.md).
- Core docs live in [docs/](.); see the tree above.
- Style guides: [docs/PYTHON_STYLE.md](PYTHON_STYLE.md),
  [docs/REPO_STYLE.md](REPO_STYLE.md),
  [docs/MARKDOWN_STYLE.md](MARKDOWN_STYLE.md).
