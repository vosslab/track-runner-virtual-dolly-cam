# tr_config files

Reference for every file the track runner persists under `tr_config/`.
One row per video, plus a single global default. This doc covers the
on-disk schema, the reader/writer functions, the lifecycle and
invalidation rules, and the two things that most often surprise a
first-time reader: why a `seeds.json` can be 20 MB, and where the
global camera-motion track lives on disk.

## Purpose of tr_config

`tr_config/` is the project's state directory. Each file is keyed by
the source video's basename so it is obvious which file belongs to
which clip. Most files follow the naming pattern:

```
<video_basename>.track_runner.<kind>.<ext>
```

A single root-level default lives at
[tr_config/track_runner.config.yaml](../tr_config/track_runner.config.yaml)
and is merged under every per-video config at load time. Note: the
loader used at runtime actually reads the default from
[track_runner/track_runner.config.yaml](../track_runner/track_runner.config.yaml)
via `read_default_config()` in
[track_runner/tr_config.py](../track_runner/tr_config.py); the
`tr_config/` root-level copy is a user-editable starting point, not
the built-in default.

## File map

| File | Format | Typical size | Writer | Reader |
| --- | --- | --- | --- | --- |
| `<video>.track_runner.config.yaml` | YAML | 250-450 B | `tr_config.write_config` | `tr_config.load_config` |
| `<video>.track_runner.seeds.json` | JSON | 150 KB - 20 MB | `state_io.write_seeds` | `state_io.load_seeds` |
| `<video>.track_runner.intervals.json` | JSON | tens of KB - several MB | `state_io.write_intervals` | `state_io.load_intervals` |
| `<video>.track_runner.diagnostics.json` | JSON | ~100 KB | `state_io.write_solver_diagnostics` | `state_io.load_diagnostics` |
| `<video>.track_runner.agreement_debug.json` | JSON | varies | `state_io.write_agreement_debug_sidecar` | manual |
| `<basename>_<frames>_<estimator>_<hash8>.npz` | NumPy `.npz` | 100-200 KB | `camera_motion.save_motion_cache` | `camera_motion.load_motion_cache` |
| `track_runner.config.yaml` (root) | YAML | ~250 B | hand-edited | merged under every per-video config |
| `archive/` | directory | varies | hand-kept | none |

## Config YAML

Files: `<video>.track_runner.config.yaml` and the root
[tr_config/track_runner.config.yaml](../tr_config/track_runner.config.yaml).

Reader / writer / merge / validator all live in
[track_runner/tr_config.py](../track_runner/tr_config.py):

- `load_config(path)` -- parses YAML, checks the header, migrates v2 in
  place, returns a v3-shaped dict.
- `write_config(path, config)` -- writes YAML with `sort_keys=False` so
  the authored order is preserved.
- `merge_config(base, override)` -- deep-merges a per-video override
  onto the defaults. Dicts merge recursively; scalars and lists are
  replaced wholesale.
- `validate_config(config)` -- enforces header and required sections.

### Top-level keys (verified against loader and bundled default)

| Key | Required | Notes |
| --- | --- | --- |
| `track_runner` | yes | Header; integer schema version. Accepted: `2` (legacy, auto-migrated) and `3`. |
| `detection` | yes | Detector settings. |
| `processing` | yes | Crop + encode + solver settings. |
| `camera` | no | Auto-filled with a default block by `validate_config` if absent. |
| `motion` | no | Camera-motion estimator settings (feeds the `.npz` cache). |

Only these five keys are referenced by the loader and validator. Any
other top-level key is preserved through load/write but is not part of
the documented schema.

### detection

Seen in the bundled default and every per-video config:

```yaml
detection:
  model: yolov8n
  confidence_threshold: 0.25
```

YOLO is scoped as optional seeding assistance per
[docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md); it is not an
active tracking signal.

### processing

Read defensively by several consumers; the only key the validator
enforces is `torso_height_multiple`:

- `torso_height_multiple` (float, required): crop height as a multiple
  of the tracked torso height. Must be `>= 1`. This is the v3 key.
