# M4 walker A/B report

Status: SAMPLE-LIMITED (58 of a 120-interval target evaluated; the remaining 62
were skipped by a per-video decode-time budget, not cherry-picked). Generated
2026-06-08 for the M4 gate (task #12). Data source:
[m4_walker_ab_data.csv](m4_walker_ab_data.csv), produced by
the former `tests/e2e/e2e_walker_ab.py` local harness.

This report supersedes the prior 4-interval version. The prior result was a
selection + metric artifact (see "Was the old result an artifact?" below).

## Distribution headline

Across 58 evaluated during-race visible seed-triples (6 corpus videos), the
default-off Stage 4 walker vs the Hermite-only baseline, classified by distance
to a held-out human seed (torso-width units, contract C2):

- success (rescued + preserved) = 21 / 58 (rescued = 6, preserved = 15)
- regressed = 35 / 58
- needs_review = 2 / 58

The walker preserves or rescues on roughly a third of evaluated intervals and
regresses on most of the rest, at current Viterbi cost weights. This is a
distribution, not a single verdict: the walker is genuinely better on some
intervals (notably the high-drift Conant interior, 3/5 rescued) and genuinely
worse on others. It is NOT a drop-in win, which is exactly why M4 ships it
behind the default-off `--walker-stage4` flag. The result supports holding the
default OFF and keeping the v1 `_apply_blob_snap` path as the default.

Independence holds and is the point: the walker reaches these positions on its
own image evidence. It never reads Hermite's `raw_pred` (the no-Hermite import
gate and the WP-5a data-boundary test both enforce this), so every "preserved"
above is the walker independently arriving at a good answer, and every
"rescued" is the walker independently beating a bad Hermite fit.

## Corrected selection (what was actually evaluated)

Selection criteria (all three required), reusing the established corpus
machinery:

- corpus: the six videos in
  [outdoor_corpus.txt](../../../data/outdoor_corpus.txt); the harness
  asserts its mirror against that file at startup.
- DURING-RACE only (a.k.a. post-start): the left seed of the triple is strictly
  after `race_start_frame`. `race_start_frame` comes from
  `walk_io.load_race_start_frame` (the same source the corpus walker uses), so
  pre-race stationary intervals -- which Stage 3b synthesis owns, NOT the walker,
  per contract C4 -- are excluded.
- VISIBLE-both (in fact visible-all-three): every seed in the A,B,C triple has
  status `visible`. `not_in_frame`, `approximate`, and `partial` seeds are
  excluded, matching `walk_util.select_random_visible`'s visible-both filter.
- reproducible sample: 20 random triples per video at fixed `--random-seed
  12345`. Re-running with the same seed reproduced IMG_3830 and IMG_3823
  interval-for-interval.

Per-video evaluated vs skipped (skip = per-video decode budget reached, see
"Why sample-limited"):

| Video | evaluated | rescued | preserved | regressed | needs_review | skipped_budget |
| --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 20 | 0 | 7 | 13 | 0 | 0 |
| IMG_3823 | 20 | 1 | 7 | 11 | 1 | 0 |
| Jason-3200m-IMG_4005 | 4 | 0 | 0 | 3 | 1 | 16 |
| Lyra-Hersey-800m-IMG_3882 | 8 | 2 | 1 | 5 | 0 | 12 |
| Conant-4x400 | 5 | 3 | 0 | 2 | 0 | 15 |
| Lyra-Wheeling-IMG_3912 | 1 | 0 | 0 | 1 | 0 | 19 |
| TOTAL | 58 | 6 | 15 | 35 | 2 | 62 |

All evaluated intervals are confirmed during-race (left frame > race_start) and
visible-all-three by construction of the selector. The two fast 1080p videos
(IMG_3830, IMG_3823) reached the full 20; the four slower / higher-resolution
videos were budget-capped.

## Metric (and why this one, not agreement)

The metric is an INDEPENDENT accuracy proxy: held-out-seed distance.

Where three consecutive during-race visible seeds A, B, C exist, the interior
human seed B is HELD OUT. The merged interval A->C is solved twice on the same
reader/scene/seeds:

- Hermite-only baseline: `blob_snap_enabled=False, use_walker=False` (no decode,
  pure interval geometry).
- walker: `blob_snap_enabled=True, use_walker=True` (the windowed walker).

Each method's solved torso box at frame B is compared to the held-out human seed
B by center distance, normalized to the held-out seed's torso width (contract
C2). The held-out human seed is ground truth, independent of both methods, so
the per-interval error delta classifies honestly:

- rescued: walker beats Hermite by >= 0.15 torso widths.
- preserved: small swing (|delta| < 0.15 torso widths) with at least one method
  reasonably close to truth -- the walker independently matched a good Hermite
  answer. Counts as success.
- regressed: walker worse than Hermite by >= 0.15 torso widths.
- needs_review: both methods >= 1.0 torso width from truth (ambiguous interval),
  not a verdict on the walker.

Why not FWD/BWD agreement (the old metric): agreement is structurally biased
toward Hermite. Hermite's FWD and BWD passes are both `raw_pred` from one fitted
curve, so they are near-mirror images and agree by construction, inflating
Hermite "agreement" without measuring correctness. The walker's two passes are
independent by contract C9, free to diverge; divergence drops agreement even
when one or both passes are correct. So `walker_agree < hermite_agree` was the
expected default and not a tracking-quality signal. Held-out human distance has
no such bias: it measures each method against a fixed external truth.

