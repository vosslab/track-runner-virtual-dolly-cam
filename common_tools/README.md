# common_tools

Shared utilities for video reading, frame geometry, FFT-friendly sizing,
and frame filtering. These modules are imported by both `track_runner/`
and stand-alone scripts under `tools/`.

## Modules

- [`frame_reader.py`](frame_reader.py) - reliable single-frame reader on top of `cv2.VideoCapture` with five seek strategies and optional pre-binning.
- [`goodbox.py`](goodbox.py) - "goodbox" sizing helpers (FFT-friendly dimensions; prime factors `<= 11`).
- [`frame_filters.py`](frame_filters.py) - simple per-frame image filters used by the encoder pipeline.
- [`tr_video_identity.py`](tr_video_identity.py) - basename + size-bytes fingerprinting used to warn on input-file identity mismatches.
- [`video_io.py`](video_io.py) - lives under [`track_runner/`](../track_runner/video_io.py); legacy `VideoReader`. New code should prefer `common_tools.frame_reader.FrameReader`.

## Read patterns and their costs (very important)

`FrameReader.read_frame(frame_index)` is **NOT a constant-cost operation**.
The cost depends on the codec, container, GOP size, and especially the
access pattern. Internal hot paths must be designed around this.

Numbers below are from a representative iPhone clip used in this repo:

| Property | Value |
| --- | --- |
| File | `TRACK_VIDEOS/Lyra-Wheeling-IMG_3912.mkv` |
| Container | `matroska,webm` (remuxed lossless from `.MOV`) |
| Codec | HEVC / H.265 Main 10 (10-bit), HDR (BT.2020 + HLG) |
| Resolution | 3840 x 2160 |
| Frame rate | 120 fps (avg `120/1`, time_base `1/1000`) |
| Total frames | ~27,372 |
| Has B-frames | NO |
| GOP size | exactly **119 frames** (uniform; iPhone 4K HEVC default) |
| OpenCV backend (built-with) | FFMPEG, AVFoundation. NO GStreamer. |
| OpenCV `cv2.VideoCapture` backend in use | FFMPEG (default) |
| `cv2.VideoCapture(path, CAP_AVFOUNDATION)` | fails to open this file |

### Strategy 0: sequential fast-path (the cheap one)

When `frame_index == self._cap_next_index` (i.e. the requested frame is
the next consecutive frame after the previous successful read),
`read_frame` skips the `cap.set(...)` call entirely. The decoder's
internal pipeline keeps streaming frames in order and the per-frame cost
is purely decode bandwidth.

| Pattern | Per-frame cost (ms) | Notes |
| --- | --- | --- |
| 50 consecutive frames (single capture, single thread) | **6.4 ms** | measured on this 4K HEVC HDR file |
| Worst sustained sequential rate observed | ~14 ms | during 7-way parallel solve |

If your hot path can be expressed as monotonically increasing
`frame_index` sequences, use that pattern. The strategy-0 fast-path
fires automatically.

### Strategy 1: scattered seek via `CAP_PROP_POS_FRAMES` (the expensive one)

Fires whenever `frame_index != self._cap_next_index`. Calls
`cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)` then `cap.read()`. On
HEVC, FFMPEG seeks to the nearest preceding keyframe and decodes forward
to the target frame.

| Workload | Per-call cost (ms) | Notes |
| --- | --- | --- |
| Single-process synthetic test, scattered seeks across the file | **130-575 ms** (range) | direct cv2 calls, no other CPU load |
| Real Stage-4 run with 7 workers all scattered-seeking concurrently | **median 2,599 ms, p95 3,683 ms, max 3,983 ms** | from `--debug-blob` instrumentation |

The 4-5x difference between the synthetic and real-run numbers is CPU
contention: 7 worker processes all decoding 4K HEVC HDR at the same time
saturate macOS's CPU and shared cache, multiplying each worker's per-call
decode cost.

Within a single GOP the cost varies with offset from the keyframe. From
the synthetic probe (GOP = 119 frames):

| Offset from keyframe | Median seek+read (ms) |
| --- | --- |
| 0 (at the keyframe) | 450-554 |
| 5 | 469-565 |
| 30 | **133-184** (cheapest band) |
| 60 | 253-340 |
| 120 (across one GOP boundary) | 440-575 |
| 200 (across one full GOP and a bit) | 320-450 |

The "offset 0" being slower than "offset 30" is counter-intuitive but
real: the decoder pays a keyframe-restart overhead at offset 0 that gets
amortized over the next handful of frames decoded forward, and the
internal buffering settles into a cheaper steady state around mid-GOP.

### Strategy 2-5 (fallbacks)

