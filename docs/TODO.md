# TODO

Backlog scratchpad. Each topic gets its own section so entries can be
claimed, refined, or closed independently.

## Solver

### Verify whether `MIN_BLOB_AREA` is a C2 violation or a denoising floor

`track_runner/residual_motion.py` defines `MIN_BLOB_AREA = 25` (pixels^2)
used by `extract_frame_blobs` to drop noise specks. The constant name
and its fixed pixel unit look like a runner-relative threshold, which
would violate
[docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) clause C2
("target size thresholds ... must be expressed in torso units").
However, C2 explicitly permits raw pixels for "low-level raster-kernel
sizes that are about the image, not the runner" -- and a speckle
denoising floor falls in that bucket. The correct classification
depends on evidence, not on the name of the constant.

Two possibilities:

- **Denoising floor (C2-allowed):** 25 px^2 is below the smallest
  legitimate runner blob (at `torso_h = 15` the torso blob is roughly
  50-150 px^2), so the filter only kills scattered compression /
  sensor speckle. In that case the constant is fine but poorly named
  and commented.
- **Runner-size threshold (C2 violation):** at `torso_h` roughly 10-15
  the filter cuts into real runner-component blobs. Then the fix is
  a torso-relative threshold with a raw-pixel raster floor below it.

Evidence-gathering step (cheap, ~10 minutes of work) decides which
regime applies. Do this **before** any code fix:

1. Pick an interval with a small, distant runner (for example the
   IMG_3629 clip or the Hononega-Varsity 4x400 clip; both have
   stretches at `torso_h ~ 15 px`).
2. Run `tools/check_interval_blob_funnel.py` on that interval with a
   small `--debug-print-blob-areas` addition (or instrument
   `extract_frame_blobs` ad hoc to log raw blob areas before the
   `MIN_BLOB_AREA` filter).
3. Count blobs in the 10-50 px^2 band and inspect them. Real runner
   components -> filter is biting -> C2 violation. Scattered speckles
   unrelated to the runner -> filter is denoising -> C2-allowed.

Follow-up actions depend on the evidence:

- **Denoising outcome:** no behavior change. Rename the constant (for
  example `SPECKLE_FLOOR_PX_SQ`) and rewrite the docstring to label it
  explicitly as a raster-kernel floor under C2's allowed bucket. No
  cache invalidation needed.
- **Runner-relative outcome:** implement a torso-aware threshold, for
  example:

  ```python
  def min_blob_area(torso_h: float) -> float:
  	# C2: area scales with torso_h**2 so the threshold must too
  	runner_relative = AREA_FRACTION_OF_TORSO_SQ * torso_h * torso_h
  	# raster-level speckle floor, permitted as raw pixels under C2
  	speckle_floor = MIN_BLOB_AREA_FLOOR_PX
  	return max(speckle_floor, runner_relative)
  ```

  Calibrate `AREA_FRACTION_OF_TORSO_SQ` so current behavior is
  recovered at a typical corpus torso height (avoids surprise
  regressions). This alters blob-extraction output and invalidates
  geometry caches, so per contract C9 and
  [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)
  bump `SCHEMA_VERSION` and log the rationale.

Do **not** jump straight to the formula-based fix without the evidence
step. If the constant is already in the denoising regime, shipping a
formula is a behavioral change for no gain and invalidates the
geometry cache needlessly.

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
