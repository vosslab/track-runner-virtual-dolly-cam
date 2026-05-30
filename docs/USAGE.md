# Usage

## Entry point

```bash
source source_me.sh
python track_runner/track_runner.py -i VIDEO.mp4 <subcommand>
```

## Global options

Global options must appear before the subcommand.

| Flag | Description |
| --- | --- |
| `-i`, `--input` | Input video file path (required). |
| `-c`, `--config` | Config YAML file path (default: auto-detected). |
| `-d`, `--debug` | Enable debug video output with tracking overlays. |
| `-w`, `--workers` | Number of parallel workers (default: half of CPU cores). |
| `--time-range` | Limit processing to a time range in seconds. Format: `START:END`, `START:`, or `:END`. |

## Performance diagnostic flag

`solve` and `refine` accept `--debug-blob` to enable verbose Stage 4 instrumentation: per-worker per-frame `read_frame` strategy timings, residual compute timings, per-pid worker-exit summaries, and a 5-second master heartbeat. Off by default and zero overhead when off; opt in only when investigating Stage 4 wall time.

```
./track_runner/track_runner.py -i video.mkv solve --bin 4 --debug-blob 2>&1 | tee /tmp/blob_debug.log
```

See [README.md](../common_tools/README.md) for read-pattern cost numbers (sequential vs scattered) and how to interpret the per-strategy histogram.

## Subcommands

The eight subcommands -- `setup`, `seed`, `solve`, `target`, `refine`, `edit`, `encode`, `analyze` -- each have a dedicated reference page. See [MODES.md](MODES.md) for the index, or jump directly:

- [modes/SETUP.md](modes/SETUP.md) -- per-video camera configuration.
- [modes/SEED.md](modes/SEED.md) -- place anchor seeds.
- [modes/SOLVE.md](modes/SOLVE.md) -- full re-solve from scratch.
- [modes/TARGET.md](modes/TARGET.md) -- add seeds at weak intervals.
- [modes/REFINE.md](modes/REFINE.md) -- incremental re-solve.
- [modes/EDIT.md](modes/EDIT.md) -- fix or review existing seeds.
- [modes/ENCODE.md](modes/ENCODE.md) -- encode the final cropped video.
- [modes/ANALYZE.md](modes/ANALYZE.md) -- pre-encode diagnostic.

The flag tables on those pages are auto-regenerated from `--help` by `tools/refresh_mode_docs.py`.

## Typical workflow

1. **setup** -- one-time camera configuration for the video.
2. **seed** -- place anchor seeds on the runner.
3. **solve** -- full solve from the seed set.
4. **target** -- add corrective seeds at weak intervals.
5. **refine** -- incremental re-solve picking up the new seeds.
6. Repeat `target` + `refine` until interval scores are acceptable.
7. **encode** -- produce the final cropped output video.

`edit` is for fixing bad seeds or double-checking ones the scorer flagged.
`analyze` is a pre-encode diagnostic that reports crop-path stability,
solver context, and motion-regime classification without producing a
video; it is not required before `encode`. Run either when you need it.

## Heat movie diagnostic (blob_walk_v2)

The `--heat-movie` flag is available on both
`tools/blob_walk_v2/make_walk_html_v2.py` and
`tools/blob_walk_v2/core/walk_driver.py`. It is off by default and
only active when `--walk` is also set.

When enabled, it writes one per-direction `.mkv` file (`heat_fwd.mkv`
and `heat_bwd.mkv`) beside each interval's render output tiles. Each
movie shows the residual-motion heat overlay cropped to a fixed ROI
derived from the larger of the two bracketing seeds; the solved torso
box and in-box hot-mean value are drawn on every frame.

**ffmpeg is required** only for `--heat-movie`. The flag is checked at
startup and raises a clean error immediately if ffmpeg is absent. A
normal `--walk` run does not need ffmpeg.

