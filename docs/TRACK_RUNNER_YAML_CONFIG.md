# Track runner YAML config reference

The track runner config file controls camera motion, crop behavior, and encoding
for the crop-and-follow pipeline. It is auto-created at
`tr_config/{video}.track_runner.config.yaml` on first run.

## Minimal example

```yaml
track_runner: 3
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
| `track_runner` | yes | Config format version, must be `3` |
| `processing` | yes | Crop, codec, and filter settings |

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
| `crop_mode` | `dolly` | Crop algorithm: `dolly`, `smooth`, or `direct_center` |

**`dolly`** (default): Offline whole-path solve for crop center and log size. It
uses future and past trajectory samples, settles the existing containment rule,
and falls back to `smooth` if that bounded solve cannot converge.

**`smooth`**: Online controller that tracks the subject with exponential
smoothing, deadband, and velocity capping. Reacts to the trajectory frame by frame.
Good general-purpose choice but assumes reasonably stable input. Can be combined
with offline post-smoothing for better results on shaky footage.

**`direct_center`**: Offline baseline that centers each crop on the solved
trajectory. It uses the full path and forward-backward EMA smoothing for crop
size only; center coordinates are not post-smoothed or velocity-capped.

### Smooth mode tuning

These keys only apply when `crop_mode: smooth`.

| Key | Default | Description |
| --- | --- | --- |
| `crop_smoothing_attack` | `0.15` | EMA alpha for large corrections (higher = faster response) |
| `crop_smoothing_release` | `0.05` | EMA alpha for small drift (higher = faster drift) |
| `crop_max_velocity` | `30.0` | Hard cap on crop center movement per frame (pixels) |
| `crop_velocity_scale` | `2.0` | Adaptive velocity multiplier based on subject speed |
| `crop_displacement_alpha` | `0.1` | EMA alpha for tracking subject displacement |

### Direct center mode tuning

`crop_mode: direct_center` has no user-tunable smoothing keys. The
forward-backward EMA applies to crop size only; crop-center smoothing and
velocity capping are not part of this mode. These are fixed implementation
choices in `tr_crop.py`, not per-video config keys.
The `crop_post_smooth_strength`, `crop_post_smooth_size_strength`,
`crop_post_smooth_max_velocity`, and `crop_min_size` keys were removed from
the config schema.

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

## Walker costs section (removed)

The `walker_costs` config section was removed (2026-06-13). Viterbi cost-model
weights for the windowed blob walker are now fixed constants in
`track_runner/blob_walk/walk_viterbi.py` (human decision 2026-06-13: too
obscure for per-video user config).

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
  video_codec: libx264
  crf: 18
  encode_filters:
  - bilateral
  - auto_levels
  - hqdn3d
```
