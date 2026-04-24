# Plan: phase 3 -- expand funnel diagnostics

## Context

Phase 1 (`tools/check_seed_blob_overlap.py`) established raw blob
detection is strong at seed frames. Phase 2
(`tools/check_interval_blob_funnel.py`) replayed the solver's snap
pipeline per interval; in the sampled phase-2 runs so far, the loss
has appeared at proximity / direction / path gates, not at corridor
selection. A full sweep (experiment 3B below) is needed before
claiming this as a universal property.

A 3-interval `--limit 3 --shuffle` sample produced one clean single-
interval diagnosis: interval [281] (0.5s) fails 0/30 frames with
`blob_dist_h median=2.76`, which is ~5x the gate threshold. That is a
robust per-interval finding -- the gate is correctly rejecting a
blob that sits far from the Hermite prediction.

What the 3-sample **does not** establish is any general claim about
short intervals. n=1 in the 0.5-1.0s bucket and n=2 in the 1.0s+
bucket is anecdotal, not statistical. We need a wider sample before
drawing conclusions about short-interval policy.

Relevant design background (from docs/TRACK_RUNNER_DESIGN.md,
docs/FWD_BWD_MODEL_METHODOLOGY.md, and docs/CHANGELOG.md):

- Blob snap is an optional local correction layer, not the primary
  tracker. Gates read only raw_pred (contract C5).
- The path gate was intentionally downgraded as a **positive tracking
  signal** -- it is not meant to drive tracking. It remains in place as a
  **rejection rule**: if a corridor blob is off the expected motion
  line, drop the snap. That means "path fails a lot" is NOT a
  contradiction of the downgrade; it is exactly the role path is
  supposed to play.

The main question from the phase-2 data is not "why is path rejecting
so much?" It is "why are the blob and torso prediction so incompatible
on the bad intervals?" Two plausible root causes:

1. **Hermite raw_pred drifts** far from the true runner track on some
   intervals (short / wide-spacing / degenerate-tangent cases).
2. **Blob centroid is not a stable proxy for torso center** in
   crowded residual-motion fields.

Phase 3 separates these two causes. Execution model: a brief tooling
/ correctness pass first (3A), then bulk runs in parallel (3B), then
merged analysis (3C). The four experiments below (3a, 3b, 3c, 3d) are
grouped under those execution phases rather than run strictly
serially, because their outputs are joined over the same set of
intervals for cross-comparison.

## Terminology

This plan uses the following terms consistently. "Track" is avoided
for per-interval geometry because it both sounds global and collides
with the track-and-field domain of the input video. A documentation
deliverable is to land these definitions in
`docs/FWD_BWD_MODEL_METHODOLOGY.md` and cross-link from the contract.
Renaming code symbols (e.g., `fused_track` -> `blended_path`) is a
separate cleanup, not required for phase 3. "Track" is reserved for
higher-level whole-video concepts if needed.

- **raw_pred** -- the frozen Hermite prior inside one pass, computed
  from seed geometry. Each pass has its own. Gates read only this.
- **forward interval path** -- current code alias: `forward_track`.
  The pass-local solved trajectory for the forward pass after
  optional blob snap, spanning a single seed-to-seed interval.
- **backward interval path** -- current code alias: `backward_track`.
  Same, for the backward pass.
- **blended interval path** -- current code alias: `fused_track`.
  The per-interval torso-box trajectory obtained by combining the
  forward and backward interval paths after both passes complete. It is an output artifact
  used for downstream rendering, stitching, anchor correction, and
  cropping. It is not a seed, not a raw pass prediction, not a
  scoring object, and not a legal input to either pass while solving.
  It must not feed FWD/BWD agreement scoring, and it must not be read
  by either pass while that interval is being solved. Scoring
  compares the forward and backward interval paths, never the
  blended one.

## Scope

