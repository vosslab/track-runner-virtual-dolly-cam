# Code architecture

## Design principle

> Human establishes identity. Machine interpolates geometry.

Users place seed annotations to identify who the runner is at specific frames.
The solver interpolates runner position between seeds using phase-correlation
camera motion estimation and cubic Hermite velocity models, with optional
per-frame residual-motion blob observations. See
[TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) for the hard
invariants and [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) for the
reasoning.

## Design decisions

### Cross-correlation over feature detection

All camera motion estimation uses FFT-based phase correlation
(`cv2.phaseCorrelate`), not feature detection (SIFT, ORB, etc.). This is a
deliberate architectural choice.

**Why cross-correlation wins for this problem:**

- **Track video has few stable features.** A track surface is a large uniform
  region. Feature detectors find corners and blobs, but a rubberized track has
  almost none. Audience members, lane markings, and distant structures produce
  sparse, unreliable keypoints that cluster in small image regions.
- **Phase correlation uses the entire frame.** It computes a single global
  translation (and scale) from the full image, treating every pixel as signal.
  This makes it robust even when most of the frame is featureless track surface.
- **Fewer failure modes.** Feature detection pipelines require: detection,
  description, matching, outlier rejection (RANSAC), and homography
  estimation. Each stage can fail silently. Phase correlation is a single FFT
  operation with a clear quality metric (the correlation peak response).
- **Predictable runtime.** Phase correlation cost depends only on frame
  resolution. Feature detection cost depends on scene content (audience shots
  produce thousands of keypoints; empty track produces nearly zero).
- **Better sub-pixel accuracy.** The correlation peak is interpolated in
  frequency domain, giving smooth sub-pixel shifts without the quantization
  noise of keypoint localization.

**Where this applies in the codebase:**

- [camera_motion.py](../track_runner/camera_motion.py) -- all
  three estimators (`FixedZoomEstimator`, `DiscreteZoomEstimator`,
  `ContinuousZoomEstimator`) use `cv2.phaseCorrelate`.
- `diagnose_residual_motion.py`
  -- camera compensation for the motion diagnostic uses the same
  phase-correlation motion estimates via `SceneTransform`.

**Rule:** Do not introduce feature-detection-based camera motion estimation
(SIFT, ORB, AKAZE, or similar) without first demonstrating it outperforms
phase correlation on actual track video. The burden of proof is on the new
method.

## Pipeline overview

```
[prepare] --> setup --> seed --> solve --> (target --> refine)* --> encode
                ^          ^         |              |
                |          |         v              v
                |          +----- interval scoring and review
                +--- camera config questionnaire (one-time)
```

0. **Prepare** (optional) -- transcodes the original source video to an
   H.264 8-bit fast-read working copy beside the original
   (`<stem>.fastread.mkv`). Recommended for 4K HEVC sources where
   random-access OpenCV reads are expensive. `fastread_video.create_fastread_video`
   owns the transcode; `validate_fastread_structural` validates the copy
   live on every subsequent run. `prepare` dispatches before config/data-path
   setup so it does not require `setup` to have been run first. The fast-read
   path is the only decode path for working modes when the validated copy
   exists; `encode` always uses the original. See
   [modes/PREPARE.md](modes/PREPARE.md).
1. **Setup** -- interactive CLI questionnaire collects camera properties
   (zoom type, height, position, track size). Stored in per-video config YAML.
   `setup_mode.run_setup` owns this step; `cli.py` gates `solve`, `refine`,
   and `target` on setup having been run, and setup should ideally precede
   `seed` as well so annotation has the correct camera/track context
   (`seed` itself is not code-gated).
2. **Seeding** -- user places bounding-box annotations on key frames via a
   PySide6 GUI. Seeds are the truth anchors for solve (contract C4).
3. **Interval solving** -- each pair of adjacent seeds defines an interval.
   Camera motion is pre-computed for the whole video; runner position is
   interpolated analytically between seeds using directionally asymmetric
   FWD/BWD cubic Hermite curves. Per-frame residual-motion blob observations
   may snap the prediction locally when proximity, direction, and temporal
   smoothness gates all pass (see
   `FWD_BWD_MODEL_METHODOLOGY.md` and
   [RESIDUAL_MOTION_OBSERVATIONS.md](archive/RESIDUAL_MOTION_OBSERVATIONS.md)).
   Intervals are independent (contract C3) and solved in parallel workers.
   `solve` is the full solve from scratch. The solve-only flag
   `--debug-paths` also writes the per-interval FWD/BWD debug NPZ sidecar
   consumed later by `encode --debug` and `analyze --debug`.
4. **Target and refine** -- `target` is a user-guided seed-authoring step
   that surfaces weak intervals (and, with `--race-start`, frames around
   the detected race-start transition) for human correction; it does not
   auto-generate seeds. `refine` is strictly incremental: it re-solves
   only intervals affected by the new seeds and refuses any full solve
   (contract C6). The race-start confirmation contact sheet PNG is a
   `solve` / `refine` Stage 2 diagnostic artifact, not a seed-authoring
   output.
