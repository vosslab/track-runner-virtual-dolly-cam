# tr_config files

Reference for every file the track runner persists under `tr_config/`.
One row per video, plus a single global default. Covers on-disk schema,
reader/writer functions, lifecycle rules, current seed records, and the
camera-motion solved artifact.

**Durable Artifacts:** On-disk files in this directory are persistent
solved data with explicit reuse rules. They are not cache. The word
"cache" in this codebase is reserved for in-memory ephemeral state.
See contract C12.2 in [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md).

## Purpose of tr_config

`tr_config/` is the project's state directory. Each per-video file is
keyed by the source video's basename so it is obvious which file
belongs to which clip. The naming pattern is:

```
<video_basename>.track_runner.<kind>.<ext>
```

A single root-level default lives at
[track_runner.config.yaml](../track_runner/track_runner.config.yaml)
and is used when no per-video config exists. The loader reads the built-in
default from [track_runner.config.yaml](../track_runner/track_runner.config.yaml)
via `read_default_config()` in [tr_config.py](../track_runner/tr_config.py).
A per-video configuration is a complete current configuration, not a partial
overlay merged with the default.

## Format rule

**Dense per-frame numeric series are stored as NPZ. Human-authored
annotations and interval-level summary records are stored as JSON.**
This single rule dictates every file format below.

## File map

| File | Format | Typical size | Writer | Reader |
| --- | --- | --- | --- | --- |
| `<video>.track_runner.config.yaml` | YAML | 250-450 B | `tr_config.write_config` | `tr_config.load_config` |
| `<video>.track_runner.seeds.json` | JSON | 5-50 KB | `state_io.write_seeds` | `state_io.load_seeds` |
| `<video>.track_runner.interval_scores.json` | JSON | ~100 KB | `state_io.write_interval_scores` | `state_io.load_interval_scores` |
| `<video>.track_runner.torso_box_coords.npz` | NPZ | 100 KB - 3 MB | `torso_box_coords_io.write_torso_box_coords` | `torso_box_coords_io.load_torso_box_coords` |
| `<video>.track_runner.camera_motion.npz` | NPZ | 150-300 KB | `camera_motion.save_motion_cache` | `camera_motion.load_motion_cache` |
| `<video>.track_runner.agreement_debug.json` | JSON | varies | `state_io.write_agreement_debug_sidecar` | manual |
| `<video>.encode_analysis.yaml` | YAML | varies | `modes.analyze.run` | `encode_analysis.load_analyze_target_frames` |
| `<video>.encode_analysis.html` | HTML | varies | `modes.analyze.run --plot` | browser/manual |
| `track_runner.config.yaml` (root) | YAML | ~250 B | hand-edited | used when no per-video config exists |

`tr_paths.default_interval_scores_path` owns the interval-score path.
`state_io.load_interval_scores`/`write_interval_scores` own its current JSON
contract; `torso_box_coords_io` owns the NPZ trajectory contract.

## Current-artifact recovery

Readers accept only current interval-score and torso-coordinate schemas. A
consumer mode reports a stale or malformed artifact and directs the user to
remove the derived interval artifacts and run `solve`. `solve` replaces
derived scores or coordinates whose saved video geometry differs from the
input video. Human seed files are not reinterpreted for another video; create
fresh seeds when their source geometry does not match.

## Seeds JSON

Purpose: human-authored annotation truth. Under contract C7, a seed
is a torso box drawn by a human (or a human-committed `not_in_frame`
state). Machine-produced geometry -- predictions, suggestions, polish
outputs, heat-map blob adjustments -- is not a seed until a human
commits it.