No changes to production solver code under `track_runner/`. Tool and
doc changes only: a narrow extension to the phase-2 tool for the
`--diagnostic-fused-reference` flag, one new standalone tool
(`tools/check_prediction_divergence.py`), and documentation updates
to `docs/FWD_BWD_MODEL_METHODOLOGY.md` and cross-links in
`docs/TRACK_RUNNER_CONTRACT.md`. All using existing solver helpers
and the geometry_cache the solver already persists.

## Execution phases

### Phase 3A: tooling completion and correctness smoke tests

Complete the small code changes (3b eligible-frame bucket summary,
`scene_displacement_px` CSV column, 3d `--diagnostic-fused-reference`
flag, new `tools/check_prediction_divergence.py`). Smoke-test each
against a 1-2 interval sample only to confirm: script runs, CSV shape
and column meaning are correct, interval/pass joins work on shared
key columns (`interval_index, start_frame, end_frame, pass`).

### Phase 3B: bulk runs, parallel

Once tools pass the smoke test, run all three on the same full
post-race interval universe for the Conant clip. Parallelizable
because outputs are independent:

1. full `check_interval_blob_funnel.py` (experiment 3a + 3b)
2. full `check_prediction_divergence.py` (experiment 3c)
3. full `check_interval_blob_funnel.py --diagnostic-fused-reference`
   (experiment 3d)

### Phase 3C: merged analysis

Join outputs by `(interval_index, start_frame, end_frame, pass)` and
produce:

- duration and eligible-frame-count bucket tables
- divergence vs accept-rate scatter/tables
- fused-reference vs real-blob accept-delta tables
- per-interval short-list of failing intervals for later targeted
  study

## Experiments (grouped under execution phases above)

### Experiment 3a (no code): full post-race sweep on the Conant clip

"Post-race" here means intervals whose `start_frame >=
race_start_frame`, read from `interval_scores.json`'s `race_phase`
metadata (same rule the phase-2 tool already uses to skip early
intervals per contract C2).

Run the existing phase-2 tool without `--limit`, producing full CSV +
JSON for every post-race interval. Use the resulting data to:

- Fill the duration-bucket table with statistically meaningful counts
  (target: 10+ intervals per bucket).
- Fill an **eligible-frame-count** bucket table (new helper -- see
  experiment 3b below). Duration alone conflates fps differences and
  seed-density effects; eligible-frame-count is sharper.
- Compare `blob_dist_h` medians across buckets.

Command:
```
python tools/check_interval_blob_funnel.py \
  -i TRACK_VIDEOS/Conant-4x400-2026_April_15.mov \
  -c /tmp/funnel_all.csv -j /tmp/funnel_all.json
```

Takes a while (each interval does its own residual computation).
Running in a background shell is fine; CSV streams per interval.

### Experiment 3b (small tool edit): add eligible-frame bucket summary

Extend `tools/check_interval_blob_funnel.py` `_print_summary` with a
second bucket table on `eligible_frame_count` (per pass, summed across
intervals in the bucket):

```
by eligible-frame count (non-endpoint, non-stationary frames):
  <   5 frames   (n= ..): proximity=..%  path=..%  accepted=..%
  5-15 frames    (n= ..): proximity=..%  path=..%  accepted=..%
  15-40 frames   (n= ..): proximity=..%  path=..%  accepted=..%
  40+  frames    (n= ..): proximity=..%  path=..%  accepted=..%
```

Eligible-frame count is `fwd_funnel["eligible"]` (already collected).
No new per-frame computation needed.

Percentage denominator: each bucket percentage is computed as the
**sum of the relevant per-frame counter across all intervals in the
bucket, divided by the sum of eligible frames across those
intervals** (not the average of per-interval percentages). That way
a 40-frame interval does not count the same as a 3-frame interval.

Also: add a scene-displacement column to the CSV:
`scene_displacement_px = sqrt((right.sx - left.sx)^2 + (right.sy -
left.sy)^2)`. Computed once per interval; lets post-hoc analysis
scatter (scene_displacement, accept_fraction).

