# Code architecture

## Design principle

> Analytic model for geometry, visual cues for validation and short-range correction.

Users place seed annotations to identify who the runner is. The solver interpolates
runner position between seeds using phase-correlation camera motion estimation and
cubic Hermite velocity models. See [docs/archive/TRACK_RUNNER_DESIGN.md](archive/TRACK_RUNNER_DESIGN.md)
for the original design philosophy and the plan at
[docs/archive/TRACK_RUNNER_PLAN_07_ANALYTICAL_REFACTOR.md](archive/TRACK_RUNNER_PLAN_07_ANALYTICAL_REFACTOR.md)
for the analytical rewrite rationale.

## Design decisions

### Cross-correlation over feature detection

All camera motion estimation uses FFT-based phase correlation (`cv2.phaseCorrelate`),
not feature detection (SIFT, ORB, etc.). This is a deliberate architectural choice.

**Why cross-correlation wins for this problem:**

- **Track video has few stable features.** A track surface is a large uniform region.
  Feature detectors find corners and blobs, but a rubberized track has almost none.
  Audience members, lane markings, and distant structures produce sparse, unreliable
  keypoints that cluster in small image regions.
- **Phase correlation uses the entire frame.** It computes a single global
  translation (and scale) from the full image, treating every pixel as signal. This
  makes it robust even when most of the frame is featureless track surface.
- **Fewer failure modes.** Feature detection pipelines require: detection, description,
  matching, outlier rejection (RANSAC), and homography estimation. Each stage can
  fail silently. Phase correlation is a single FFT operation with a clear quality
  metric (the correlation peak response).
- **Predictable runtime.** Phase correlation cost depends only on frame resolution.
  Feature detection cost depends on scene content (audience shots produce thousands
  of keypoints; empty track produces nearly zero).
- **Better sub-pixel accuracy.** The correlation peak is interpolated in frequency
  domain, giving smooth sub-pixel shifts without the quantization noise of
  keypoint localization.

**Where this applies in the codebase:**

- `camera_motion.py` -- all three estimators (`FixedZoomEstimator`,
  `DiscreteZoomEstimator`, `ContinuousZoomEstimator`) use `cv2.phaseCorrelate`
- `tools/diagnose_residual_motion.py` -- camera compensation for the motion
  diagnostic uses the same phase-correlation motion estimates via `SceneTransform`
- The legacy solver's Lucas-Kanade optical flow (`propagator.py`) is a sparse
  feature tracker and is intentionally being replaced by the analytical path

**Rule:** Do not introduce feature-detection-based camera motion estimation
(SIFT, ORB, AKAZE, or similar) without first demonstrating it outperforms phase
correlation on actual track video. The burden of proof is on the new method.

## Solver backends

The tool supports two solver backends selected by `processing.solver_backend` in config:

- `scene_interp` (default) -- analytical solver. Pre-computes camera motion via
  phase correlation, converts seeds to scene coordinates, fits directionally
  asymmetric Hermite curves for FWD/BWD propagation. No optical flow or detection
  during solve.
- `legacy_interval` -- original optical-flow solver. Uses Lucas-Kanade flow,
  patch correlation, and YOLO person detection. Kept for rollback.

## Pipeline overview

```
setup --> seed --> solve --> crop trajectory --> encode
  ^          ^         |
  |          |         v
  |          +--- refine (incremental re-solve)
  +--- camera config questionnaire (one-time)
```

1. **Setup** -- interactive CLI questionnaire collects camera properties (zoom type,
   height, position, track size). Stored in per-video config YAML.
2. **Seeding** -- user places bounding box annotations on key frames via PySide6 GUI.
3. **Interval solving** -- each pair of adjacent seeds defines an interval.
   - `scene_interp`: camera motion pre-computed for whole video, runner position
     interpolated analytically between seeds using directionally asymmetric
     FWD/BWD Hermite curves. Disagreement between directions signals uncertainty.
   - `legacy_interval`: optical flow frame-by-frame tracking with YOLO detection.
4. **Crop trajectory** -- adaptive smoothing with exponential filtering, deadband,
   and velocity capping produces a stable crop path.
