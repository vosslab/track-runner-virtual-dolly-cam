# encode mode

Produce the final cropped and stabilized video from the existing solved trajectory.
Encode defaults to the offline whole-path `dolly` crop, applies the configured
filter pipeline, and uses ffmpeg to render the output file.

## When to use it

- As the final step after achieving acceptable interval scores via target/refine cycles.
- To experiment with output format, aspect ratio, or filters without re-solving.

## Command line reference

<!-- BEGIN AUTO HELP: encode -->
```text
usage: track_runner.py encode [-h] [-o OUTPUT_FILE] [--aspect ASPECT]
                              [--keep-temp] [-F ENCODE_FILTERS] [--no-filters]
                              [--mp4] [--allow-offcenter-crop]
                              [--draw-tracking-overlay | --draw-debug-overlay]
                              [--draw-velocity-arrow]
                              [--torso-multiple TORSO_MULTIPLE]
                              [-r OUTPUT_RESOLUTION] [--crf CRF]
                              [--video-codec VIDEO_CODEC]

options:
  -h, --help            show this help message and exit
  -o OUTPUT_FILE, --output OUTPUT_FILE
                        Output video file path (auto-generated if not
                        provided).
  --aspect ASPECT       Override crop aspect ratio (e.g. '1:1', '16:9').
  --keep-temp           Keep temporary files after encoding.
  -F ENCODE_FILTERS, --encode-filters ENCODE_FILTERS
                        Comma-separated filter pipeline for encode output
                        (overrides config). Example: bilateral,hqdn3d. Pass
                        '-F none' as an alias for --no-filters.
  --no-filters          Disable all encode filters (overrides config and -F).
                        Cannot be combined with -F/--encode-filters.
  --mp4                 Write final output as .mp4 (stream-copy remux from the
                        internal MKV; no re-encode). Default container is
                        .mkv.
  --allow-offcenter-crop
                        Skip the central-window torso-center check and let the
                        encode proceed even when the runner is sustained
                        outside the safe central window of the output frame.
                        The encoded video may show black bars where the crop
                        window extends past the source frame.
  --draw-tracking-overlay
                        Draw the normal review overlay: final torso box and
                        small center crosshair only.
  --draw-debug-overlay  Draw the developer overlay: tracking overlay plus raw
                        box, FWD/BWD boxes, source/confidence labels, and
                        other diagnostic geometry.
  --draw-velocity-arrow
                        Draw the per-frame motion arrow. Requires --draw-
                        tracking-overlay or --draw-debug-overlay.
  --torso-multiple TORSO_MULTIPLE
                        Override torso_height_multiple for this encode only.
                        Crop height = this x tracked torso height; larger =
                        wider view.
  -r OUTPUT_RESOLUTION, --output-resolution OUTPUT_RESOLUTION
                        Override output_resolution as WxH (e.g. '1920x1080').
                        Must match --aspect.
  --crf CRF             Override CRF quality for this encode only (lower =
                        higher quality).
  --video-codec VIDEO_CODEC
                        Override FFmpeg video codec (e.g. 'libx264',
                        'libx265').

Global -d/--debug controls diagnostic output only and does not affect rendered
overlays. Use --draw-tracking-overlay (review), --draw-debug-overlay
(developer), and --draw-velocity-arrow (motion cue) to burn overlays into the
encoded video.
```
<!-- END AUTO HELP: encode -->

## Notes

**Output format:**

- Default container is `.mkv` (Matroska).
- `--mp4` flag switches to `.mp4` container (slower, but widely compatible).

See the auto-generated "Command line reference" block above for the full flag list.

Encode reuses the solved trajectory, but it has additional crop-only handling
for `not_in_frame` spans: temporary edge anchors guide output framing and are
not interpolated runner geometry or persisted tracking state. The default
`dolly` crop uses the full solved path and falls back to `smooth` only if its
bounded containment solve does not converge. The final output is an
ffmpeg-encoded video with configured crop positioning, stabilization, and
optional filters.

For deeper reference on analyze and encode architecture, see
[../TRACK_RUNNER_ANALYZE_AND_ENCODE.md](../TRACK_RUNNER_ANALYZE_AND_ENCODE.md).
For default config settings (codec, CRF, default filters), see
[../TRACK_RUNNER_YAML_CONFIG.md](../TRACK_RUNNER_YAML_CONFIG.md).