### Experiment 3c (new tool): prediction vs blended-interval-path divergence

New script: `tools/check_prediction_divergence.py`.

Answers one question: **how far is the Hermite raw_pred from the
solver's blended interval path (the current `fused_track` code
symbol), per frame?** The blended interval path is the solver's own
output, not ground truth. Divergence is measured relative to that
accepted output, and interpretation has to stay within that frame.

Per interval, per pass:

1. Load the solved `fused_track` list for that interval from
   `geometry_cache.npz` via `state_io.load_geometry_cache`. Each entry
   has `cx, cy, w, h` in pixel coordinates.
2. Rebuild raw_pred_fwd and raw_pred_bwd using the same helpers the
   phase-2 tool uses (`fit_interval_curves`,
   `_compute_raw_pred_forward`, `_compute_raw_pred_backward`).
3. For each non-endpoint non-stationary frame, compute
   `div_fwd[t] = sqrt((raw_pred_fwd[t].cx - fused[t].cx)^2 + (.cy -
   .cy)^2) / fused[t].h`, same for `div_bwd`.
4. Summarize per interval: median, p90, max.

Output: CSV with columns
`interval_index, start_frame, end_frame, duration_s, eligible_frames,
fused_frames_available, pass, median_div_h, p90_div_h, max_div_h`.
One row per `(interval, pass)`.

Console: per-interval two-line summary mirroring phase 2 style, so
direct eyeball comparison is easy.

**No residual computation, no video decode**: this tool reads only the
geometry_cache and the seeds. Runs in seconds on the full clip. That
makes it cheap to iterate.

Interpretation (all relative to solver output, not ground truth):

- Failing intervals with `median_div_h > 0.5` -> Hermite differs
  materially from the solver's accepted output; the prior is drifting
  relative to what the solver ultimately shipped. Consider short-
  interval policy or improved prediction.
- Failing intervals with `median_div_h < 0.2` but `blob_dist_h > 0.6`
  (from phase-2 CSV) -> blob centroid disagrees with the solver
  output more than the Hermite prior does. Consider blob-to-torso
  calibration.
- Both small on healthy intervals -> control; validates the measure.

Cross-tool join: both CSVs include `interval_index, start_frame,
end_frame, pass`. A trivial awk / pandas merge groups them.

### Experiment 3d (flag on existing tool): fused-reference gate test

**Offline diagnostic only. Not a valid solve-time input.** This is a
post-hoc counterfactual: for an already-solved interval, load its
saved `fused_track` and ask "if the observation were exactly where
the solver's final accepted output says the runner is, would the
current gates accept it?" It does not alter the solver and must not
be used as a production feature -- using `fused_track` during solve
would collapse the FWD/BWD independence the methodology protects and
would violate the "fused is output-only" rule.

Add `--diagnostic-fused-reference` to `tools/check_interval_blob_funnel.py`.
When set, replace the tool's own corridor-best-blob pick with a
synthetic observation whose `center_pixel` is the solver's fused-track
position at that frame. All three gates then run against this
synthetic observation instead of the real blob.

Caveat: this is an internal reference using `fused_track`, not
ground truth. It tests whether the current gates would accept the
solver's own final output location, not whether that location is
objectively correct. It must never be wired into solve. Interpret
accordingly.

Interpretation:

- Accept rate rises sharply (approaches the eligible-frame ceiling)
  on the failing intervals -> gates are correctly rejecting noisy
  **real** blobs; fix the upstream. But
  "upstream" here is still ambiguous between prediction drift and
  blob-centroid error, because the oracle replaces BOTH sources at
  once. To disambiguate, cross-reference with experiment 3c results
  and with the per-frame CSV column `dist(real_blob_centroid, fused
  position) / torso_h`. A new column is added to the oracle run's CSV
  so this comparison is one join away.
