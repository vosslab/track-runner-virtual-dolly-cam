# Blob walk v2 implementation audit report

Audit date: 2026-06-10. Read-only audit; no behavior changes.

## Context

User asked for a deep dive into blob walk v2: suspected slight bug, blob
misinterpretation, blob-coordinate misuse, or a gate enforcing the wrong
model. Deliverable is an AUDIT REPORT (this document), separating proven
findings, likely findings, and open questions. No patch ladder, no M3
performance work, no byte-gate re-baselining, no docs close-out, no
experiment sequencing.

Audit basis: independent line-level read of all six
`track_runner/blob_walk/` modules by the main thread, three parallel
read-only explorations (spec docs, blob extraction side, dispatch
integration), cross-checked against the shipped amendment spec
(`docs/archive/windowed_path_selection_amendment.md`), the stall diagnosis
(`docs/active_plans/audits/fwd_zero_coverage_diagnosis.md`), and the m4 A/B
report (`docs/active_plans/reports/m4_walker_ab_report.md`). The
root-level `blob_walk_refine.md` (present in the repo, untracked) is an
OLDER, UNAPPROVED plan from a prior session: its phase structure, gates,
and trial ladder are NOT adopted here. Only its raw findings were used as
leads, and each was independently re-verified against current code before
inclusion (all file:line citations below are fresh checks). New findings
not in that document: P4, P6, P8-P10, P15, P17.

User constraint noted: some guards may be too strong; when improvements
come later, do not re-bake old less-ideal behavior into validation gates.
Flagged guards are marked [GUARD] below.

---

## PROVEN FINDINGS (verifiable in code/docs today)

### Viterbi scoring

- **P1. Evidence term uses raw `integrated_mag`, spec requires normalized
  confidence.** Spec (amendment section 3): "subtract a small term scaled
  by `confidence`" -- a defined [0,1] quantity
  (`total_score = 0.5*strength + 0.5*proximity`,
  `residual_motion.py:879-893`, strength normalizer 10000.0 at :880).
  Code: `WEIGHT_EVIDENCE(-0.05) * integrated_mag` with raw pixel-sum
  magnitudes in the thousands (`walk_viterbi.py:38,93-96,113-114`;
  `integrated_mag = sum(DoG mag over component)` `residual_motion.py:288`).
  Scale: evidence approx -135..-500 per node vs displacement cost <= ~0.8
  and SKIP_COST 2.0. Static scale strongly predicts selection will be
  dominated by raw blob magnitude wherever the displacement cap permits;
  the "small tie-break" became the largest term in the cost. Whether real
  corpus paths are actually flipped by this is claim B (unverified).
- **P2. Specified trajectory-consistency terms never implemented.**
  Amendment section 3 specifies velocity-magnitude variance and angle
  variance costs ("computed only when the path has at least 3 nodes") with
  YAML-resident weights. `WEIGHT_MAG_VAR = 0.5` and `WEIGHT_ANGLE_VAR =
  0.3` are defined (`walk_viterbi.py:34-36`) and referenced NOWHERE in the
  repo (grep-verified). The walker's stated reason to exist -- "only
  window-level trajectory consistency reliably identifies the runner"
  (TRACK_RUNNER_DESIGN.md) -- is mostly absent from the cost: what remains
  is a displacement cap, a linear displacement cost, and the oversized
  evidence term (P1). The DP therefore answers "which blob is strongest
  per frame, within a loose displacement check" rather than the spec's
  "which blob SEQUENCE forms the most plausible runner path."
  INTERPRETATION CAUTION (what the missing terms would protect against is
  NOT proven): the classic failure model for these terms is limb
  switching, but the DoG band-pass is tuned to torso scale and likely
  merges limb motion into one broad runner-body blob. The realistic
  failure mode is then WITHIN-BODY centroid jitter: the selected blob
  stays on the runner while its centroid jumps between the top and bottom
  of the torso region depending on which body part has the strongest
  residual that frame. That is a softer problem than identity switching
  (right object, unstable point on it), and the right corrective term --
  if any -- might be a centroid-stability / vertical-jitter penalty
  rather than the spec's full angle-consistency model. Which failure mode
  actually occurs must be measured before any consistency term is added
  (claims D and E in the assumption table).
- **P3. Weights hardcoded; spec requires YAML residence.** Amendment
  section 3: "All weights live in overlay_styles.yaml or a sibling YAML."
  All five constants are module literals (`walk_viterbi.py:32-41`).
