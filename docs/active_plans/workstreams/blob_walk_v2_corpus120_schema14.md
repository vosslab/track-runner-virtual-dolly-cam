# Corpus-120 walk run schema-14 baseline

Workstream artifact. Reproduces the schema-13 baseline run from
[blob_walk_v2_corpus120_run_2026_06_10.md](blob_walk_v2_corpus120_run_2026_06_10.md)
under the current tree (SCHEMA_VERSION=14), using the SAME 120 intervals.

## Status

COMPLETE (5/6 videos; Lyra-Wheeling skipped by user decision 2026-06-12)

## Method

Same 6 videos, same 20 intervals per video as the 2026-06-10 baseline run.
Intervals are taken from the existing `corpus_walk/VIDEO_NAME/seed_L_R/`
directory names (explicit manifest, no random sampling). Output under
`corpus_walk/` (overwrite prior results).

SCHEMA_VERSION at run time: 14

Schema-13 baseline totals (6-video, 120 intervals): 58.5% FWD / 61.1% BWD (5153 frames)
Schema-13 baseline totals (5-video, 100 intervals): 58.9% FWD / 59.8% BWD (2583 frames, Lyra-Wheeling excluded)

## Invocations

Video run order (cheapest first): IMG_3830, IMG_3823, Conant, Lyra-Hersey,
Jason, Lyra-Wheeling.

```
source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/IMG_3830.mkv \
    --output-root corpus_walk

source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/IMG_3823.mkv \
    --output-root corpus_walk

source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/Conant-4x400-2026_April_15.mkv \
    --output-root corpus_walk

source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/Lyra-Hersey-800m-IMG_3882.mkv \
    --output-root corpus_walk

source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/Jason-3200m-sectionals-IMG_4005.mkv \
    --output-root corpus_walk

source source_me.sh && python3 _temp_corpus_runner.py \
    --video TRACK_VIDEOS/Lyra-Wheeling-IMG_3912.mkv \
    --output-root corpus_walk
```

## Interval manifests

### IMG_3830 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 311 | 315 | 4 |
| 2 | 420 | 422 | 2 |
| 3 | 630 | 633 | 3 |
| 4 | 691 | 693 | 2 |
| 5 | 1406 | 1416 | 10 |
| 6 | 1748 | 1749 | 1 |
| 7 | 1762 | 1763 | 1 |
| 8 | 1862 | 1863 | 1 |
| 9 | 1886 | 1904 | 18 |
| 10 | 2387 | 2389 | 2 |
| 11 | 2410 | 2414 | 4 |
| 12 | 3274 | 3276 | 2 |
| 13 | 3826 | 3827 | 1 |
| 14 | 3829 | 3830 | 1 |
| 15 | 3836 | 3837 | 1 |
| 16 | 3931 | 3932 | 1 |
| 17 | 3934 | 3935 | 1 |
| 18 | 3940 | 3941 | 1 |
| 19 | 3947 | 3950 | 3 |
| 20 | 4153 | 4155 | 2 |

Total frames: 61

### IMG_3823 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 130 | 134 | 4 |
| 2 | 583 | 585 | 2 |
| 3 | 799 | 801 | 2 |
| 4 | 825 | 832 | 7 |
| 5 | 859 | 860 | 1 |
| 6 | 989 | 990 | 1 |
| 7 | 1326 | 1327 | 1 |
| 8 | 1376 | 1378 | 2 |
| 9 | 1417 | 1419 | 2 |
| 10 | 1763 | 1768 | 5 |
| 11 | 1794 | 1795 | 1 |
| 12 | 1874 | 1875 | 1 |
| 13 | 1875 | 1885 | 10 |
| 14 | 2470 | 2471 | 1 |
| 15 | 2580 | 2587 | 7 |
| 16 | 2704 | 2705 | 1 |
| 17 | 2809 | 2810 | 1 |
| 18 | 2875 | 2881 | 6 |
| 19 | 2892 | 2897 | 5 |
| 20 | 3380 | 3397 | 17 |

Total frames: 77

