# Extended corpus outcomes

Status: complete. Eight extended stems produced schema-15 solve artifacts. The
ninth, Jason 3200 m, reproduced its documented pre-existing terminal-decode
condition before any solver stage ran. No `plan_related` failure remains.

## Method

Each stem used the normal noninteractive solve command:

```bash
source source_me.sh && python3 track_runner/track_runner.py --workers 1 \
  -i TRACK_VIDEOS/STEM.mkv solve --yes
```

The first three extended attempts also ran `prepare`. Each rebuilt derivative
failed its structural smoke read on the advertised final frame. This repeated
evidence invalidated the plan's assumption that another fresh `prepare` was a
useful solver gate. `prepare` is optional for these non-4K sources, and
`docs/modes/PREPARE.md` documents deletion of an invalid derivative followed by
original-video decode as the normal fallback. Six invalid, reproducible
fast-read derivatives were removed; all source videos and human state were left
untouched. Existing fast-read videos were retained when live validation passed.

## Outcomes

| Stem | Outcome | Decode and stage evidence | Schema | Promoted intervals / frames | Wall time | Classification |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 2025-Glenbrook_South-1600m-IMG_1503 | success | Existing fast-read passed; 69 Stage-3 intervals and 2 Stage-4 intervals completed. | 15 | 2 / 1682 | 1606.1 s | success |
| Conant-4x400-2026_April_15 | success | Fresh fast-read failed at frame 14463; documented original fallback completed 361 Stage-3 and 28 Stage-4 intervals. | 15 | 28 / 1339 | 1563.1 s | success after pre-existing decode-path fallback |
| Hononega-Orion-1600m-IMG_3629 | success | Existing fast-read failed at frame 17413; original fallback completed 413 Stage-3 and 33 Stage-4 intervals. | 15 | 33 / 1720 | 5030.3 s | success after pre-existing decode-path fallback |
| Hononega-Varsity_4x400m-IMG_3707 | success | Existing fast-read failed at frame 15172; original fallback completed 975 Stage-3 and 76 Stage-4 intervals. | 15 | 76 / 1466 | 2293.5 s | success after pre-existing decode-path fallback |
| IMG_3627 | success | Existing fast-read failed at frame 8229; original fallback completed 84 Stage-3 and 11 Stage-4 intervals. | 15 | 11 / 801 | 1653.4 s | success after pre-existing decode-path fallback |
| IMG_3839 | success | Fresh fast-read failed at frame 8549; original fallback completed 22 Stage-3 and 2 Stage-4 intervals. | 15 | 2 / 756 | 1580.3 s | success after pre-existing decode-path fallback |
| Jason-3200m-sectionals-IMG_4005 | failed before solve | Live fast-read validation failed at frame 36042. The v10 artifact remains untouched; existing repository evidence records the same source-tail condition at advertised final frame 36043. | 10 | unavailable / unavailable | failed before timing | `pre_existing`: video-tail decode, before Stage 1 or changed solver code |
| Lyra-Hersey-800m-IMG_3882 | success | Fresh fast-read failed at frame 13773; original fallback completed 536 Stage-3 and 84 Stage-4 intervals. | 15 | 84 / 1343 | 971.7 s | success after pre-existing decode-path fallback |
| Lyra-Wheeling-IMG_3912 | success | Existing fast-read passed; production bin 2 completed 309 Stage-3 and 16 Stage-4 intervals. | 15 | 16 / 2623 | 1141.6 s | success; real bin-2 evidence |

The Jason historical evidence is recorded in
[m4_prepass_memory_budget_report.md](../active_plans/reports/m4_prepass_memory_budget_report.md)
and the current changelog: the original rejects advertised final frame 36043. The
current attempt fails one frame earlier in its derived fast-read, at the same
terminal-decode boundary. Since video-context validation fails before Stage 1,
the schema-15 interpolation, scoring, promotion, and persistence changes cannot
be causal.

## Lifecycle result

Successful solves report fair/low intervals for human attention when Stage 3
and the bounded Stage-4 allocation cannot finish them. Counts range from no
additional seed request on IMG_3839 to 50 on Hononega Varsity. Those are normal
`target -> refine` inputs, not failed random-interval gates. The corpus therefore
confirms the intended behavior: solve what the automatic stages can, then give
the user deterministic criteria for the remaining work.
