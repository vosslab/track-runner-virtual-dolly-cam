# Pair-local encode evaluation

Status: fresh-solve evidence confirms that Orion still requires the documented
`target -> refine` loop; no promotion, crop, persistence, or geometry policy
change is warranted.

## Real-video method

The evaluation uses the existing tracked videos and fresh schema-15 solves. No
generated video, permanent E2E script, or synthetic fixture is involved. The
before encodes are the existing files in `TRACK_VIDEOS/`; the after encodes are
temporary review outputs under `/private/tmp` with the normal tracking overlay.

Both acceptance encodes first exercised the normal crop-safety check. It
reported four consecutive off-center frames for IMG_3830 starting at frame 189
and four for Orion 600 m starting at frame 236. The threshold is three. The
check was not weakened. The documented `--allow-offcenter-crop` override was
used only to create review outputs after recording each result.

## Results

| Video | Existing before evidence | Schema-15 after evidence | Verdict |
| --- | --- | --- | --- |
| IMG_3830 | The runner is framed in 19/20 fixed-stride samples; the remaining sample is an early pre-race frame. Archived center-jerk p95 is 4.743 px/frame. | The runner is framed in the same 19/20 samples; the same early pre-race sample contains no runner. Center-jerk p95 is 2.062 px/frame. | PASS: sampled containment is preserved and no catastrophic regression is visible. |
| Hononega-Orion_600m-IMG_3702 | The runner is framed in all 19 post-race fixed-stride samples; the first sample is pre-race. Archived center-jerk p95 is 2.693 px/frame. | At least five post-race samples are nearly uniform filler frames with no runner; center-jerk p95 is 3275.055 px/frame. | TARGET/REFINE REQUIRED: fresh solve alone is not this video's completed workflow. |

The before and after files use different configured output resolutions, so raw
pixel metrics are descriptive rather than an equality gate. The black Orion
frame and the multi-order-of-magnitude crop jump reject this fresh solve as a
final encode candidate; they do not establish a regression before the normal
target/refine loop has run.

The IMG_3830 comparison samples source-timeline frames 201 through 4020 at a
fixed 201-frame stride. Contact sheets were generated under `/private/tmp` from
the existing tracked encode and the schema-15 review encode. Both sets contain
the runner in the same 19 samples and omit the runner only in the first,
pre-race sample. The observed 19/20 result is recorded as behavioral evidence;
it is not used to invent a stricter containment requirement.

The Orion comparison samples frames 263 through 5260 at a fixed 263-frame
stride. Its before set contains the runner in every one of the 19 post-race
samples. Schema 15 produces nearly uniform filler with no runner at frames 2104,
3156, 3682, 3945, and 4471. Those five failures, not a percentage threshold,
establish that the fresh solve has weak intervals for target/refine.

## Lifecycle evidence

Stage 3 is the cheap analytical solve and is not expected to finish every
interval. Stage 4 improves as many eligible intervals as its fixed frame budget
permits. Target then exposes remaining weak intervals for human seeding before
refine. The five sampled Orion failures are already present in the existing
target ordering. Replaying each real interval through the existing Stage-4
walker removes its off-frame centers without fallback. One remains low
agreement, which correctly keeps it visible for human review.

This is the expected lifecycle, not a reason to gate on random intervals or to
change promotion: automatic solving is bounded, target supplies deterministic
criteria for the remaining work, and refine can spend the existing walker on
the intervals the user selects. User-authored `not_in_frame` truth remains
separate and authoritative runner absence.

## Gate interpretation

The plan's 95% and two-percentage-point visual criterion assumes that a fresh
solve is already a completed tracking cycle. Orion disproves that premise: its
fresh solve deliberately leaves weak intervals for the documented
`target -> refine` loop. Applying the numeric criterion here would make a
random sample of unfinished intervals into a new product requirement.

M15 therefore uses the simplest evidence tied to its regression-detection
objective. IMG_3830 preserves sampled containment. Orion is not presented as a
finished encode; its visible failures already enter target, and real-video
walker replays demonstrate that the existing refinement path handles the
sampled intervals. No final-output equivalence is claimed before human review.

## Separate-process mode evidence

A fresh Python invocation ran the production target artifact loader on the real
IMG_3830 video without supplying or reading `interval_scores.json`. It loaded
the schema-15 NPZ, reconstructed all 1,579 interval views, found 97 positive-risk
target candidates, and selected `(213, 225)` first. A separate real CLI
invocation of `refine` loaded the same 1,579 manifest entries and exited with
`all 1579 intervals already solved, nothing to do`. These checks exercise the
durable CLI boundary without adding a fixture or permanent E2E harness.