File: `<video>.track_runner.seeds.json`. Atomic write via sibling
temp file and `os.replace`.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_seeds` | int | Header; required value is `3`. Other values are rejected. |
| `seeds` | list | One record per annotated frame. |
| `video_identity` | dict | Present on recent files; mismatch-detection metadata. |

### Per-seed schema (v3 canonical, four fields)

| Field | Type | Meaning |
| --- | --- | --- |
| `frame_index` | int | Frame number in the source video (0-based). Deduped on merge; sorted on load. |
| `torso_box` | `[x, y, w, h]` (ints) | Pixel rectangle the human drew. Omitted when `status` is `not_in_frame`. Seeds are integer pixel geometry; any float-valued cx/cy/w/h the solver or fingerprint code uses are derived convenience geometry, not stored truth. |
| `status` | str | `visible`, `partial`, `approximate`, or `not_in_frame`. Drives solver and erasure behavior. Load-bearing. |
| `pass` | int | Seeding pass that produced this seed. Load-bearing for duplicate-frame deduplication. |

### Not on disk (stripped by the writer)

- `histogram`, `jersey_hsv` -- banned by contract C6 (runner appearance unreliable).
- `frame` -- duplicate of `frame_index`.
- `time_s` -- derived as `frame_index / fps`.
- `cx`, `cy`, `w`, `h` -- derived in memory by `load_seeds` from `torso_box`.
- `mode` -- workflow provenance with no solver branches.
- `conf` -- derivable from `status` (visible/partial -> 1.0, approximate -> 0.3).
- `source` -- always `"human"` under C7.

The canonical allow-list is `{frame_index, torso_box, status, pass}`.
Stored records with any other key are rejected. The writer drops derived
in-memory values before serializing.

### Load-time behavior

[state_io.load_seeds](../track_runner/state_io.py):
1. If the file does not exist, return
   `{track_runner_seeds: 3, seeds: []}`.
2. Accept header 3 only; reject any other header.
3. For every seed: validate the four-field schema and status/torso-box
   pairing, then re-derive cx/cy/w/h from torso_box in memory (skipped for
   `not_in_frame` seeds, which carry no box).
4. Sort by `frame_index`.

### Why seeds files are now small

Removed per-seed `histogram` fields (`(30, 32)` float64 arrays, ~11 KB
of JSON each) previously dominated file sizes. Canonical v3 files are
~150-200 B per seed.

## Torso-box-coords NPZ

File: `<video>.track_runner.torso_box_coords.npz`. This is the **per-interval
solved-result store of per-frame trajectory geometry that refine mode and
the encoder consume.** Reader `torso_box_coords_io.load_torso_box_coords`,
writer `torso_box_coords_io.write_torso_box_coords`.

### Top-level keys (NPZ)

| Key | Type | Notes |
| --- | --- | --- |
| `schema_version` | int32 | Current writer emits `15`; readers accept `{15}`. Older layouts require a fresh `solve`. |
| `manifest` | bytes (JSON-encoded) | List of per-interval entries mapping fingerprint to an `array_index` plus `start_frame`/`end_frame`. |
| `i<k>_blended_cx`, `i<k>_blended_cy`, `i<k>_blended_w`, `i<k>_blended_h` | uint16 arrays | Required blended SOURCE-coordinate path. Array length equals `end_frame - start_frame + 1`; values are pixel-snapped to [0, 65535]. |
| `i<k>_conf` | uint8 array | Required per-frame raw-pass agreement transport. Values map [0, 255] to [0, 1]. |
| `i<k>_fwd_*`, `i<k>_bwd_*` | uint16 arrays | Optional paired raw paths. Both groups are written only when their quantized geometry differs; neither group may appear alone. |
| `video_identity` | bytes (JSON-encoded) | Required source identity. |
| `race_start` | bytes (JSON-encoded) | Optional detected race-start block. Absence means no pre-race phase was detected. |
| `solve_complete` | bool | Whether the solve completed vs. was interrupted. |

### In-memory shape

`load_torso_box_coords` reassembles the current in-memory shape:

```python
{
    "track_runner_intervals": 2,
    "solved_intervals": {
        "<fingerprint>": {
            "start_frame": int,
            "end_frame": int,
            "forward_path": list | None,
            "backward_path": list | None,
            "blended_path": [
                # blended interval path: combined FWD+BWD output
                # trajectory for this interval. Output artifact only;
                # never used for FWD/BWD agreement scoring.
                {"cx": int, "cy": int, "w": int, "h": int},
                ...
            ],
            "conf": [float, ...],
        },
    },
    "video_identity": {...},
    "race_start": {...},  # present only when Stage 2 detected a pre-race phase
    "solve_complete": bool,
}
```

The loaded raw paths are both present or both `None`. Stored `conf` remains the
agreement authority even when raw coordinate paths are omitted.

### Explicitly not stored

- `interval_score` -- lives exclusively in `interval_scores.json`.
- Per-frame extras (`source`, `blend_flag`, `blob_gate`) -- omitted from the
  coordinate transport. `conf` is the deliberate Schema-15 exception.

### Fingerprint format

Computed by `state_io.interval_fingerprint(seed_start, seed_end,
solver_tag)`. Derived `cx/cy/w/h` from the bracketing seeds are
serialized with `:.2f`; the two-decimal format is a fingerprint
component, not a claim that seeds carry subpixel precision.

```
49|1635.00|754.50|64.00|81.00|56|1630.50|756.50|69.00|87.00||schema_v15
```

### Reuse semantics

Solve computes a fingerprint for each interval using its bracketing seeds.
Matching entries are reused from the store; others are solved and added.
Refine uses the same fingerprint so its store hits align with solve's.
Deleting the file forces a full re-solve; nothing else depends on it.

**Reuse key rule:** The key describes the persisted SOURCE-frame result, not
the runtime method used to compute it. The stored torso boxes are always
unbinned SOURCE-frame coordinates. Consequently the key carries only:

1. Seed-pair frame indices.
2. Human-authored SOURCE seed geometry (the box coords x, y, w, h of each
   endpoint seed).
3. The current `SCHEMA_VERSION` tag from `tr_schema`.

Processed dimensions, CLI flags, solver mode, stage name, tuning constants,
basename, file size, and container extension stay out of the key. These are
runtime or performance details; they do not change what the persisted artifact
stores. Bin factor belongs to the camera-motion artifact identity, so refine
must use the existing motion bin or the user runs `solve` at the requested bin.
Method-only changes (walker DP, cost weights, residual stride) use `solve` to
refresh stale values rather than widening the key. See
[TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md) for the full
allow-list and justification rule.

## Interval scores JSON

File: `<video>.track_runner.interval_scores.json`. **Owner of interval-level
diagnostic and review summaries.** Reader `state_io.load_interval_scores`, writer
`state_io.write_interval_scores`. `write_solver_interval_scores` assembles a
solver result before it delegates to that canonical writer.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_diagnostics` | int | Header; must equal current `SCHEMA_VERSION` (`15`). |
| `fps` | float | Video fps, rounded to 6 decimals. |
| `intervals` | list | Per-interval scoring entries. |
| `video_identity` | dict | Required source identity. |

