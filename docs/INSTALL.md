# Install

## Python

Requires Python 3.12. On macOS with Homebrew:

```bash
brew install python@3.12
```

## System dependencies

- **ffmpeg** -- video encoding and decoding (used by `cv2.VideoCapture` and `cv2.VideoWriter`).
- **mediainfo** -- video metadata probing.
- **mkvtoolnix** -- `mkvmerge` for losslessly remuxing MP4/MOV sources to MKV.

```bash
brew install ffmpeg mediainfo mkvtoolnix
```

## Source video format

`FrameReader` requires `.mkv` source video. MP4/MOV users must remux losslessly once before use:

```bash
mkvmerge -o input.mkv input.mov
```

The pipeline does not transcode; remux is a fast, lossless container repackage.

## Pip dependencies

Install runtime dependencies:

```bash
pip3 install -r pip_requirements.txt
```

This installs numpy, opencv-python, PySide6, pyyaml, rich, and scipy.

For development tools (pytest, pyflakes, bandit):

```bash
pip3 install -r pip_requirements-dev.txt
```

## YOLO weights (optional)

The optional person detector in [tr_detection.py](../track_runner/tr_detection.py) uses a YOLOv8n ONNX model loaded through OpenCV DNN. Detection is not an active tracking signal in the analytical solver (see [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C6); it is only used for optional seeding assistance.

When needed, the detector expects the model at `~/.cache/track_runner/yolov8n.onnx`. Export it once from the upstream ultralytics package:

```bash
pip3 install ultralytics
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='onnx')"
mkdir -p ~/.cache/track_runner
mv yolov8n.onnx ~/.cache/track_runner/
pip3 uninstall ultralytics
```

The `ultralytics` package is only needed for the one-time export. It is not a runtime dependency and is not listed in [pip_requirements.txt](../pip_requirements.txt).

## Bootstrap

Before running any commands, source the environment bootstrap:

```bash
source source_me.sh
```

This sets `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.

## First run: per-video setup

The environment steps above (Python, system deps, pip, bootstrap) are a
one-time install. Each video then needs its own `setup` pass before
`solve`, `refine`, or `target` will run:

```bash
python track_runner/track_runner.py -i VIDEO.mp4 setup
```

`setup` is an interactive questionnaire that captures camera zoom type,
height, position, and track size for this specific video. Those answers
are written to the per-video config YAML. `setup` is required before
`solve`, `refine`, or `target`, and should ideally run before `seed` as
well so the annotation UI has the correct camera/track context from the
first seed. For the full file layout (config YAML, seeds, geometry
cache, diagnostics, contact sheet, debug paths, encoded output), see
[TR_CONFIG_FILES.md](TR_CONFIG_FILES.md). For the full subcommand
reference and workflow, see [USAGE.md](USAGE.md).