- `crop_fill_ratio` (float, legacy): the v2 name. On load,
  `_migrate_crop_fill_ratio` rewrites the dict in place:
  `torso_height_multiple = 1 / crop_fill_ratio`. The old key is then
  deleted. A one-line `[config migration]` notice prints when the
  migration fires.
- `crop_mode`, `crop_aspect`, `crop_min_size`, `output_resolution`,
  `video_codec`, `crf`, `encode_filters`, `solver_backend`: read by
  the crop and encoder stages. Not validated at config load.

### camera

Auto-filled with this default block if missing:

```yaml
camera:
  zoom_type: fixed
  zoom_levels: [1]
  camera_height: elevated
  camera_position: side
  track_size: 400
  venue_type: outdoor
  lighting: daylight_sunny
```

`zoom_type` selects the camera-motion estimator; see the NPZ section
below.

### motion (optional)

Controls the estimator used when computing the camera-motion NPZ:

```yaml
motion:
  estimator:
    type: iphone_discrete
```

`type` is the one field the estimator-selection logic in
`camera_motion.precompute_camera_motion` looks at. Accepted values
include the class names `FixedZoomEstimator`, `DiscreteZoomEstimator`,
`ContinuousZoomEstimator` and the YAML aliases `iphone_discrete`,
`discrete`, `continuous`. If `motion` is absent, the estimator is
selected from `camera.zoom_type` instead.

### Layering

At startup the loader reads the built-in default from
`track_runner/track_runner.config.yaml`, then `merge_config` overlays
the per-video `<video>.track_runner.config.yaml`. The per-video file
can therefore be minimal; it only needs the keys that differ from the
default.

## Seeds JSON