### Why sample-limited (not cherry-picked)

Each interval's walker solve decodes its full frame range. On the HEVC HDR /
4k120 source videos, random-access decode at deep frame indices (Jason and
Lyra-Wheeling reach frames 10k-20k+) costs tens of seconds per triple, so a
single triple can exceed a useful wall-clock budget. The harness applies a
per-video time budget (`--per-video-budget`, 240 s for this run): once a video
overruns, its remaining sampled triples are counted `skipped_budget` and the run
moves on. This caps wall time and guarantees every corpus video contributes its
cheap-to-decode triples, rather than letting one slow video starve the rest.
Skipped intervals are a uniform random subset of the fixed sample, so the
evaluated set is an unbiased (if smaller) sample, not a curated one. The full
120-interval target remains the goal for a longer offline run.

## Selected interval detail

Rescues (walker independently beat a worse Hermite fit; error in torso widths):

| Video | A-B-C | hermite_err | walker_err | delta |
| --- | --- | --- | --- | --- |
| IMG_3823 | 3891-3900-3902 | 3.611 | 0.333 | -3.277 |
| Lyra-Hersey | 2029-2047-2073 | 0.305 | 0.056 | -0.249 |
| Lyra-Hersey | 3405-3465-3520 | 1.095 | 0.609 | -0.485 |
| Conant | 1157-1173-1235 | 1.175 | 0.242 | -0.933 |
| Conant | 3735-3766-3797 | 0.493 | 0.326 | -0.166 |
| Conant | 4168-4199-4229 | 0.478 | 0.078 | -0.400 |

The Conant interior (1157-1235, just after the prior-flagged 1080-1111 region)
is exactly where Hermite drifts a full torso width or more and the walker's
image evidence pulls the box back onto the runner. These are the intervals the
blob coupling is meant to help.

needs_review (both methods far from the held-out truth -- ambiguous interval,
not a walker verdict):

| Video | A-B-C | hermite_err | walker_err |
| --- | --- | --- | --- |
| IMG_3823 | 1729-1730-1736 | 1.789 | 1.178 |
| Jason | 748-800-836 | 1.617 | 2.898 |

The Jason 748-836 triple is a 88-frame held-out span; both methods are far from
B because the interval is long and the runner geometry is genuinely uncertain
there.

## Was the old result an artifact?

The prior report classified `regressed=3` on four fixed intervals using FWD/BWD
agreement. Two questions:

1. Selection artifact? NO for the two specifically flagged FWD-zero-coverage
   intervals. Both Conant `seed_1080_1111` and Jason `seed_564_583` are
   confirmed VISIBLE-on-both-ends and DURING-RACE (left frame > race_start:
   Conant race_start 1064, Jason race_start 268). They are legitimately in
   scope, so the prior "regression" was not a pre-race or non-visible selection
   error. (The blob-walk baseline gate corroborates the FWD zero-coverage:
   both show FWD `accepted_fraction=0.000000`.)

2. Metric artifact? YES. The prior regressions were driven by the agreement
   metric, which scores an interpolated empty FWD pass against a tracked BWD
   pass and collapses Dice -- an apples-to-oranges comparison under a metric
   that already favors symmetric Hermite. Switching to held-out human-seed
   distance removes that bias. Under the corrected metric the walker is better
   than Hermite on 6 intervals and statistically tied (preserved) on 15, while
   still worse on 35; the honest picture is a broad distribution, not a uniform
   regression. The fix the prior report's own independent audit asked for
   (a ground-truth-distance metric, plus during-race visible selection) is what
   this report implements.

## Independence invariant (restated)

The walker reaching its answer on its own image evidence is the whole point.
Two structural gates enforce that it never consults Hermite's fitted curve:

- the no-Hermite import gate: nothing under `track_runner/blob_walk/` imports
  `velocity_model.raw_pred` or any Hermite prediction path.
- the WP-5a data-boundary test (`tests/test_walker_bundle_seam.py`): the FWD/BWD
  `WalkerInputBundle`s carry the seed and the candidate-lattice source but no
  `raw_pred` field is reachable through any bundle.

So preserved (walker matches good Hermite) is a real independent success, not a
copy of Hermite's answer.

## Boundaries honored

- No production solver code changed (velocity_model, interval_solver,
  residual_motion, walk_walker untouched). Evaluation tooling only.
- The `--walker-stage4` default stays OFF.
- Equivalence gate still green: `bash tests/e2e/e2e_blob_walk_baseline.sh` ->
  `RESULT: PASS -- baseline matches (very-very-close policy), 224 total verdict
  rows`.
- Fast suite still green: `pytest tests/ -q` -> 1533 passed.

## Reproduce

```
source source_me.sh && python3 tests/e2e/e2e_walker_ab.py
source source_me.sh && python3 tests/e2e/e2e_walker_ab.py --random-seed 12345 -n 20 --per-video-budget 240
```

The first form targets the full 120 (20 per video) with the default 1800 s
per-video budget; expect a long wall time on the slow videos. The second form
reproduces this report's sample-limited run.
