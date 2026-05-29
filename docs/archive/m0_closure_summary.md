# M0 closure summary

## Verdict

M0 closes with the conclusion that the dominant failure was NOT heat-map collapse. It was a
combination of (a) over-gated extraction and an observable-contract mismatch and (b) walker
control flow that killed intervals after a few early rejects or misses. Both have been fixed.
The authoritative artifact is the #70 render of `walk.html`,
where every walk across the 24-interval random corpus now reaches the neighbor seed.

## Evidence

Authoritative artifact: `walk.html`
(render #70). Corpus: 24 random intervals, seed 42, four per video across six outdoor 400 m
videos (Conant, Jason, Lyra-Hersey, Lyra-Wheeling, IMG_3823, IMG_3830). All 24 intervals times
two directions equals 48 walks, and all 48 reach `stop_reason=hit_neighbor_seed`.

| Metric                              | Value |
| ---                                 | ---   |
| Walks reaching neighbor seed        | 48 / 48 |
| FWD aggregate accepted              | 132   |
| BWD aggregate accepted              | 49    |
| `gate_reject_cap` stops             | 0     |
| `miss_cap_no_blob` stops            | 0     |
| Spurious `after_walk_terminated` rows | 0   |
| Step-1 `rejected_motion_gate` rows  | 6 (continued via soft-miss) |
| `RuntimeError` from `frame_reader`  | 0     |

## Hard failure fixed

- Walker control flow: `REJECT_CAP` and `MISS_CAP` no longer terminate the walk. Rejected and
  missed frames record status and the walker continues to the next frame. Per plan WP-1C.
- Bootstrap-mode gate: step 1 uses `bootstrap_search_radius_w = MAX_RUNNER_SPEED_W_PER_S /
  source_fps + BOOTSTRAP_UNCERTAINTY_W` instead of the tracking-mode three-cap-min with
  `v_recent=0`. Per plan WP-1A.
- Reference: changes to [walk_walker.py](../../tools/blob_walk_v2/walk_walker.py)
  landed 2026-05-28.

## Remaining limitation

- Zero-residual and weak-residual frames produce few accepted observations. The walker now
  completes to the neighbor seed but with low `accepted_fraction` on intervals where the
  runner is moving directly toward or away from the camera; motion residual approaches zero
  by physical design, not by pipeline defect.
- Affected intervals in the 24-corpus: Jason 12878-12925 (0/0), Lyra-Wheeling 1131-1196 (0/0),
  Lyra-Wheeling 1196-1319 (1/0), Lyra-Wheeling 22148-22243 (1/0), IMG_3830 various (1-2 each).
- This is not a walker bug. A future observable extension (for example, a size-change cue for
  radial motion) is M2 or M3 territory, not M0.

## Performance

- Lyra-Wheeling HEVC random-access decode costs about 60 s per 65-frame walk on Strategy-1
  `cv2.VideoCapture` seek. Corpus re-render took 2.5 hours wall-clock.
- This is a performance issue, not a correctness issue. Tracked as task #72 (HEVC caching and
  keyframe-aligned access).

## Language correction

- The no-null fix in [frame_reader.py](../../common_tools/frame_reader.py) (#69)
  did NOT explain zero residual. After the #69 fix, the 24-corpus dump completed without any
  `RuntimeError`, confirming all 13 zero-residual frames decode successfully. Zero residual
  means no usable motion signal at that frame (the runner is not producing image-plane
  motion), not a decode failure.
- Earlier framing that classified the dominant failure as `no_residual = heat-map collapse`
  or as `HEVC seek failure` was wrong. The classifier-bypass bug in
  [replay_step1.py](../../tools/blob_walk_v2/replay_step1.py) had
  hard-coded `no_raw_blobs` over the untestable branch, masking the actual `reject_reason`
  `acceptance_box_empty`. The 5-bin reframe and the no-null `read_frame` fix were valuable
  as cleanup, but neither was the root cause.

## What M0 does NOT claim

- The walker is not yet claimed accurate. Walks reach the neighbor seed; per-frame accuracy
  and trajectory quality are M1 and M3 work.
- `accepted_fraction` varies widely across intervals. The walker exposes quality differences
  but does not yet score them.

## Handoff to M1 and M3

- Quality scoring of completed walks: task #71 (windowed velocity plausibility,
  `accepted_fraction`, `longest_no_accept_streak`, FWD/BWD agreement, no-signal
  classification).
- Performance: task #72 (HEVC caching).
- Plan-level: M0 milestone closed. M1 quality evaluation is the next active milestone.

## References

- Plan: `/Users/vosslab/.claude/plans/sequential-soaring-hopper.md` (outside repo)
- Walk artifact: `walk.html`
- Walker source: [walk_walker.py](../../tools/blob_walk_v2/walk_walker.py)
- Bootstrap radius: [walk_motion_gate.py](../../tools/blob_walk_v2/walk_motion_gate.py)
  (`bootstrap_search_radius_w()`)
- `frame_reader` fix: [frame_reader.py](../../common_tools/frame_reader.py)
- 5-bin reframe (superseded): `REPLAY_REPORT.md`
- Audits: `NO_RAW_BLOBS_VERIFICATION.md`,
  `ZERO_RESIDUAL_INVESTIGATION.md`,
  `FRAME_READER_NULL_AUDIT.md`

## 2026-05-29: M3 closure note

The "M2/M3 work" referenced above as pending has landed against the windowed
path-selection walker. The amendment closed 2026-05-29 with the #85 24-corpus
rerun (FWD 42.3%, BWD 41.0%) and the #95 v2 120-corpus rerun (FWD 38.7%, BWD
39.1% across 61 valid intervals), both clearing the amendment Section 10 bars
(FWD >= 34.7%, BWD >= 24.6%). Auto-bin re-enabled per the #100 fix gives a
~5x wall-clock speedup on 4K HEVC sources. The amendment is now archived at
[windowed_path_selection_amendment.md](windowed_path_selection_amendment.md);
the extraction re-scope follow-ups doc is archived at
[extraction_rescope_followups.md](extraction_rescope_followups.md).
The full review package lives at `dump_step1/REVIEW_PACKAGE/`. Remaining open
followup is #101 (Lyra-Wheeling degenerate-ROI filter/clamp divergence).
