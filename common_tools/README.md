# common_tools

Shared utilities for video reading, frame geometry, FFT-friendly sizing,
and frame filtering. These modules are imported by both `track_runner/`
and stand-alone scripts under `tools/`.

## Modules

- [`frame_reader.py`](frame_reader.py) - reliable single-frame reader on top of `cv2.VideoCapture` with two seek strategies (sequential fast-path + `CAP_PROP_POS_FRAMES` random-access seek) and optional pre-binning. Source videos must be `.mkv`.
- [`probe_video.py`](probe_video.py) - video metadata probe via mediainfo CLI.
- [`goodbox.py`](goodbox.py) - "goodbox" sizing helpers (FFT-friendly dimensions; prime factors `<= 11`).
- [`frame_filters.py`](frame_filters.py) - simple per-frame image filters used by the encoder pipeline.
- [`tr_video_identity.py`](tr_video_identity.py) - basename + size-bytes fingerprinting used to warn on input-file identity mismatches.

## Source video format requirement

`FrameReader.__init__` rejects any path whose extension is not `.mkv`
(case-insensitive) and points the user at `mkvmerge`. MP4/MOV users
must remux losslessly once:

```bash
mkvmerge -o input.mkv input.mov
```

The pipeline does not transcode; remux is a fast, lossless container
repackage. This restriction landed when the PyAV decode backend was
removed (PyAV's bundled libav family collided in-process with OpenCV's
bundled libav, producing the `objc[..] AVFFrameReceiver is implemented
in both` warning at import time). The cv2-only path keeps decode and
encode on a single libav family.

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
| OpenCV `cv2.VideoCapture` backend in use | FFMPEG (default) |

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
| Real Stage-4 run with 7 workers all scattered-seeking concurrently | **median 2,599 ms, p95 3,683 ms, max 3,983 ms** | historical instrumentation |

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

### No further fallbacks

`FrameReader` does not retry, does not reopen the capture, does not walk
sequentially from frame 0 on failure, and does not remux to a different
container at runtime. If `cap.read()` returns failure on a healthy MKV
this is a real error and the reader returns `None`. The previous
5-strategy waterfall and mkvmerge remux fallback existed to cope with
HEVC-in-MOV containers; the `.mkv` requirement at init makes those
fallbacks unnecessary. The Stage-4 sequential pre-pass in
[`track_runner/residual_pre_pass.py`](../track_runner/residual_pre_pass.py)
absorbs scattered access into bounded sequential walks per worker.

### Bin factor and what it does NOT do

`FrameReader(..., bin_factor=N)` applies `cv2.resize(frame, ..., cv2.INTER_AREA)`
to the decoded BGR frame **after decode**, then returns the binned frame.
It does *not* tell the decoder to produce a smaller frame. Implications:

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
   the per-worker pre-pass in
   [`track_runner/residual_pre_pass.py`](../track_runner/residual_pre_pass.py)
   was designed to eliminate.
3. **If you must do scattered access, batch and pre-fetch.** Read all
   needed frames sequentially up front, store in RAM, consume from RAM.
   The Stage-4 pre-pass uses a bounded rolling buffer (cap of 40 frames
   per worker per buffer) to keep memory predictable.
4. **Don't expect `--bin` to make seeks cheap.** It doesn't. Bin
   reduces post-decode work; it does not reduce decode or seek work.
5. **GOP rewrites are an option of last resort.** Re-encoding the source
   to a smaller GOP (e.g. `ffmpeg -c:v hevc_videotoolbox -g 30`) would
   make scattered seeks ~4x cheaper, but at the cost of file size,
   re-encode time, and a quality-quantization loss. Not recommended for
   archival source files; possibly acceptable for a derived "proxy"
   workflow.

## See also

- [`docs/CHANGELOG.md`](../docs/CHANGELOG.md) for the cv2/PyAV decode
  history.
- [`docs/TRACK_RUNNER_DESIGN.md`](../docs/TRACK_RUNNER_DESIGN.md) for the
  per-interval, sequential-pre-pass architecture (M3+M4 plan) that
  eliminates scattered seeks from the Stage 4 hot path.
