# setup mode

Interactive CLI questionnaire that collects per-video camera configuration (zoom type, camera height, camera position, and track size) and stores it in the per-video config YAML. Setup establishes the camera context needed for accurate solving and target placement.

## When to use it

- Before running `solve`, `refine`, or `target` (enforced by those modes; they will exit and redirect you to setup if it has not been run).
- Ideally before `seed` as well, so the annotation UI has correct camera and track context from the first seed.
- Required once per video file.

## Command line reference

<!-- BEGIN AUTO HELP: setup -->
```text
usage: track_runner.py setup [-h]

options:
  -h, --help  show this help message and exit
```
<!-- END AUTO HELP: setup -->

## Notes

`setup` is a prerequisite for all solve-path modes. The questionnaire captures zoom model (fixed or variable), camera height in mm, camera horizontal position relative to the track (left, right, center), and track size in meters. These values are used internally by the camera motion precompute (Stage 1 of the solve pipeline) and by the annotation UI to properly render the torso-box overlay.

See [../TRACK_RUNNER_CONTRACT.md](../TRACK_RUNNER_CONTRACT.md) (contract C2 on torso-box scale) and [../TR_CONFIG_FILES.md](../TR_CONFIG_FILES.md) for the underlying configuration storage layout.
