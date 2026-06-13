# Track runner YAML config reference

The track runner config file controls detection, crop behavior, and encoding
for the crop-and-follow pipeline. It is auto-created at
`tr_config/{video}.track_runner.config.yaml` on first run.

## Minimal example

```yaml
track_runner: 3
detection:
  model: yolov8n
  confidence_threshold: 0.25
processing:
  crop_aspect: '16:9'
  torso_height_multiple: 8
  video_codec: libx264
  crf: 18
  encode_filters:
  - bilateral
  - auto_levels
  - hqdn3d
```

## Top-level keys

| Key | Required | Description |
| --- | --- | --- |
| `track_runner` | yes | Schema version, must be `3` (v2 auto-migrates at load) |
| `detection` | yes | Object detection model settings |
| `processing` | yes | Crop, codec, and filter settings |

## Detection section

| Key | Default | Description |
| --- | --- | --- |
| `model` | `yolov8n` | YOLO model name for person detection |
| `confidence_threshold` | `0.25` | Minimum detection confidence (0.0-1.0) |

## Processing section

### Required keys

| Key | Default | Description |
| --- | --- | --- |
| `crop_aspect` | `16:9` | Output aspect ratio as `W:H` string |
| `torso_height_multiple` | `8` | Crop height as a multiple of the tracked torso-box height. Larger = more surroundings. Must be >= 1. |
| `video_codec` | `libx264` | FFmpeg video codec name |
| `crf` | `18` | Constant rate factor (lower = higher quality) |
| `encode_filters` | `[bilateral, auto_levels, hqdn3d]` | Ordered filter pipeline for encode |

### Crop mode

| Key | Default | Description |
| --- | --- | --- |
| `crop_mode` | `smooth` | Crop algorithm: `smooth`, `direct_center`, or `smart` |

**`smooth`** (default): Online controller that tracks the subject with exponential
smoothing, deadband, and velocity capping. Reacts to the trajectory frame by frame.
Good general-purpose choice but assumes reasonably stable input. Can be combined
with offline post-smoothing for better results on shaky footage.

**`direct_center`**: Offline algorithm that centers the crop directly on the solved
trajectory, then applies forward-backward EMA smoothing. Sees the full trajectory
(past and future) before deciding crop positions, so it handles sudden jumps
better than `smooth` mode. Recommended for handheld or shaky camera footage.

**`smart`**: Experimental regime-switching crop controller. Classifies trajectory
spans into regimes (clear, uncertain, distance) and applies different crop targets
per regime. Uses `direct_center`-style offline processing with per-frame torso
multiple and zoom rate from the regime policy. Includes vertical asymmetry and torso
protection composition rules. Thresholds are provisional.

### Smooth mode tuning

These keys only apply when `crop_mode: smooth`.

| Key | Default | Description |
| --- | --- | --- |
| `crop_smoothing_attack` | `0.15` | EMA alpha for large corrections (higher = faster response) |
| `crop_smoothing_release` | `0.05` | EMA alpha for small drift (higher = faster drift) |
| `crop_max_velocity` | `30.0` | Hard cap on crop center movement per frame (pixels) |
| `crop_velocity_scale` | `2.0` | Adaptive velocity multiplier based on subject speed |
| `crop_displacement_alpha` | `0.1` | EMA alpha for tracking subject displacement |
| `crop_min_size` | `480` | Minimum crop height in pixels. Raising this lets `torso_height_multiple` control zoom instead of silently clamping. |

#### Post-smoothing (optional, applied after smooth mode)

These apply an offline forward-backward EMA pass on top of the smooth controller
output. This sees future frames and produces much more stable results.

| Key | Default | Description |
| --- | --- | --- |
| `crop_post_smooth_strength` | `0.0` | Position smoothing alpha (0 = off, try 0.05-0.15) |
| `crop_post_smooth_size_strength` | `0.0` | Size smoothing alpha (0 = defaults to half of position) |
| `crop_post_smooth_max_velocity` | `0.0` | Velocity cap after post-smoothing (0 = no cap) |

### Direct center mode tuning

These keys only apply when `crop_mode: direct_center`. The direct center
algorithm reuses `crop_post_smooth_*` keys for its smoothing pass.

| Key | Default | Description |
| --- | --- | --- |
| `crop_post_smooth_strength` | `0.0` | Position smoothing alpha (0 = no smoothing) |
| `crop_post_smooth_size_strength` | `0.0` | Size smoothing alpha (0 = defaults to half of position) |
| `crop_post_smooth_max_velocity` | `0.0` | Velocity cap on center per frame (0 = no cap) |
| `crop_min_size` | `480` | Minimum crop height in pixels. Raising this lets `torso_height_multiple` control zoom instead of silently clamping. |

### Encode filters

`encode_filters` is an ordered list of filter names applied during encoding.
Filters run in two stages: OpenCV filters run per-frame in Python before writing,
FFmpeg filters run as `-vf` flags in the encode command.

