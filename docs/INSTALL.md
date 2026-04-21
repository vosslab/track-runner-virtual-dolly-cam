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

## YOLO weights (optional)

The optional person detector in [track_runner/tr_detection.py](../track_runner/tr_detection.py) uses a YOLOv8n ONNX model loaded through OpenCV DNN. Detection is not an active tracking signal in the analytical solver (see [docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C6); it is only used for optional seeding assistance.

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
