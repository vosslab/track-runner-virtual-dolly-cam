# Install

## Python

Requires Python 3.12. On macOS with Homebrew:

```bash
brew install python@3.12
```

## System dependencies

- **ffmpeg** -- video encoding and decoding.
- **mediainfo** -- video metadata probing.

```bash
brew install ffmpeg mediainfo
```

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

## YOLO weights (one-time setup)

The person detector uses a YOLOv8n ONNX model loaded through OpenCV DNN. The model file is cached at `~/.cache/track_runner/yolov8n.onnx`. To create it:

```bash
pip3 install ultralytics
python3 tools/export_yolo_onnx.py
pip3 uninstall ultralytics
```

The `ultralytics` package is only needed for the one-time export. It is not a runtime dependency.

## Bootstrap

Before running any commands, source the environment bootstrap:

```bash
source source_me.sh
```

This sets `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.
