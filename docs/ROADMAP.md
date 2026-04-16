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

### Motion-based automatic seed generation

Use the residual motion diagnostic's blob tracking (corridor filtering + temporal
consistency) to place automatic anchor points between manual seeds. This would
reduce the number of manual seeds required, especially in long inter-seed gaps
where solver confidence is low.

Depends on: race start/end detection (above), gate 1 passing on the motion diagnostic.

## Not started

### Real-time preview during seeding

Show a live crop preview while placing seeds so the user can judge quality before
running a full solve.

### Multi-runner tracking

Track multiple runners simultaneously for relay or multi-athlete videos. Currently
the solver tracks one runner per video.
