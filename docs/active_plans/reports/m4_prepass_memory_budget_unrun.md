# M4 pre-pass memory-budget measurement

Status: superseded as an all-UNRUN status record on 2026-08-20. The historical
local protocol remains documented here, but its private-video generator and
harness-only support were removed from the permanent test suite. The separately
recorded [m4_prepass_memory_budget_report.md](m4_prepass_memory_budget_report.md)
contains the completed bin-1 receipt and the current bin-2 pre-pool decoder
gate. It makes no M4 or M5 closure claim.

The corresponding accepted WP-M1 implementation status is maintained in the
[active plan](../active/interaction_shell_and_trajectory_truth.md). This report
does not reopen that implementation review; it records only the remaining
measurement and worker-sizing gates.

## Required measurement

WP-M2 requires an actual corpus run with the following values for each
bin-factor / interval-length row:

| bin factor | interval span (frames) | worker peak RSS | cache lookups | cache misses | miss rate | wall time |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `1-60`, `61-180`, `181+` | UNRUN | UNRUN | UNRUN | UNRUN | UNRUN |
| 2 | `1-60`, `61-180`, `181+` | UNRUN | UNRUN | UNRUN | UNRUN | UNRUN |

It also requires the resident footprint of the driver process after it has
loaded the same video/configuration but before it has created any workers.
That value is deliberately not inferred from this Python process or from the
512 MiB store cap: WP-M5 reserves a *measured* driver baseline.

## Audit result

The canonical corpus manifest is present at `data/outdoor_corpus.txt`. Jason is
permanently excluded by user direction; five active inputs remain:

- `TRACK_VIDEOS/IMG_3830.mkv`
- `TRACK_VIDEOS/IMG_3823.mkv`
- `TRACK_VIDEOS/Lyra-Hersey-800m-IMG_3882.mkv`
- `TRACK_VIDEOS/Conant-4x400-2026_April_15.mkv`
- `TRACK_VIDEOS/Lyra-Wheeling-IMG_3912.mkv`

The private video and artifact volume is available only through live
symlinks. `TRACK_VIDEOS -> /Users/vosslab/Documents/TRACK_VIDEOS` resolves to
`/Volumes/Ex2GB/Documents/TRACK_VIDEOS`; `tr_config ->
/Users/vosslab/Documents/Track_Runner_Config` resolves to
`/Volumes/Ex2GB/Documents/Track_Runner_Config`. The initial non-following
directory scan was incorrect and is superseded by this symlink-resolved audit.

The existing E2E harnesses are real-video harnesses, not substitutes for the
WP-M2 measurement runner:

- `tests/e2e/e2e_blob_walk_baseline.py` walks four fixed intervals from the
  Conant and Jason corpus videos.
- `tests/e2e/e2e_walker_ab.py` samples the six-video manifest.
- `tests/e2e/baseline_blob_walk/` contains committed verdict CSV snapshots,
  not video frames, reader inputs, or residual-store measurements.

A retired local measurement generator froze Stage-4-promoted intervals, sampled
the zero-worker parent baseline and per-worker peak RSS, and recorded cache
counters and wall time. It depended on the private corpus and is no longer a
repository test. The historical bin-1 dry run and measurement remain receipts,
not portable test results. After Jason's permanent exclusion, no active video
has three candidates in every required physical span bucket.

The accepted WP-M1 adapter repair forwards `precomputed_store` through both
passes into walker observations. `_ByteBoundedLruStore` tracks
`lookup_count`, `miss_count`, and `miss_rate()` for the runner to record.
Walker-active cache use therefore covers the exact deterministic FWD/BWD
bootstrap and initial-window ROIs. Cached residual arrays remain lossless
`float32` values that preserve the legacy residual values. Later adaptive,
decision-dependent walker ROIs intentionally miss because they cannot be known
at prepass time; those requests use the legacy reader fallback. Consequently,
the counters are meaningful true-consumer cache counters. The bin-1 measurement
has since completed and is recorded in the partial receipt. For bin 2, the
earlier missing-artifact cause is superseded by a measurement-only isolated
Stage-1 preparation path. Three static artifact pairs for Conant,
`IMG_3823.mkv`, and `IMG_3830.mkv` remain provenance-valid, but are not current
dynamic terminal-decode or complete-source evidence. A controlled fresh
production Conant fast-read rebuild at `/private/tmp/m4-fastread-decode-v2-20260820`
completed its transcode and then failed its own `validate_fastread_structural`
at final frame 14463. It produced no v2 decode manifest or new motion, pool, or
bin evidence. This rules out stale fast-read assets and isolates an
OpenCV/probe frame-count-tail incompatibility; its transient receipt is
`/private/tmp/m4-prepare-decode-v2.log`. Those failures belonged to the
superseded all-video selection. Jason is permanently excluded by user direction,
and its former single-video manifest must not be used. No remaining video has
three promoted intervals in all required span buckets, so selection now fails
before preparation. No replacement bin-2 worker pool, output, or figures exist;
the missing evidence is not an instrumentation, cache-wiring, or RSS-sampler gap.

