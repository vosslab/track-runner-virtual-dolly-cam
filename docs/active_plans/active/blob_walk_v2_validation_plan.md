# Blob walk v2 validation plan

> **Status: historical.** The separate `blob_walk_v2` diagnostic product was
> removed on 2026-08-21. This validation plan is retained as prior design
> context and contains no current implementation gate or user dependency.

Follow-up to the implementation audit at
[blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md).
This plan validates the audit's assumption table, smallest checks first.
Every check is a measurement that moves a claim from likely/speculative to
proven (or refutes it). No behavior changes to walker selection logic; the
single code change this plan contains is the P15 telemetry fix (Check 1),
which is allowed only if field-wise decision equality confirms no change to
selected path, statuses, positions, accepted counts, and fallback behavior on
baseline cases; that historical implementation step required user approval.

Plan date: 2026-06-10.

## Deliverable

The next deliverable is a validation report at
`docs/active_plans/reports/blob_walk_v2_validation_report.md`
that updates each assumption-table claim as proven, refuted, or still unknown.
This is NOT a repair plan. The only allowed code change during validation is
the P15 telemetry fix under the strict gate described in Check 1.

## Standing constraints (user feedback, 2026-06-10)

- P2: do not presume the missing variance/angle terms should be added
  later. The candidate failure model is within-body vertical centroid
  jitter, not limb switching; claims D and E must be measured before any
  consistency-term design.
- P8: no physical-speed reasoning in later plans. Anchor-staleness effect
  is conditional on IMAGE-SPACE drift, which must be measured (claims F, I).
- P11: the gate provably excludes blobs before Viterbi sees them, but
  whether excluded blobs are valid runner blobs needs overlay verification
  (claim A) before any gate change.
- No behavior trials until the gating claims are proven; user approves
  each trial separately.

## Check 0: corpus FPS probe (claim K) -- DONE

- Method: `cv2.CAP_PROP_FPS` over every video in `data/outdoor_corpus.txt`;
  stride computed as `max(1, round(fps / 60))` mirroring
  `residual_motion.resolve_stride`.
- Result (2026-06-10):

| Video | fps | stride | P12 status |
| --- | --- | --- | --- |
| IMG_3830.mkv | 30.000 | 1 | ok |
| IMG_3823.mkv | 30.000 | 1 | ok |
| Jason-3200m-sectionals-IMG_4005.mkv | 60.000 | 1 | ok |
| Lyra-Hersey-800m-IMG_3882.mkv | 60.000 | 1 | ok |
| Conant-4x400-2026_April_15.mkv | 60.000 | 1 | ok |
| Lyra-Wheeling-IMG_3912.mkv | 120.000 | 2 | LIVE |

- Verdict: claim K is LIVE, not latent. The stride>1 stepping/termination
  bug (audit P12) is exercised on 1 of 6 corpus videos. On
  Lyra-Wheeling-IMG_3912.mkv the walker observes every other frame and the
  `frame_f == neighbor_seed_frame` termination test can miss, overrunning
  the neighbor seed until the step guard.
- Consequence: P12 fix priority rises from cleanup to live bug. Until
  fixed, treat walker results on Lyra-Wheeling-IMG_3912.mkv as suspect in
  any corpus-level metric.
- Sub-check (separate workstream executing): confirm whether any actual
  Lyra-Wheeling promoted interval has a span not divisible by stride=2,
  causing the `frame_f == neighbor_seed_frame` termination test to miss the
  neighbor seed. If yes, P12 moves from "live risk" to "observed failure"
  and becomes an early fix-plan item.

## Check 1: P15 telemetry truthfulness (prerequisite, approval required)

- What: make the walk debug log's `path_cost` column truthful (per-frame
  cost contribution, matching its own header doc at
  `walk_debug_log.py:112-114`), add the spec section 7 `path_step_cost`
  and `window_head_frame` columns, bump `SCHEMA_VERSION` per C10 with a
  `docs/TR_SCHEMA_VERSION_HISTORY.md` entry.
- Gate: P15 telemetry fix is allowed only if field-wise decision equality
  confirms no change to selected path, statuses, positions, accepted counts,
  and fallback behavior on baseline cases; only telemetry columns change.
- Why first: Checks 4-8 read per-frame cost columns; any analysis built on
  the current column is wrong (audit P15). This is the only code change in
  this plan.
- Unblocks: claims B, L; improves F, I measurement quality.

## Check 2: rejected-blob overlays (claim A)

- What: render overlay tiles of rejected (`acceptance_box_empty`) blob
  centroids against the seed/Hermite reference on the two diagnosed stall
  intervals: Conant 1080-1111 FWD and Jason 564-583 FWD.
- Read-only diagnostic render; no solver change.
- Outcome: proves or refutes "rejected blobs are the runner's blobs."
  Gates any candidate-supply widening (acceptance-box change).

## Check 3: bootstrap-accept masking counts (claim J)

- What: from existing walk debug logs, count passes where
  `accepted_count == 1` and the only accepted frame is the bootstrap
  (seed) frame.
- Outcome: incidence of audit P10 masking (walks frozen at the seed that
  skip the Hermite fallback). Gates any fallback-gate change.

## Check 4: anchor-lag telemetry (claims F, I) -- after Check 1

- What: distribution of frame distance from each observed frame to the
  frame of its acceptance-box anchor (`last_accepted` age), plus anchor
  age at each rejection.
- Outcome: quantifies how stale the gate geometry actually is, in
  image-space terms, per pass.

## Check 5: normalized-cy trace (claims D, E) -- after Check 1

- What: for selected blobs, per-frame normalized vertical position
  `(cy_blob - torso_cy) / torso_h`, its frame-to-frame delta, top/bottom
  alternation rate, and correlation with `integrated_mag`.
- Outcome: establishes whether within-body centroid jitter exists and
  whether limb switching occurs at all. Gates any consistency-term or
  centroid-stability design discussion.

## Check 6: per-term cost telemetry (claim B) -- after Check 1

- What: per-node, per-term cost breakdown (displacement vs evidence vs
  skip) on a corpus walk.
- Outcome: proves or refutes that the raw `integrated_mag` evidence term
  dominates real path selection. Gates the evidence-normalization trial.

## Check 7: regressed-bucket split (claim C)

- What: split the m4 A/B regressed bucket (35 passes) by empty-lattice
  fraction from existing debug logs.
- Outcome: separates starvation-driven regressions from ranking-driven
  ones; orders any future box trial vs cost trial.

## Check 8: identity-jump count (claim L) -- after Check 1

- What: count per-step drift events above pooled P99 + 0.3 W on a corpus
  walk (teleport-across-skip exploitation frequency).
- Outcome: decides whether the skip-transition geometry hole matters in
  practice.

## Ordering rationale

Checks 0-3 need no Viterbi cost telemetry and run on existing artifacts
or read-only renders. Checks 4-8 need truthful per-frame telemetry and
are blocked behind Check 1. Check 7 uses existing m4 logs and can run
any time.

## Stop rule

No walker behavior trial (evidence normalization, acceptance-box change,
anchor-advance change, fallback-gate change, stepping fix, skip-cap
change) starts until its gating claim is proven and the user approves
that specific trial.
