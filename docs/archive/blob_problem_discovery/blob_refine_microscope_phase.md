# Blob refinement microscope phase

Short tactical follow-up to M3 BLOCKED. No new corpus-wide machinery.
Three interval-local experiments. Visualization only. Iterate fast.

## Observation

M3 evidence (74-interval ranking sample, AC-3b 100% on 10146 rows):

- All blob-side policies (B prox 0.6 -> 1.0, F weighted snap, G cue-conf
  reshape) produce 0% trust_0 rescue across all 12 videos.
- On trust_0 samples (Conant 11191-11207, Hononega-Orion 270-288),
  every rejected frame has `winner_dist_h <= 1.0`. Proximity alone
  does not gate them; downstream gates do.
- `lost_at_stage` on trust_0 across 8 videos is heterogeneous:
  - gate_prox dominant: Hononega-Orion-1600m 79%, IMG_3627 100%
  - gate_dir dominant: Conant 68%, IMG_3830 100%
  - gate_path notable: Hononega-Varsity 41%, Jason 30%
- No single mechanism dominates the corpus. Branch (a) of M3 decision
  tree (gate_path >= 60% on >= 6 of 12) does not fire: 0/8.

## Success criterion

Do not optimize for 100% blob acceptance. Optimize for high precision with
broad useful coverage.

Target:
- Blob works on most normal intervals.
- Blob safely abstains on bad intervals.
- A failed blob should not make tracking worse.
- Success metric is not "all intervals green." It is "runner found more often without harming trusted controls."

Practical gate for approval:
- Trust 0% intervals improve substantially.
- Positive controls do not regress.
- Weird intervals may still fail, and that is acceptable.
- Any new rule must be allowed to abstain.

The bug is systematic under-acceptance when the blob is visibly valid, not
abstention in genuinely weak intervals.

## Immediate experiments

Three, each scoped to one interval. Run, look, decide.

### E1: direction-gate rejects on Conant + IMG_3830

- Pick one Conant trust_0 interval (Conant 11191-11207) and one
  IMG_3830 trust_0 interval.
- Render per-frame overlay: rejected frame with raw_pred arrow,
  winner blob center, accepted-direction cone, computed `dir_dot`,
  and the corridor blob list.
- Look for: is the direction vector wrong (motion prior bad), is
  the cone too narrow, or is the blob legitimately on the wrong
  side of the runner.

### E2: path rejects on Jason + Hononega-Varsity

- Pick one Jason trust_0 interval and one Hononega-Varsity trust_0
  interval where `lost_at_stage = gate_path`.
- Render per-frame overlay: rejected frame with raw_pred trail
  (t-3..t+3 from `raw_pred[t]` Hermite-only), winner blob, computed
  path verdict, and accepted-vs-rejected path geometry.
- Look for: is the path test rejecting smooth blobs by accident,
  is the path window too short, is the blob actually off-path.

### E3: accepted vs rejected direction vectors, one interval

- Pick one mixed-bucket interval with >50% accept and >20% reject
  (so both classes have samples).
- Side-by-side overlay: 6 accepted frames vs 6 rejected frames
  on the same interval.
- Look for: visual difference in `dir_dot`, raw_pred motion shape,
  or blob displacement direction.

All three use `tools/visualize_blob_gates.py` with per-frame PNGs on,
contact sheets on. No corpus runs. No solver changes. No refine.

## Rules

- Never delete or modify solved interval data.
- No refine runs unless a single-interval refine is explicitly needed.
- No cache deletion outside `output_smoke/`.
- All outputs under `output_smoke/blob_refine_fix/<run_id>/microscope/`.
- No `tr_config/` writes.
- No new corpus-wide tools, plans, or analyzers until a local mechanism
  is identified.
- Abstention on weak intervals is success, not failure. Do not chase 0% accept rates that look correct on visual review.
- Evaluate blob detection as seed propagation. Near-seed accept rate is the primary metric. Midpoint accept rate is a stress test, not a success bar.

## Exit criterion

Identify one concrete mechanism with visual evidence strong enough to
justify a small production edit (one file, one function). If no
mechanism surfaces after the three experiments, write a second short
note proposing the next three -- do not escalate to a master plan.

## Artifacts to keep

- per-frame PNGs and contact sheets per experiment, under
  `output_smoke/blob_refine_fix/<run_id>/microscope/<exp>/<interval>/`
- one short observation paragraph per experiment in this doc,
  appended under a `## Findings` heading as experiments complete