| Strategy | When | Cost | What it does |
| --- | --- | --- | --- |
| 2 | strategy 1 returns `ret=False` | ~same as 1 plus prior failure | `cap.set(CAP_PROP_POS_MSEC, ...)` then `cap.read()`. On H.264 mp4/MOV historically slower than POS_FRAMES; kept as a fallback for codecs where POS_FRAMES does not honor random access. |
| 3 | strategy 2 returns `ret=False` | ~1-3 s | Releases and reopens the capture; retries POS_FRAMES. Fixes some FFMPEG state issues. |
| 4 | strategy 3 returns `ret=False` | proportional to total_frames | Sequential walk on a *separate* dedicated capture from frame 0 (or wherever the dedicated capture is). **Catastrophic** on scattered access; the dedicated capture is held aside specifically so this fallback can exist. |
| 5 | strategy 4 returns `ret=False` and remux not yet attempted | one-shot: minutes | Remux the source video to MKV via `mkvmerge` (lossless container repackage), reopen captures, retry strategies 1-4. Fixes HEVC-in-MOV containers where OpenCV cannot seek at all. |

For the iPhone HEVC HDR file documented here, `--debug-blob` confirmed
strategies 4 and 5 do **not** fire on a healthy run. Strategy 1 succeeds;
the cost is just high.

### Bin factor and what it does NOT do

`FrameReader(..., bin_factor=N)` (added 2026-05-01 to 2026-05-02) applies
`cv2.resize(frame, ..., cv2.INTER_AREA)` to the decoded BGR frame **after
decode**, then returns the binned frame. It does *not* tell the decoder
to produce a smaller frame. Implications:

- **Decode bandwidth is unchanged.** OpenCV/FFMPEG always decodes at the
  full source resolution (3840 x 2160 here).
- **Scattered-seek cost is unchanged.** Bin only reduces post-decode
  pixel work, not seek work.
- **Sequential-read cost goes down only marginally.** The decode is
  still 6+ ms/frame; bin saves ~1-2 ms of cv2.resize per frame.
- The benefit of `--bin` lies entirely in the *consumers* of frames:
  smaller arrays for camera-motion phase correlation, smaller residual
  maps in the blob path, less memory traffic. None of that helps if
  hot-path callers are scattering reads.

## Implications for downstream callers

1. **Sequentialize when possible.** If your code needs frames `t0, t1,
   t2, ..., tN` and they're monotonically increasing, request them in
   order. The strategy-0 fast-path is the only cheap path.
2. **Avoid scattered random-access in tight loops.** A 200-frame loop
   that reads each frame's `t-8 .. t+8` neighbor stack costs ~400 reads
   per frame x 7 workers x 2,599 ms/call = **~30 minutes per worker per
   200-frame interval** at typical Stage 4 sizes. This is the bottleneck
   the per-worker pre-pass (`solve_interval_analytical` plan, M3+M4) was
   designed to eliminate.
3. **If you must do scattered access, batch and pre-fetch.** Read all
   needed frames sequentially up front, store in RAM, consume from RAM.
   The 81.7% BGR cache hit rate measured under load shows the cache
   already does this well at the per-call neighbor level; the missed
   18.3% are the cross-call boundaries (FWD/BWD direction reversal,
   cold start of a new interval).
4. **Don't expect `--bin` to make seeks cheap.** It doesn't. Bin
   reduces post-decode work; it does not reduce decode or seek work.
5. **Do not switch to OpenCV's AVFoundation backend on macOS** without
   testing on the file in question. For this repo's iPhone HEVC HDR
   file, `cv2.VideoCapture(path, cv2.CAP_AVFOUNDATION)` failed to open
   the file. Hardware-accelerated decode for HEVC/HDR via macOS
   VideoToolbox would require a different binding (PyAV with explicit
   `hwaccel='videotoolbox'`, or an `ffmpeg` subprocess pipe), which is
   out of scope for `frame_reader.py` today.
6. **GOP rewrites are an option of last resort.** Re-encoding the source
   to a smaller GOP (e.g. `ffmpeg -c:v hevc_videotoolbox -g 30`) would
   make scattered seeks ~4x cheaper, but at the cost of file size,
   re-encode time, and a quality-quantization loss. Not recommended for
   archival source files; possibly acceptable for a derived "proxy"
   workflow.

## How these numbers were generated

- Codec / GOP info: `ffprobe -v error -select_streams v:0 -show_packets
  -show_entries packet=pts,flags -of csv=print_section=0 <path>` then
  count packet indices with `K` flag set; the gap between consecutive
  flagged indices is GOP size.
- Sequential rate: `cap.set(POS_FRAMES, 100); cap.read()` (warmup);
  then `time.perf_counter()` around 50 consecutive `cap.read()` calls.
- Scattered seek rate: between each measurement, `cap.set(POS_FRAMES, 0);
  cap.read()` to invalidate the decoder cursor; then time
  `cap.set(POS_FRAMES, target); cap.read()`.
- Real-run distribution: instrumentation gated on `--debug-blob` in
  `solve` mode (added 2026-05-03). See
  [`docs/CHANGELOG.md`](../docs/CHANGELOG.md).

When in doubt about a new file's read characteristics, run the
diagnostic flag and look at the per-strategy histogram.

## See also

- [`docs/CHANGELOG.md`](../docs/CHANGELOG.md) entry dated 2026-05-03 for
  the `--debug-blob` flag and the FrameReader scattered-read measurement
  campaign.
- [`docs/TRACK_RUNNER_DESIGN.md`](../docs/TRACK_RUNNER_DESIGN.md) for the
  per-interval, sequential-pre-pass architecture (M3+M4 plan) that
  eliminates scattered seeks from the Stage 4 hot path.
