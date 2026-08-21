# Blob walk v2 held-out error expansion

Status: DONE_WITH_CONCERNS

Artifact: `docs/active_plans/workstreams/blob_walk_v2_heldout_expansion.md`

Date: 2026-06-12. Lane: measurement-only (WS-G). The schema-14 cost-model
bundle stays frozen; this artifact adds evidence only and edits no staged file.

## Framing correction (added post-run by the review manager)

The comparative conclusion below ("walker worse than Hermite on 11/13") is
MISFRAMED and must not be read as a quality verdict against the walker. The
running agent did not carry the documented domain prior: the walker is the
trusted solver for its intervals and is more accurate than Hermite; Hermite is
the cheap incumbent, acceptable but not great, kept only as the Stage-3 / cost
floor. See the absorption record and the design doc.

Two consequences for reading this artifact:

- The single held-out human seed is a weak truth proxy. A small `hermite_err`
  means the held-out frame was EASY (M landed near Hermite's L-to-R cubic), not
  that Hermite tracked well. Scoring the trusted tracker by closeness to a
  yardstick the mediocre incumbent passes trivially is the wrong axis. The
  comparative "regressed 11/13" therefore does not show the walker is a worse
  tracker, and does not undercut the bundle.
- What this data IS still good for: the ABSOLUTE walker outliers. A walker box
  2 to 3 torso-widths off the runner (IMG_3830 [1288,1308] 3.279; IMG_3823
  [2316,2337] 2.625; IMG_3823 [1014,1036] 1.457; Conant [3218,3241] 1.885) is
  the walker failing at its own job regardless of Hermite, and those specific
  rows are worth an eyes-on tile check. Small deltas are noise; multi-torso
  absolute misses are real leads.

The original agent text is preserved below unchanged for provenance.

## Headline

On 13 newly sampled held-out triples across 4 corpus videos, the schema-14
walker is WORSE than Hermite on 11 of 13. Walker held-out error median 1.105
torso-widths (max 3.279); Hermite median 0.100 (max 0.770). Only 31 percent of
walker measurements land under 0.5 torso-widths. This contradicts the prior
small-n headline, which reported the walker rescuing the hardest intervals.
The difference is driven by the sample: this expansion deliberately targets
mid-length spans where the pairwise velocity-delta cost is actually exercised,
and it avoids the prior sample's span-1 and span-2 triples. Read the concerns
section before any bundle ruling.

## Method

The method replicates the frozen held-out instrument in
the former `tests/e2e/e2e_walker_ab.py` local harness. The runner
script imports that harness's helper functions directly so the measurement is
byte-identical to the validated instrument; only seed selection differs (an
explicit auditable triple list instead of a random sample).

Held-out measurement, per the prior artifact
[blob_walk_v2_cost_model_ab.md](../../archive/blob_walk_v2_cost_model_ab.md):

- A triple is three consecutive during-race human seeds L, M, R, all status
  visible, with L strictly after `race_start_frame` (contract C4).
- The interior human seed M is held out. The merged interval L-to-R is solved
  twice: Hermite-only (`blob_pass=False`) and walker-on (`blob_pass=True`).
- Error is the center distance from the solved box at frame M to the held-out
  human seed M, normalized to torso-width units using M's seed width (contract
  C2). This is exactly `e2e_walker_ab._torso_err`.

Direction measured: BLENDED. The solved box at M is read from
`result["blended_path"]` via `e2e_walker_ab._solved_box_at_frame`, the same
blended-path read the prior artifact used. This is the blended interval path
(forward and backward passes combined after both complete), not a single pass.

Classification uses `e2e_walker_ab._classify` unchanged: rescued when the
walker beats Hermite by at least 0.15 torso-widths, regressed when worse by at
least 0.15, preserved on a small swing with at least one method close,
needs_review when both methods exceed 1.0 torso-widths.

