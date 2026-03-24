## 2026-03-24

### Additions and New Features

- Ported track runner code from parent repo into self-contained project repository.
- Copied `common_tools/` package (frame_filters, frame_reader, tools_common) into the repo.
- Created [docs/INSTALL.md](docs/INSTALL.md) with setup steps, system dependencies, and pip requirements.
- Created [docs/USAGE.md](docs/USAGE.md) with subcommand reference, global options, and typical workflow.
- Created [docs/CODE_ARCHITECTURE.md](docs/CODE_ARCHITECTURE.md) with pipeline overview and module descriptions.
- Created [docs/FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md) with directory map.
- Populated [pip_requirements.txt](pip_requirements.txt) with runtime dependencies: numpy, opencv-python, PySide6, pyyaml, rich, scipy.
- Rewrote [README.md](README.md) to describe the track runner project instead of the starter template.

### Fixes and Maintenance

- Replaced starter-template changelog with project-specific history.

### Decisions and Failures

- `common_tools` package was copied separately from the parent repo to satisfy the `cli.py` import.