5. **Encoding** -- `tr_crop.py` builds an adaptive crop trajectory with
   exponential smoothing, deadband, and velocity capping; `encoder.py`
   drives ffmpeg to produce the final cropped video with optional filters.

## Module map

### CLI and orchestration

- [track_runner.py](../track_runner/track_runner.py) -- entry
  point shim.
- [cli.py](../track_runner/cli.py) -- subcommand dispatch, solve
  and refine orchestration, diagnostics writers. The `prepare` arm
  (`_mode_prepare`) dispatches before config/data-path setup.
- [cli_args.py](../track_runner/cli_args.py) -- argparse
  configuration for all subcommands (`prepare`, `seed`, `edit`,
  `target`, `solve`, `refine`, `encode`, `analyze`, `setup`).
- [setup_mode.py](../track_runner/setup_mode.py) -- interactive
  camera configuration questionnaire.

### Analytical solver

- [camera_motion.py](../track_runner/camera_motion.py) --
  `MotionTrack` dataclass, `MotionEstimator` interface, three estimators,
  NPZ caching with identity metadata.
- [scene_coords.py](../track_runner/scene_coords.py) --
  `SceneTransform` for pixel-to-scene coordinate conversion using
  accumulated camera motion.
- [velocity_model.py](../track_runner/velocity_model.py) --
  directional slope estimation, cubic Hermite interpolation, PCHIP log-space
  size interpolation, stationary lock, FWD/BWD analytical propagation with
  optional residual-blob snap gates.
- [residual_motion.py](../track_runner/residual_motion.py) --
  per-frame residual-motion cue map, ROI extraction, corridor filter,
  `observe_blob_at`. The fps-invariant 9-sample neighbor stack uses
  `resolve_stride(fps)` (`REFERENCE_FPS = 60`); time span is fixed at
  ~133 ms regardless of camera fps.
- [residual_pre_pass.py](../track_runner/residual_pre_pass.py)
  -- per-worker per-interval sequential residual pre-pass that
  eliminates scattered random-access reads from Stage 4.
  `precompute_interval_residuals` walks `pad_lo..pad_hi` once via
  `FrameReader` strategy-0 fast-path and builds a worker-local
  `(frame_index, roi) -> (uint8 residual, uint8 validity)` store.
  `observe_blob_at` reads from the store via `precomputed_store`;
  on a hit it bypasses on-the-fly residual computation.
- [residual_heat_map.py](../track_runner/residual_heat_map.py)
  -- residual heat-map generation for overlays and diagnostics.
- [interval_solver.py](../track_runner/interval_solver.py) --
  `solve_interval_analytical()`, interval merging, trajectory stitching.
  `_dispatch_blob_pass` owns Stage 4 dispatch including the always-on
  `[blob] dispatch / complete` UX lines and the `--debug-blob`-gated
  master-side heartbeat thread + final-summary roll-up.
- [solve_queue.py](../track_runner/solve_queue.py) -- driver
  for solve and refine: seed filtering, fingerprint walk, cache-hit
  partition, pool dispatch, result aggregation. Shared by solve and refine
  call sites.
- [solver_workers.py](../track_runner/solver_workers.py) --
  worker-process state and per-worker `VideoReader`; workers return pure
  interval results with no I/O.
- [interval_fingerprint.py](../track_runner/interval_fingerprint.py)
  -- per-interval cache-key fingerprinting.

### Scoring, review, and regime

- [scoring.py](../track_runner/scoring.py) -- interval scoring
  (agreement, velocity consistency, size consistency, motion quality,
  occlusion fraction). Sole owner of score data on disk.
- [review.py](../track_runner/review.py) -- interval quality
  assessment, severity classification, seed suggestions.
- [regime_classifier.py](../track_runner/regime_classifier.py)
  -- motion regime classification (straight, curve, stationary).
- [regime_policies.py](../track_runner/regime_policies.py) --
  per-regime parameter policies.
- [race_phases.py](../track_runner/race_phases.py) -- race
  phase logic (pre-race, race, post-race) referenced by contract C2.

### Crop, encode, and analysis

- [tr_crop.py](../track_runner/tr_crop.py) -- adaptive crop
  rectangle computation with smoothing, deadband, and velocity capping.
- [encoder.py](../track_runner/encoder.py) -- ffmpeg-based
  encoding with optional filter pipeline.
- [encode_analysis.py](../track_runner/encode_analysis.py) --
  post-encode quality analysis and reporting.
- [analyze_report.py](../track_runner/analyze_report.py) --
  self-contained HTML diagnostic report builder for `analyze --plot`,
  with embedded JSON and an inlined vanilla-JS canvas renderer.
- [video_io.py](../track_runner/video_io.py) -- `VideoReader`
  and frame decode utilities.
- [fastread_video.py](../track_runner/fastread_video.py) --
  fast-read working video creation (`create_fastread_video`) and live
  structural validation (`validate_fastread_structural`). Returns a
  frozen `FastreadValidation` dataclass on success; raises
  `RuntimeError` naming the failed check and remedy on any mismatch.
  Path helper lives in [tr_paths.py](../track_runner/tr_paths.py)
  (`fastread_video_path`). Consumed by `prepare` mode (`_mode_prepare`
  in `cli.py`).