- Accept rate stays low even with oracle -> gate logic itself has a
  problem (e.g., tangent from raw_pred disagrees with the fused-track
  direction). Worth a gate-by-gate audit.

Implementation: the diagnostic reference does NOT replace the
corridor or raw-blob pipeline. Those stages still run so the funnel
counters remain comparable. Only the "best blob centroid" used for
proximity / direction / path is overridden. Skip frames where
fused_track has no entry (mostly endpoints).

Comparison rule: the real-blob and `--diagnostic-fused-reference`
runs must be compared on the same interval/pass set -- same
`--limit`, same post-race filter, and do not use `--shuffle` for the
comparison pair (shuffle draws from OS entropy and is not
reproducible across runs). Accept-rate deltas are only meaningful
frame-for-frame on matched intervals.

CLI and naming:
```
python tools/check_interval_blob_funnel.py \
  -i TRACK_VIDEOS/Conant-4x400-2026_April_15.mov \
  --diagnostic-fused-reference \
  -c /tmp/funnel_diagref.csv
```

### Deferred

Not part of phase 3; revisit if needed after 3a-3d:

- **FWD vs BWD asymmetry**. Add later as a sort-key on the CSV.
- **Seed-to-nearest-blob offset calibration** (phase-1 extension).
  Only relevant if experiment 3c shows prediction is fine and blob
  centroid is the remaining source of noise.
- **Within-interval temporal clustering**. Useful for zooming into a
  specific bad interval after 3a gives us a short-list.
- **Cross-video sweep** on a second clip. Validates
  generalization once we have a hypothesis for this clip.

## Files

- **Modify**: `tools/check_interval_blob_funnel.py`
  - Add eligible-frame bucket summary to `_print_summary`.
  - Add `scene_displacement_px` CSV column.
  - Add `--diagnostic-fused-reference` flag (loads geometry_cache once in
    main, threads a fused-track lookup into the replay loop).
- **New**: `tools/check_prediction_divergence.py` (runnable shebang,
  passes existing lint gates).
- **Modify**: `docs/FWD_BWD_MODEL_METHODOLOGY.md` to add the
  terminology block (raw_pred / forward_track / backward_track /
  fused_track definitions above). Cross-link from
  `docs/TRACK_RUNNER_CONTRACT.md` where fused/output track is
  referenced.
- **Unchanged**: every file under `track_runner/`, phase-1 tool, the
  existing drift test.
- **Changelog entry** under today's date summarizing the phase.

## Reused solver helpers

- `velocity_model.fit_interval_curves`,
  `_compute_raw_pred_forward`, `_compute_raw_pred_backward` -- already
  imported by phase-2 tool.
- `state_io.load_geometry_cache(path)` -- returns in-memory dict with
  `solved_intervals[fingerprint] = {start_frame, end_frame,
  fused_track, ...}`.
- `interval_fingerprint.filter_usable_seeds_sorted` -- interval
  enumeration ordering.
- `camera_motion.load_motion_cache`, `scene_coords.SceneTransform`,
  `tr_paths.default_*_path`, `state_io.load_seeds` -- same loader
  pattern as phase 1 and 2.

## Verification

1. `source source_me.sh && pyflakes` on both modified / new tools.
2. `python -m pytest tests/test_pyflakes_code_lint.py
   tests/test_ascii_compliance.py tests/test_indentation.py
   tests/test_whitespace.py tests/test_shebangs.py
   tests/test_import_dot.py tests/test_import_star.py
   tests/test_import_requirements.py tests/test_init_files.py
   tests/test_blob_funnel_tool.py -q` -- existing tests plus the
   phase-2 drift test must all stay green. The oracle flag is off by
   default so the drift test (which does not use the flag) is
   unaffected.
3. Run experiment 3a end to end on the Conant clip; verify CSV +
   JSON are written and the summary table has meaningful bucket
   counts (n > 1 per bucket).
