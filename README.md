# Track runner virtual dolly cam

Track runner is a Python tool that tracks a runner in track meet video and produces a cropped, stabilized "virtual dolly camera" output. Users place seed annotations on key frames, and the solver propagates tracking between seeds to produce a smooth cropped video that follows the athlete.

**Status:** v26.02, active development.

## Quick start

```bash
source source_me.sh

# 1. Place seed annotations on the runner
python track_runner/track_runner.py -i VIDEO.mp4 seed

# 2. Solve tracking between seeds
python track_runner/track_runner.py -i VIDEO.mp4 solve

# 3. Encode cropped output video
python track_runner/track_runner.py -i VIDEO.mp4 encode
```

See [docs/USAGE.md](docs/USAGE.md) for the full subcommand reference and workflow details.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md): Setup steps, system dependencies, and pip requirements.
- [docs/USAGE.md](docs/USAGE.md): Subcommand reference, global options, and typical workflow.
- [docs/TRACK_RUNNER_KEYBINDINGS.md](docs/TRACK_RUNNER_KEYBINDINGS.md): Annotation UI keyboard shortcuts.
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
source source_me.sh && python -m pytest tests/ -q
```

## License

Code is licensed under [LGPLv3](LICENSE.LGPL_v3). Non-code content is licensed under [CC BY 4.0](LICENSE.CC_BY_4_0).

## Maintainer

Neil Voss, https://bsky.app/profile/neilvosslab.bsky.social