Per-measurement walker status: each row records `propagator_path` from the
solve result (`walker` means the walker drove the interval; `hermite` means
both passes stalled and the P10 fallback returned pure Hermite) plus the
per-pass `walker_fallback_fwd` / `walker_fallback_bwd` stall stamps. The
per-frame five-value status enum (accepted / interpolated / extrapolated /
miss) is not surfaced on the returned blended-path API, so the table records
the available per-frame `source` proxy at M (`propagated` means M's blended
slot carried no accepted blob and is an interpolated/extrapolated propagated
slot; `merged` means M's slot is a blended forward/backward value). This
matches the prior artifact, which also did not record the per-frame status
enum.

### Seed-triple selection rule

Stated explicitly so the sample is auditable:

1. For each chosen video, enumerate all qualifying during-race visible triples
   (same filter as the harness) and sort by span ascending.
2. Pick a spread of distinct spans, preferring span at or above 15 frames so
   the pairwise velocity-delta cost has at least two frames of real-node
   history. The prior artifact flagged spans of 1 to 13 frames as degenerate
   for this cost term, so they are intentionally avoided here.
3. Skip any triple already present in the prior random-seed-12345 sample
   (verified disjoint: none of the 13 selected triples appears in the prior
   25-pass sample).
4. Keep per-video decode within budget by capping the span and the count on
   the expensive videos.

Triple frames are taken from the decode-free enumerator output
(`_temp_wsg_enumerate.py`), which reuses the harness qualifying filter.

### Video selection and budget

Videos: IMG_3830 and IMG_3823 (cheap decode, dense seed regions), Lyra-Hersey
(moderate decode, dense), Conant (moderate decode; the prior rescue video).
Lyra-Wheeling is excluded (6 h decode, banned for this lane). Jason is excluded
from this pass: at roughly 550 s per mid-span triple on 4K HEVC, two triples
would consume most of the per-video budget for little marginal coverage, and
the prior artifact already characterizes Jason as signal-absence dominated.

Total measured decode was well under budget: the cheap videos finished in
seconds per triple; Lyra-Hersey 17 to 30 s per triple; Conant 11 to 21 s per
triple. No single video approached the 90 min ceiling.

## Results

All errors in torso-width units. delta = walker_err minus hermite_err
(positive means the walker is farther from the held-out human truth).
prop = propagator_path; fb = per-pass Hermite fallback fired.

| video | L | M | R | span | hermite_err | walker_err | delta | class | prop | fb_fwd | fb_bwd | source@M |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| IMG_3830 | 1288 | 1298 | 1308 | 20 | 0.086 | 3.279 | +3.193 | regressed | walker | NO | NO | propagated |
| IMG_3830 | 1624 | 1632 | 1656 | 32 | 0.068 | 0.245 | +0.177 | regressed | walker | NO | NO | propagated |
| IMG_3830 | 1886 | 1904 | 1925 | 39 | 0.770 | 0.280 | -0.489 | rescued | walker | NO | NO | propagated |
| IMG_3830 | 3491 | 3496 | 3536 | 45 | 0.044 | 0.136 | +0.091 | preserved | walker | NO | NO | propagated |
| IMG_3823 | 110 | 117 | 130 | 20 | 0.077 | 0.688 | +0.612 | regressed | walker | NO | NO | propagated |
| IMG_3823 | 1014 | 1027 | 1036 | 22 | 0.156 | 1.457 | +1.301 | regressed | walker | NO | NO | propagated |
| IMG_3823 | 2316 | 2332 | 2337 | 21 | 0.051 | 2.625 | +2.573 | regressed | walker | NO | NO | propagated |
| IMG_3823 | 3979 | 3990 | 3999 | 20 | 0.103 | 0.551 | +0.448 | regressed | walker | NO | NO | propagated |
| Lyra-Hersey | 2310 | 2326 | 2327 | 17 | 0.021 | 1.168 | +1.146 | regressed | walker | NO | NO | merged |
| Lyra-Hersey | 3150 | 3160 | 3172 | 22 | 0.512 | 1.105 | +0.592 | regressed | walker | NO | NO | propagated |
| Lyra-Hersey | 2178 | 2197 | 2206 | 28 | 0.100 | 0.309 | +0.209 | regressed | walker | NO | NO | merged |
| Conant | 3211 | 3218 | 3226 | 15 | 0.343 | 1.413 | +1.071 | regressed | walker | NO | NO | propagated |
| Conant | 3218 | 3226 | 3241 | 23 | 0.196 | 1.885 | +1.689 | regressed | walker | NO | NO | propagated |

Every row drove the walker (prop = walker), and the P10 Hermite fallback fired
on zero rows. The regressions are genuine walker drift, not stalled-fallback
artifacts.

## Summary stats

| metric | walker | hermite |
| --- | --- | --- |
| n | 13 | 13 |
| median error (torso-w) | 1.105 | 0.100 |
| max error (torso-w) | 3.279 | 0.770 |
| min error (torso-w) | 0.136 | 0.021 |
| fraction under 0.5 torso-w | 4/13 (31%) | 13/13 (100%) |

Classifications: regressed 11, rescued 1, preserved 1, needs_review 0.

## Comparison vs prior small-n results

| sample | n | selection | walker outcome |
| --- | --- | --- | --- |
| prior (cost_model_ab) | 25 | random seed 12345, 5 per video, spans 1-188 | 12 preserved, 7 regressed, 4 rescued, 1 needs_review, 1 skipped |
| this expansion (WS-G) | 13 | explicit mid-span triples, spans 15-45, 4 videos | 1 rescued, 1 preserved, 11 regressed |

The prior sample's rescues were the headline (Conant hermite_err 0.916-1.487 to
walker_err 0.238-0.428). This expansion does not reproduce that picture on
mid-length spans: the single rescue here is IMG_3830 [1886,1925] (0.770 to
0.280), and everything else regresses, several severely (IMG_3823 [2316,2337]
2.625, IMG_3830 [1288,1308] 3.279). The two samples disagree because they probe
different span regimes and the prior sample's preserved count was dominated by
short / already-easy intervals where both methods are close to truth.

