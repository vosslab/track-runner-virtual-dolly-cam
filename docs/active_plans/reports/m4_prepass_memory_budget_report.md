# M4 pre-pass memory-budget measurement

Status: PARTIAL on 2026-08-20. Bin 1 completed all 11 canonical intervals and
produced the measurements below. The earlier bin-2 missing-artifact failure is
historical. An isolated measurement-only Stage-1 probe was used without writing
canonical `tr_config`, but that private-video probe and its support code were
later removed from the permanent test suite. A controlled fresh production
fast-read rebuild for
Conant at `/private/tmp/m4-fastread-decode-v2-20260820` completed its
`create_fastread_video` transcode, then its own `validate_fastread_structural`
failed while decoding the advertised final frame (14463). It produced no v2
decode manifest and no new motion, pool, or bin evidence. This rules out stale
fast-read assets as the cause and isolates an OpenCV/probe frame-count-tail
incompatibility. The transient command receipt is
`/private/tmp/m4-prepare-decode-v2.log`. No bin-2 worker pool or measurement
output was created.
Therefore this is not an M4 or M5 exit record, and it does not infer bin-2
figures from bin 1.

The protocol and original UNRUN audit are retained in
[m4_prepass_memory_budget_unrun.md](m4_prepass_memory_budget_unrun.md). Raw
artifacts are outside the repository at `/private/tmp/m4-measure-bin1-20260820`:
`summary.json` is the aggregate receipt and `raw.jsonl` is the per-interval
record.

## Bin-1 receipt

The retired local generator accepted and completed 11 of 11 intervals from the frozen canonical
selection. Its global physical-span buckets were five `1-60` intervals, three
`61-180` intervals, and three `181+` intervals. The historical local command was:

```bash
source source_me.sh && python3 tests/e2e/e2e_prepass_memory_measure.py measure \
  --selection /private/tmp/m4-global-selection-v2.json \
  --output-dir /private/tmp/m4-measure-bin1-20260820 \
  --bin-factor 1 --workers 3
```

The machine receipt is macOS 26.6.2 arm64. RSS sampling ran every 0.1 seconds.
The zero-worker driver baseline was 127401984 B. The maximum worker
`ru_maxrss` was 1395294208 B.

| Bin | Span | Intervals | Peak worker RSS (B) | Lookups | Misses | Miss rate | Wall time (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `1-60` | 5 | 1394769920 | 172 | 98 | 0.569767 | 51.049261 |
| 1 | `61-180` | 3 | 1392525312 | 552 | 492 | 0.891304 | 567.768802 |
| 1 | `181+` | 3 | 1395294208 | 1128 | 1068 | 0.946809 | 1214.122110 |

## Worker-sizing receipt

The bin-1 summary uses 13021069312 B available memory and 2147483648 B
(2 GiB) headroom. With the maximum measured worker RSS, the recorded formula
selected seven workers:

```text
floor((13021069312 - 127401984 - 2147483648) / 1395294208) = 7
```

This is a bin-1 calculation only. It is not a default-worker adoption, because
the plan requires the corresponding bin-2 data and an observed pool-memory
confirmation.

## Bin-2 preparation failure and remaining gates

The bin-2 run has no measurement rows. The original missing-camera-motion
artifact cause is superseded by a measurement-only isolated Stage-1 path. The
historical frozen all-six selection is
`/private/tmp/m4-bin2-selection-v3.json`. Jason is permanently excluded by user
direction, so the later nine-interval Jason manifest is retired and must not be
used. No remaining corpus video currently covers every required span bucket.
Three static artifact
pairs under `/private/tmp/m4-bin2-motion-v3/bin-2/` remain
provenance-valid for Conant, `IMG_3823.mkv`, and `IMG_3830.mkv`; they are not
current dynamic terminal-decode or complete-source evidence. They also are not
canonical `tr_config` artifacts.

The controlled fresh Conant rebuild above established that the old all-video
failure was not merely a stale-asset failure. That route and the Jason-only route
are both retired. Consequently no replacement bin-1/bin-2 worker pool, RSS
sample, cache counter, wall-time measurement, output directory, or sizing value
exists. No bin-2 value is derived from the successful historical bin-1 run.

M4 remains open: the plan requires measured bin-1 and bin-2 worker RSS, cache
miss rate, and wall time, together with its other walker-cost/parity gates. M5
also remains open: its worker-sizing dependency has no bin-2 receipt, and its
separate M2 promotion-attribution and tangent-evidence gates remain unresolved.