- a single `## Recommendation` paragraph when the exit criterion is met,
  or a single `## Next experiments` paragraph when it is not

## Findings

### E1 direction gate

Raw_pred overshoots the runner in both Conant and IMG_3830 intervals. In Conant 3115-3147, all 24 direction-gate rejected frames have negative dir_dot (range -0.03 to -13.78) with v_pred_mag 2.0-2.5, well above the vacuous floor. The direction cone is placed at a forward point; the correct blob sits behind it, making direction rejection a valid detector of overshoot, not a marginal gate failure. IMG_3830 shows the same pattern with dir_dot = -83.3 and -161.2 on both trust_0 frames.

### E2 path gate

Path-gate rejection surfaces two distinct failure modes. Hononega-Varsity (5-frame interval) fails due to bootstrapping: dir_dot ranges 97-231 but path_ok_prev=False throughout, so path acceptance never initiates on short intervals. Lyra-Hersey (80-frame interval) shows cascading rejection: 73% of frames trigger gate_path, leaving only 2 accepted frames out of 80. Positive dir_dot on path-rejected frames indicates they are geometrically moving in the right direction but fail the prior-acceptance gate due to lack of early acceptance anchors.

### E3 accept-vs-reject contrast

Lyra-Wheeling interval (5089-5278, 86 accepted / 102 rejected) reveals acceptance hinges on two simultaneous conditions: v_pred_mag drops below the vacuous floor AND blob center sits essentially at raw_pred (winner_dist_h ~0.01h). Dir-rejected frames uniformly show negative dir_dot; path-rejected frames show positive dir_dot but lack prior acceptance. This contrast indicates direction and path gates operate on distinct geometric signals and rejection is not a noise artifact.

## Recommendation

No production edit is recommended at this time.

Policy H (halfway-shifted cone origin when prox passes + dir fails): produces
+8 newly accepted frames across the microscope set with 0 pos-control damage,
but all gains concentrate on Lyra-Wheeling E3 (a mixed interval, not trust_0).
On Conant E1 3115-3147 (the dir-overshoot trust_0 interval that motivated
investigation), policy H produces zero new accepts. The halfway-shift does not
address the overshoot mechanism the microscope identified. See
`output_smoke/blob_refine_fix/2026-05-23/counterfactual/dir_gate_experiments.md`
for evidence.

Policy I (vacuous direction when winner_dist_h < 0.5): produces +4 newly
accepted frames but regresses pos-control by -4 frames on Lyra-Hersey.
Rejected under success criterion. See `dir_gate_experiments.md`.

Policy J (torso-normalized velocity threshold 0.05 h/frame): produces +4 newly
accepted frames but regresses pos-control by -4 frames on Lyra-Hersey.
Rejected under success criterion. See `dir_gate_experiments.md`.

## Reframed evaluation: near-seed propagation

Blob detection is evaluated as seed propagation, not midpoint rescue. The
primary metric is near-seed accept rate (frames 1-10 from each seed boundary).
See output_smoke/blob_refine_fix/2026-05-23/near_seed/PROPAGATION_REPORT.md
and output_smoke/blob_refine_fix/2026-05-23/near_seed/near_seed_visual.html.

Per-bucket near-seed accept rates: trust_0 0.0%, trust_high 90.3%, mixed 44.8%,
weak_fair_low 0.9%. Near-seed failure (trust_0, weak_fair_low) indicates the
observer/gates may need attention; mixed bucket exhibits intermediate accept
rate with graceful decay toward midpoint. Midpoint accept rate is a stress test,
not a success bar.

## Next experiments

NE1: Render Lyra-Wheeling E3 5089-5278 PNG contact sheet annotated with
policy-H newly-accepted frames in green to visually verify whether those new
accepts are on-runner. If they are NOT on-runner, H is over-accepting and even
the safe-looking +8 is a false win.

NE2: Re-inspect Conant E1 3115-3147 dir_dot values and corridor_blobs_json to
ask: where is the runner ACTUALLY in those frames, given raw_pred overshoots?
Is the closest corridor blob the runner, or is the corridor empty? If empty,
no policy that snaps to a blob can help and the gate is correctly abstaining;
that is success per the new lens, not a bug.

NE3: Replay a single fourth policy K = "abstain (no snap) when prox passes but
dir fails AND v_pred_mag/h_seed > 0.05" (the explicit abstention policy under
the new lens). Measure: does production already do this? If yes, the current
direction gate is correct and no fix is needed. If no, K is the abstention
policy worth landing.