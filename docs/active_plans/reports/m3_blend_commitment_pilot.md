# M3 blend commitment pilot

Date: 2026-08-20

## Scope and result

This is a bounded, frozen three-interval M3 pilot. It is not the M3 full-corpus
exit. Its private-video generator was retired from the permanent test suite, so
this report and its images are historical local evidence rather than a portable
repository test. The authoritative receipt is
`/private/tmp/m3-blend-commitment-pilot-production-field-label-20260820.json`.
It uses the shared production field, labeled
`shared_full_frame_residual_mean_fwd_bwd_raw_width_dog`, rather than a separate
ROI or box-only metric.

- All three intervals completed: three FWD commitments, one tie, zero BWD wins,
  zero unavailable results, zero errors, and zero infeasible results.
- The maximum center step fell from 0.9883623025 to 0.8052584366 torso widths.
- The canonical committed-run heat mean rose from 27795.469809 to 30117.390607.
  The receipt's heat veto therefore passed.
- The canonical evaluator made 421 decode reads in 60.339451 seconds.

## Frozen interval and overlay map

The 24 exact PNGs below are copied from
`/private/tmp/m3-commitment-overlays-production-field-label-20260820` without
re-rendering. Orange dashed geometry is the baseline and magenta solid geometry
is the committed path. Each image is a receipt-derived overlay, not a second
selection or tracking computation.

| Frozen interval | Route | Committed overlays |
| --- | --- | --- |
| `IMG_3830.mkv`, 1624-1656 | Stage 4 walker | `m3_blend_commitment_overlays/IMG_3830_1624_1656/commitment/frame_001636.png`, `frame_001638.png` through `frame_001642.png` (6) |
| `Conant-4x400-2026_April_15.mkv`, 1080-1111 | Stage 4 walker; FWD fallback | `m3_blend_commitment_overlays/Conant-4x400-2026_April_15_1080_1111/commitment/frame_001081.png` through `frame_001098.png` (18) |
| `IMG_3823.mkv`, 3891-3902 | Stage 3 Hermite | no committed run and no overlay (0) |

The artifact directory contains exactly 24 PNGs: 6 for IMG_3830 and 18 for
Conant. The receipt records all source, configuration, seed, motion, race-start,
probe, geometry, and overlay-payload identities for these intervals.

## Review boundary

Commitment direction, transition alpha, and evidence status are live-only review
metadata. The solved NPZ remains schema-stable and intentionally omits this
ephemeral metadata on reload; after reload, review and status show geometry only.

This pilot demonstrates the production-field policy, its continuity result, its
non-worse heat veto, and reviewable committed-run overlays. It does not establish
the required full-corpus maximum-step and heat measurements, transition-band sweep,
or every-committed-run corpus artifact required for M3 exit.