Memory and scratch: raw `.bgr` frames are spilled one at a time to a
run-scoped scratch directory under `/tmp`, encoded with ffmpeg
`image2` (libx264, yuv420p), verified, then copied beside the render
output. The scratch directory is deleted at the end of each interval
encode. Nothing is retained between runs.

```bash
# Walk with heat movies (ffmpeg required)
python3 tools/blob_walk_v2/make_walk_html_v2.py --walk --heat-movie

# Explicitly disable (default)
python3 tools/blob_walk_v2/make_walk_html_v2.py --walk --no-heat-movie
```

Install ffmpeg if missing:

```bash
brew install ffmpeg
```

See [tools/blob_walk_v2/README.md](../tools/blob_walk_v2/README.md) for
the full blob_walk_v2 flag reference.

## Motion heat-map overlay

The annotation GUI (`seed`, `edit`, `target`) can show a residual-motion heat
map as a diagnostic overlay to help you see where real motion is happening
on the current frame.

- Toggle with the `H` key or the **Heat** button in the overlay toolbar.
- Sticky mode: the overlay stays ON across frame advances and recomputes
  automatically for each new frame. Press `H` again to turn it off.
- During a frame advance the previous heat is hidden immediately and the
  status label next to the toolbar action shows `computing...` until the
  new frame's heat is ready.
- If the current frame has no prediction (pre-race, unsolved intervals),
  the overlay stays hidden and the label reads
  `no prediction for this frame`. The toggle remains ON so heat will
  resume automatically on the next frame with a prediction.
- The heat map is scoped to the solver's 8x torso-height ROI around the
  predicted center, not the entire frame. Compute is expected to be
  interactive on typical review frames, though actual latency depends on
  the video's resolution and codec.
- The overlay is a full composite, not a translucent wash. Below-
  threshold pixels render as grayscale of the source frame (luminance
  preserved, color removed so irrelevant areas are visibly
  de-emphasized). Above-threshold pixels render as the JET-colorized
  residual mixed with the original color frame. The final pixmap is
  opaque.
- The JET colormap carries known accessibility caveats (non-monotonic
  luminance, red/green confusion). The `blend_alpha` value in
  [overlay_styles.yaml](../track_runner/overlay_styles.yaml)
  under the `heat_map:` block (default `0.40`) controls the JET-over-
  color mix in above-threshold pixels, not overlay transparency. Lower
  values make the color frame show through more under the heat tint;
  higher values make the JET dominate.
- A `camera motion not compensated` badge appears beneath the ROI whenever
  the overlay is shown. The GUI uses an identity scene transform in this
  release, so the overlay will ghost on panning footage. A future patch
  will load the solver's motion-track artifact.
- The overlay is strictly read-only. Enabling it does not alter
  trajectories, the geometry cache NPZ, interval-scores JSON, or any
  solver artifact. `SOLVER_FINGERPRINT_TAG` is unchanged.
- The overlay renders a display-oriented view: residual magnitudes below
  the configured `threshold` (default `10.0`) are suppressed so sensor
  noise does not fog the frame. This is an intentional divergence from
  `tools/diagnose_residual_motion.py`, which shows the full residual
  field including the noise floor.

## Configuration

The default config file is [track_runner.config.yaml](../track_runner/track_runner.config.yaml). Override with `-c`/`--config`. Settings include detection confidence threshold, crop aspect ratio, fill ratio, video codec, CRF, and encode filter pipeline.

## Input and output

- **Input:** any video file readable by ffmpeg/mediainfo.
- **Output:** cropped and stabilized video file. Per-video state (seeds, geometry cache, interval scores, debug tracks, camera motion) is stored in the per-video `tr_config` store; see [TR_CONFIG_FILES.md](TR_CONFIG_FILES.md) for the file layout.

## Keyboard shortcuts

See [TRACK_RUNNER_KEYBINDINGS.md](TRACK_RUNNER_KEYBINDINGS.md) for the full annotation UI keybindings reference.
