# prepare mode

Creates a fast-read working video beside the original. All working modes decode
from the fast-read video when it is present and structurally valid; the final
encode always uses the original. This is an optional but recommended first step
for 4K HEVC sources.

## When to use it

- Before `setup` on 4K HEVC Main-10 HDR sources, where scattered OpenCV frame
  reads cost 130-575 ms per call (see [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md)).
- Once per video file; re-run with `--force` only if the source changes or you
  want to recreate the fast-read artifact.
- Optional for H.264 or lower-resolution sources; working modes fall back to the
  original with no warning when the fast-read video is absent.

## Command line reference

<!-- BEGIN AUTO HELP: prepare -->
```text
usage: track_runner.py prepare [-h] [-f] [-v]

options:
  -h, --help     show this help message and exit
  -f, --force    Delete any existing fast-read video and recreate
                 unconditionally. Without --force, an existing valid fast-read
                 is kept; an invalid one raises an error.
  -v, --verbose  Stream full ffmpeg command and stderr to the terminal.
```
<!-- END AUTO HELP: prepare -->

## What it creates

`prepare` transcodes the source to a derived working video placed beside the
original at the deterministic path:

```text
original:  Lyra-Wheeling-IMG_3912.mkv
fast-read: Lyra-Wheeling-IMG_3912.fastread.mkv     (same directory)
state:     Lyra-Wheeling-IMG_3912.track_runner.*   (unchanged; all keyed to original)
```

The fast-read video is:

- H.264, 8-bit yuv420p, CRF 23, GOP 30.
- 3D-denoised (`hqdn3d`).
- Video-only (no audio track).
- Same resolution, same frame count, equivalent frame timing as the original.

The settings are fixed constants in `track_runner/fastread_video.py`; there is
no per-video settings file. If the defaults change later, the user re-runs
`prepare --force` to recreate.

## Role policy

| Mode | Decodes from | Writes / keys state under |
| --- | --- | --- |
| prepare | original | fast-read video at deterministic path |
| setup, seed, edit, target | fast-read when valid, else original | config/seeds/state under original |
| solve, refine | fast-read when valid, else original | caches/state/output names under original |
| analyze | fast-read when valid, else original | reports keyed to original; includes `canonical_source` and `decode_source` fields |
| encode | always original | output naming keyed to original |

Every mode banner names both the source video and the decode video:

```text
source video: Lyra-Wheeling-IMG_3912.mkv
decode video: Lyra-Wheeling-IMG_3912.fastread.mkv
```

When no fast-read video exists, the banner shows `decode video: original`.

Working modes decode from the fast-read video when it is present and structurally
valid; the final encode always uses the original.

## Structural validation

Every mode run that finds a fast-read video at the deterministic path validates
it live against the current original before decoding from it. Validation checks:

- Width and height (exact match).
- Frame count (exact match).
- Duration (within a small tolerance).
- Frame rate (within probe-precision tolerance).
- Best-effort first, middle, and last frame timestamp alignment; falls back to
  frame-count plus duration invariant when per-frame timestamps are not
  available, and notes the fallback in console output.
- Smoke reads of frames 0, frame_count//2, and frame_count-1 via FrameReader.

A structurally valid fast-read video is the only gate for routing. There is no
sidecar file and no persisted bookkeeping; all checks use live-probed data.

## Loud failure on invalid fast-read

If the fast-read video is present but fails any validation check, the run
raises immediately with:

- The fast-read path.
- The specific failed check.
- The remedy: "re-run prepare --force, or delete the fast-read video to use
  the original."

This is intentional: a present-but-mismatched fast-read file means the source
changed after `prepare` was run (re-remux, trim, or file replacement). Silent
fallback to the original would hide the mismatch.

## Idempotency

- Fast-read absent: `prepare` creates it.
- Fast-read present and structurally valid: `prepare` skips transcode and
  reports the existing file. Use `--force` to recreate.
- Fast-read present and structurally invalid: `prepare` raises with the remedy.
  Pass `--force` to delete and recreate.

## Progress and status summary

`prepare` prints coarse step progress and a status summary at the end of every
run:

```text
Track Runner prepare
source video:    Lyra-Wheeling-IMG_3912.mkv
fast-read video: Lyra-Wheeling-IMG_3912.fastread.mkv
settings:        crf 23, gop 30, filter hqdn3d,format=yuv420p
[  0%] probing source video
[  5%] checking existing fast-read video
[ 10%] creating fast-read video with ffmpeg
[ ... ] ffmpeg running, elapsed 00:30
ffmpeg summary:
  frame=27372 fps=351 q=-1.0 Lsize=...
[ 90%] validating fast-read video
[100%] prepare complete

fast-read video: Lyra-Wheeling-IMG_3912.fastread.mkv
structural validity: OK
timestamp alignment: fallback used (frame count + duration)
next: all working modes will now decode from the fast-read video
      encode will still use the original video
```

Use `--verbose` to stream the full ffmpeg command and all stderr output.

## Rollback

To stop using the fast-read video, delete the `.fastread.mkv` file. All working
modes fall back to the original automatically on the next run. No other cleanup
is needed because nothing else is persisted.

```bash
rm Lyra-Wheeling-IMG_3912.fastread.mkv
```

## Frame-identity contract

The fast-read video preserves:

- Frame geometry (same width and height as the original).
- Frame count (exact match).
- Equivalent frame timing (same frame-index to time mapping).

It does NOT preserve:

- Exact pixel values. The 8-bit pixel-format conversion (no explicit HDR
  tonemapping; out-of-range values handled by the format conversion) and hqdn3d
  denoise change pixel statistics. This is the accepted quality trade-off for
  fast decode everywhere except final encode.

Consequence for seeding: seeds drawn on fast-read frames have slightly different
appearance (denoised, SDR) than the original source. Geometry is identical, so
torso boxes are correct. The `analyze` output includes both `canonical_source`
and `decode_source` fields to keep provenance traceable. The encode always reads
the original for final output quality.

See [../TRACK_RUNNER_DESIGN.md](../TRACK_RUNNER_DESIGN.md) (five-stage pipeline
and signal hierarchy) and [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) (known
slow-decode symptoms and remedies).