5. **Encoding** -- ffmpeg-based encoding with optional filter pipeline.

## Module map

### CLI layer

- `cli.py` -- main entry point, subcommand dispatch, solver backend selection.
- `cli_args.py` -- argparse configuration for all subcommands (seed, edit, target,
  solve, refine, encode, analyze, setup).
- `setup_mode.py` -- interactive camera configuration questionnaire.

### Analytical solver (scene_interp backend)

- `camera_motion.py` -- `MotionTrack` dataclass, `MotionEstimator` interface,
  three estimators (`FixedZoomEstimator`, `DiscreteZoomEstimator`,
  `ContinuousZoomEstimator`), NPZ caching.
- `scene_coords.py` -- `SceneTransform` class for pixel-to-scene coordinate
  conversion using accumulated camera motion.
- `velocity_model.py` -- directional slope estimation, cubic Hermite interpolation,
  PCHIP log-space size interpolation, stationary lock, FWD/BWD analytical propagation.

### Core pipeline

- `seeding.py` -- seed frame collection logic.
- `interval_solver.py` -- `solve_interval_analytical()` (scene_interp) and
  `solve_interval()` (legacy) paths, interval merging, trajectory stitching.
- `propagator.py` -- frame-to-frame tracking wrapper. Delegates to velocity_model
  when scene_transform is provided, otherwise uses legacy optical flow.
- `tr_crop.py` -- adaptive crop rectangle computation with smoothing.
- `encoder.py` -- video encoding with optional filter pipeline.

### Scoring and review

- `scoring.py` -- `score_interval_analytical()` (v3: agreement, velocity_consistency,
  size_consistency, motion_quality, occlusion_fraction) and legacy `score_interval()`
  (v2: agreement, identity, competitor margin).
- `review.py` -- interval quality assessment, severity classification, seed
  suggestions. Reads confidence_tier (v3) or confidence (v2) via `_get_confidence()`.
- `regime_classifier.py` -- motion regime classification (straight, curve, stationary).
- `regime_policies.py` -- per-regime parameter policies.

### Detection (legacy/subordinate)

- `tr_detection.py` -- YOLOv8n person detection. Not used in scene_interp solve
  path. Kept for future subordinate visual validation roles.
- `hypothesis.py` -- stub. Competitor tracking removed in analytical rewrite.
- `seed_color.py` -- jersey color extraction.
- `box_utils.py` -- bounding box utilities.

### State and config

- `state_io.py` -- JSON serialization for seeds, intervals, and diagnostics.
  Diagnostics v3 stores nested `interval_score_v2` for analytical mode.
- `tr_config.py` -- YAML config parsing with camera section and solver_backend.
- `tr_paths.py` -- path utilities for state, output, and motion cache files.
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

- `encode_analysis.py` -- encoding quality analysis. Reports analytical metrics
  (velocity_consistency, size_consistency, motion_quality) or legacy metrics
  (convergence, identity, competitor margin) depending on solver mode.
- `video_io.py` -- video I/O utilities.
- `key_input.py` -- keyboard input handling.

### Shared utilities

- `common_tools/frame_filters.py` -- frame filtering utilities.
- `common_tools/frame_reader.py` -- frame reading utilities.
- `common_tools/tools_common.py` -- shared tool helpers.

### Configuration files

- `track_runner/track_runner.config.yaml` -- default runtime config (includes
  camera section, solver_backend, detection, processing).
- `track_runner/overlay_styles.yaml` -- visualization overlay styles.

## Diagnostics schema

Two versions coexist:

- **v2 (legacy)** -- flat fields: `agreement_score`, `identity_score`,
  `competitor_margin`, `confidence`. Written by legacy solver.
- **v3 (analytical)** -- nested `interval_score` dict with: `agreement`,
  `velocity_consistency`, `size_consistency`, `motion_quality`,
  `occlusion_fraction`, `confidence_tier`, `severity`, `failure_reasons`,
  `warning_flags`. Written by analytical solver.

Seeds JSON format is unchanged across both versions.
