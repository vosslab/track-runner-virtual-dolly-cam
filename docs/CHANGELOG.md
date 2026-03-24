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
- Created [track_runner/velocity_model.py](track_runner/velocity_model.py) with analytical Hermite curve propagation. Implements directionally asymmetric cubic Hermite curves for seed-to-seed intervals, using backward-looking regression for forward pass and forward-looking regression for backward pass. Includes confidence decay (0.97/frame) and stationary lock detection.
- Extended [track_runner/propagator.py](track_runner/propagator.py) `propagate_forward()` and `propagate_backward()` with optional scene_transform parameter. When provided, uses analytical velocity model; otherwise falls back to legacy optical flow (backward compatible).
- Created [tests/test_velocity_model.py](tests/test_velocity_model.py) with 11 comprehensive tests covering Hermite interpolation, directional slope estimation, endpoint accuracy, stationary lock, confidence decay, and output format validation. All tests pass.
- Added `solve_interval_analytical()` to [track_runner/interval_solver.py](track_runner/interval_solver.py): solves seed-to-seed intervals using analytical velocity model (no optical flow, no detector required).
- Extended `solve_all_intervals()` with optional `scene_transform` parameter. When provided, uses analytical solver for all intervals in sequential mode (no parallel processing).
- Added `score_interval_analytical()` to [track_runner/scoring.py](track_runner/scoring.py): computes interval_score_v2 schema with agreement (Dice FWD/BWD), velocity_consistency (LOO prediction error), size_consistency (interpolation residual), confidence_tier (high|good|fair|low), and severity (high|medium|low).
- Updated `_apply_trajectory_erasure()` occlusion policy: approximate/obstructed seeds are NO LONGER erased (removed APPROX_ERASE_RADIUS_S logic), only not_in_frame seeds are erased. Approximate seeds now provide useful guidance in analytical mode.
- Bumped diagnostics header version from 2 to 3 in [track_runner/state_io.py](track_runner/state_io.py). `load_diagnostics()` now accepts both v2 and v3 formats for backward compatibility.
- Updated [track_runner/review.py](track_runner/review.py) failure reasons: replaced (low_separation, weak_appearance, likely_identity_swap, ...) with (low_agreement, weak_motion_model, sparse_support) for analytical scoring. `classify_interval_severity()` now detects analytical vs optical-flow mode and uses appropriate metric names.
- Updated [track_runner/encode_analysis.py](track_runner/encode_analysis.py) `analyze_solver_context()` to map analytical metrics (velocity_consistency -> identity, size_consistency -> margin) for compatibility with diagnostics reports.
- Replaced [track_runner/hypothesis.py](track_runner/hypothesis.py) with minimal stub. Competitor tracking removed in scene-interp rewrite; file reserved for future visual validation warnings.
- Fixed imports in [track_runner/velocity_model.py](track_runner/velocity_model.py), [track_runner/scene_coords.py](track_runner/scene_coords.py), and [track_runner/camera_motion.py](track_runner/camera_motion.py): changed from qualified (track_runner.module) to relative (module) imports for consistency with package structure. Type hints adjusted to use object type where full package paths caused NameError.

### Fixes and Maintenance

- Replaced starter-template changelog with project-specific history.

### Decisions and Failures

- `common_tools` package was copied separately from the parent repo to satisfy the `cli.py` import.
- Approximate seeds no longer erased near endpoints: decision rationale is that in analytical mode (no detectors), approximate seeds guide the trajectory meaningfully rather than creating ambiguity as they do in optical flow.