### Per-interval entry

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

No per-frame trajectory data -- trajectory lives in
`torso_box_coords.npz`. This JSON remains a reporting sidecar; target and
refine reconstruct operational score views from the durable NPZ inputs.

## Camera motion NPZ

File: `<video>.track_runner.camera_motion.npz`. Single durable solved
artifact per video; motion-model identity lives inside the file. Reader
`camera_motion.load_motion_cache`, writer
`camera_motion.save_motion_cache`.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `motion_model` | bytes (UTF-8) | One of `fixed_zoom`, `discrete_zoom`, `continuous_zoom`. Staleness determined by comparing persisted model against the current configuration. |
| `video_identity` | bytes (UTF-8 JSON) | Source geometry identity (`width`, `height`, `frame_count`) used for cache reuse. |
| `frame_count` | int64 | Frame count from video probe. |
| `bin_factor` | int64 | Processed-frame bin used for measurement; required for reuse. |

### Per-model arrays (float32)

| Model | Arrays |
| --- | --- |
| `fixed_zoom` | `dx`, `dy`, `quality` (no `scale` -- constant 1.0 carries no signal) |
| `discrete_zoom` | `dx`, `dy`, `scale`, `quality` |
| `continuous_zoom` | `dx`, `dy`, `scale`, `quality` |

`event_flags` was removed from the schema (zero downstream readers).
`quality` stays because `scoring.py` uses it for `motion_quality` in
the interval scoring.

### Reuse semantics

- Computed once per video by `precompute_camera_motion`. The result
  is written as the canonical file alongside the video's other
  `tr_config/` files.
- Solve reuses the artifact only when its motion model, bin factor, and source
  geometry identity match. Container name, byte size, and fps do not gate reuse.
  Otherwise it recomputes and atomically replaces
  the file.
- Refine, analyze, encode, and the UI read the same canonical filename. A
  missing or stale camera-motion artifact directs the user to run solve.

## FAQ

- **Why is my seeds.json no longer 20 MB?** The removed `histogram`
  field is prohibited under C6. The canonical four-field schema is
  ~150-200 B per seed.
- **Where is the camera-motion track stored?** In
  `<video>.track_runner.camera_motion.npz`. Keys are `dx`, `dy`,
  `quality` (plus `scale` for discrete/continuous zoom), and
  artifact-identity metadata (`motion_model`,
  `video_identity`, `frame_count`, `bin_factor`).
- **What happens if I hand-edit a per-video config YAML?** The next run reads
  the complete file and validates its current header and required sections.
- **Does re-saving seeds.json introduce extra fields?** No.
  `write_seeds` emits only the canonical four fields.

## Related docs

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- hard
  invariants; C6 (appearance banned) and C7 (human-only seeds) drive
  most of the schema cleanup.
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- overall
  philosophy, including how seeds, intervals, and motion fit together.
- [CHANGELOG.md](CHANGELOG.md) -- the 2026-04-21 entries record
  the tr_config storage cleanup patches and size deltas.
