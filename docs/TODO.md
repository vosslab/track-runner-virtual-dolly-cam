# TODO

Backlog scratchpad. Each topic gets its own section so entries can be
claimed, refined, or closed independently.

## Solver

### Dedicated worker module for interval-job queueing

Interval-job queueing logic is currently duplicated across several
call sites. Extract it into one worker module so additions and fixes
only have to land in one place.

## Seeding UI

### Combine YOLO-assist with the motion-cue residual map

Each signal alone is weak:

- YOLO misses small or occluded runners and picks up spectators.
- Motion cues highlight anything moving (other runners, crowd,
  camera shake).

Their intersection -- "person-shaped AND moving in a way consistent
with the predicted trajectory" -- should filter out most false
positives and surface the runner even when either signal alone would
fail.

Useful in the seed UI (to rank candidate boxes) and possibly as a
gated per-frame observation during propagation.

## GUI overlays

### Show motion heat map in the annotation window

Offer a toggle/overlay for the motion heat map inside the GUI so the
user can see moving objects directly while seeding or reviewing,
without having to run a separate diagnostic tool.
