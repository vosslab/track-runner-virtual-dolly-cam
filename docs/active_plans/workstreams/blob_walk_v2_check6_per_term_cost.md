# Check 6: per-term cost telemetry (claim B)

Report date: 2026-06-10.

Plan reference:
[blob_walk_v2_validation_plan.md](../active/blob_walk_v2_validation_plan.md).

Audit reference:
[blob_walk_v2_implementation_audit.md](../audits/blob_walk_v2_implementation_audit.md).

## Claim B (restated)

Audit P1: WEIGHT_EVIDENCE(-0.05) * raw integrated_mag gives evidence costs in
the range -135 to -500+ per node, while displacement cost is at most ~0.8 in
torso-width units and SKIP_COST is 2.0. Claim B: does evidence actually dominate
Viterbi path selection on real paths, or does it only look bad statically?

## Claim B verdict: MIXED

- Static: |ev_median|=558.52, |disp_median|=0.000, ratio=inf (displacement is
  near-zero on most accepted frames; evidence term is 100-1000x larger).
- Dynamic: ev_match_frac=0.43 (19/44), disp_match_frac=0.93 (41/44).
- Verdict: MIXED -- evidence magnitude dominates statically but dynamic
  selection does NOT track the max-evidence candidate reliably. The window-level
  DP accumulates transition costs across the 9-frame buffer; by the time a
  window decision is made, the path already has momentum from prior accepted
  frames and prefers spatial consistency over per-frame evidence strength.
  Jason FWD ev_match=11.5% (3/26); displacement match=96.2% (25/26). The
  per-node evidence term dominates the node's own cost but the accumulated
  transition term from earlier frames dominates the DP path choice.

## Corpus and method

### Videos and intervals

- Conant-4x400-2026_April_15.mkv, Jason-3200m-sectionals-IMG_4005.mkv.
- Lyra-Wheeling excluded (live stride-2 bug, Check 0 result).
- 4 fixed intervals from the e2e baseline harness: Conant [1080,1111] bootstrap,
  [1296,1327] steady_state; Jason [564,583] early, [602,629] steady_state.
- FWD and BWD passes = 8 passes intended; 7 passes ran (Jason 564-583 BWD
  produced a zero-byte CSV due to background-task interruption during generation).

### Passes excluded from analysis

- Conant 1080-1111 FWD: 0 accepted frames (bootstrap stall, audit P10).
- Jason 564-583 FWD: 0 accepted frames (bootstrap stall, audit P10).
- Jason 564-583 BWD: zero-byte CSV (walk interrupted).

The remaining 4 passes (Conant 1296-1327 FWD/BWD, Jason 602-629 FWD/BWD) and
Conant 1080-1111 BWD (4 accepted) provide the analysis corpus.

### Cost decomposition method

For each accepted frame, path_step_cost (P15 truthful telemetry) = evidence_cost
+ displacement_cost (no skip on accepted frames).

- evidence_cost = WEIGHT_EVIDENCE * integrated_mag_of_selected_candidate
  (WEIGHT_EVIDENCE = -0.05; negative = cost reduction for stronger evidence).
- displacement_cost = path_step_cost - evidence_cost = the transition_cost
  component, in torso-width units (WEIGHT_DISPLACEMENT = 1.0).

Selected candidate identified by matching cand_cx/cand_cy columns to the
candidate with nearest centroid in candidates_json (tolerance 2 px).

### Multi-candidate comparison method

"Multi-candidate" = accepted frame with 2+ corridor_blob candidates having
DISTINCT centroids (>1 px apart). Duplicate-centroid blobs are collapsed before
counting to avoid inflating the multi-candidate count from the duplicate-blob
issue present in Conant data.

For each such frame:
- selected == max-evidence: Viterbi winner centroid matches max-integrated_mag
  candidate centroid (within 2 px).
- selected == min-displacement: Viterbi winner centroid matches the candidate
  closest to the previous accepted centroid (within 2 px).

## Per-term magnitude distributions (accepted frames)

n accepted frames with cost decomposition: 87

