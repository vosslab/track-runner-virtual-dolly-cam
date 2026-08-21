# File structure

## Top-level layout

```text
track-runner-virtual-dolly-cam/
|-- track_runner/       application package and CLI
|-- common_tools/       shared frame and coordinate utilities
|-- tools/              developer and offline diagnostic tools
|-- tests/              pytest unit, integration, and repository gates
|-- data/               non-Python runtime assets
|-- docs/               user, design, and developer documentation
|-- source_me.sh        Python 3.12 environment bootstrap
|-- pip_requirements.txt runtime Python dependencies
|-- README.md           overview and canonical workflow
`-- AGENTS.md           repository instructions
```

## Application package

- [track_runner.py](../track_runner/track_runner.py) starts the CLI.
- [cli.py](../track_runner/cli.py) owns shared command orchestration;
  [cli_args.py](../track_runner/cli_args.py) owns parser definitions.
- `modes` contains current CLI mode implementations:
  [prepare.py](../track_runner/modes/prepare.py),
  [setup.py](../track_runner/modes/setup.py),
  [seed.py](../track_runner/modes/seed.py),
  [edit.py](../track_runner/modes/edit.py),
  [target.py](../track_runner/modes/target.py),
  [solve.py](../track_runner/modes/solve.py),
  [refine.py](../track_runner/modes/refine.py),
  [encode.py](../track_runner/modes/encode.py), and
  [analyze.py](../track_runner/modes/analyze.py). Shared mode helpers are in
  [shared.py](../track_runner/modes/shared.py), video probes/artifact checks in
  [video_artifacts.py](../track_runner/modes/video_artifacts.py), and seed
  eligibility checks in [seed_validation.py](../track_runner/modes/seed_validation.py).

## Solver and artifacts

- [interval_solver.py](../track_runner/interval_solver.py) is the interval
  facade. Its cohesive supporting owners are
  [interval_progress.py](../track_runner/interval_progress.py),
  [interval_analytical.py](../track_runner/interval_analytical.py), and
  [interval_seed_anchoring.py](../track_runner/interval_seed_anchoring.py).
- [camera_motion.py](../track_runner/camera_motion.py) contains the unchanged
  Stage 1 algorithms. [camera_motion_artifact.py](../track_runner/camera_motion_artifact.py)
  owns motion artifacts and model resolution.
- [residual_motion.py](../track_runner/residual_motion.py) owns observation
  policy, with image mechanics in [residual_frame.py](../track_runner/residual_frame.py)
  and trace records in [blob_trace.py](../track_runner/blob_trace.py).
- [state_io.py](../track_runner/state_io.py) owns seed and interval-score JSON;
  [torso_box_coords_io.py](../track_runner/torso_box_coords_io.py) owns
  SOURCE-space torso-box NPZ I/O; and
  [camera_motion_artifact.py](../track_runner/camera_motion_artifact.py) owns
  camera-motion NPZ I/O.

## Walker, crop, encode, and UI

- `blob_walk` contains the Stage-4 walker.
  [walk_walker.py](../track_runner/blob_walk/walk_walker.py) is its facade;
  [walk_engine.py](../track_runner/blob_walk/walk_engine.py),
  [walk_observer.py](../track_runner/blob_walk/walk_observer.py), and
  [walk_summary.py](../track_runner/blob_walk/walk_summary.py) own execution,
  observation, and summary metrics.
- [tr_crop.py](../track_runner/tr_crop.py) is the crop facade, split into
  [tr_crop_math.py](../track_runner/tr_crop_math.py),
  [tr_crop_direct.py](../track_runner/tr_crop_direct.py), and
  [tr_crop_controller.py](../track_runner/tr_crop_controller.py).
- [encoder.py](../track_runner/encoder.py) encodes frames;
  [encode_audio.py](../track_runner/encode_audio.py) muxes audio;
  [process_pool_control.py](../track_runner/process_pool_control.py) controls
  emergency pool shutdown; [encode_analysis_report.py](../track_runner/encode_analysis_report.py)
  formats analysis reports.
- `ui` contains PySide6 UI code. Shared UI concerns live
  in [heat_map_support.py](../track_runner/ui/heat_map_support.py),
  [edit_polish.py](../track_runner/ui/edit_polish.py), and
  [mode_status_support.py](../track_runner/ui/mode_status_support.py).

## Tools, tests, and generated files

- `tests` holds test modules, inline synthetic inputs, and
  repository-wide lint, link, and ASCII checks.
- `tr_config/` and `TRACK_VIDEOS/` are local per-video working areas.
  Per-video config, NPZ, JSON, diagnostics, and output paths are calculated
  by [tr_paths.py](../track_runner/tr_paths.py).

## Documentation map

- [README.md](../README.md) gives the first-run workflow.
- [MODES.md](MODES.md) and `modes` document commands.
- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) is authoritative for
  invariants; [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) explains the
  pipeline; [COORDINATE_SPACES.md](COORDINATE_SPACES.md) defines spatial data.
- [CODE_ARCHITECTURE.md](CODE_ARCHITECTURE.md) explains component ownership.

## Where to add work

- Put mode-specific behavior in `modes` and tests in
  `tests`.
- Put reusable frame or coordinate utilities in
  `common_tools`.
- Put offline reports and batch diagnostics in `tools`.
- Put user and developer documentation in [docs/](.).