- **P4. Skip semantics deviate from spec in two ways.**
  (a) Any transition touching a skip node costs flat `SKIP_COST` with no
  geometric check, and the displacement cap never scales across skipped
  frames (`walk_viterbi.py:191-192`) -- a path may "teleport" across a
  skip (exploitation frequency: open Q4).
  (b) A skip frame is double-charged: the local skip-node cost adds
  SKIP_COST once per skip frame (`walk_viterbi.py:109` in the DP loop;
  `:93` at frame-0 init) and the transition into the skip node adds
  SKIP_COST again (`walk_viterbi.py:191-192`), ~= 4.0/frame vs the spec's
  single "fixed skip cost"; combined with P1's -135..-500 real-node bonus,
  skip is essentially never chosen when any in-box candidate exists.
- **P5. Bootstrap slack inflates the cap on every step.** [GUARD]
  `max_jump_px = (MAX_RUNNER_SPEED_W_PER_S/fps + BOOTSTRAP_UNCERTAINTY_W)
  * torso_w` (`walk_viterbi.py:72-74`). BOOTSTRAP_UNCERTAINTY_W (0.30) is
  documented as bootstrap-only search slack (`walk_motion_gate.py:30-34`)
  but is applied to every transition: at 60 fps the per-frame cap is 0.8 W
  instead of 0.5 W (+60%) everywhere.

### Emission timing and status model

- **P6. Steady-state emission makes `interpolated` and `extrapolated`
  structurally unreachable.** New finding. The walker emits the OLDEST
  window frame (offset 0), one per advance (`walk_walker.py:1063-1082`,
  `emit_count=1`). `emit_status_from_path` classifies a skip frame as
  interpolated/extrapolated only relative to accepted frames WITHIN the
  current window (`walk_status.py:88-125`); offset 0 can never have an
  in-window accept before it, so an emitted skip frame is always
  `soft_miss_no_path` (or `soft_miss_no_blob`), with position emitted as a
  hold of the pre-window last-accepted anchor (`walk_status.py:119,123`).
  Proof trace: steady state calls `_run_viterbi_and_emit_oldest` with
  `emit_count=1` (`walk_walker.py:1077`), which emits `results[0]`, the
  frame at window offset t=0 (`walk_walker.py:371-374`). For a skip frame,
  `interpolated` requires one accepted offset BEFORE t and one AFTER t
  (`walk_status.py:101`); `extrapolated` requires an accepted offset
  before t (`walk_status.py:110`). No offset ta < 0 exists, so prev_accept is
  always None at t=0 and control falls to the no-prior-accept branch
  (`walk_status.py:120-125`, `soft_miss_no_path`). Steady-state emitted
  statuses are therefore exactly {accepted, soft_miss_no_blob,
  soft_miss_no_path}. Interpolated/extrapolated can only appear during the
  end-of-walk flush (`emit_count=len(buffer)`, `walk_walker.py:1089-1108`),
  where t > 0 frames are emitted. Consequence: the
  spec's gap-interpolation behavior (section 4) effectively never runs
  during the walk; every steady-state miss freezes position at the stale
  anchor. This also corrupts the diagnostic meaning of
  `interpolated_fraction`/`extrapolated_fraction` (amendment section 6).
- **P7. Emission at oldest frame, spec says center.** Amendment section 2:
  decision emitted at offset `(N-1)//2 = 4`; section 5 bookend flush of
  "(N-1)//2 decisions" confirms. Code emits offset 0 and flushes the whole
  buffer. Effects: each decision is made with zero past context inside the
  window (all 8 context frames are future), and the accepted-anchor update
  lags further (see P8). P6 is a direct corollary of this choice.
- **P8. Acceptance-box anchor is stale by at least the window depth.** New
  finding. The per-frame prediction is `pred = last_accepted` where
  last_accepted updates only when an EMITTED (oldest) frame is accepted
  (`walk_walker.py:1036-1039`, `:377-379`). In steady state the frame
  being observed is 9 frames ahead of the newest possibly-accepted emitted
  frame, so the +-0.5W x +-0.75H acceptance box (`walk_walker.py:628-631`)
  is centered on geometry at least ~9 frames old. The staleness itself is
  proven by code. Its EFFECT is conditional: this is a structural
  starvation mechanism WHEN image-space drift over ~9 frames (after
  camera panning and subject distance are accounted for; image drift can
  be far smaller than physical motion) plus the blob-centroid offset (L4)
  exceeds the box -- not proof that ordinary runner motion always exits
  the box. The named stall intervals are near-stationary in image space,
  so there the operative term is the centroid offset, not motion.
  Quantified by the anchor-lag telemetry (claims F/I).