### Detection and annotation support

- [tr_detection.py](../track_runner/tr_detection.py) -- YOLOv8n
  person detection, available for optional seeding assistance. Not an
  active tracking signal in the analytical solver (contract C6 bans
  appearance-based identity evidence).
- [seed_color.py](../track_runner/seed_color.py) -- jersey
  color extraction helpers. Banned as identity evidence per C6; kept for
  non-identity uses only.
- [box_utils.py](../track_runner/box_utils.py) -- bounding box
  utilities.
- [seeding.py](../track_runner/seeding.py) -- seed frame
  collection logic.
- [seed_editor.py](../track_runner/seed_editor.py) -- seed
  edit logic.

### State and config

- [state_io.py](../track_runner/state_io.py) -- seed, geometry
  cache, interval-scores, debug-tracks, and camera-motion serialization.
  Format rule: dense per-frame numeric series to NPZ, human-authored and
  interval-level summary records to JSON. See
  [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md).
- [tr_config.py](../track_runner/tr_config.py) -- YAML config
  parsing, auto-migration of legacy keys.
- [tr_paths.py](../track_runner/tr_paths.py) -- per-video
  state and output path helpers.
- [tr_video_identity.py](../track_runner/tr_video_identity.py)
  -- video file identity (hash-based) for cache invalidation.
- [overlay_config.py](../track_runner/overlay_config.py) --
  overlay style loader, backed by
  [overlay_styles.yaml](../track_runner/overlay_styles.yaml).
  `overlay_styles.yaml` also holds the `walk_tile_layer_order` key consumed by
  `walk_palette.resolve_layer_order` for blob-walk tile draw-order resolution.
- [track_runner.config.yaml](../track_runner/track_runner.config.yaml)
  -- default runtime config (camera, detection, processing sections).

### UI (PySide6)

All UI modules live in `ui`:

- `app_shell.py` -- main window and dark/light theme management.
- `base_controller.py` -- shared annotation controller base class.
- `seed_controller.py` -- seed placement interface.
- `edit_controller.py` -- seed editing interface.
- `target_controller.py` -- weak-interval targeting interface.
- `frame_view.py` -- frame display widget.
- `workspace.py` -- multi-frame workspace.
- `overlay_items.py` -- visual overlays (bounding boxes, lines).
- `heat_map_overlay.py` -- residual heat-map overlay.
- `status_presenter.py` -- status bar display.
- `zoom_controls.py` -- zoom interface.
- `actions.py` -- UI action handlers.
- `theme.py` -- dark/light theme definitions.
- `key_input.py` (in package root) -- keyboard input handling.

### Shared utilities

- [frame_filters.py](../common_tools/frame_filters.py) --
  frame filtering utilities used by both `track_runner/` and `tools/`.
- [frame_reader.py](../common_tools/frame_reader.py) -- frame
  reading utilities.
- [tools_common.py](../common_tools/tools_common.py) -- shared
  helpers.

## Per-video state files

Per the Format rule in [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md):

- Seeds (human-authored): JSON, canonical four-field schema.
- Geometry cache (dense per-frame arrays): NPZ manifest plus indexed float32
  arrays.
- Interval scores: JSON, sole owner of score data.
- Debug tracks: NPZ, eight float32 arrays per interval.
- Camera motion: NPZ, per-model schema plus cache-identity metadata, one
  file per video, overwritten on hash mismatch.

Legacy filenames (`intervals.json`, `diagnostics.json`) are no longer
produced; migration is handled by a one-shot script.

## Testing and verification

- Repo-wide lint and style gates:
  [test_pyflakes_code_lint.py](../tests/test_pyflakes_code_lint.py),
  [test_ascii_compliance.py](../tests/test_ascii_compliance.py),
  [test_indentation.py](../tests/test_indentation.py),
  [test_import_dot.py](../tests/test_import_dot.py),
  [test_import_star.py](../tests/test_import_star.py),
  [test_import_requirements.py](../tests/test_import_requirements.py),
  [test_init_files.py](../tests/test_init_files.py),
  [test_shebangs.py](../tests/test_shebangs.py),
  [test_whitespace.py](../tests/test_whitespace.py),
  [test_bandit_security.py](../tests/test_bandit_security.py).
- Unit and integration tests cover camera motion, scene coords, velocity
  model, scoring, review, seed schema, geometry cache schema, blob snap,
  residual heat map, solver integration, and solver parallelism.
- Run with `source source_me.sh && python -m pytest tests/ -q`.

## Extension points

- New camera-motion estimator: add a class in
  [camera_motion.py](../track_runner/camera_motion.py)
  implementing the `MotionEstimator` interface and wire it into the motion
  section of the default config.
- New scoring term: extend
  [scoring.py](../track_runner/scoring.py); keep the on-disk
  schema documented in [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) in
  sync.
- New UI tool: add a controller under
  `ui` following the
  `base_controller.py` pattern.

## Known gaps

- Inter-interval smoothing (noted in
  [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) C3 as a
  possible future pass layered on top of solve and refine) is not
  implemented.