**OpenCV filters** (per-frame, Python):

| Name | Description |
| --- | --- |
| `bilateral` | Edge-preserving noise reduction |
| `clahe` | Adaptive contrast enhancement (good for low light) |
| `sharpen` | Unsharp mask sharpening |
| `denoise` | Non-local means denoising (strong, slow) |
| `auto_levels` | Per-channel percentile histogram stretch |

**FFmpeg filters** (in encode command):

| Name | Description |
| --- | --- |
| `hqdn3d` | High-quality 3D denoising (spatial + temporal) |
| `nlmeans` | Non-local means denoising |

When both types are present, OpenCV filters run first per-frame, then FFmpeg
filters are applied during the final encode pass. When any encode filters are
active, the resizing interpolation upgrades from bilinear to Lanczos.

### Output resolution

| Key | Default | Description |
| --- | --- | --- |
| `output_resolution` | `[1920, 1080]` | Explicit `[width, height]` for output. Must match `crop_aspect`. If omitted, uses the median of all crop rectangles. |

## Walker costs section

Controls Viterbi cost-model weights for the windowed blob walker on
Stage-4-promoted intervals. The section lives in
`track_runner/track_runner.config.yaml` and is merged into per-video configs
via the standard per-video config merge.

All six keys are required when the `walker_costs` section is present;
omitting any key raises a configuration error at solve startup.

| Key | Default | Description |
| --- | --- | --- |
| `WEIGHT_DISPLACEMENT` | `0.25` | Linear cost per torso-width/frame of motion along the selected path edge |
| `WEIGHT_SPEED_DELTA` | `1.0` | Cost per torso-width/frame of speed change between consecutive steps (pairwise velocity-delta) |
| `WEIGHT_HEADING_DELTA` | `0.5` | Cost per radian of heading change between consecutive steps (pairwise velocity-delta) |
| `WEIGHT_OVERSPEED` | `4.0` | Quadratic penalty applied when candidate motion exceeds the physical speed envelope |
| `WEIGHT_EVIDENCE_NORM` | `0.5` | Maximum tie-break cost for a candidate weaker than the strongest residual evidence on that frame |
| `SKIP_COST` | `2.0` | Cost per frame where no candidate survives to the selected path |

Example per-video override (add to the video's `tr_config/*.config.yaml`):

```yaml
walker_costs:
  WEIGHT_DISPLACEMENT: 0.25
  WEIGHT_SPEED_DELTA: 1.0
  WEIGHT_HEADING_DELTA: 0.5
  WEIGHT_OVERSPEED: 4.0
  WEIGHT_EVIDENCE_NORM: 0.5
  SKIP_COST: 2.0
```

## Migrating from v2

Schema v2 used `crop_fill_ratio`, the inverted reciprocal of the new
`torso_height_multiple`. v2 configs load unchanged; the loader converts
`crop_fill_ratio` to `torso_height_multiple = 1 / crop_fill_ratio` in
memory and prints a one-line deprecation notice. To silence the notice,
update your per-video YAML to use the new key.

Examples: `crop_fill_ratio: 0.30` becomes `torso_height_multiple: 3.33`;
`crop_fill_ratio: 0.1` becomes `torso_height_multiple: 10`.

## CLI flags that override config

| Flag | Overrides |
| --- | --- |
| `-c CONFIG` | Config file path (default: `tr_config/{video}.track_runner.config.yaml`) |
| `-o OUTPUT` | Output video path (default: next to input with `_tracked` suffix) |
| `--aspect W:H` | `crop_aspect` for this encode only |
| `-F filters` | `encode_filters` as comma-separated list for this encode only |
| `--torso-multiple N` | `torso_height_multiple` for this encode only |
| `-r WxH` | `output_resolution` for this encode only (e.g. `1920x1080`) |
| `--crf N` | `crf` quality for this encode only |
| `--video-codec NAME` | `video_codec` for this encode only |

## Recommended presets

### Handheld/shaky camera (e.g. filming from stands)

```yaml
processing:
  crop_mode: direct_center
  crop_aspect: '16:9'
  torso_height_multiple: 8
  crop_post_smooth_strength: 0.03
  crop_post_smooth_max_velocity: 15.0
  video_codec: libx264
  crf: 18
  encode_filters:
  - bilateral
  - auto_levels
  - hqdn3d
```

### Stable tripod footage

```yaml
processing:
  crop_mode: smooth
  crop_aspect: '16:9'
  torso_height_multiple: 8
  video_codec: libx264
  crf: 18
  encode_filters:
  - auto_levels
  - hqdn3d
```

### Maximum smoothness (slow camera movement feel)

```yaml
processing:
  crop_mode: direct_center
  crop_aspect: '16:9'
  torso_height_multiple: 8
  crop_post_smooth_strength: 0.02
  crop_post_smooth_max_velocity: 10.0
  video_codec: libx264
  crf: 18
  encode_filters:
  - bilateral
  - auto_levels
  - hqdn3d
```