- **P9. Extrapolated position is a hold, spec says linear extension of the
  last two accepts** (`walk_status.py:114-116` "Simple hold" vs amendment
  section 4). Reachable only in flush (P6). Related minor: the
  `consec_extrap` counter resets on an intervening `soft_miss_no_blob`
  (`walk_status.py:85`), so EXTRAP_MAX=2 demotion can be defeated by
  alternating empty frames.
- **P10. Bootstrap observation counts as an "accept" and can mask the
  pure-stall Hermite fallback.** New finding. Bootstrap status is
  `accepted` iff `obs is not None` (`walk_walker.py:901-904`), increments
  `status_counts["accepted"]`, and lands in `WalkSummary.accepted_count`.
  The Stage-4 fallback fires only when a pass's accepted count is zero
  (`interval_solver.py:551-552`). A walk that observes ANY winner blob at
  the seed frame but then stalls for the whole interval reports
  accepted_count=1, skips the fallback, and ships a path frozen at the
  seed -- strictly worse than Hermite. Also a doc mismatch:
  FWD_BWD_MODEL_METHODOLOGY says "the walker produces no output at the
  seed endpoints" (geometry is later pinned by the bundle, so only the
  COUNT leaks). The diagnosed stall cases happened to have bootstrap=None
  (so fallback fired); the masking variant is unmeasured (open Q6).

### Gates and candidate supply

- **P11. The candidate gate excludes raw blobs before Viterbi sees them.**
  [GUARD] Proven code behavior: `corridor_blobs` is a misnomer -- the
  corridor filter was removed (2026-05-28) and the list is raw blobs
  filtered to the ACCEPTANCE BOX +-0.5W x +-0.75H around the (stale, P8)
  last-accepted anchor (`residual_motion.py:1310-1346`;
  `walk_walker.py:628-639`). The Viterbi lattice only ever contains blobs
  within half a torso of that anchor; window-level reasoning cannot
  consider candidates the per-frame box already excluded. The amendment's
  objective was "make extraction return every geometric-ROI-passing blob;
  defer winner choice to the walker" -- so per-frame locality gating
  surviving the windowed redesign is a proven spec deviation.
  INTERPRETATION (likely, not proven): that this gate excludes VALID
  runner blobs is claim A and must be visually verified by overlays
  before any gate change. Supporting but indirect: the extraction audit
  (L4) puts documented runner-centroid offsets (0.5-0.75 torso heights
  from torso center) exactly at the box's half-height (0.75 H), so the
  runner's own blobs would sit at the gate edge.
- **P12. Stride > 1 breaks stepping and termination.** New finding,
  latent. The walker steps `dt = stride` frames per step
  (`walk_walker.py:1012,1019`) where `stride = max(1, round(fps/60))`
  (`residual_motion.py:471`, REFERENCE_FPS=60). For >=90 fps sources
  (stride 2+): intermediate frames are never observed, and the termination
  test `frame_f == neighbor_seed_frame` (`walk_walker.py:1027`) misses
  whenever the interval span is not divisible by stride; the walk then
  continues PAST the neighbor seed until the loop guard
  (`max_steps_guard = span+1` STEPS, i.e. up to ~stride x span frames of
  overrun) observing frames outside its interval. Benign at 60 fps
  (stride 1). Corpus fps distribution: open Q7.
- **P13. Dead superseded velocity-gate code.** `walk_motion_gate.evaluate`
  and the three-cap-min constants implement the SUPERSEDED per-frame gate
  spec; production imports only the two envelope constants
  (`walk_viterbi.py:72-74`). `evaluate()` is kept alive by its tests only.
  Identify-and-retire is cleanup, deferred per scope.
- **P14. `seed_w` is frozen for the whole walk** in the Viterbi cap and
  acceptance-box geometry; `size_at_frame` interpolation sizes only the
  emitted boxes (`walk_walker.py` passes scalar seed_w/seed_h everywhere).
  On intervals with large scale change, cap and box are wrong near the far
  seed.

### Telemetry / doc mismatches (measurement trust)

- **P15. `path_cost` column lies about its own meaning.** New finding.
  Header doc: "Viterbi DP cost contribution at this frame"
  (`walk_debug_log.py:112-114`). Writer stamps the SAME whole-window total
  on every emitted row (`walk_walker.py:356,476`). Spec section 7 also
  requires `path_step_cost` and `window_head_frame` columns (absent) and a
  `chosen_blob_index` semantic change (column absent). Any cost analysis
  built on the current CSV would be wrong; fix-and-bump is the one
  telemetry prerequisite for the open questions below.