## Interpretation for the bundle ruling

This expansion is a caution flag, not a clean accept or reject. The deliberate
mid-span selection -- chosen so the pairwise velocity-delta cost is actually
exercised rather than degenerate -- shows the schema-14 walker drifting off the
held-out human seed on 11 of 13 intervals, with a walker median error roughly
11x the Hermite median and only 31 percent of walker measurements within half a
torso-width. Because every row drove the walker with no fallback, this is the
cost model's own geometry choosing a worse path, not a stall artifact. The
honest read is that the walker materially helps a narrow band of hard,
long-drift intervals (the prior Conant rescues) while hurting a broad band of
ordinary mid-length intervals where Hermite was already accurate. Whether that
trade is acceptable is the human's call under the "most intervals working
better" acceptance frame; by the held-out-error instrument, this sample does
not meet that bar. The reviewer should weigh this against the prior sample's
rescues and the corpus accepted-fraction tables (which showed no regression but
measure coverage, not positional accuracy) before ruling on the frozen bundle.

### Concerns and caveats

- Selection is intentionally adversarial toward the cost model: it targets the
  regime the prior artifact already flagged as the cost model's hardest case.
  It is not a uniform random sample, so the 11/13 regressed rate is not an
  unbiased corpus estimate. It is an existence proof that mid-span regressions
  are common and large, not rare.
- Some Conant and Lyra picks land in regions the prior corpus run already
  flagged as low-coverage (Conant [3211,3226] was a starvation interval at
  FWD 0.133 in the corpus run). Those rows confirm the walker also has a
  positional-accuracy problem there, not only a coverage problem.
- The per-frame five-value status enum is not surfaced on the blended-path API;
  the table records the `source` proxy (propagated vs merged) instead. The
  `propagator_path` and fallback stamps are exact, not proxies.
- n is 13, single-sampled per triple (deterministic, no RNG). The instrument is
  deterministic, so re-runs reproduce these numbers exactly.
- Jason is not measured in this pass (decode cost vs budget); its signal-absence
  behavior is already characterized in the prior artifact.

## Reproduction

Exact commands run (repo root):

```
source source_me.sh && python3 _temp_wsg_enumerate.py
source source_me.sh && python3 _temp_wsg_heldout.py
```

`_temp_wsg_enumerate.py` is a decode-free triple enumerator (reads seed JSON
only). `_temp_wsg_heldout.py` runs the held-out A/B on the explicit triple list
and prints the CSV plus summary. Both are underscore-prefixed scratch scripts
at the repo root. The triple list lives in `TRIPLES_BY_VIDEO` in
`_temp_wsg_heldout.py`.

### Seeds tried and dropped

- Span-1 and span-2 triples (abundant on every video): dropped by the selection
  rule. The pairwise velocity-delta cost is degenerate below 2 real-node
  frames; the prior artifact already characterizes these as not representative.
- Lyra-Wheeling: dropped (6 h decode, banned for this lane).
- Jason mid-span triples: dropped for this pass (roughly 550 s per triple on 4K
  HEVC vs the budget; marginal coverage over the prior artifact's Jason rows).
