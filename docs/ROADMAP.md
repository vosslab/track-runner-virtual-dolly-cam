# Roadmap

Planned work, priorities, and what is intentionally not started.

## Planned

### Detect race start and end frames during solve

The solver should automatically identify when the race starts (gun/motion onset)
and when it ends (runner crosses finish or stops). Currently, stationary pre-race
and post-race seeds are handled ad hoc -- the frame sampler in the motion diagnostic
skips them, and confidence decays in those regions without context for why.

Recording race timing in the diagnostics or config would:
- Provide a hard boundary for motion detection: only meaningful for the target
  runner within this window, replacing the current heuristic gap-displacement checks
- Let the motion diagnostic and future auto-seed system skip pre/post-race frames
  by definition, not by guessing whether the runner is moving
- Let the crop trajectory hold a static frame before/after the race
- Improve seed suggestion targeting (only suggest seeds in the active race window)
- Simplify the frame sampler: one range check instead of per-gap motion heuristics
- Decouple "where is the runner" from "is motion detection valid here" -- currently
  the system tries to infer both at once, which is too much coupling

Storage structure (extensible):
```yaml
race_window:
  start_frame: 1023
  end_frame: 13850
  start_confidence: 0.9
  end_confidence: 0.7
```

Detection approach: use the target runner's fused track velocity, not global motion.
- Start: sustained velocity increase after a low-velocity period, persisting for
  5-10 frames to avoid triggering on jitter
- End: sustained drop to near-zero velocity or plateau in along-track progress
- Must use track-specific velocity, not global cues, because other runners or
  officials may be moving before the target runner starts

### Motion-cue seed recommendation (advisory)

Use the residual motion diagnostic's blob tracking as an advisory seed
recommendation tool. For low-confidence intervals, identify frames where the
motion cue is strong and the solver's geometric interpolation is weak. Report
as "review candidates" -- frames the user should inspect and potentially
re-seed. Strictly advisory: the user decides identity.

Depends on: race start/end detection (above).

## In progress

### Per-frame motion-cue observation fusion (implemented)

Residual motion blob tracking is now integrated as a per-frame center-position
observation channel inside the Hermite kinematic scaffold. Implemented in
`track_runner/residual_motion.py`, called between `stitch_trajectories()` and
`anchor_to_seeds()` in the solve pipeline.

Hermite owns geometry (path shape, size, continuity). Blob owns center
observation only. Two-tier acceptance gate with temporal continuity as primary
identity defense. Anisotropic correction: cross-track tighter, along-track
looser and downweighted.

Remaining work: parameter tuning on real videos, user-pain metric validation
(intervals flagged for review, seeds needed). See
[docs/active_plans/MOTION_CUE_OBSERVATION_FUSION.md](active_plans/MOTION_CUE_OBSERVATION_FUSION.md).

## Not started

### Real-time preview during seeding

Show a live crop preview while placing seeds so the user can judge quality before
running a full solve.

### Multi-runner tracking

Track multiple runners simultaneously for relay or multi-athlete videos. Currently
the solver tracks one runner per video.