4. Run experiment 3c on the same clip; verify it completes in
   seconds (no residual compute) and the CSV produces sensible
   `median_div_h` values (small on long intervals, potentially
   larger on short).
5. Run experiment 3d with `--diagnostic-fused-reference --limit 5` and
   compare accept rates with and without the flag on the same sample.

## Non-goals

- No gate-threshold tuning.
- No changes to the propagator, scoring, or any on-disk solver
  artifact.
- No cross-pass trajectory reads (contract C5 as revised: pass-local
  sequential state is allowed; FWD reading BWD state or vice versa
  is not).
- No appearance-based identity cues (contract C6).
- Not claiming short-interval failure is a general problem until the
  full-sweep data backs it up.

## Framing correction (from phase-2 analysis)

Replace my earlier phrasing "the duration bucket summary is the key
finding" with the narrower, defensible version:

> The 3-interval sample gives one robust per-interval diagnosis:
> interval [281] fails because the predicted center is ~2.7 torso
> heights from the chosen blob, far outside the 0.6h gate. That is a
> prediction-vs-blob mismatch, not a threshold near-miss. The
> duration-bucket pattern in the 3-sample is suggestive but not yet
> statistical; experiment 3a is needed before any claim about short
> intervals in general.

And on the path gate: the downgrade made it a rejection rule, not a
positive tracking signal. Seeing path reject many frames is
consistent with the design, not a contradiction of it. The question
is upstream: why is the blob-to-prediction geometry incompatible on
failing intervals?

## Phase 4 (deferred): architectural review of FWD/BWD + blob

Not executed now. Queued for after phase 3 data is in.

### The question

Current
[docs/FWD_BWD_MODEL_METHODOLOGY.md](../nsh/track-runner-virtual-dolly-cam/docs/FWD_BWD_MODEL_METHODOLOGY.md)
defines an asymmetric merge: each pass builds its own Hermite
raw_pred from seed geometry, then `_apply_blob_snap` queries a
residual-motion blob and applies three gates (proximity, direction,
motion-path) all reading only `raw_pred`. The observed outcome
follows the design: blob correction is a cautious local perturbation
on top of Hermite, not an independent estimator.

Operationally, phase 2 shows the perturbation layer is gate-dominated
on the intervals that need it most. On short or under-constrained
intervals, Hermite raw_pred can drift far enough that gates correctly
veto even a visually-on-runner blob. The kill-switch test
`test_delete_test_no_observer_equals_pure_hermite` is passing by
design, but in the bad-interval regime the operational reality is
close to that kill-switch: blob snap rarely fires, and the trajectory
is effectively pure Hermite.

The architectural question is not "is the path gate too strict?". It
is:

> Should blob be a weak correction to Hermite (current design), or
> should the blob-derived observation and the Hermite prior be
> treated as two separate measurements whose disagreement is modeled
> more explicitly?

### Constraints any redesign has to respect

From [docs/TRACK_RUNNER_CONTRACT.md](../nsh/track-runner-virtual-dolly-cam/docs/TRACK_RUNNER_CONTRACT.md):

- **C3** -- intervals are independent. No redesign may introduce
  cross-interval state.
- **C4** -- seeds are truth at solve time. Endpoints are never moved
  by blob evidence.
- **C5 (revised)** -- pass-local working state is allowed. FWD must
  not read BWD trajectory state and vice versa; neither may read
  fused/stitched output while solving. Agreement and uncertainty
  must be computed from the two independent pass trajectories, not
  from fused output. A separate output-only corrected track is
  explicitly allowed provided it is not used for FWD/BWD agreement
  scoring.
- **C6** -- no appearance-based identity evidence. Local patch
  correlation for short-horizon propagation is allowed; jersey-color
  and template matching are not.

From FWD_BWD_MODEL_METHODOLOGY.md invariants:

