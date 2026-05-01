# Track runner virtual dolly cam

Track runner is a Python tool that tracks a runner in track meet video and produces a cropped, stabilized "virtual dolly camera" output. Users place seed annotations on key frames, and the solver propagates tracking between seeds to produce a smooth cropped video that follows the athlete.

**Status:** v26.05, active development.

## What it produces

Given a wide track meet video, track runner emits a cropped MKV (or MP4 with `--mp4`) that stays centered on the chosen athlete with smooth virtual-dolly motion. Sample inputs and outputs live under `TRACK_VIDEOS/` (gitignored) on a working install. See [docs/modes/ENCODE.md](docs/modes/ENCODE.md) for the encode pipeline and [docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md](docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md) for output format details.

## Design philosophy

Human establishes identity; the machine interpolates geometry. See
[docs/TRACK_RUNNER_DESIGN.md](docs/TRACK_RUNNER_DESIGN.md) for the
design philosophy and
[docs/TRACK_RUNNER_CONTRACT.md](docs/TRACK_RUNNER_CONTRACT.md) for the
non-negotiable invariants.

## Quick start

Prerequisites: install system and Python dependencies first -- see [docs/INSTALL.md](docs/INSTALL.md).

```bash
source source_me.sh
python3 track_runner/track_runner.py -i VIDEO.mp4 setup
python3 track_runner/track_runner.py -i VIDEO.mp4 seed
python3 track_runner/track_runner.py -i VIDEO.mp4 solve
python3 track_runner/track_runner.py -i VIDEO.mp4 target
python3 track_runner/track_runner.py -i VIDEO.mp4 refine
# Repeat target + refine until interval scores are acceptable.
python3 track_runner/track_runner.py -i VIDEO.mp4 encode
```

`setup` runs once per video and is required before `solve`, `refine`, or `target`. To see flags for any subcommand, append `-h` (for example `python3 track_runner/track_runner.py -i VIDEO.mp4 encode -h`). For the per-mode reference, see [docs/MODES.md](docs/MODES.md); for the workflow narrative, see [docs/USAGE.md](docs/USAGE.md).

## Documentation

### Run it
- [docs/INSTALL.md](docs/INSTALL.md): Setup steps, system dependencies, and pip requirements.
- [docs/MODES.md](docs/MODES.md): Per-mode (subcommand) reference. Start here when you want to know what a specific mode does.
- [docs/USAGE.md](docs/USAGE.md): Workflow narrative, global options, configuration, and keyboard-shortcut pointers.
- [docs/TRACK_RUNNER_KEYBINDINGS.md](docs/TRACK_RUNNER_KEYBINDINGS.md): Annotation UI keyboard shortcuts.

### Understand it
- [docs/TRACK_RUNNER_DESIGN.md](docs/TRACK_RUNNER_DESIGN.md): Design philosophy and signal hierarchy behind the solver.
- [docs/TRACK_RUNNER_CONTRACT.md](docs/TRACK_RUNNER_CONTRACT.md): Hard invariants the tool and contributors must respect.

### Develop on it
- [docs/CODE_ARCHITECTURE.md](docs/CODE_ARCHITECTURE.md): Pipeline overview and module descriptions.
- [docs/FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md): Directory map with what belongs where.
- [docs/CHANGELOG.md](docs/CHANGELOG.md): Chronological record of changes.
- [docs/AUTHORS.md](docs/AUTHORS.md): Maintainer and attribution information.

### Style guides

- [docs/PYTHON_STYLE.md](docs/PYTHON_STYLE.md): Python formatting, imports, and testing conventions.
- [docs/REPO_STYLE.md](docs/REPO_STYLE.md): Repository structure, naming, and versioning conventions.
- [docs/MARKDOWN_STYLE.md](docs/MARKDOWN_STYLE.md): Markdown writing and formatting conventions.

### Design archive

Historical design documents, specifications, and implementation plans are in [docs/archive/](docs/archive/).

## Testing

```bash
source source_me.sh && python3 -m pytest tests/ -q
```

## License

Code is licensed under [LGPLv3](LICENSE.LGPL_v3). Non-code content is licensed under [CC BY 4.0](LICENSE.CC_BY_4_0).

## Maintainer

Neil Voss, https://bsky.app/profile/neilvosslab.bsky.social
