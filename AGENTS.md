## Coding style

See [docs/PYTHON_STYLE.md](docs/PYTHON_STYLE.md) for Python conventions (tabs, no try/except, type hints, etc.).
See [docs/MARKDOWN_STYLE.md](docs/MARKDOWN_STYLE.md) for Markdown formatting rules.
See [docs/REPO_STYLE.md](docs/REPO_STYLE.md) for repo-wide conventions, naming, git, and changelog rules.
When making edits, document them in [docs/CHANGELOG.md](docs/CHANGELOG.md).
When in doubt, implement the changes the user asked for rather than waiting for a response.
When changing code, always run focused tests on changed code; documentation does not require tests.

## Python environment

AI agents (Codex/Claude) must run Python using `source source_me.sh && python3` (Python 3.12 only).
This is only for AI agents runtime, not a requirement for repo scripts.
On this user's macOS (Homebrew Python 3.12), Python modules are installed to
`/opt/homebrew/lib/python3.12/site-packages/`.

## Testing

Run pytest via: `pytest tests/`
Tests support the `-k` flag: `pytest tests/test_feature.py -k changed_file`
Smoke tests and pyflakes runners are under `tests/`.
See [docs/PYTEST_STYLE.md](docs/PYTEST_STYLE.md) for test-writing rules, fixtures, and failure triage.
See [docs/E2E_TESTS.md](docs/E2E_TESTS.md) for end-to-end tests under `tests/e2e/`.

## Project overview

Track runner is a tool for annotating and tracking a single runner in track footage.
- [README.md](README.md) - purpose, quick start, links to docs
- [docs/CODE_ARCHITECTURE.md](docs/CODE_ARCHITECTURE.md) - system design, pipeline stages, major components
- [docs/FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md) - directory map and what belongs where
- [docs/INSTALL.md](docs/INSTALL.md) - setup, dependencies, environment requirements
- [docs/USAGE.md](docs/USAGE.md) - CLI flags, practical examples

## Modes and workflow

The CLI operates in distinct modes. Full reference: [docs/MODES.md](docs/MODES.md).

Mode quick-reference:
- [docs/modes/SETUP.md](docs/modes/SETUP.md) - initial config for a new video
- [docs/modes/PREPARE.md](docs/modes/PREPARE.md) - fast read-video step for 4K HEVC sources (run before solve)
- [docs/modes/SEED.md](docs/modes/SEED.md) - human annotation of torso boxes
- [docs/modes/SOLVE.md](docs/modes/SOLVE.md) - FWD/BWD interval solve
- [docs/modes/REFINE.md](docs/modes/REFINE.md) - re-solve specific intervals
- [docs/modes/TARGET.md](docs/modes/TARGET.md) - preview solved trajectory
- [docs/modes/ANALYZE.md](docs/modes/ANALYZE.md) - diagnostic analysis
- [docs/modes/ENCODE.md](docs/modes/ENCODE.md) - produce the output video
- [docs/modes/EDIT.md](docs/modes/EDIT.md) - edit seeds directly

## Track runner design and contract

- [docs/TRACK_RUNNER_CONTRACT.md](docs/TRACK_RUNNER_CONTRACT.md) - hard non-negotiable invariants; wins on any conflict
- [docs/TRACK_RUNNER_DESIGN.md](docs/TRACK_RUNNER_DESIGN.md) - architecture philosophy and five-stage pipeline
- [docs/TRACK_RUNNER_V3_SPEC.md](docs/TRACK_RUNNER_V3_SPEC.md) - technical specification
- [docs/TR_FWD_BWD_MODEL_METHODOLOGY.md](docs/TR_FWD_BWD_MODEL_METHODOLOGY.md) - FWD/BWD pass mechanics
- [docs/TRACK_RUNNER_YAML_CONFIG.md](docs/TRACK_RUNNER_YAML_CONFIG.md) - YAML config schema

Key contract rules agents must know:
- Seeds are human-authored truth anchors; machine geometry is never a seed until a human commits it (C1).
- All spatial thresholds must be in torso-width units, not raw pixels (C2).
- FWD and BWD passes must remain independent for scoring (C9).
- Use one unified SCHEMA_VERSION constant everywhere (C10).

## Blob walk and Viterbi walker

- Walker lives under `track_runner/blob_walk/` and is the default solver on Stage-4-promoted intervals.
- Stage 3 stays pure Hermite; walker runs only where blob_pass=True.
- Walker costs are tuned via the `walker_costs` section of `track_runner/track_runner.config.yaml`.
- Anti-pattern: no cross-frame blob state (`last_blob`, `prev_accepted_blob`, chain memory). See design doc.

## Configuration

- [docs/TRACK_RUNNER_YAML_CONFIG.md](docs/TRACK_RUNNER_YAML_CONFIG.md) - full config reference
- [docs/TR_CONFIG_FILES.md](docs/TR_CONFIG_FILES.md) - file layout and loading order
- No directories in tr_config; no config_hash; frame data goes in .npz (prefer integers over floats).

## Schema versioning

- [docs/TR_SCHEMA_VERSION_HISTORY.md](docs/TR_SCHEMA_VERSION_HISTORY.md) - history of SCHEMA_VERSION bumps
- One unified SCHEMA_VERSION constant. No split item/object version constants.

## Common agent tasks

- Run repo-wide lint: `pytest tests/test_pyflakes_code_lint.py`
- Run ASCII compliance check: `pytest tests/test_ascii_compliance.py`
- Check Markdown links: `pytest tests/test_markdown_links.py`
- Run all fast unit tests: `pytest tests/`
- Determine repo root: `git rev-parse --show-toplevel`
- Only humans run `git commit`; agents stage and update `docs/CHANGELOG.md`.

## Git workflow

- Use `git mv` for all renames; never use `mv` on tracked files.
- Check for `.git/index.lock` before any index-writing git command; stop and report if present.
- See [docs/REPO_STYLE.md](docs/REPO_STYLE.md) for changelog rotation rules and active-plans folder structure.

## Developer reference

- [docs/TR_DEVELOPER_GUIDE.md](docs/TR_DEVELOPER_GUIDE.md) - developer workflows and internals
- [docs/COORDINATE_SPACES.md](docs/COORDINATE_SPACES.md) - SOURCE vs PROCESSED coordinate spaces
- [docs/TR_CAMERA_MOTION_METHOD.md](docs/TR_CAMERA_MOTION_METHOD.md) - camera motion Stage 1
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - known issues and debugging steps
- [docs/TR_MOTION_CUE_HEAT_MAP.md](docs/TR_MOTION_CUE_HEAT_MAP.md) - residual-motion blob pipeline
- [docs/TRACK_RUNNER_HISTORY.md](docs/TRACK_RUNNER_HISTORY.md) - evolution history and archived findings
