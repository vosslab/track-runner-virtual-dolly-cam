## 2026-04-15

### Fixes and Maintenance

- Fixed `AttributeError: 'VideoReader' object has no attribute 'total_frames'` in [track_runner/camera_motion.py](../track_runner/camera_motion.py): `FixedZoomEstimator.estimate`, `DiscreteZoomEstimator.estimate`, and `ContinuousZoomEstimator.estimate` now read `reader.frame_count` to match the `VideoReader` API in [track_runner/video_io.py](../track_runner/video_io.py). The bug was masked whenever a motion cache `.npz` already existed, so it only surfaced on the first `solve` run for a new video. Docstrings on all three estimators and on `precompute_camera_motion` updated to match.

## 2026-03-31

### Additions and New Features

- Added box rejection feedback in [track_runner/ui/base_controller.py](../track_runner/ui/base_controller.py): when user draws a box outside valid size range, status bar now shows "Box too small -- draw a larger rectangle" or "Box too large -- draw a smaller rectangle" for 3 seconds.
- Added ESC/Q double-press exit confirmation in [track_runner/ui/base_controller.py](../track_runner/ui/base_controller.py): first ESC/Q press shows "Press ESC/Q again to quit" for 2 seconds; second press within 2 seconds triggers quit. Prevents accidental exit.
- Added F-key failure feedback in [track_runner/ui/seed_controller.py](../track_runner/ui/seed_controller.py): when F key does nothing, status bar now shows reason: "No predictions available for F-key", "Need both FWD and BWD predictions for F-key", or "FWD/BWD overlap too low to auto-accept" (3 second timeout). Helps user understand why auto-accept failed.
- Added targeting reason display in [track_runner/ui/seed_controller.py](../track_runner/ui/seed_controller.py): when predictions have interval_info with reasons, status bar shows "Targeted: reason1, reason2..." so user knows why this frame was targeted for seed collection.
- Added seed coverage statistics summary in [track_runner/ui/seed_controller.py](../track_runner/ui/seed_controller.py) at exit: prints total, usable, not-in-frame, and approximate seed counts; computes and displays average spacing and largest gap between usable seeds; warns if largest gap is more than 2.5x average or if fewer than 2 usable seeds.

### Fixes and Maintenance

- Fixed [`track_runner/cli.py`](../track_runner/cli.py): `target` mode now auto-runs a fresh solve when diagnostics are missing, empty, or stale instead of aborting and forcing a separate manual solve step first.
- Fixed optional YOLO warning spam in [`track_runner/ui/seed_controller.py`](../track_runner/ui/seed_controller.py) and [`track_runner/tr_detection.py`](../track_runner/tr_detection.py): normal seed collection no longer repeatedly prints missing ONNX export instructions when detector-backed auto-suggestions are unavailable. Manual seeding continues to work without ONNX weights.
- Fixed seed navigation in [`track_runner/ui/seed_controller.py`](../track_runner/ui/seed_controller.py): Prev/Next toolbar buttons now advance frames again, and plain left/right arrow keys now scrub at fit-to-view zoom instead of no-oping when pan is unavailable.
- Fixed analytical solve persistence in [`track_runner/interval_solver.py`](../track_runner/interval_solver.py): each solved interval now writes through the existing `on_interval_solved` callback so [`track_runner/cli.py`](../track_runner/cli.py) analyze/encode modes can reconstruct trajectories from `*.intervals.json` after a completed solve or refine run.
- Removed dead legacy code from [track_runner/seeding.py](../track_runner/seeding.py): deleted `SEED_WINDOW_TITLE` constant, `_draw_trajectory_preview()` function, and `_interactive_draw_box()` function (285 lines total). Removed unused imports `numpy` and `overlay_config` that only supported these functions. This completes the migration from legacy cv2-based interactive UI to PySide6 controllers.

### Removals and Deprecations

- Removed legacy optical-flow solver entirely. The analytical scene-interp solver is now the only backend. Removed from [track_runner/interval_solver.py](../track_runner/interval_solver.py): `_init_worker()`, `_detect_cyclical_prior()`, `refine_interval()`, `solve_interval()`, `_solve_interval_worker()`, `_force_kill_pool()`, orphaned constants, legacy dispatch in `solve_all_intervals()` (1035 lines total).
- Deleted [track_runner/hypothesis.py](../track_runner/hypothesis.py) (gutted stub) and [track_runner/propagator.py](../track_runner/propagator.py) (legacy optical-flow propagation, no longer imported).
- Simplified [track_runner/cli.py](../track_runner/cli.py): removed `solver_backend` config dispatch, always uses analytical solver.
- Removed `solver_backend` default from [track_runner/tr_config.py](../track_runner/tr_config.py) and `solver_backend: scene_interp` from [track_runner/track_runner.config.yaml](../track_runner/track_runner.config.yaml).
- Fixed `create_detector()` in [track_runner/tr_detection.py](../track_runner/tr_detection.py) reading `config["settings"]["detection"]` instead of `config["detection"]`, causing detector to always use hardcoded defaults and ignore user config.
- Fixed `score_interval()` docstring in [track_runner/scoring.py](../track_runner/scoring.py) claiming confidence tiers are "high/medium/low" when actual tiers are "high/good/fair/low".
- Fixed `_KNOWN_REASONS` and `_REASON_EXPLANATIONS` in [track_runner/review.py](../track_runner/review.py) listing only 3 of 7 failure reasons generated by scoring.py. Added `low_separation`, `likely_identity_swap`, `weak_appearance`, `long_occlusion`, `low_motion_quality`.
- Fixed `merge_seeds()` in [track_runner/state_io.py](../track_runner/state_io.py) using legacy `"frame"` key instead of canonical `"frame_index"`. Updated test data in `__main__` block to match.
- Removed no-op `prev_gray = prev_gray` assignment in [track_runner/camera_motion.py](../track_runner/camera_motion.py) line 255 (frame-skip branch).
- Removed dead `_draw_candidate_overlays()` method from [track_runner/ui/seed_controller.py](../track_runner/ui/seed_controller.py) (never called, referenced nonexistent FrameView draw methods).
- Fixed `_refresh_frame_title()` return type annotation in [track_runner/ui/seed_controller.py](../track_runner/ui/seed_controller.py) from `-> str` to `-> None`.
- Added clarifying comment in [track_runner/velocity_model.py](../track_runner/velocity_model.py) explaining that analytical propagation leaves `disp_history` empty (legacy optical-flow path populates it via propagator.py).

