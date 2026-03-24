# File structure

```
track-runner-virtual-dolly-cam/
|-- README.md                  project overview and quick start
|-- AGENTS.md                  AI agent instructions
|-- CLAUDE.md                  Claude Code configuration
|-- VERSION                    CalVer version (26.02)
|-- LICENSE.LGPL_v3            LGPL v3 license for code
|-- LICENSE.CC_BY_4_0          CC BY 4.0 license for docs
|-- source_me.sh               shell environment bootstrap
|-- pip_requirements.txt        runtime Python dependencies
|-- pip_requirements-dev.txt    development Python dependencies
|-- .gitignore
|
|-- track_runner/              main application package
|   |-- track_runner.py        entry point script
|   |-- cli.py                 subcommand dispatch and orchestration
|   |-- cli_args.py            argparse configuration
|   |-- interval_solver.py     forward/backward propagation solver
|   |-- propagator.py          optical flow and patch correlation
|   |-- tr_crop.py             adaptive crop trajectory
|   |-- encoder.py             video encoding with filters
|   |-- seeding.py             seed collection logic
|   |-- seed_editor.py         seed editing logic
|   |-- scoring.py             interval confidence scoring
|   |-- review.py              interval quality assessment
|   |-- tr_detection.py        YOLOv8n person detection
|   |-- hypothesis.py          track hypothesis representation
|   |-- regime_classifier.py   motion regime classification
|   |-- regime_policies.py     per-regime parameter policies
|   |-- seed_color.py          jersey color extraction
|   |-- box_utils.py           bounding box utilities
|   |-- encode_analysis.py     encoding quality analysis
|   |-- video_io.py            video I/O utilities
|   |-- key_input.py           keyboard input handling
|   |-- state_io.py            JSON state serialization
|   |-- tr_config.py           YAML config parsing
|   |-- tr_paths.py            path utilities
|   |-- tr_video_identity.py   video file identity tracking
|   |-- overlay_config.py      overlay configuration
|   |-- track_runner.config.yaml   default runtime config
|   |-- overlay_styles.yaml        overlay style definitions
|   |
|   +-- ui/                    PySide6 GUI modules
|       |-- app_shell.py       main window and theme
|       |-- base_controller.py annotation controller base
|       |-- seed_controller.py seed placement interface
|       |-- edit_controller.py seed editing interface
|       |-- target_controller.py  weak interval targeting
|       |-- frame_view.py      frame display widget
|       |-- workspace.py       multi-frame workspace
|       |-- overlay_items.py   visual overlays
|       |-- status_presenter.py   status bar display
|       |-- zoom_controls.py   zoom interface
|       |-- actions.py         UI action handlers
|       +-- theme.py           dark/light theme definitions
|
|-- common_tools/              shared utility modules
|   |-- frame_filters.py       frame filtering utilities
|   |-- frame_reader.py        frame reading utilities
|   +-- tools_common.py        shared tool helpers
|
|-- tools/                     batch processing and analysis scripts
|   |-- batch_encode_experiment.py
|   |-- batch_smart_experiment.py
|   |-- analyze_crop_path_stability.py
|   +-- analyze_track_runner_json.py
|
|-- tests/                     pytest test suite
|   |-- test_pyflakes_code_lint.py   pyflakes lint gate
|   |-- test_ascii_compliance.py     ASCII encoding checks
|   |-- test_import_star.py          import * prevention
|   |-- test_import_dot.py           relative import prevention
|   |-- test_import_requirements.py  dependency declaration checks
|   |-- test_indentation.py          tab indentation checks
|   |-- test_init_files.py           __init__.py style checks
|   |-- test_shebangs.py             shebang requirement checks
|   |-- test_whitespace.py           trailing whitespace checks
|   |-- test_bandit_security.py      security lint gate
|   |-- git_file_utils.py            shared test utility
|   +-- conftest.py                  pytest configuration
|
|-- devel/                     developer tooling
|   |-- commit_changelog.py    changelog commit automation
|   +-- submit_to_pypi.py      PyPI submission tool
|
|-- docs/                      documentation
|   |-- CHANGELOG.md           chronological change history
|   |-- AUTHORS.md             maintainer information
|   |-- INSTALL.md             setup and dependencies
|   |-- USAGE.md               subcommand reference and workflow
|   |-- CODE_ARCHITECTURE.md   pipeline and module overview
|   |-- FILE_STRUCTURE.md      this file
|   |-- PYTHON_STYLE.md        Python style conventions
|   |-- REPO_STYLE.md          repository conventions
|   |-- MARKDOWN_STYLE.md      Markdown style conventions
|   |
|   +-- archive/               historical design documents
|       |-- TRACK_RUNNER_DESIGN.md
|       |-- TRACK_RUNNER_V3_SPEC.md
|       |-- TRACK_RUNNER_V3_FINDINGS.md
|       |-- TRACK_RUNNER_SPEC.md
|       |-- TRACK_RUNNER_V2_SPEC.md
|       |-- TRACK_RUNNER_HISTORY.md
|       |-- TRACK_RUNNER_KEYBINDINGS.md
|       |-- TRACK_RUNNER_YAML_CONFIG.md
|       |-- TRACK_RUNNER_ANALYZE_AND_ENCODE.md
|       |-- TRACK_RUNNER_CROP_PATH_FINDINGS.md
|       |-- TRACK_RUNNER_TOOL_PLAN.md
|       +-- TRACK_RUNNER_PLAN_01 through 05
```