## Why existing numbers do not satisfy WP-M2

`PREPASS_RESULT_STORE_MAX_BYTES = 512 MiB` is a cap on cached result arrays;
it is not a measured process RSS. Prior walker reports and the committed
baseline establish other questions (output behavior or bin safety), but do
not provide a same-run zero-worker driver baseline, worker-attributed peak
RSS, actual walker pre-pass lookup/miss totals, and solve wall time for both
required bins. They cannot be reused as this acceptance evidence.

## Rerun protocol

1. Verify the selected manifest video and matching seed, camera-motion, and
   diagnostics artifact through the resolved `TRACK_VIDEOS/` and `tr_config/`
   paths before starting; a missing corpus member fails the run rather than
   being skipped.
2. Use an explicitly local evidence utility outside the permanent `tests/`
   suite. It must
   emit, per solved interval: video basename, seed-pair frames, span,
   bin-factor, store `lookup_count`, `miss_count`, `miss_rate`, and elapsed
   monotonic wall time. It must sample RSS for the driver and every worker by
   PID at a fixed interval and record each PID's maximum resident bytes.
3. Freeze the canonical complete selection before execution: exactly three
   intervals in each `1-60`,
   `61-180`, and `181+` bucket from one eligible real corpus video. The manifest
   validator must accept the selection before `measure` may run. Run the same
   recorded set for both bins, with no cache reuse between rows. Preserve raw
   CSV/JSON samples alongside the report. Missing bucket capacity, identity
   drift, a second video, or a noncanonical selection leaves the result UNRUN.
4. For each video/bin run, start the solver to the point where its input,
   reader metadata, configuration, and artifacts are loaded, hold worker
   creation at zero, and sample the driver RSS. Record the maximum of that
   zero-worker interval as `parent_baseline_bytes`.
5. Run the recorded Stage-4 intervals with the intended pool configuration.
   Record peak RSS for every worker, report the maximum worker peak for each
   bin/span row, aggregate the pre-pass lookup/miss counters only from that
   row, and record end-to-end solve wall time. A worker crash, an unavailable
   video, or a missing row makes the dataset UNRUN rather than a partial
   corpus result.
6. State an explicit reserve (`headroom_bytes`) and available-memory source.
   Only then calculate the default worker count:

   ```text
   floor((available_bytes - parent_baseline_bytes - headroom_bytes)
         / per_worker_peak_bytes)
   ```

   Confirm the selected count against observed peak system memory from a
   corresponding bin-1 pool run. An explicit CLI `--workers` override remains
   outside this calculation.

7. Publish the completed table, raw-data paths, commands, machine memory,
   sampler cadence, commit SHA, and the resulting calculation in a replacement
   report. Only then can WP-M2 and the WP-M5 dependency be closed. WP-M4 and
   WP-M5 also remain subject to their other stated gates.

## Historical commands audited

The deleted private-video generator commands below are retained only to identify
the frozen local receipt. They are not current repository test commands.

```bash
ls -ld TRACK_VIDEOS tr_config
realpath TRACK_VIDEOS
realpath tr_config
find -L TRACK_VIDEOS -maxdepth 1 -type f -print
find -L tr_config -maxdepth 1 -type f -print
sed -n '1,120p' data/outdoor_corpus.txt
source source_me.sh && python3 tests/e2e/e2e_blob_walk_baseline.py --help
source source_me.sh && python3 tests/e2e/e2e_walker_ab.py --help
source source_me.sh && python3 tests/e2e/e2e_prepass_memory_measure.py select --output /private/tmp/m4-global-selection-v2.json
source source_me.sh && python3 tests/e2e/e2e_prepass_memory_measure.py measure --selection /private/tmp/m4-global-selection-v2.json --bin-factor 1 --dry-run
source source_me.sh && python3 -m pytest tests/test_m4_measurement_runner.py
```

The symlink-resolved availability checks found the corpus and configuration
artifacts. The canonical global selection and its bin-1 dry run both succeeded:
the frozen manifest has 11 intervals. This historic audit is superseded for
execution status by [m4_prepass_memory_budget_report.md](m4_prepass_memory_budget_report.md).
No M4 exit or dependent M5 worker-sizing exit is claimed.
