## 2026-03-24

### Additions and New Features

- Ported track runner code from parent repo into self-contained project repository.
- Copied `common_tools/` package (frame_filters, frame_reader, tools_common) into the repo.
- Created [docs/INSTALL.md](docs/INSTALL.md) with setup steps, system dependencies, and pip requirements.
- Created [docs/USAGE.md](docs/USAGE.md) with subcommand reference, global options, and typical workflow.
- Created [docs/CODE_ARCHITECTURE.md](docs/CODE_ARCHITECTURE.md) with pipeline overview and module descriptions.
- Created [docs/FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md) with directory map.
- Populated [pip_requirements.txt](pip_requirements.txt) with runtime dependencies: numpy, opencv-python, PySide6, pyyaml, rich, scipy.
- Rewrote [README.md](README.md) to describe the track runner project instead of the starter template.
- Added `DiscreteZoomEstimator` to [track_runner/camera_motion.py](track_runner/camera_motion.py): detects zoom jumps using 5-frame sliding window on per-frame scale ratios, snaps detected scales to configured zoom levels, marks zoom_jump frames with event_flags bit 0.
- Added `ContinuousZoomEstimator` to [track_runner/camera_motion.py](track_runner/camera_motion.py): estimates per-frame scale via log-polar phase correlation, applies stricter quality gating (response < 0.3), includes 3-frame median filtering for smoothness.
- Updated `precompute_camera_motion()` to support all three estimator types: "FixedZoomEstimator", "iphone_discrete" (DiscreteZoomEstimator), "continuous" (ContinuousZoomEstimator).
- Verified [track_runner/scene_coords.py](track_runner/scene_coords.py) already handles piecewise constant scale (zoom jumps) correctly via numpy.cumprod.
- Created [track_runner/setup_mode.py](track_runner/setup_mode.py): interactive CLI questionnaire for per-video camera configuration (zoom type, camera height/position, track size).
- Added `setup` CLI subcommand for running the interactive camera configuration wizard.
- Integrated `solver_backend` config key with dispatch logic in [track_runner/cli.py](track_runner/cli.py): "scene_interp" (default, analytical) vs "legacy_interval" (optical flow).
- Scene_interp backend precomputes camera motion and creates SceneTransform before calling solve_all_intervals().

### Behavior or Interface Changes

- `precompute_camera_motion()` now selects estimator based on config["motion"]["estimator"]["type"].
- `_run_solve()` checks `config.get("processing", {}).get("solver_backend", "scene_interp")` to select solver backend.
- Scene_interp backend forces single-threaded solving (num_workers=1) due to analytical nature.

### Fixes and Maintenance

- Replaced starter-template changelog with project-specific history.

### Developer Tests and Notes

- Added `test_discrete_zoom_estimator_produces_valid_output()` and `test_discrete_zoom_estimator_respects_config()` tests.
- Added `test_continuous_zoom_estimator_produces_valid_output()` test.
- Added `test_scene_transform_zoom_jump()` test in camera_motion tests.
- Added `test_piecewise_scale_zoom_jump()` test in scene_coords tests to verify cumulative scale handling with jumps.
- All tests pass (14 camera_motion tests, 9 scene_coords tests, 67 pyflakes lint checks).
- Log-polar phase correlation for zoom estimation proved fragile with synthetic test frames; implemented fallback approach with clamped scale ratios (0.5-2.0 range).

### Decisions and Failures

- `common_tools` package was copied separately from the parent repo to satisfy the `cli.py` import.
- Three deliberate philosophy changes in this milestone: (1) visual tracking to scene interpolation, (2) erasure of occluded seeds to occlusion interpolation, (3) detection-first to seed-trust model.
- Seeds JSON preserved as canonical user work; intervals and diagnostics are disposable derived artifacts that can be regenerated.
- Solver backend dispatch allows rollback to legacy_interval without code changes, purely config-driven.
