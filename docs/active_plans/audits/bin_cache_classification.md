# Bin cache classification (WS2-cache)

Classification of each named solve cache by how its identity relates to the
analysis `bin_factor`, with code evidence. The production solve default now
bins the walker (default `bin_factor > 1`; 4K floor@1440 -> bin2). bin>1
lowers the analysis resolution, so per-frame candidate lattices and residual
blobs are extracted at processed scale and upscaled to SOURCE. The solved
SOURCE boxes are numerically correct but NOT identical to the bin=1 solve, so
a bin change must force recompute on every cache whose computation depends on
bin.

Classification key:

- (i) identity depends on bin: the cached value differs across bins; the key
  MUST include bin.
- (ii) computation depends on bin but output is SOURCE: the stored units are
  SOURCE yet the value was computed at processed scale, so reuse across bins
  serves wrong geometry; key on bin conservatively.
- (iii) safely reusable across bins: value is bin-independent; no key change.

## Classification table

| Cache | Class | Bin in key? | Evidence (file:line) |
| --- | --- | --- | --- |
| Interval / geometry fingerprint store (`solved_intervals` in `torso_box_coords.npz`) | i | YES | `track_runner/interval_fingerprint.py` `build_geometry_tag` / `compute_interval_fingerprint` (tag now embeds `bin<B>`); keyed by `track_runner/solve_queue.py:213` and `track_runner/solver_workers.py:163` |
| Camera-motion artifact (`*.camera_motion.npz`) | ii | YES | phase correlation runs on processed frames and dx/dy upscaled by bin: `track_runner/camera_motion.py:720-723, 800-803, 908-911`; identity/recompute now keyed via persisted `bin_factor` in `save_motion_cache` and the staleness check in `load_motion_cache` |
| Per-interval residual store (worker-local pre-pass dict) | iii (ephemeral) | n/a | in-memory dict, never persisted, scoped to one worker/one interval: `track_runner/residual_pre_pass.py:72-103` (`precompute_interval_residuals` returns `dict | None`, no disk write); reader's bin is fixed per run so no cross-bin reuse is possible |
| `torso_box_coords.npz` SOURCE box values | i (follows fingerprint) | YES (indirect) | stores SOURCE boxes that change with bin: `track_runner/state_io.py` `write_torso_box_coords` / `load_torso_box_coords`; regenerated because its producing intervals now recompute (their fingerprints carry bin); no separate key added |

## Why interval fingerprint must key on bin

`compute_interval_fingerprint` previously took NO `bin_factor` and its
docstring stated "changing `--bin` between runs reuses the interval store".
That was the bug: bin>1 changes the analysis resolution, so the walker's
solved SOURCE boxes are not numerically identical to bin=1. The M1 round-trip
proved bin=3 stores SOURCE-correct values, but correct is not the same as
identical-to-bin=1. `build_geometry_tag(bin_factor)` now appends `/bin<B>` so
a bin change yields a different cache key and recomputes.

## Why camera motion must key on bin

The phase-correlation estimator runs on PROCESSED frames; dx/dy are upscaled
by `bin_factor` to SOURCE before storage (`camera_motion.py:720-723` and
sibling blocks). The output units are SOURCE, but the computation depends on
bin (class ii). Identity was purely `motion_model` + `basename` + `frame_count`
before; `bin_factor` is now persisted in the artifact and `load_motion_cache`
returns None on a bin mismatch so a bin change recomputes. A legacy artifact
without a `bin_factor` key is treated as a bin=1 solve.

## Residual store: ephemeral, no key change

`precompute_interval_residuals` (`residual_pre_pass.py:72`) builds an in-memory
dict keyed by `(frame_index, roi)` and returns it; nothing writes it to disk.
It is owned by the worker that solves one interval and destroyed at worker exit
(contract C13: cache is never persisted or depended on between runs). The
worker's reader carries the run's `bin_factor`, so the residuals are always at
the current bin and cannot be reused across bins. No key change is required.

## Contract notes

- This is a cache-key (bookkeeping) change only. No `SCHEMA_VERSION` bump and
  no per-video config were added.
- One-time recompute: the first solve after upgrading to the binned default
  will not reuse any bin=1 interval store or bin=1 camera-motion artifact; it
  recomputes both at the new bin. This is the intended correctness behavior.