File: `<video>.track_runner.seeds.json`. Reader and writer are in
[track_runner/state_io.py](../track_runner/state_io.py): `load_seeds`,
`write_seeds`. Atomic write via a sibling temp file and `os.replace`.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_seeds` | int | Header; required value is `2`. |
| `seeds` | list | One record per annotated frame. |
| `video_identity` | dict | Present on recent files; written by the seeding UI for mismatch detection. Fields mirror `tr_video_identity.make_video_identity`: `basename`, `size_bytes`, `width`, `height`, `fps`, `frame_count`, `duration_s`. |

### Per-seed schema

| Field | Type | Meaning |
| --- | --- | --- |
| `frame_index` | int | Frame number in the source video (0-based). Deduped on merge; sorted on load. |
| `frame` | int | Legacy duplicate of `frame_index`. Still written for compatibility. |
| `time_s` | float | `frame_index / fps`. Convenience only. |
| `torso_box` | `[x, y, w, h]` | Pixel-coordinate bounding box of the torso. Ints. |
| `cx`, `cy` | float | Box center (derived from `torso_box`). |
| `w`, `h` | float | Box dimensions (derived from `torso_box`). |
| `pass` | int | Seeding pass that produced this seed (`1`, `2`, ...). |
| `source` | str | `human` or `propagated`. |
| `mode` | str | One of `VALID_SEED_MODES` (see below). |
| `status` | str | `visible`, `partial`, `approximate`, or `not_in_frame`. Legacy `obstructed` is auto-migrated. |
| `conf` | float or null | Confidence for approximate seeds (default 0.3); null for visible/partial. Backfilled on load. |
| `jersey_hsv` | `[h, s, v]` | Legacy: median HSV of the torso. **Not used as identity evidence per contract C6**; preserved for on-disk schema stability. |
| `histogram` | 2-D list of floats | **Legacy: present on files written before 2026-04-20.** Normalized HS histogram over the torso ROI (see below). No longer extracted, but still loaded and written verbatim if already present. |

Valid `mode` values come from `VALID_SEED_MODES` in
[track_runner/state_io.py](../track_runner/state_io.py): `initial`,
`suggested_refine`, `interval_refine`, `gap_refine`, `edit_redraw`,
`solve_refine`, `interactive_refine`, `bbox_polish`, `target_refine`.

`not_in_frame` seeds intentionally omit `cx`, `cy`, `w`, `h`, and
`torso_box`. Consumers that iterate all seeds must filter by `status`
before reading positions. See the
[2026-04-20 CHANGELOG entry](CHANGELOG.md) for a recent solve crash
caused by a missing filter.

### Load-time normalization

[state_io.load_seeds](../track_runner/state_io.py):

1. If the file does not exist, return `{track_runner_seeds: 2, seeds: []}`.
2. Reject any file whose header is not exactly `2`.
3. For every seed: backfill `conf: None` if missing.
4. Migrate legacy `obstructed` seeds: keep them as `approximate` if
   they have a `torso_box`; drop them otherwise (no position data,
   unusable).
5. Sort the final list by `frame_index` so consumers always receive
   time-ordered data.

### Write-time normalization

[state_io.write_seeds](../track_runner/state_io.py):

1. Validate every seed with `validate_seed`; warn once about
   `approximate` seeds that lack `torso_box`.
2. Force header to `2`.
3. Sort by `frame_index` for human-readable output.
4. Write to a sibling `.tmp.json`, then `os.replace` for atomicity.

### Merge rule

`state_io.merge_seeds(existing, new)` appends a new seed only when its
`frame_index` is not already in `existing`. Existing seeds at a frame
are never overwritten, so iterative refine passes enrich the set
without clobbering prior work.

### Why seeds.json can reach 20 MB

The legacy `histogram` field is a `(30, 32)` `float64` array stored
inline in each seed. That is ~11 KB of JSON per seed; stripping it
leaves ~240 B per seed. On a clip with ~1000 seeds the histogram alone
accounts for roughly 10-11 MB, which explains the 20 MB seed files
written before 2026-04-20.

Histogram extraction was removed from the seeding UI on 2026-04-20
(contract C6 in
[docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md); CHANGELOG
entry for that date). New seed files written by the current code do
not add `histogram`, so removing legacy histograms eliminates the
known 20 MB growth mode.

However: `load_seeds` does not strip `histogram` or `jersey_hsv`, and
`write_seeds` calls `json.dump(seeds_data, ...)` on whatever it is
handed. A plain open-then-save cycle therefore preserves legacy
histograms. Stripping them from existing files is a future cleanup
task that would need a small migration script (load, pop `histogram`
from each seed, write). Documenting that script is out of scope for
this doc.

## Intervals JSON

File: `<video>.track_runner.intervals.json`. This is the **cache of
solved interval geometry**. Reader and writer are
`state_io.load_intervals` and `state_io.write_intervals` in
[track_runner/state_io.py](../track_runner/state_io.py). Intervals
themselves are produced by
[track_runner/interval_solver.py](../track_runner/interval_solver.py)
and include per-frame tracks emitted by
[track_runner/velocity_model.py](../track_runner/velocity_model.py).

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_intervals` | int | Header; required value is `1`. |
| `solved_intervals` | dict | Keyed by fingerprint string. |
| `video_identity` | dict | Same shape as the seeds `video_identity` block. |

### Fingerprint format

Computed by `state_io.interval_fingerprint(seed_start, seed_end, solver_tag)`:

```
<fi_start>|<cx_s>|<cy_s>|<w_s>|<h_s>|<fi_end>|<cx_e>|<cy_e>|<w_e>|<h_e>||<solver_tag>
```

All position values are rounded to two decimal places with
`:.2f` formatting. Example from a real file:

```
49|1635.00|754.50|64.00|81.00|56|1630.50|756.50|69.00|87.00||blob_snap/v1/a0.600/slk0.500/prp0.750/vf1.500/am0.500/ms0.500
```

The suffix after `||` encodes the solver semantics (blob-snap
version, alpha, slack, propagation weight, velocity factor, etc.). Any
change to seed position, seed frame index, or solver tag yields a new
key, so stale entries are never reused; the old entry simply stays in
the file until something prunes it.

### Per-interval entry

Each value in `solved_intervals` has:

| Field | Type | Meaning |
| --- | --- | --- |
| `start_frame` | int | Inclusive interval start. |
| `end_frame` | int | Inclusive interval end. |
| `fused_track` | list of dicts | Blended forward + backward track, per frame. |
| `forward_track` | list of dicts | Forward propagation track, per frame. |
| `backward_track` | list of dicts | Backward propagation track, per frame. |
| `interval_score` | dict | Compact per-interval score (see diagnostics section). |

