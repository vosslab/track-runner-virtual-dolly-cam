# Roadmap

Planned work, priorities, and what is intentionally not started.

## Planned

### Stage 2 race-start refinement (deactivated; needs redesign)

Production currently picks `race_start_frame` as the deterministic
midpoint of Stage 1's seed-to-seed interval. The original Stage 2
velocity-onset detector
([race_phases.py](../track_runner/race_phases.py)
`detect_race_start`) is preserved but not called -- it required a
45-frame trailing baseline window that does not fit short Stage 1
intervals, and produced None on ambiguous velocity profiles.

A reworked Stage 2 should refine race_start_frame inside Stage 1's
interval to sub-seed precision using the motion-cue heat map
([residual_motion.py](../track_runner/residual_motion.py)
`compute_residual_for_frame`, 9-frame aligned-background window) --
not a 45-frame velocity baseline. Full redesign brief in
[TODO.md](TODO.md) "Stage 2 race-start refinement".

### Detect race end frame during solve

The solver should automatically identify when the race ends (runner crosses finish
or stops). Currently post-race seeds are handled ad hoc. Recording race end timing
in the diagnostics would complement the existing race start detection.

Detection approach: use the target runner's blended-interval-path velocity in scene coordinates.
- End: sustained drop to near-zero velocity or plateau in along-track progress
- Must use track-specific velocity, not global cues

Depends on: race start detection (implemented in `race_phases.py`).

### Motion-cue seed recommendation (advisory)

Use the residual motion diagnostic's blob tracking as an advisory seed
recommendation tool. For low-confidence intervals, identify frames where the
motion cue is strong and the solver's geometric interpolation is weak. Report
as "review candidates" -- frames the user should inspect and potentially
re-seed. Strictly advisory: the user decides identity.

Depends on: race start/end detection (above).

## In progress

### Race start detection (implemented)

Race start detection is implemented in `track_runner/race_phases.py`. Uses
scene-coordinate velocity with ratio-based transition detection from the solved
trajectory. Integrated after `refine_with_motion_cues()` in the solve pipeline.
Result serialized in diagnostics JSON.

Remaining work: parameter tuning on real videos, downstream integration with
seed recommendation and crop trajectory.

### Residual-motion blob observation (measurement pipeline shipped; consumer is the windowed walker)

The measurement pipeline in [residual_motion.py](../track_runner/residual_motion.py)
(`observe_blob_at`) is shipped and stable. The Stage 4 hot-path optimization
(pre-worker per-interval sequential pre-pass in
[residual_pre_pass.py](../track_runner/residual_pre_pass.py)) is also shipped;
`observe_blob_at` reads from the precomputed store on hit.

The v1 per-frame blob-snap consumer inside the propagator (`_apply_blob_snap`)
has been removed. The current consumer is the windowed Viterbi walker
(`track_runner/blob_walk/`), which is the DEFAULT blob pass on Stage-4-promoted
intervals (and Stage-5 `--full`). The `--walker-stage4` flag was removed; the
walker is on by default. A zero-accepted-frame Hermite fallback is in place so
the walker never degrades a promoted interval below pure Hermite.

Remaining work: bootstrap-stall root-cause fix, Viterbi weight tuning, and a
promoted-only A/B validation. See [CHANGELOG.md](CHANGELOG.md) for the landing
history.

## Not started

### Real-time preview during seeding

Show a live crop preview while placing seeds so the user can judge quality before
running a full solve.

### Multi-runner tracking

Track multiple runners simultaneously for relay or multi-athlete videos. Currently
the solver tracks one runner per video.
