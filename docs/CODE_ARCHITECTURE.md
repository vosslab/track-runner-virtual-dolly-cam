# Code architecture

## Design principle

> Human establishes identity. Machine interpolates geometry.

Users place seed annotations to identify who the runner is. The solver propagates tracking between seeds using optical flow, patch correlation, and person detection. See [docs/archive/TRACK_RUNNER_DESIGN.md](archive/TRACK_RUNNER_DESIGN.md) for the full design philosophy.

## Pipeline overview

```
seed --> solve --> crop trajectory --> encode
  ^         |
  |         v
  +--- refine (incremental re-solve)
```

1. **Seeding** -- user places bounding box annotations on key frames via a PySide6 GUI.
2. **Interval solving** -- each pair of adjacent seeds defines an interval. Forward and backward propagation meet in the middle. Disagreement between directions signals uncertainty.
3. **Crop trajectory** -- adaptive smoothing with exponential filtering, deadband, and velocity capping produces a stable crop path.
4. **Encoding** -- ffmpeg-based encoding with optional filter pipeline (bilateral, auto-levels, hqdn3d).

## Module map

### CLI layer

- `cli.py` -- main entry point, subcommand dispatch, orchestration.
- `cli_args.py` -- argparse configuration for all subcommands.

### Core pipeline

- `seeding.py` -- seed frame collection logic.
- `interval_solver.py` -- forward/backward propagation, interval merging, and confidence scoring.
- `propagator.py` -- frame-to-frame optical flow and patch correlation.
- `tr_crop.py` -- adaptive crop rectangle computation with smoothing.
- `encoder.py` -- video encoding with optional filter pipeline.

### Scoring and review

- `scoring.py` -- interval confidence and agreement scoring.
- `review.py` -- interval quality assessment and severity classification.
- `regime_classifier.py` -- motion regime classification (straight, curve, stationary).
- `regime_policies.py` -- per-regime parameter policies.

### Detection and tracking

- `tr_detection.py` -- YOLOv8n person detection via OpenCV DNN (no ultralytics at runtime).
- `hypothesis.py` -- track hypothesis representation.
- `seed_color.py` -- jersey color extraction for identity matching.
- `box_utils.py` -- bounding box utilities.

### State and config

- `state_io.py` -- JSON serialization for seeds, intervals, and diagnostics.
- `tr_config.py` -- YAML config file parsing.
- `tr_paths.py` -- path utilities for state and output files.
- `tr_video_identity.py` -- video file identity tracking (hash-based).

### UI (PySide6)

All UI modules are in `track_runner/ui/`:

- `app_shell.py` -- main window with dark/light theme management.
- `base_controller.py` -- shared annotation controller base class.
- `seed_controller.py` -- seed placement interface.
- `edit_controller.py` -- seed editing interface.
- `target_controller.py` -- weak interval targeting interface.
- `frame_view.py` -- frame display widget.
- `workspace.py` -- multi-frame workspace.
- `overlay_items.py` -- visual overlays (bounding boxes, lines).
- `status_presenter.py` -- status bar display.
- `zoom_controls.py` -- zoom interface.
- `actions.py` -- UI action handlers.
- `theme.py` -- dark/light theme definitions.

### Analysis and encoding

- `encode_analysis.py` -- encoding quality analysis.
- `video_io.py` -- video I/O utilities.
- `key_input.py` -- keyboard input handling.

### Shared utilities

- `common_tools/frame_filters.py` -- frame filtering utilities.
- `common_tools/frame_reader.py` -- frame reading utilities.
- `common_tools/tools_common.py` -- shared tool helpers.

### Configuration files

- `track_runner/track_runner.config.yaml` -- default runtime config.
- `track_runner/overlay_styles.yaml` -- visualization overlay styles.