### Per-frame track record

Fused-track records carry:

| Field | Type | Meaning |
| --- | --- | --- |
| `cx`, `cy`, `w`, `h` | float | Box center and size. |
| `conf` | float | Per-frame confidence. |
| `source` | str | `propagated`, `merged`, `fused`. |
| `fuse_flag` | bool | True if the fused record was blended from multiple sources. |
| `occlusion_risk` | bool | True if the frame sits in an occlusion-risk window. |

Forward/backward records carry `cx/cy/w/h/conf/source` plus:

| Field | Type | Meaning |
| --- | --- | --- |
| `stationary_lock` | bool | True on the endpoint frame (start or end seed). Still emitted by the current `velocity_model.py` propagator. |
| `blob_gate` | str | Output-only metadata (`skipped`, `pass`, `fail`, ...). Not a gate input; read only by `interval_solver._coverage_from_track` for diagnostics per contract C5. |

### Cache semantics

- Solve computes a fingerprint for each (start-seed, end-seed,
  solver-tag) triple. If the key is already in `solved_intervals`, the
  entry is reused; otherwise the interval is solved and the result
  added.
- Refine uses the same fingerprint function so its cache hits align
  with solve's.
- The file is a pure cache. Deleting it forces a full re-solve on the
  next run; nothing else depends on it.

## Camera-motion NPZ

Files named `<basename>_<frame_count>_<estimator>_<hash8>.npz`. Real
examples from the working `tr_config/`:

- `canon_60d_600m_zoom.MP4_2886_continuous_aebe0b2c.npz`
- `Hononega-Orion_600m-IMG_3702.mkv_5536_iphone_discrete_4c91af73.npz`

This is where the "global" camera-motion track is stored. Reader and
writer are `load_motion_cache` and `save_motion_cache` in
[track_runner/camera_motion.py](../track_runner/camera_motion.py); the
compute-or-load driver is `precompute_camera_motion` in the same file.

### Arrays

`numpy.savez` writes five equal-length arrays:

| Name | dtype | Shape | Meaning |
| --- | --- | --- | --- |
| `dx` | float64 | `(N,)` | Per-frame x translation in pixels. |
| `dy` | float64 | `(N,)` | Per-frame y translation in pixels. |
| `scale` | float64 | `(N,)` | Per-frame scale factor; `1.0` = no zoom change. |
| `quality` | float64 | `(N,)` | Phase-correlation response / confidence. |
| `event_flags` | int32 | `(N,)` | Bitfield: zoom-jump and low-quality markers. |

`N` matches the video's frame count (possibly plus a small buffer
from the estimator).

### Cache key

`camera_motion._compute_cache_key` joins four components with
underscores:

1. `video_identity["basename"]` -- the filename.
2. `str(video_identity["frame_count"])`.
3. The estimator string (for example `continuous`, `iphone_discrete`,
   `FixedZoomEstimator`) -- the value taken directly from
   `motion.estimator.type` in the config.
4. The first 8 hex characters of an MD5 over the `motion.estimator`
   dict serialized as sorted-key JSON
   (`camera_motion._compute_config_fingerprint`).

`video_identity` comes from
[track_runner/tr_video_identity.py](../track_runner/tr_video_identity.py)
`make_video_identity`, which records basename, size_bytes, width,
height, fps, frame_count, and duration_s from the probed video.

### Estimator selection

`precompute_camera_motion` picks an estimator from
`motion.estimator.type`, falling back to `camera.zoom_type`:

| Config value | Estimator class |
| --- | --- |
| `fixed` / `FixedZoomEstimator` | `FixedZoomEstimator` |
| `discrete` / `iphone_discrete` / `DiscreteZoomEstimator` | `DiscreteZoomEstimator` |
| `continuous` / `ContinuousZoomEstimator` | `ContinuousZoomEstimator` |

The fixed variant assumes no zoom. The discrete variant snaps scale to
the configured `camera.zoom_levels`. The continuous variant estimates
per-frame scale via log-polar phase correlation.

