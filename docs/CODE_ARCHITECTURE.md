# Code architecture

## Overview

Track runner turns human-authored torso-box seeds in one input video into a
smoothed, cropped output video. The command-line application is rooted at
[track_runner.py](../track_runner/track_runner.py). Its pipeline keeps human
identity anchors separate from machine-generated geometry; the hard rules are
in [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md).

## Command and modes

- [cli.py](../track_runner/cli.py) owns input validation, shared runtime
  context, artifact paths, mode gates, and dispatch.
- [cli_args.py](../track_runner/cli_args.py) owns argparse declarations.
- `modes` owns mode bodies: prepare, setup, seed,
  edit, target, solve, refine, encode, and analyze. [setup.py](../track_runner/modes/setup.py)
  owns setup mode.
- [video_artifacts.py](../track_runner/modes/video_artifacts.py) owns video
  metadata probes and saved-artifact identity checks.
- [seed_validation.py](../track_runner/modes/seed_validation.py) owns the
  shared canonical usable-seed check for solve and refine.

## Solve pipeline

1. [camera_motion.py](../track_runner/camera_motion.py) runs Stage 1 camera
   motion estimation. Its Stage 1 algorithms remain in this module unchanged;
   [camera_motion_artifact.py](../track_runner/camera_motion_artifact.py)
   owns motion-model resolution and NPZ artifact storage.
2. [scene_coords.py](../track_runner/scene_coords.py) converts between camera
   corrected scene geometry and frame coordinates. [velocity_model.py](../track_runner/velocity_model.py)
   propagates independent FWD/BWD analytical paths with linear centers and
   log-linear box sizes.
3. [interval_solver.py](../track_runner/interval_solver.py) is the compatibility
   facade for interval solving and stitching. It delegates progress display to
   [interval_progress.py](../track_runner/interval_progress.py), analytical
   mechanics and Stage-4 walker dispatch to
   [interval_analytical.py](../track_runner/interval_analytical.py), and seed
   stamping, anchoring, and reconstruction to
   [interval_seed_anchoring.py](../track_runner/interval_seed_anchoring.py).
4. [residual_motion.py](../track_runner/residual_motion.py) owns residual
   motion and DoG/blob observation policy. [residual_frame.py](../track_runner/residual_frame.py)
   owns frame-reading, warp, cache, and residual-image mechanics;
   [blob_trace.py](../track_runner/blob_trace.py) owns observer trace records.
   [residual_pre_pass.py](../track_runner/residual_pre_pass.py) prepares
   worker-local residual data for Stage 4.
5. [walk_walker.py](../track_runner/blob_walk/walk_walker.py) remains
   the compatibility facade for independent directional walks. It delegates
   path execution to [walk_engine.py](../track_runner/blob_walk/walk_engine.py),
   residual observation and trace handling to
   [walk_observer.py](../track_runner/blob_walk/walk_observer.py), and metrics
   to [walk_summary.py](../track_runner/blob_walk/walk_summary.py).
   [walk_viterbi.py](../track_runner/blob_walk/walk_viterbi.py) owns the
   candidate-lattice optimization. FWD and BWD remain independent for scoring.
6. [scoring.py](../track_runner/scoring.py),
   [trajectory_confidence.py](../track_runner/trajectory_confidence.py), and
   [blend_commitment.py](../track_runner/blend_commitment.py) score and commit
   paths without replacing human seed truth.

## State and coordinate artifacts

- [state_io.py](../track_runner/state_io.py) owns seed JSON and interval-score
  JSON. [torso_box_coords_io.py](../track_runner/torso_box_coords_io.py) owns
  solved torso-coordinate NPZ, and
  [camera_motion_artifact.py](../track_runner/camera_motion_artifact.py) owns
  camera-motion NPZ.
- [torso_box_coords_io.py](../track_runner/torso_box_coords_io.py) owns atomic
  SOURCE-space torso-box NPZ serialization and schema checks.
- [tr_schema.py](../track_runner/tr_schema.py) supplies the unified schema
  contract; [tr_paths.py](../track_runner/tr_paths.py) computes per-video
  artifact paths; [tr_config.py](../track_runner/tr_config.py) loads YAML.

## Crop, encode, and analysis

- [tr_crop.py](../track_runner/tr_crop.py) is the crop facade. It delegates
  offline trajectory math to [tr_crop_math.py](../track_runner/tr_crop_math.py),
  direct-center baselines to [tr_crop_direct.py](../track_runner/tr_crop_direct.py),
  and stateful controller dispatch to
  [tr_crop_controller.py](../track_runner/tr_crop_controller.py).
- [encoder.py](../track_runner/encoder.py) writes video frames and applies
  configured filters. [encode_audio.py](../track_runner/encode_audio.py) owns
  audio detection and muxing; [process_pool_control.py](../track_runner/process_pool_control.py)
  owns emergency worker-pool termination.
- [encode_analysis.py](../track_runner/encode_analysis.py) computes encode and
  solver diagnostics. [encode_analysis_report.py](../track_runner/encode_analysis_report.py)
  formats console/YAML analysis output, while
  [analyze_report.py](../track_runner/analyze_report.py) builds the HTML report.

## Annotation UI

- `ui` contains the PySide6 annotation application,
  controllers, frame source/view, session wiring, overlays, and theme.
- [heat_map_support.py](../track_runner/ui/heat_map_support.py) owns shared
  residual heat-map setup and visibility helpers.
- [edit_polish.py](../track_runner/ui/edit_polish.py) owns edit-mode detector,
  consensus-refinement, and preview helpers.
- [mode_status_support.py](../track_runner/ui/mode_status_support.py) owns
  toolbar synchronization and draw-mode status presentation.

## Tools and verification

- `tests` contains unit, integration, CLI, and repository gates.
  Run `source source_me.sh && python3 -m pytest tests/` for the fast suite.
  [E2E_TESTS.md](E2E_TESTS.md) describes slower end-to-end checks.

## Extension points

- Add a CLI behavior to its owning file in `modes`,
  leaving dispatch and shared gates in [cli.py](../track_runner/cli.py).
- Add a new solver concern behind the appropriate facade rather than expanding
  [interval_solver.py](../track_runner/interval_solver.py) or
  [walk_walker.py](../track_runner/blob_walk/walk_walker.py).
- Add seed or interval-score JSON formats through [state_io.py](../track_runner/state_io.py),
  torso-coordinate NPZ through [torso_box_coords_io.py](../track_runner/torso_box_coords_io.py),
  and camera-motion NPZ through [camera_motion_artifact.py](../track_runner/camera_motion_artifact.py)
  and their dedicated serializer, preserving SOURCE/PROCESSED coordinate rules
  in [COORDINATE_SPACES.md](COORDINATE_SPACES.md).
