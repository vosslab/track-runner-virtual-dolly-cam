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
|   |-- walker_bundle.py           Stage-4 walker input-bundle seam
|   |-- fastread_video.py          fast-read video creation and live structural validation
|   |-- track_runner.config.yaml   default runtime config
|   |-- overlay_styles.yaml        overlay style definitions
|   |
|   +-- blob_walk/                 windowed path-selection walker package
|   |   |-- __init__.py            package marker (empty)
|   |   |-- walk_walker.py         window-buffered blob walker
|   |   |-- walk_viterbi.py        Viterbi DP over candidate lattice
|   |   |-- walk_motion_gate.py    per-step motion gate predicates
|   |   |-- walk_status.py         per-frame status enum
|   |   |-- walk_io.py             walker NPZ reader/writer
|   |   `-- walk_debug_log.py      debug-log schema and writer
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
|   |-- goodbox.py                 FFT-friendly crop dimension snap
|   |-- probe_video.py             mediainfo metadata probe (warns on 4K+ sources)
|   `-- tools_common.py            shared tool helpers
|
|-- data/                          non-Python build-time assets
|   `-- js/
|       `-- analyze_report_renderer.js  vanilla-JS canvas renderer for
|                                        analyze --plot HTML reports
|
|-- tools/                         analysis and batch scripts
|   |-- dump_cli_help.py           dump argparse help text for all subcommands
|   |-- refresh_mode_docs.py       regenerate --help blocks in docs/modes/
|   `-- blob_walk_v2/              walker diagnostics and HTML visualizer
|       |-- walk_driver.py         batch walker run driver
|       |-- walk_paths.py          output path helpers
|       |-- walk_util.py           shared walker utilities
|       |-- make_walk_html_v2.py   per-interval HTML tile generator
|       `-- check_render_manifest.py  render manifest validator
|
|-- tests/                         pytest suite
|   |-- conftest.py                pytest configuration
|   |-- file_utils.py          shared test helper
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
|   `-- unit and integration (selected)
|       |-- test_fastread_video.py
|       |-- test_tr_camera_motion.py
|       |-- test_tr_interval_fingerprint.py
|       |-- test_tr_race_phases.py
|       |-- test_tr_residual_heat_map.py
|       |-- test_tr_scene_coords.py
|       |-- test_tr_scoring.py
|       |-- test_tr_seed_schema_v3.py
|       |-- test_tr_solve_queue.py
|       |-- test_tr_solver_integration.py
|       |-- test_tr_velocity_model.py
|       |-- test_walk_cost_model.py
|       |-- test_walk_viterbi_brute_force.py
|       |-- test_walker_costs_config.py
|       |-- test_walker_flag.py
|       |-- test_walker_stall_fallback.py
|       |-- test_blob_walk_v2_windowed.py
|       `-- analyze_report_fixtures.py    shared fixtures for analyze_report tests
|
|-- devel/                         developer tooling
|   |-- bump_version.py            version bump helper
|   |-- changelog_lib.py           shared changelog parser and git helpers
|   |-- commit_changelog.py        changelog commit automation
|   |-- query_changelog.py         changelog search by date/category/keyword
|   |-- rotate_changelog.py        changelog rotation enforcer
|   `-- submit_to_pypi.py          PyPI submission tool
|
|-- TRACK_VIDEOS/                  sample / working videos (gitignored content)
|-- corpus_walk/                   per-video walker output artifacts (gitignored)
|
`-- docs/                          documentation
    |-- CHANGELOG.md               chronological change history
    |-- AUTHORS.md                 maintainer information
    |-- INSTALL.md                 setup and dependencies
    |-- USAGE.md                   workflow narrative, global options, configuration
    |-- TROUBLESHOOTING.md         known issues with symptoms, causes, next steps
    |-- MODES.md                   per-mode (subcommand) index pointing to docs/modes/
    |-- modes/                     per-mode reference pages with auto-stamped --help
    |   |-- PREPARE.md
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
    |-- TR_FWD_BWD_MODEL_METHODOLOGY.md   coupled FWD/BWD model mechanics
    |-- TR_MOTION_CUE_HEAT_MAP.md  heat-map mechanism technical doc
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
  documented in [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) and
  generated by [tr_paths.py](../track_runner/tr_paths.py).
- Per-video state files (seeds JSON, geometry NPZ, interval-scores JSON,
  diagnostics JSON, camera-motion NPZ) live under the per-video
  `tr_config` store. See
  [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).
- Race-start confirmation contact sheet PNG:
  `<video>.track_runner.race_start_check.png`, produced by `solve` /
  `refine` Stage 2 whenever `race_start_frame` is detected. Sidecar path
  computed by `default_race_start_contact_sheet_path` in
  [tr_paths.py](../track_runner/tr_paths.py).
- Debug paths NPZ: `<video>.track_runner.debug_paths.npz`, written only
  by `solve --debug-paths`. `refine` intentionally does not touch this
  sidecar, so a partial re-solve cannot clobber a complete one.
- Encoded output videos land in the per-video output directory computed
  by [tr_paths.py](../track_runner/tr_paths.py).
- Smoke-test output reuses a stable `output_smoke/` directory per
  [REPO_STYLE.md](REPO_STYLE.md).

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
- Style guides: [PYTHON_STYLE.md](PYTHON_STYLE.md),
  [REPO_STYLE.md](REPO_STYLE.md),
  [MARKDOWN_STYLE.md](MARKDOWN_STYLE.md).
