# Encode design and implementation

This document explains the design choices behind the track runner's
`encode` subcommand and the implementation that backs them. It is a
companion to:

- [docs/modes/ENCODE.md](modes/ENCODE.md): user-facing CLI reference
  (auto-generated from `--help`).
- [docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md](TRACK_RUNNER_ANALYZE_AND_ENCODE.md):
  analyze-mode diagnostics for crop-path quality.
- [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): system-wide
  design philosophy.

If anything here contradicts
[docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md), the
contract wins and this document is wrong.

## Scope

Encode mode takes a fully solved interval set (per-frame torso boxes
in source-frame pixel coordinates) and produces a cropped, optionally
overlay-decorated MKV or MP4 of the runner. It is the final stage in
the canonical pipeline:

```
setup -> seed -> solve -> (target -> refine) x N -> encode
```

Encode does not solve, refine, or estimate camera motion. It reads
solved geometry, builds a per-frame crop trajectory, decodes frames,
applies overlays and filters, and pipes raw frames to ffmpeg. Camera
motion is consulted only for the velocity-arrow projection.

## Core philosophy

Five principles drive the encode design.

**1. The crop is a virtual camera operator, not a tracker.** The
runner's location at every frame is already known by the time encode
runs. Encode's job is cinematic: produce a smooth, watchable output
where the runner stays approximately centered. Tracking accuracy is
upstream's responsibility.

**2. Source-frame is canonical.** Solved torso boxes, seeds, raw
torso geometry, and the user-facing CLI numbers all live in the
source frame's pixel space. Crop rectangles are computed in
source-frame and then applied by extracting + resizing pixels to the
output resolution. Coordinate-space confusion has been the source of
every "why does this look wrong?" bug in the encode path; the
contract is "if you see a coordinate, ask which frame's pixels it
references."

**3. Overlays are tiered, not toggled.** Three independent overlay
flags (`--draw-tracking-overlay`, `--draw-debug-overlay`,
`--draw-velocity-arrow`) cover three distinct audiences (review,
developer, motion-vector visualization). Tying them to a single
`-d/--debug` flag conflated developer diagnostics with reviewer
legibility. The global `-d/--debug` is for stdout output only and
never affects what the encoded video looks like.

**4. The user's zoom knob should be authoritative.** The
`torso_height_multiple` config key controls how zoomed-in the crop
is. A long history of "I set this to 5 and got a multiple of 10"
bugs traced to silent floors and clamps overriding the user's
request. Hidden variables that quietly contradict the user's setting
are the failure mode this design fights hardest against.

**5. Truthful container labeling.** When the file is `.mp4` the
bytes are MP4. When it's `.mkv` the bytes are Matroska. Earlier code
wrote Matroska bytes to a `.MOV`-named file because the input was
`.MOV`; that is now forbidden. The default output container is
`.mkv` regardless of input extension; `--mp4` opts into MP4.

## Pipeline overview

```
solved interval set
  -> trajectory_to_crop_rects (per-frame solved torso box -> crop rect)
  -> direct_center_crop_trajectory (W+H averaging, smoothing, fit-to-source)
  -> apply_experiment_overrides (optional: fixed_crop / slow_size)
  -> validate_torso_within_central_window (pre-flight gate)
  -> for each frame:
       decode -> apply_crop -> resize to output res
       -> apply ffmpeg filters (bilateral, hqdn3d, auto_levels, ...)
       -> draw overlays (tracking / debug / velocity)
       -> pipe raw bytes to ffmpeg encoder
  -> mux audio (ffmpeg -c copy)
```

The hot path is parallelizable: `encode_cropped_video_parallel`
splits frames into chunks, each worker decodes and encodes its slice
to a temporary segment, then `mkvmerge` concatenates the segments.

## Crop trajectory: `direct_center_crop_trajectory`

This is the production crop path. The legacy `CropController` (an
online, stateful smooth crop) still exists for the smooth-mode crop
path, but `direct_center` is the default and where day-to-day work
happens.

### W+H averaging contract

The crop's desired height is computed from BOTH torso dimensions:

```
desired_h_from_h = raw_h * torso_height_multiple
desired_h_from_w = (raw_w * torso_height_multiple) / aspect_ratio
desired_crop_h   = 0.5 * (desired_h_from_h + desired_h_from_w)
desired_crop_w   = desired_crop_h * aspect_ratio
```

For a torso whose W:H matches the canonical aspect ratio, both
estimates agree exactly. Averaging is the robustness move: it makes
the zoom resistant to per-frame bbox jitter on either dimension
(arms-out widens raw_w; head-back tilts raw_h; either alone would
otherwise lurch the crop).