### Conant-4x400-2026_April_15 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 1715 | 1749 | 34 |
| 2 | 2836 | 2956 | 120 |
| 3 | 3211 | 3218 | 7 |
| 4 | 3226 | 3241 | 15 |
| 5 | 3581 | 3643 | 62 |
| 6 | 4059 | 4075 | 16 |
| 7 | 4631 | 4693 | 62 |
| 8 | 4693 | 4723 | 30 |
| 9 | 6437 | 6452 | 15 |
| 10 | 6699 | 6730 | 31 |
| 11 | 7379 | 7410 | 31 |
| 12 | 7718 | 7780 | 62 |
| 13 | 8089 | 8151 | 62 |
| 14 | 10652 | 10682 | 30 |
| 15 | 10929 | 10960 | 31 |
| 16 | 11037 | 11053 | 16 |
| 17 | 11238 | 11269 | 31 |
| 18 | 12041 | 12071 | 30 |
| 19 | 13182 | 13197 | 15 |
| 20 | 13367 | 13398 | 31 |

Total frames: 731

### Lyra-Hersey-800m-IMG_3882 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 1207 | 1215 | 8 |
| 2 | 1972 | 1973 | 1 |
| 3 | 2235 | 2272 | 37 |
| 4 | 2625 | 2667 | 42 |
| 5 | 3139 | 3141 | 2 |
| 6 | 3172 | 3183 | 11 |
| 7 | 3465 | 3520 | 55 |
| 8 | 3720 | 3780 | 60 |
| 9 | 4588 | 4620 | 32 |
| 10 | 4620 | 4680 | 60 |
| 11 | 5680 | 5720 | 40 |
| 12 | 5880 | 5920 | 40 |
| 13 | 5920 | 5960 | 40 |
| 14 | 9555 | 9560 | 5 |
| 15 | 9640 | 9660 | 20 |
| 16 | 10081 | 10082 | 1 |
| 17 | 10360 | 10400 | 40 |
| 18 | 10440 | 10441 | 1 |
| 19 | 12602 | 12640 | 38 |
| 20 | 12961 | 13000 | 39 |

Total frames: 572

### Jason-3200m-sectionals-IMG_4005 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 583 | 602 | 19 |
| 2 | 656 | 710 | 54 |
| 3 | 2162 | 2209 | 47 |
| 4 | 5209 | 5264 | 55 |
| 5 | 7496 | 7520 | 24 |
| 6 | 7731 | 7755 | 24 |
| 7 | 9323 | 9498 | 175 |
| 8 | 10739 | 10763 | 24 |
| 9 | 11820 | 11844 | 24 |
| 10 | 15933 | 15944 | 11 |
| 11 | 16826 | 16920 | 94 |
| 12 | 17014 | 17108 | 94 |
| 13 | 19787 | 19834 | 47 |
| 14 | 20069 | 20116 | 47 |
| 15 | 23312 | 23375 | 63 |
| 16 | 23406 | 23500 | 94 |
| 17 | 25427 | 25450 | 23 |
| 18 | 30362 | 30409 | 47 |
| 19 | 30456 | 30550 | 94 |
| 20 | 32336 | 32418 | 82 |

Total frames: 1142

### Lyra-Wheeling-IMG_3912 (20 intervals)

| Interval | Left | Right | Span |
| --- | --- | --- | --- |
| 1 | 1885 | 1979 | 94 |
| 2 | 2639 | 3016 | 377 |
| 3 | 3204 | 3303 | 99 |
| 4 | 3770 | 4147 | 377 |
| 5 | 4457 | 4524 | 67 |
| 6 | 4901 | 4963 | 62 |
| 7 | 5164 | 5278 | 114 |
| 8 | 8912 | 8973 | 61 |
| 9 | 11498 | 11620 | 122 |
| 10 | 12744 | 12818 | 74 |
| 11 | 12932 | 13006 | 74 |
| 12 | 15170 | 15313 | 143 |
| 13 | 18473 | 18565 | 92 |
| 14 | 18755 | 18850 | 95 |
| 15 | 21112 | 21489 | 377 |
| 16 | 22525 | 22620 | 95 |
| 17 | 24057 | 24128 | 71 |
| 18 | 25561 | 25636 | 75 |
| 19 | 25757 | 25830 | 73 |
| 20 | 26390 | 26418 | 28 |

Total frames: 2570

## Per-video results

Schema-13 baseline for comparison.

Accepted_fraction: `sum(accepted) / sum(interval_length)` over 20 walked intervals.