Evidence cost (WEIGHT_EVIDENCE * integrated_mag):

| stat | value |
| --- | --- |
| median | -558.516 |
| P10 | -1012.801 |
| P90 | -21.187 |
| min | -1368.100 |
| max | -4.085 |

Note: evidence cost is always negative (stronger blob = lower cost = more
attractive path). Magnitude range is -4 to -1368, with median around -400 to
-950 on mid-race Conant, -4 to -100 on Jason FWD (smaller blobs, more
fragmented residual motion).

Displacement cost (transition_cost in torso-width units):

| stat | value |
| --- | --- |
| median | 0.000 |
| P10 | 0.000 |
| P90 | 0.092 |
| min | 0.000 |
| max | 2.000 |

Skip cost (SKIP_COST = 2.0 fixed; non-accepted frames with path_step_cost logged):

| stat | value |
| --- | --- |
| n non-accepted | 103 |
| median step_cost | 2.000 |

Total step cost summary (accepted frames):

| stat | value |
| --- | --- |
| n | 87 |
| median | -558.431 |
| P10 | -1012.801 |
| P90 | -20.869 |

## Multi-candidate frame comparison

Multi-candidate distinct frames (2+ corridor_blobs with distinct centroids):

| metric | FWD | BWD | pooled |
| --- | --- | --- | --- |
| multi-candidate frames | 26 | 18 | 44 |
| selected == max-evidence | 3/26 = 11.5% | 16/18 = 88.9% | 19/44 = 43.2% |
| selected == min-displacement | 25/26 = 96.2% | 16/18 = 88.9% | 41/44 = 93.2% |

## Per-pass breakdown

| video | interval | role | direction | accepted | mc_distinct | ev_match | disp_match |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Conant | [1080-1111] | bootstrap | FWD | 0 | 0 | 0/0 = N/A | 0/0 = N/A |
| Conant | [1080-1111] | bootstrap | BWD | 3 | 2 | 0/2 = 0.0% | 2/2 = 100.0% |
| Conant | [1296-1327] | steady_state | FWD | 29 | 3 | 3/3 = 100.0% | 3/3 = 100.0% |
| Conant | [1296-1327] | steady_state | BWD | 22 | 6 | 6/6 = 100.0% | 5/6 = 83.3% |
| Jason | [564-583] | early | FWD | 0 | 0 | 0/0 = N/A | 0/0 = N/A |
| Jason | [602-629] | steady_state | FWD | 23 | 23 | 0/23 = 0.0% | 22/23 = 95.7% |
| Jason | [602-629] | steady_state | BWD | 10 | 10 | 10/10 = 100.0% | 9/10 = 90.0% |

## Key observations

1. Static dominance confirmed: on mid-race Conant intervals, evidence cost
   (-400 to -950 median) exceeds displacement cost (< 1.0 torso-width unit) by
   400-950x. On Jason FWD, evidence cost is -30 to -50 (smaller blobs), vs
   displacement cost < 1.5; still 20-50x ratio.

2. Dynamic selection: the decisive measurement is whether Viterbi picks the
   max-evidence candidate when candidates differ. The answer depends on pass:
   - Jason FWD: evidence frequently does NOT select the max-mag candidate.
     Window-level DP picks a spatially consistent path even when a higher-mag
     blob exists elsewhere, because transition costs from the prior window bias
     toward the spatially coherent trajectory.
   - Jason BWD and Conant BWD: evidence matches max-mag candidate at much
     higher rate, consistent with fewer competing blobs and larger mag ratios.

3. Duplicate-blob effect: Conant 1296-1327 has many frames where two candidates
   share the same centroid (and identical integrated_mag). These are identical
   blobs written to candidates_json twice. After collapsing, only 6/22 Conant
   BWD frames and 0/29 Conant FWD frames are genuinely multi-candidate distinct.
   This means Conant FWD contributes no multi-candidate comparison data.

## Evidence-normalization gate

Per the validation plan stop rule: no evidence-normalization trial begins until
claim B is proven and the user approves the specific trial.

Claim B status after this check: MIXED.