`torso_height_multiple` is the only zoom knob. There is no
`crop_min_size` floor (it was removed in 2026-05-02 because it
silently overrode the user's setting on long-lens footage). An
internal 1-pixel sanity floor exists only to prevent zero-divide on
degenerate frames where the runner sits exactly on a frame edge.

### Smoothing

Two independent smoothing alphas:

- `crop_post_smooth_strength` (default `0.0`): forward-backward EMA
  on crop center (pan/tilt). Off by default so the crop stays glued
  to the runner.
- `crop_post_smooth_size_strength` (default `0.15`): forward-backward
  EMA on crop height (zoom). On by default to prevent the historical
  "zoom bouncing" failure mode where per-frame torso-bbox jitter
  (typically +/-5%) translates directly into visible breathing.

Both use forward-backward EMA (one forward pass, one backward pass)
so smoothing introduces no group delay. The size signal is
re-clamped after smoothing so the EMA cannot lift any frame past its
fit-to-source bound.

### Step 3.6: fit-to-source

By default (`crop_centered_fit_to_source: True`), the centered
crop is shrunk per frame so at least one edge touches the source
frame. This keeps the runner perfectly centered at the cost of
making `torso_height_multiple` an upper bound (cap), not a fixed
target. When the runner moves through a frame region where the
centered crop would extend off the source, the crop zooms IN.

The legacy alternative is `crop_centered_fit_to_source: False`: the
crop holds at the requested size and slides off the source edge,
with `apply_crop` filling the off-frame region with black ("black
fill policy"). This is the historical behavior; users opt in when
they prefer black bars over auto-zoom.

The current default is `True` because the most common complaint was
"the runner is pinned to one side with black on the other" --
fit-to-source eliminates that. Users who explicitly accept black
bars should set the flag to `False` in their per-video config.

### Pre-flight gate

`validate_torso_within_central_window` runs before any frame is
written. It refuses to encode if the runner's torso center sits
outside the safe central window (default: central 50% horizontally,
70% vertically) for more than 3 consecutive frames. The exception
(`OffCenterCropError`) carries structured fields
(`first_violating_frame`, `run_length`, `edge`) so callers can
inspect the violation programmatically. The CLI flag
`--allow-offcenter-crop` bypasses the gate for the rare runs that
need the legacy black-fill behavior at the source-frame edges.

This validator catches the failure mode where an aggressive
`torso_multiple` plus an extreme aspect ratio plus a runner near a
frame edge produces an encoded file with the runner pinned to one
side and the other 50%+ of the frame black. ffmpeg never starts on
an invalid configuration; the gate fires within ~1 second of CLI
launch.

## Overlay tiers

Three independent flags gate three overlay sets. They are
intentionally independent so a user can request "review overlay plus
velocity arrow" or "developer overlay only" without forcing a
particular combination.

| Flag | Audience | Elements |
| --- | --- | --- |
| `--draw-tracking-overlay` | Review (playback) | Torso box, crosshair |
| `--draw-debug-overlay` | Developer (frame-by-frame) | Torso box, crosshair, FWD/BWD prediction boxes, competitor box, raw box, source/confidence labels, interval ID, per-frame diagnostic text |
| `--draw-velocity-arrow` | Both | Per-frame motion arrow at the crosshair |

`--draw-debug-overlay` implies `--draw-tracking-overlay` (the
developer overlay always includes the review elements). The two
overlay tiers are mutually exclusive at parse time -- requesting
both is an error.

`--draw-velocity-arrow` cannot be set alone; it requires one of the
overlay tiers. This is enforced at parse time, not silently
ignored, because an arrow with no anchoring crosshair is a
misleading visualization.

### Why tiers, not flags

Earlier code used a single `--debug` flag that turned on every
overlay element at once. The result was that "I want to confirm the
tracker is on the runner" and "I want to debug a divergent FWD/BWD
pass" used the same dense overlay -- the review use case got
overwhelmed with diagnostic boxes that pulled attention from the
runner. Splitting into review and developer tiers fixed this.

`-d/--debug` (global) was repurposed: it now controls only stdout
diagnostic output and never affects encoded video.

## Velocity arrow design

The velocity arrow visualizes the runner's land-relative motion
direction and (gain-amplified) magnitude.

### Land-relative, not camera-relative

A camera-relative arrow (subtract previous-frame pixel center from
current-frame pixel center) points the WRONG WAY when the camera
pans faster than the runner moves on the ground. Imagine a runner
moving left-to-right on the track while the camera pans right faster
than the runner's ground speed. The runner's pixel position moves
LEFT in the frame, even though physically she's moving right. A
camera-relative arrow would point left -- contradicting the runner's
actual motion.

The fix: project both endpoints through the scene transform.

```
prev_scene = scene_transform.pixel_to_scene(prev_idx, prev_x, prev_y)
prev_in_current = scene_transform.scene_to_pixel(i, prev_scene[0], prev_scene[1])
arrow = current_pixel_center - prev_in_current
```

The arrow then reflects ground motion expressed in the current
camera's pixel space. The driver in `cli.py:_mode_encode`
precomputes `prev_center` for every frame on the driver side
(loading the active per-hash camera-motion cache via
`load_active_camera_motion_or_fail`); the encoder reads
`state["prev_center"]` per frame.

When no camera-motion cache is available, the arrow falls back to
camera-relative motion with a one-line warning. This is read-only
graceful degradation -- encode never hard-errors on missing motion.

### Subpixel angles

The arrow direction is `atan2(dy, dx)` from a 5-frame look-back. At
60 fps with a slow runner, dx/dy can be 1-5 pixels. Truncating
endpoints to integer pixel coordinates -- which the original
implementation did via `int()` casts in `_point_to_crop_coords` --
quantizes the angle to ~10-15 degree steps.

The fix: keep coordinates as floats end-to-end through the
displacement calculation, round to int only at the final
`cv2.arrowedLine` call. `_point_to_crop_coords` returns floats; the
velocity-arrow block computes `dx`, `dy`, and the tip endpoint in
floats, then `int(round(...))` for the cv2 call.

### Length is gain-only

`_VELOCITY_GAIN` (default `9.0`) is the only length knob. A previous
implementation capped the arrow at half the torso-box width, which
made `_VELOCITY_GAIN` dead in the typical case (raw 5-frame
displacement at sprinting speeds always exceeded the cap, so 3.0 vs
9.0 produced visually identical arrows). The cap was removed in
2026-05-02. Doubling the gain now visibly doubles the arrow.

Glitched-frame protection comes from the bounded look-back window
(`_VELOCITY_LOOKBACK_FRAMES = 5`): a long not-in-frame gap simply
produces no arrow (no valid prior found within 5 frames).

## Container and codec policy

### Default container is `.mkv`

Earlier code wrote Matroska bytes via `mkvmerge` regardless of
output extension, then named the file with the input's extension
(`.MOV` in -> `.MOV` out). The label lied about the format. Fixed
by forcing `.mkv` as the default output extension across all input
types. MKV's index-based seeking is also more reliable for review
playback than MOV.

### `--mp4` for MP4 export

`--mp4` (or an explicit `-o foo.mp4`) writes a real MP4 via the
existing audio-mux step. ffmpeg `-c copy` honors the destination
extension's container, so the byte-stream gets remuxed (no
re-encode) into the MP4 container. The parser rejects `--mp4 -o
foo.mkv` and any `-o` with a non-mkv/non-mp4 extension.

### Codec defaults

| Knob | Default | Source |
| --- | --- | --- |
| `video_codec` | `libx264` | YAML config `processing.video_codec` |
| `crf` | `18` | YAML config `processing.crf` |
| `pix_fmt` | `yuv420p` | hardcoded in `VideoWriter` |

CRF 18 is "visually lossless" for h.264 at most reasonable resolutions.
yuv420p is the universal browser/QuickTime-compatible pixel format.

## Filters

Filters are applied via ffmpeg's `-vf` chain on the encoder side
(after frame extraction and overlay drawing). The filter chain is
declarative in YAML config:

```yaml
processing:
  encode_filters:
    - bilateral
    - auto_levels
    - hqdn3d
```

Order matters. Each filter resolves to an ffmpeg filter graph
fragment via a small lookup table in
`encoder._resolve_encode_filters` / `cli._resolve_encode_filters`.

### Filter precedence

Three ways to control the filter chain at the CLI:

1. `-F/--filter`: override the YAML chain entirely.
2. `--no-filters` or `-F none`: disable all filters; produces an
   ffmpeg invocation with no `-vf` flag.
3. (default): use the YAML chain.

`--no-filters -F blur` is rejected at parse time. `-F none` cannot
combine with other filter names. `none` is case-insensitive and
whitespace-tolerant.

## Camera-motion identity

Encode loads the camera-motion artifact via
`camera_motion.load_active_camera_motion_or_fail(input_file, config)`,
which reads the canonical file `<video>.track_runner.camera_motion.npz`
and validates that the persisted `motion_model` matches the current
configuration. This ensures encode uses EXACTLY the same camera
motion that solve used to build the torso boxes. There is no
silent recompute.

If the artifact file is missing or the stored `motion_model` does not
match the current config, the loader raises `RuntimeError`; encode
catches it and prints a one-line warning that the velocity arrow will
use camera-relative motion.

This is the only reason encode reads camera motion at all. The crop
trajectory itself does not consult camera motion; it consumes the
solved torso boxes directly.

## Parallel encoding

`encode_cropped_video_parallel` splits the frame range across worker
processes, each encoding to a temporary `.mkv` segment. `mkvmerge`
concatenates the segments at the end. Each worker:

- Opens its own `VideoReader` (file handles do not cross process
  boundaries).
- Decodes its slice of frames sequentially.
- Receives the run-invariant state (crop rects, frame_states,
  overlay tier flags, codec/crf settings) once via the pool
  initializer.
- Writes raw frame bytes to its own ffmpeg subprocess.

Worker boundary discipline: drivers own all I/O and control state
(progress bar, keyboard quit, cache lookups, persistence); workers
own only compute. No worker writes to stdout or to disk outside its
assigned segment file.

The driver-side `prev_center` precomputation for the velocity arrow
exists specifically because chunk seams broke the per-worker
look-back. A worker only sees its own slice of `frame_states`, so a
chunk-local 5-frame look-back at the first frame of a non-first
worker chunk would always find `None` and the arrow would
disappear at every chunk boundary. Driver-side precomputation lets
each worker just read `state["prev_center"]` without reaching across
the boundary.

## Output resolution

The encoder accepts an explicit `output_resolution: [W, H]` in the
YAML processing config, or computes it from the median of all crop
rectangles when omitted (auto-resolution). CLI `-r/--output-resolution`
overrides both. The resolved (out_w, out_h) is logged at startup and
then used both for ffmpeg's `-s WxH` flag and the per-frame resize.

When `output_resolution` is set, the encoded width/height is fixed;
when auto, it tracks the median crop dimensions so a video where the
runner is consistently small produces a smaller output (no upscale
to a hardcoded resolution). Either way, the per-frame resize from
crop dimensions to output dimensions runs through `cv2.resize`.

## Aspect ratio handling

Aspect is height-anchored: `crop_w = crop_h * aspect_ratio`. The
runner stays centered vertically; horizontal padding around the
runner grows or shrinks with `torso_height_multiple` and the aspect
ratio.

Aspect strings parse as `W:H` floats; common values are `16:9`,
`23:9`, `4:3`, `1:1`. Invalid formats (`"16-9"`, `"16:0"`,
`"abc:9"`) raise `RuntimeError` at config load.

If the user wants a wide aspect (e.g. `23:9 = 2.556`) on footage
where the runner is near a horizontal frame edge, the fit-to-source
shrink (Step 3.6) becomes especially active and `torso_multiple`
becomes an aggressive cap. This is the most common surprise. The
remediation is either to lower `torso_multiple` (so the unshrunk
crop already fits) or to set `crop_centered_fit_to_source: False`
and accept black bars.

## Hidden-variable audit

The encode path's silent defaults (where `processing.get(key,
default)` returns a non-zero / non-identity value if the key is
absent) are listed below. Unaware users have hit each of these at
some point.

| Key | Default | Effect when default fires |
| --- | --- | --- |
| `crop_centered_fit_to_source` | `True` | Shrinks crop to fit source frame; makes `torso_multiple` an upper bound rather than a fixed target |
| `crop_post_smooth_size_strength` | `0.15` | Forward-backward EMA on crop height; smooths zoom but introduces a soft delay |
| `crop_torso_anchor` | `0.50` (identity) | No effect; safe |
| `crop_max_velocity` | `30.0 px/frame` | Legacy `CropController` only; ignored by direct_center mode |
| `crop_smoothing_attack` / `crop_smoothing_release` | `0.15` / `0.05` | Legacy `CropController` only |
| `crop_velocity_scale` | `2.0` | Legacy `CropController` only |
| `crop_displacement_alpha` | `0.1` | Legacy `CropController` only |
| `crop_zoom_stabilization` | `False` | Off; safe |
| `crop_max_height_change` | `0.005` | Only active when zoom_stabilization is True |
| `crop_containment_radius` | `0.20` | Pulls crop center back toward runner if drift exceeds 20% of crop width |
| `crop_post_smooth_max_velocity` | `0.0` | Off; safe |
| `crop_post_smooth_strength` | `0.0` | Off; safe |
| `_VELOCITY_GAIN` (encoder.py) | `9.0` | Hardcoded velocity-arrow length gain |
| `_VELOCITY_LOOKBACK_FRAMES` (encoder.py) | `5` | Hardcoded velocity-arrow look-back window |
| `_OVERLAY_ALPHA_BOXES` / `_OVERLAY_ALPHA_TEXT` | from `overlay_styles.yaml` | Box and text alpha for overlay blending |

When a knob is config-driven, its current effective value should be
visible to the user at encode-mode startup. Where this is not yet
the case, that is a UX bug to file rather than a documentation gap.

## Failure modes and remediation

| Symptom | Likely cause | Where to look | Remediation |
| --- | --- | --- | --- |
| Runner pinned to one frame edge with black on the other | `crop_centered_fit_to_source: False` plus aggressive `torso_multiple` | Per-video config | Set fit_to_source true, or lower torso_multiple, or set `--allow-offcenter-crop` |
| `OffCenterCropError` at startup | Validator caught a sustained off-center streak | `tr_crop:validate_torso_within_central_window` | Lower torso_multiple or pass `--allow-offcenter-crop` |
| Visible zoom breathing | Size smoothing disabled in per-video config | `crop_post_smooth_size_strength` | Set to `0.15` (default) or higher |
| Velocity arrow points opposite the runner's actual motion | Camera pan faster than runner; missing camera-motion cache | Active marker / per-hash cache | Run `solve` to rebuild the cache |
| Velocity arrow never appears | No prior valid center within 5 frames | `_VELOCITY_LOOKBACK_FRAMES` | Acceptable for not-in-frame stretches; otherwise a tracking gap |
| Velocity arrow has discrete angle steps | Should not happen post-2026-05-02 | encoder.py float-coord path | File a bug; fast regression check |
| Velocity arrow length unchanged when `_VELOCITY_GAIN` doubles | Should not happen post-2026-05-02 | encoder.py cap removal | File a bug; fast regression check |
| `.mp4` output but bytes are Matroska | Should not happen post-2026-04-27 | `cli.py:_mode_encode` temp-file naming | File a bug |
| Encoded torso much smaller than expected from `torso_multiple` | Was the `crop_min_size` floor pre-2026-05-02; now should not happen | `tr_crop:direct_center_crop_trajectory` | File a bug; if it reproduces, check no per-video config still sets `crop_min_size` |

## File layout

| Concern | File |
| --- | --- |
| Crop trajectory | [track_runner/tr_crop.py](../track_runner/tr_crop.py) |
| Frame draw and overlays | [track_runner/encoder.py](../track_runner/encoder.py) |
| ffmpeg writer and audio mux | [track_runner/encoder.py](../track_runner/encoder.py) `VideoWriter`, `copy_audio` |
| Driver-side encode mode | [track_runner/cli.py](../track_runner/cli.py) `_mode_encode` |
| CLI argument parsing | [track_runner/cli_args.py](../track_runner/cli_args.py) |
| Aspect ratio parsing | [track_runner/tr_crop.py](../track_runner/tr_crop.py) `parse_aspect_ratio` |
| Camera motion load | [track_runner/camera_motion.py](../track_runner/camera_motion.py) `load_active_camera_motion_or_fail` |
| Pre-flight validator | [track_runner/tr_crop.py](../track_runner/tr_crop.py) `validate_torso_within_central_window`, `OffCenterCropError` |
| Filter resolution | [track_runner/cli.py](../track_runner/cli.py) `_resolve_encode_filters` |

## Related contracts and design docs

- [docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md): hard
  invariants. Source-frame canonicality (C2 derivative), camera-motion
  identity (C9), and pre-race anchoring (C4) all touch encode.
- [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md): general
  philosophy. The "tracker follows accurately, crop moves smoothly"
  separation is articulated here.
- [docs/TR_CAMERA_MOTION_METHOD.md](TR_CAMERA_MOTION_METHOD.md):
  Stage 1 details that encode consumes for the velocity-arrow
  projection.
- [docs/TRACK_RUNNER_ANALYZE_AND_ENCODE.md](TRACK_RUNNER_ANALYZE_AND_ENCODE.md):
  pre-encode crop-stability diagnostics. For pre-encode diagnostics including a
  per-frame view of zoom stability, camera motion, and runner ground speed, see
  [docs/modes/ANALYZE.md](modes/ANALYZE.md) and run `analyze --plot` on a solved
  video to produce a self-contained HTML report.