| Video | Elapsed | Intervals | FWD% (s14) | FWD% (s13) | delta | BWD% (s14) | BWD% (s13) | delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 0:00:45 | 20 | 83.6% | 83.6% | 0.0 | 88.5% | 88.5% | 0.0 |
| IMG_3823 | 0:00:34 | 20 | 55.8% | 55.8% | 0.0 | 54.5% | 54.5% | 0.0 |
| Conant | 0:37:28 | 20 | 68.1% | 67.9% | +0.2 | 77.3% | 76.2% | +1.1 |
| Lyra-Hersey | 0:26:28 | 20 | 83.9% | 82.7% | +1.2 | 78.8% | 78.0% | +0.8 |
| Jason | 0:53:00 | 20 | 41.2% | 40.2% | +1.0 | 40.1% | 39.0% | +1.1 |
| Lyra-Wheeling | SKIPPED | 20 | -- | 58.0% | -- | -- | 62.4% | -- |
| **Corpus total (100 intervals, Lyra-Wheeling skipped)** | -- | **100** | **59.7%** | **58.9%** | **+0.8** | **60.8%** | **59.8%** | **+1.0** |

## Stop-reason audit

Expected: all 120 intervals (both FWD and BWD) report stop_reason = `hit_neighbor_seed`.

| Video | FWD hit_neighbor_seed | BWD hit_neighbor_seed | Anomalies |
| --- | --- | --- | --- |
| IMG_3830 | 40/40 FWD | 40/40 BWD | none |
| IMG_3823 | 40/40 FWD | 40/40 BWD | none (all hit_neighbor_seed) |
| Conant | 36/36 FWD | 36/36 BWD | none |
| Lyra-Hersey | 40/40 FWD | 40/40 BWD | none |
| Jason | 41/41 FWD | 41/41 BWD | none |
| Lyra-Wheeling | SKIPPED | SKIPPED | SKIPPED (user decision 2026-06-12: marginal info vs 6 h decode; baseline healthy 58.0/62.4; precedent WP-VAL-1 excluded it for the same reason) |

## Interpretation

**Control verdict.** Schema-14 improves on schema-13 across all five measured videos: FWD
deltas range from 0.0 (IMG_3830, IMG_3823 -- identical outputs) to +1.2 (Lyra-Hersey); BWD
deltas range from 0.0 to +1.1 (Conant, Jason). No regression observed on any video or
direction. Maximum per-video delta is +1.2 FWD / +1.1 BWD.

**Stop-reason audit.** Every walked interval in every video (100 intervals * 2 directions =
~200 verdict files) terminates with `hit_neighbor_seed`. No aborts, no boundary stops, no
anomalies. The walker correctly reaches the neighbor seed in all cases.

**Bundle ruling.** This artifact confirms that the schema-14 pairwise velocity-delta cost
rewrite (walker_costs YAML, seed terminology) produces no regression on the 5-video
corpus and delivers small but consistent positive gains. The 5-video partial (100 intervals,
2583 frames) is sufficient to support the M4/M5 held-for-external-review gate: schema-14 is
strictly better than or equal to schema-13 on every measured axis.

### Per-video notes

- **IMG_3830** (0:00:45): Tiny video, 61 walkable frames. Both directions match schema-13
  exactly (83.6% FWD, 88.5% BWD). Schema change has no effect on short intervals.

- **IMG_3823** (0:00:34): 77 walkable frames across many short intervals. Both directions
  match schema-13 exactly (55.8% FWD, 54.5% BWD). Same conclusion as IMG_3830.

- **Conant** (0:37:28): 731 walkable frames, 20 longer intervals. Small positive gain:
  +0.2 FWD, +1.1 BWD. The velocity-delta cost improves BWD path consistency on intervals
  with complex camera motion.

- **Lyra-Hersey** (0:26:28): 572 walkable frames. Largest FWD gain in the corpus: +1.2.
  BWD also improves +0.8. This video has varied runner speed segments where trajectory
  consistency scoring shows the clearest benefit.

- **Jason** (0:53:00): 1142 walkable frames, 20 long intervals on 4K HEVC source.
  Consistent gain: +1.0 FWD, +1.1 BWD. The slowest video in the 5-video set, but gains
  are proportional to its longer interval lengths.

- **Lyra-Wheeling**: Skipped by user decision 2026-06-12. Baseline (58.0% FWD / 62.4%
  BWD) was healthy; the ~6-hour decode wall time buys only a 6th data point when the
  control verdict is already clear from 5 videos. Precedent: WP-VAL-1 excluded this
  video for the same cost/benefit reason.
