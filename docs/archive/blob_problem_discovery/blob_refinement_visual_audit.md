# Blob refinement visual audit

Hand-off audit for WP-4A of plan `kind-exploring-cray.md`. Combines milestones
M0, M0.5, M0.7, M1, M2, and M3 results on the Jason 3200m IMG_4005 clip and the
oracle smoke corpus.

Status: PRELIMINARY. Heat-map equivalence (M0.5) and funnel-stage classification
(M2, M3) verdicts are stable. Seed-oracle (M0.7) hypothesis verdicts are
preliminary pending WP-0.7A fix-2 and a regenerated CSV.

The word "proved" is intentionally avoided. Each finding states "supported by
N seed frames across M videos and K visual frames in interval X" so a reader
can see the scope of every claim.

## 1. Cross-interval funnel-stage distribution

- Interval 2444-2491 (TRUST 0%, FWD pass): supported by 1 interval and 46
  non-endpoint frames in one video (Jason-3200m-sectionals-IMG_4005). Tag
  shares: 43/46 (~93%) `lost_at_gate_prox`, 3/46 (~7%) `lost_at_gate_path`,
  0/46 `lost_at_observer`, 0/46 `lost_at_raw_blobs`, 0/46 `lost_at_corridor`.
  The observer returns blobs; the corridor retains them; production gates
  reject them because the blob centroid sits beyond 0.6 * h from `raw_pred`.
- Cross-corpus sweep across the remaining TRUST 0% intervals
  (2491-2538, 2632-2726, 2773-2820, 2820-2867) is pending. Until that sweep
  runs and the per-interval tag distribution is recorded, the dominant-tag
  claim is scoped to interval 2444-2491.

## 2. Per-pass asymmetry (FWD vs BWD)

- Within interval 2444-2491 the smoke run shows BWD mirroring FWD on the
  dominant tag, supported by 1 interval and 92 non-endpoint pass-frames
  across one video. No FWD/BWD divergence in dominant stage was observed in
  this sample.
- A corpus-wide FWD/BWD asymmetry pattern across the remaining TRUST 0%
  intervals is not yet recorded. The cross-pass claim is scoped to interval
  2444-2491 until the M3 corpus sweep completes.

## 3. Per-bucket asymmetry (endpoint vs interior)

- M3 buckets `endpoint_start`, `interior`, and `endpoint_end` showed no
  bucket-level disagreement on the dominant stage for interval 2444-2491,
  supported by 46 non-endpoint frames split across the three buckets in one
  video. `lost_at_gate_prox` is dominant in all three buckets.
- Corpus-wide per-bucket asymmetry is pending. The bucket-uniformity claim is
  scoped to interval 2444-2491.

## 4. Confirmed and refuted prior findings

- [docs/BLOB_REDESIGN_REPORT.md](../../BLOB_REDESIGN_REPORT.md), Conant oracle
  test, Finding 4: of 72 intervals with real accept < 20%, the oracle
  rescue distribution put 46 intervals in the "gates fine, real blob is
  wrong" bucket. The 2444-2491 funnel-stage finding is CONSISTENT with the
  inverse case ("real blob is fine, gates reject it"): observer and corridor
  pass while the proximity gate rejects. Full corpus-wide replication on the
  Jason clip is pending the M2/M3 sweep over the remaining TRUST 0%
  intervals. Supported by 46 visual frames in one interval and one video,
  cross-referenced against 72 Conant intervals.
- [docs/BLOB_SEED_DISTANCE_FINDINGS.md](../../BLOB_SEED_DISTANCE_FINDINGS.md),
  Orion seed-distance gradient (good-rate climbs 30% to 59% from bin 1 to
  bin 11-20): the gradient claim cannot yet be confirmed on the Jason clip
  because the M3 per-bucket histogram is restricted to one interval.
  Confirmation requires the corpus-wide M3 sweep. Supported by 622 traced
  Orion pass-frames in the prior finding, with zero replication frames on
  the Jason clip so far.
- Preliminary M0.7 oracle verdicts in `/tmp/hypothesis_smoke_v2/HYPOTHESIS_TESTS.md`
  (regenerate after WP-0.7A fix-2): H1 limb-centroid bias SUPPORTED, H2
  raw_pred quality REFUTED, H6 wrong-winner SUPPORTED, H8 DoG suppresses
  REFUTED. Supported by 200 seed frames across 2 videos. Treat as
  PRELIMINARY until fix-2 lands and the CSV is regenerated.
- M0.5 heat-map equivalence (`output_smoke/heatmap_equivalence/heatmap_equivalence_report.md`):
  VERDICT EQUIVALENT, max_abs_diff = 0.0 across the sampled frame on Jason.
  Gate G-0.5 cleared: the heat-map and blob observer compute the same
  residual array. Supported by 1 sampled frame in one video. The clearance
  means the user's "blob is obvious in the motion map" observation is
  looking at the same array the solver gates consume.

## 5. Recommendation for follow-up plan target

The dominant funnel-stage tag on interval 2444-2491 is `lost_at_gate_prox`,
supported by 43/46 non-endpoint frames in that interval. Per the decision
tree in `kind-exploring-cray.md`, the right follow-up is a focused proximity
gate mechanism plan targeting [track_runner/velocity_model.py](../../../track_runner/velocity_model.py)
proximity gate and [track_runner/residual_motion.py](../../../track_runner/residual_motion.py)
`observe_blob_at` centroid logic. That plan should cross-check H1
(observer centroid bias), H2 (raw_pred quality), and H6 (wrong-winner
selection) from the M0.7 oracle, after WP-0.7A fix-2 lands and
`HYPOTHESIS_TESTS.md` is regenerated. This audit names the follow-up plan
target; it does not write the fix plan.

## Scope summary

- Heat-map equivalence: supported by 1 sampled frame in 1 video.
- Funnel-stage classification (M2, M3): supported by 46 visual frames in 1
  interval (2444-2491) in 1 video.
- Cross-corpus TRUST 0% sweep over the remaining 4 intervals: pending.
- M0.7 oracle hypothesis verdicts: supported by 200 seed frames across 2
  videos, PRELIMINARY pending WP-0.7A fix-2.

On any conflict with [docs/TRACK_RUNNER_CONTRACT.md](../../TRACK_RUNNER_CONTRACT.md),
the contract wins and this audit is corrected.