- Each pass's gates read only that pass's `raw_pred`.
- The residual cache holds raw image data only, never decisions.
- Agreement metrics come from raw FWD and BWD, not from fused.
- FWD and BWD must remain structurally independent -- their
  disagreement is the uncertainty probe scoring depends on.

Any redesign option must satisfy all of the above.

### What phase-3 data would inform the answer

A redesign is only worth doing if the data says the current design
is structurally inadequate, not just that a threshold is wrong. The
evidence we need:

1. From 3a (full sweep, bucketed by duration AND eligible-frame
   count): is short-interval failure systematic or a single-clip
   artifact? If only ~5% of intervals fail and they all cluster in
   the shortest bucket, a policy fix may be enough.
2. From 3c (prediction vs fused-track divergence): on failing
   intervals, is `median_div_h` small (prediction is fine, blob
   measurement is the problem) or large (Hermite is drifting)? That
   determines which half of the "blob and Hermite as two
   measurements" framing is the weak one.
3. From 3d (oracle injection): do the gates actually accept a
   perfect observer? If no, the gate logic itself is too strict
   under a correct measurement, independent of upstream quality.

### Possible architectural options (not committing to any)

These are listed in increasing scope. The right option depends on
phase-3 findings; many failure modes are fixable without any
architectural change.

- **Option A: short-interval policy only.** Below N eligible frames,
  disable blob snap (pure Hermite) OR widen the path gate alone (keep
  proximity and direction at current thresholds). Cheapest; respects
  every invariant. Appropriate only if phase-3 shows failures cluster
  in very short intervals and the current blob snap is net harmful
  there (i.e., prediction divergence is small enough that Hermite
  alone beats Hermite + noisy snap).
- **Option B: symmetric two-estimator merge, deferred to fusion.**
  Keep raw_pred and a "blob-only" per-frame estimate as two
  independent pass-local measurements. Gate-independent. Fuse them at
  `fuse_tracks` time using explicit disagreement modeling instead of
  a cautious compatibility test. Bigger change: `_apply_blob_snap`
  becomes a scoring and labelling layer, not a gating layer. Must
  keep pass-independence and the raw-cache rule.
- **Option B' (preferred sharpening of B): per-pass sequential update
  with frozen prior retained.** Each pass maintains three tracks:
  `prior_pred_{fwd,bwd}` (frozen Hermite, used for scoring and gate
  reference), `updated_pred_{fwd,bwd}` (sequentially nudged by
  accepted blobs within that pass only, damped toward the blob rather
  than hard-replaced), and `blended_path` (the output combination of
  the two `updated_pred`; conceptually the role currently served by
  `fused_track`). Invariants required for this to not regress:
  (1) update is damped, not replacement -- `updated[t] = prior[t] +
  k*(blob[t]-prior[t])` with k well under 1; (2) gates keep reading
  `prior_pred`, never `updated_pred`, so a drifted updated state
  cannot re-qualify noisy blobs; (3) scoring uses `prior_pred`
  disagreement, so both passes absorbing similar measurement evidence
  cannot quietly inflate agreement; (4) endpoints stay hard --
  `updated_pred` must still land exactly on the opposite seed (blend
  back toward terminal seed over final N frames, or do not update
  within N of endpoint); (5) no cross-pass reads -- FWD's
  `updated_pred` never informs BWD and vice versa. On-contract under
  revised C5. Directly addresses the failure mode phase 2 identified
  (stale Hermite drift on short intervals) while preserving the raw
  FWD/BWD uncertainty probe. Not mutually exclusive with Option A:
  A can gate when B' activates (e.g., only on short intervals).
- **Option C: improve the raw-pred model on short intervals.** No
  architectural shift; make the Hermite propagator better constrained
  on short spans (e.g., extend the directional-slope window to pull
  in more neighbors, add curvature estimation from more seeds,
  explicit short-span fallback to a linear interpolant with the
  chord). Respects every current invariant; the failure symptom
  becomes rarer because the gates' raw_pred input is better.