- **P16. Pre-pass store built per promoted interval and never consumed by
  the walker.** Built at `interval_solver.py:497-507` from Hermite raw_pred
  ROIs; deferral documented at `walker_bundle.py:434-439`; the walker's
  observe call passes no `precomputed_store` (`walk_walker.py:665-679`)
  and its anchor-ROI keys could not match the Hermite-ROI keys anyway.
  Stated as fact only (performance work excluded from this audit's scope).
- **P17. Over-broad cache-bypass guard.** [GUARD] New finding.
  `overrides_in_use = dog_diameter_override is not None or acceptance_box
  is not None` bypasses residual-cache read AND write
  (`residual_motion.py:1222-1225,1242,1301`). The bypass is justified only
  for the DoG diameter (cached dog_residual/blobs depend on it); the
  acceptance-box filter is applied AFTER the cache fetch, so its inclusion
  in the bypass forces recomputation without correctness benefit. The
  walker always passes both overrides, so every walker observation
  recomputes residual+DoG+extraction (raw frame reads are still shared via
  the nested `_frames` cache). Example of a guard stronger than its
  rationale.

### Clean areas (negative findings)

- **N1. Coordinate handling is CLEAN.** Blobs extracted ROI-local, ROI
  origin added back once (`residual_motion.py:1290-1294`), candidates are
  PROCESSED full-frame; single PROCESSED->SOURCE conversion at the
  observe exit applies only to `BlobObservation.center_pixel`, which the
  walker does not consume; typed coord primitives guard the boundary; ROI
  clamp is double-bounded with no off-by-one; ROI quantization is
  intentional. No space mixing found anywhere in the walker path.
- **N2. No temporal-direction bias in blobs.** The residual is the
  absolute difference of the center frame against the MEDIAN of symmetric
  +-4-stride neighbors (`residual_motion.py:494-499,648-660`); unsigned and
  time-symmetric, so FWD and BWD passes see the same evidence with no
  lead/lag. Consistent with the near-equal FWD 42.3% / BWD 41.0% baseline.
- **N3. Integration is clean.** FWD/BWD bundles symmetric (correct seed
  roles, per-pass torso width); blob_pass threads identically through the
  worker pool (commit 2cb1a8e) and in-process paths; seeds pinned on
  output by the bundle; no decision-shaped state crosses passes or
  intervals (C9/C6 hold); pre-pass store holds image data only.
- **N4. Blob interpretation note (not a bug).** Centroids are GEOMETRIC
  centroids of thresholded DoG motion components
  (`residual_motion.py:276-290`) -- centers of motion energy, not torso
  centers. The documented 0.5-0.75 H offsets (L4) are inherent to the
  signal; consumers must treat blob position as "on the runner," not "at
  the torso center." Relevant when judging P11's box geometry.

### Minor C2 stragglers

- `MIN_BLOB_AREA = 25 px^2` fixed pixel threshold with an unresolved C2
  TODO (`residual_motion.py:47-52`); `roi_pad = max(20, seed_w)` raw-pixel
  floor (`walk_walker.py:644`); strength normalizer 10000.0 undocumented
  (`residual_motion.py:880,1433`).

---

## LIKELY FINDINGS (supported by repo measurements, not re-measured here)

- **L1. Candidate starvation is the stall mechanism.** Stall diagnosis
  (`docs/active_plans/audits/fwd_zero_coverage_diagnosis.md`): on Conant
  1080-1111 FWD and Jason 564-583 FWD, raw blobs extract nearly every
  frame (integrated_mag ~2700-3700) and ALL reject as
  `acceptance_box_empty` against the frozen-anchor box; both intervals
  near-stationary (0.74 / 0.20 px/frame), so the mechanism is exclusion
  geometry, not speed. Code mechanisms P8 + P11 fully account for this.
- **L2. Walker quality distribution at current weights.** m4 held-out-seed
  A/B: rescued 6 / preserved 15 / regressed 35 / needs_review 2 of 58
  evaluated passes, "at current Viterbi cost weights"
  (`docs/active_plans/reports/m4_walker_ab_report.md`).
- **L3. Corrected baseline and metric authority.** The comparison baseline
  is the SHIPPED walker: accepted_fraction 42.3% FWD / 41.0% BWD
  (24-corpus amendment closure; 38.7%/39.1% on the 120-corpus). The
  19.7%/9.6% figures are PRE-walker history and must not be used.
  Authoritative quality metric: held-out-seed distance (the m4 report
  documents FWD/BWD agreement as structurally biased); accepted_fraction,
  drift, reversal rate are diagnostic only.
- **L4. Blob centroids sit on the body 0.5-0.75 torso heights from torso
  center** (2026-06-02 extraction audit), exactly at the 0.75 H acceptance
  half-height -- the runner's own blobs ride the gate edge (with P11).

---

## ASSUMPTION AUDIT (no behavior changes; trials blocked until claims move to proven)

The proven sections above state CODE BEHAVIOR. Every claim about QUALITY
IMPACT or failure MECHANISM is listed here with an evidence-strength
label and the smallest validation step that would move it to proven.
Labels: proven-by-code, proven-by-docs, proven-by-measurement,
likely-but-unverified, speculative.

| Claim | Status | Evidence needed (smallest step) | Behavior change blocked by it |
| --- | --- | --- | --- |
| A. Rejected blobs are the runner's blobs | likely-but-unverified (inferred from L4 offsets) | Overlay rejected centroids vs seed/Hermite reference on Conant 1080-1111 and Jason 564-583 | Any candidate-supply widening (acceptance-box change) |
| B. Evidence term dominates selection on REAL paths | proven-by-code at static scale (P1); real-path effect likely-but-unverified | Per-term per-node cost telemetry on a corpus walk (requires P15 fix first) | Evidence-term normalization trial |
| C. A/B regressions are ranking-driven (vs starvation-driven) | speculative | Split m4 regressed bucket by empty-lattice fraction from existing debug logs | Ordering of cost trial vs box trial |
| D. DoG blur merges limbs into one body blob (no limb switching) | likely-but-unverified | Visual blob tiles on the named intervals: count distinct simultaneous runner blobs | Interpretation of P2; any consistency-term design |
| E. Top/bottom within-body centroid jitter exists | speculative | Normalized vertical trace of selected blobs: (cy_blob - torso_cy)/torso_h per frame; its frame-to-frame delta; correlation with integrated_mag | Any centroid-stability / vertical-jitter penalty |
| F. Oldest-frame emission contributes meaningfully to quality loss | proven-by-docs as spec deviation (P7); proven-by-code that interpolated/extrapolated are unreachable steady-state (P6); QUALITY impact speculative | Effective anchor-lag distribution (frame distance gather-to-anchor-update) per pass | Center-emission redesign (stays out of scope regardless) |
| G. Linear extrapolation beats hold | proven-by-docs as spec deviation (P9); benefit speculative | Miss-run trace comparison on existing logs (hold vs linear, offline replay) | Extrapolation change |
| H. Soft scoring should replace hard acceptance-box exclusion | speculative (design choice) | Blocked by A + C outcomes first | Any gate redesign |
| I. Anchor staleness (P8) is the operative starvation path beyond bootstrap failure | proven-by-code as mechanism; share of real stalls unverified (L1 cases had bootstrap=None) | Same anchor-lag telemetry as F, plus per-reject anchor age | Anchor-advance change |
| J. Bootstrap-accept masks the Hermite fallback in practice | proven-by-code as possible (P10); incidence unknown | Count passes with accepted_count == 1 where the only accept is the bootstrap frame (existing logs) | Fallback-gate change |
| K. Stride > 1 stepping/termination bug is exercised | proven-by-code as latent (P12) | Probe fps of `data/outdoor_corpus.txt` videos; any >= 90 fps makes it live | Stepping fix priority |
| L. Teleport-across-skip hole (P4a) is exercised | proven-by-code as possible | Identity-jump count (per-step drift > pooled P99 + 0.3 W) on a corpus walk | Skip-cap change |

Telemetry note: per the within-body jitter framing, direction-reversal
rate is likely a WEAKER diagnostic than the normalized-cy trace; the
useful measurements are vertical centroid position within the torso box,
its frame-to-frame change, top/bottom alternation, correlation with
integrated_mag, and whether normalized evidence reduces those jumps.
The only code change any of this requires is the P15 telemetry fix
(truthful per-frame cost columns), which is decision-neutral by
construction and gated by field-wise decision equality on baseline cases.

---

## Status

- Published 2026-06-10. This report is the evidence base that gates any future behavior change; all behavior changes remain blocked per the assumption table.
- The root-level `blob_walk_refine.md` is an older, unapproved plan superseded by this report for the audit portion.