### Lifecycle and invalidation

- Computed once per (video, estimator config) pair. The result is
  written alongside the video's other `tr_config/` files.
- Changing the estimator type, any field inside `motion.estimator`, or
  any video-identity field that feeds the cache key (basename,
  frame_count) produces a new filename. Old caches linger on disk but
  are never loaded.
- Reused by solve, refine, encode, and the UI. The UI heat-map overlay
  looks up the cache via a glob on `<basename>_*.npz`, so there should
  be exactly one active motion cache per video at a time. Stale caches
  are safe to delete.

## Diagnostics JSON

File: `<video>.track_runner.diagnostics.json`. Reader and writer are
`state_io.load_diagnostics` and `state_io.write_solver_diagnostics` in
[track_runner/state_io.py](../track_runner/state_io.py).

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_diagnostics` | int | Header; accepted values are `2` (legacy, migrated on load) and `3` (current). |
| `fps` | float | Video fps, rounded to 6 decimals. |
| `intervals` | list | Compact per-interval scores (see below). |
| `cyclical_prior` | dict or null | Optional: period-detection result. |
| `race_phase` | dict | Optional: race-start frame detection. |
| `video_identity` | dict | Same shape as elsewhere. |

### Per-interval entry (v3 analytical)

```json
{
  "start_frame": 49,
  "end_frame": 56,
  "start_s": 1.635,
  "end_s": 1.869,
  "interval_score": {
    "agreement": 0.8956,
    "velocity_consistency": 0.7123,
    "size_consistency": 0.9012,
    "motion_quality": 0.8234,
    "occlusion_fraction": 0.0,
    "confidence_tier": "high",
    "failure_reasons": [],
    "warning_flags": []
  }
}
```

Legacy v2 entries flatten the same fields onto the interval dict;
`load_diagnostics` rebuilds the nested `interval_score` on read so
downstream consumers see a single shape.

Per-frame trajectory is **not** written into this file -- it lives in
`intervals.json` (in the per-interval tracks) or in the agreement-debug
sidecar. The diagnostics file is a compact scoring summary sized for
review tooling.

## Agreement-debug sidecar (optional)

File: `<video>.track_runner.agreement_debug.json`. Written by
`state_io.write_agreement_debug_sidecar` only when the solver ran with
`--debug` and at least one interval carries an `agreement_debug`
sub-dict. Schema identifier: `track_runner.agreement_debug.v1`. Holds
per-frame agreement series plus p10/p50/p90 IoU summaries. No reader
is wired into the runtime; the file is for manual inspection.

## tr_config/archive

Hand-kept backups of older runs (prior diagnostics, intervals,
seeds, old motion caches, snapshot YAMLs). No code path reads or
writes `tr_config/archive/`. It is safe to delete the directory to
reclaim disk space.

## FAQ

- **Why is my seeds.json 20 MB?** Legacy per-seed `histogram` fields
  from before 2026-04-20. See the seeds section.
- **Where is the global camera-motion track stored?** In the `.npz`
  file whose name starts with the video basename. Keys are `dx`, `dy`,
  `scale`, `quality`, `event_flags`.
- **Can I delete tr_config/archive/ ?** Yes. Nothing reads it.
- **Can I delete an old `_<hash8>.npz` that does not match the current
  config?** Yes. It is a cache; the next run recomputes as needed.
- **What happens if I hand-edit a per-video config YAML?** The next
  run reloads it; per-video values override the merged defaults. If
  you drop `processing.torso_height_multiple`, `validate_config` will
  refuse to load the file.
- **Does re-saving seeds.json strip the legacy histogram?** No. The
  writer serializes whatever the loader returned. Stripping legacy
  fields requires a small migration script; that is a future task.

## Related docs

- [docs/TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- hard
  invariants; C6 is why `histogram` and `jersey_hsv` are no longer
  read.
- [docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- overall
  philosophy, including how seeds, intervals, and motion fit together.
- [docs/CHANGELOG.md](CHANGELOG.md) -- the 2026-04-20 entry for the
  histogram removal and the `not_in_frame` seed-filter fix.
