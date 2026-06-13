# Related projects

Sibling repos, shared libraries, and external dependencies that this project
integrates with or depends on.

## Bundled shared library

- `common_tools/` -- frame reader, video prober, and utilities shared across
  scripts in this repo. Not a separate pip package; lives at the repo root and
  is on the Python path via `source_me.sh`. See
  [common_tools/README.md](../common_tools/README.md) for the measured frame-
  read strategy table (including HEVC random-access cost breakdown).

## External tools and runtimes

- **ffmpeg / ffprobe** -- used by `prepare` for fast-read video transcode and
  by the encoder for final output. Must be installed separately; see
  [INSTALL.md](INSTALL.md).
- **OpenCV** (`opencv-python`) -- primary frame reader for working modes.
- **PyTorch / torchvision** -- optional; YOLO-assist seeding uses it when
  available. Not required for core solve/encode.

## Known gaps

- No upstream or consumer repos identified at time of writing. Add entries here
  when other repos depend on track-runner state files or artifacts.
