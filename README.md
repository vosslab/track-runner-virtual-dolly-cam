# Track runner virtual dolly cam

A Python tool for track-meet videographers that produces a cropped, stabilized virtual-dolly output following a single athlete. Users place a handful of seed annotations; the solver propagates geometry between seeds across the full race.

**Status:** v26.05, active development.

## What it produces

Given a wide track meet video, track runner emits a cropped MKV (or MP4 with `--mp4`) that stays centered on the chosen athlete with smooth virtual-dolly motion. Supply your own video; local media is private and is not included in the repository. See [docs/modes/ENCODE.md](docs/modes/ENCODE.md) for the encode pipeline and [docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md](docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md) for output format details.

## Design philosophy

Human establishes identity; the machine interpolates geometry. See
[docs/TRACK_RUNNER_DESIGN.md](docs/TRACK_RUNNER_DESIGN.md) for the
design philosophy and
[docs/TRACK_RUNNER_CONTRACT.md](docs/TRACK_RUNNER_CONTRACT.md) for the
non-negotiable invariants.

## Quick start

Prerequisites: install system and Python dependencies first -- see [docs/INSTALL.md](docs/INSTALL.md).

The canonical run order:

```text
[ prepare ] -> setup -> seed -> solve -> ( target -> refine ) x N -> encode
                                               ^________|
                                         repeat until scores OK
```

```bash
source source_me.sh
# Optional but recommended for 4K HEVC sources -- creates a fast-read working video.
python3 track_runner/track_runner.py -i VIDEO.mp4 prepare
python3 track_runner/track_runner.py -i VIDEO.mp4 setup
python3 track_runner/track_runner.py -i VIDEO.mp4 seed
python3 track_runner/track_runner.py -i VIDEO.mp4 solve
python3 track_runner/track_runner.py -i VIDEO.mp4 target
python3 track_runner/track_runner.py -i VIDEO.mp4 refine
# Repeat target + refine until interval scores are acceptable.
python3 track_runner/track_runner.py -i VIDEO.mp4 encode
```

`setup` runs once per video and is required before `solve`, `refine`, or `target`. `prepare` is optional but recommended for 4K HEVC sources; see [docs/modes/PREPARE.md](docs/modes/PREPARE.md). To see flags for any subcommand, append `-h` (for example `python3 track_runner/track_runner.py -i VIDEO.mp4 encode -h`). For the per-mode reference, see [docs/MODES.md](docs/MODES.md); for the workflow narrative, see [docs/USAGE.md](docs/USAGE.md).

## Documentation

### Run it
- [docs/INSTALL.md](docs/INSTALL.md): Setup steps, system dependencies, and pip requirements.
- [docs/MODES.md](docs/MODES.md): Per-mode (subcommand) reference. Start here when you want to know what a specific mode does.
- [docs/modes/PREPARE.md](docs/modes/PREPARE.md): Optional first step for 4K HEVC sources; creates a fast-read working video for lower decode latency.
- [docs/USAGE.md](docs/USAGE.md): Workflow narrative, global options, configuration, and keyboard-shortcut pointers.
- [docs/TRACK_RUNNER_KEYBINDINGS.md](docs/TRACK_RUNNER_KEYBINDINGS.md): Annotation UI keyboard shortcuts.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): Known issues with symptoms, causes, and next steps.

### Understand it
- [docs/TRACK_RUNNER_DESIGN.md](docs/TRACK_RUNNER_DESIGN.md): Design philosophy and signal hierarchy behind the solver.
- [docs/TRACK_RUNNER_CONTRACT.md](docs/TRACK_RUNNER_CONTRACT.md): Hard invariants the tool and contributors must respect.
- [docs/ENCODE_DESIGN.md](docs/ENCODE_DESIGN.md): Encode-mode design choices and implementation, including crop trajectory, overlay tiers, and velocity-arrow projection.

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