## 2026-03-29

### Fixes and Maintenance

- Added license section to [README.md](README.md) linking [LICENSE.LGPL_v3](../LICENSE.LGPL_v3) and [LICENSE.CC_BY_4_0](../LICENSE.CC_BY_4_0).

## 2026-03-24

### Additions and New Features

- Created [tools/benchmark_solver_gates.py](tools/benchmark_solver_gates.py): standalone M4/M5 benchmark gate evaluation script for the analytical solver. Re-solves all 7 test videos from seeds, evaluates 12 numeric closure gates from Plan 07 acceptance criteria (WP-4.10), and writes audit artifact to `output_smoke/benchmark_gates.txt`. Supports `--video` for single-video runs and `--verbose` for per-frame details.
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
- `state_io.py` `write_solver_diagnostics()` now writes nested `interval_score` dict with v3 fields for analytical mode (agreement, velocity_consistency, size_consistency, confidence_tier, severity, failure_reasons, warning_flags). Legacy v2 format preserved for legacy solver.
- `review.py` `format_review_summary()` now renders v3 metrics (vel_cons, size_cons) for analytical intervals instead of legacy identity/margin fields.
- `encode_analysis.py` report rendering now detects analytical vs legacy mode and prints velocity_consistency_median, size_consistency_median, motion_quality_median instead of legacy convergence/identity/margin.
- `scoring.py` `compute_seed_confidences()` now reads v3 `interval_score` dict with agreement + velocity_consistency instead of legacy flat fields.
- `scoring.py` `score_interval_analytical()` now implements all planned modifiers: long-interval demotion (>10s), low-motion-quality demotion (<0.5), occlusion cap at fair (>0.3), plus `long_occlusion`, `low_motion_quality` failure reasons and `approximate_span`, `no_directional_support`, `scale_unstable` warning flags.
- Refreshed [docs/CODE_ARCHITECTURE.md](docs/CODE_ARCHITECTURE.md) to describe analytical solver as default backend, camera motion pipeline, scene_interp vs legacy_interval, and diagnostics v2/v3 schema.

### Fixes and Maintenance

- Fixed estimator dispatch in [track_runner/camera_motion.py](track_runner/camera_motion.py): `precompute_camera_motion()` now recognizes `estimator_type == "iphone_discrete"` and `zoom_type == "discrete"` as aliases for `DiscreteZoomEstimator`. Previously only matched `"DiscreteZoomEstimator"` or `zoom_type == "iphone_discrete"`, causing `ValueError: unsupported estimator type` for per-video configs saved by the setup wizard.
- Replaced starter-template changelog with project-specific history.
- Fixed `./track_runner/track_runner.py` launcher import path so repo-root packages like `common_tools/` resolve correctly when running the script directly from the repository root.
- Fixed `source source_me.sh && ./track_runner/track_runner.py ...` bootstrap to prefer Homebrew Python from `/opt/homebrew/bin`, so the executable launcher uses the repo's Python 3.12 environment instead of macOS system Python.
- Fixed `_format_interval_result()` in `interval_solver.py` crashing with `KeyError: 'agreement_score'` on analytical v3 interval scores. Now detects v2/v3 format and renders correct metric names.
- Fixed `_run_solve()` unconditionally creating YOLO detector even for `scene_interp` backend. Analytical path now passes `None` detector, removing the YOLO model download dependency.
- Fixed `_build_predictions_from_diagnostics()` in `cli.py` reading only legacy fields (`confidence`, `agreement_score`, `competitor_margin`). Now extracts `confidence_tier`/`agreement`/`velocity_consistency` for v3 scores.
- Fixed `rank_target_frames_by_severity()` in `review.py` reading `competitor_margin` for analytical scores. Now reads `velocity_consistency` when v3 format detected.
- Fixed `default_cache_dir()` call in `cli.py` (function did not exist). Replaced with `tr_paths.ensure_data_dir()`.
- Replaced stubbed scoring metrics in `scoring.py`: velocity_consistency now uses real slope-prediction error against support seeds; motion_quality reads from MotionTrack quality array; occlusion_fraction computed from approximate seeds in interval.
- Fixed review.py `_get_confidence()` self-recursion caused by replace_all replacing the fallback line inside the function itself.
- Removed erroneous shebangs from library modules (camera_motion.py, scene_coords.py, velocity_model.py, setup_mode.py) and test files that are not executable scripts.
- Fixed unused pytest imports in test_camera_motion.py and test_scene_coords.py (pyflakes).
- Fixed bandit B324 (weak MD5 hash) in camera_motion.py by adding `usedforsecurity=False`.
- Fixed bandit B108 (hardcoded /tmp) in test_camera_motion.py by using `tmp_path` fixture.
- Fixed `frame` vs `frame_index` key mismatch in velocity_model.py causing KeyError when called from interval_solver.

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