- **Option D: upstream blob-to-torso calibration.** If phase-3 shows
  blob centroid is a biased torso-center estimator (e.g., consistent
  offset due to leg motion), apply a scene-local correction when
  constructing the observation, not in the gates. Orthogonal to the
  architectural question.

None of A-D or B' reintroduces appearance cues or cross-pass state.
Options B and B' are the biggest design changes and the only ones
that meaningfully change the "merge model"; A, C, and D work within
the existing architecture. B' is preferred over B because it
explicitly separates the scoring reference (frozen prior) from the
output track (sequentially updated), which preserves the FWD/BWD
uncertainty probe that scoring depends on.

### Propagation-error guards (required for any B' prototype)

The central risk of sequential per-pass updating is that a single
wrong accepted blob poisons the rest of the interval. Any B'
prototype must bake in these guards before being compared to the
current model:

- **A. Keep the Hermite prior alive.** Do not overwrite it. Maintain
  `prior[t]` (frozen) and `updated[t]` (sequentially corrected) in
  parallel. Future prediction starts from `prior` and is nudged by
  accepted measurement history, so the prior keeps pulling the track
  back toward the seed-anchored structure.
- **B. Damped updates, not hard snaps.** Never set `state[t] =
  blob_measurement`. Use `state[t] = prior[t] + beta * (blob[t] -
  prior[t])` with `beta < 1`, smaller on uncertain frames. Hard
  replacement is the single biggest source of propagation blow-ups.
- **C. High-confidence-only updates.** For sequential updating,
  accepted blobs must be stricter than for one-frame snap: corridor
  winner clear, proximity strong, path strong, best/second-best mag
  ratio high. Ambiguous blobs do not update future state.
- **D. Bounded memory / decay.** One accepted blob must not equally
  influence the next 40 frames. Use a short memory window, decay
  factor, periodic reversion toward the Hermite prior, or reset on
  weak-evidence streaks.
- **E. Hard endpoint seeds.** Frame 0 and frame N remain fixed
  regardless of mid-interval updates. The pass must still land
  exactly on the opposite seed.
- **F. Gates read the frozen prior, not the updated state.** This is
  the explicit tradeoff: it preserves scoring purity and prevents a
  drifted updated state from re-qualifying noisy blobs, but it does
  limit how much sequential updating can rescue a drifting interval.
  Accept this tradeoff deliberately; revisit only if phase-3 data
  strongly indicates it is the dominant limiting factor.

### Prototype in isolation, do not replace solve

B' (and any other option) must be built first as an experimental
branch or standalone tool that:

- consumes the same seeds, interval boundaries, blobs, and FWD/BWD
  independence rules as the current solver,
- compares old vs new on the same interval universe (especially the
  failing short intervals, healthy intervals, and asymmetric FWD/BWD
  intervals identified in phase 3C),
- answers: did accept rate improve? did visual output improve? did
  obvious wrong cascades appear?
- does not touch the production solver until that comparison is in.

### Out of scope for this plan

- We are not committing to any of A-D or B'. Phase 4 is a decision
  point, not an execution plan.
- Phase 4 does not write production solver code. Its deliverables are
  (1) a short decision document in `docs/` summarizing phase-3
  findings and the chosen path forward, and (2) if the decision is
  to change something, a standalone prototype (not a solver edit)
  plus its comparison results against the current model.

### Trigger

Revisit phase 4 once experiment 3a, 3c, and 3d outputs exist for at
least the Conant clip. At that point we have the evidence needed to
choose among A, B', C, D (or to conclude no change is needed).
Expected read of the evidence: if 3c shows `median_div_h` is large on
failing intervals (Hermite drifting), B' is the strong candidate and
D is weak; if 3c shows `median_div_h` is small but blob-to-fused
distance is large, D is the strong candidate and B' alone will not
help. A is always available as a cheap gating layer regardless.

