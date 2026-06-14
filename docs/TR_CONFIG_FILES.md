# tr_config files

Reference for every file the track runner persists under `tr_config/`.
One row per video, plus a single global default. Covers on-disk schema,
reader/writer functions, lifecycle rules, and the two things that most
often surprise a first-time reader: why seeds files are so small now
(they used to carry dead appearance data) and where the camera-motion
solved artifact lives.

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
and is merged under every per-video config at load time. Note: the
loader used at runtime reads the built-in default from
[track_runner.config.yaml](../track_runner/track_runner.config.yaml)
via `read_default_config()` in
[tr_config.py](../track_runner/tr_config.py); the
`tr_config/` root-level copy is a user-editable starting point, not
the built-in default.

## Format rule

**Dense per-frame numeric series are stored as NPZ. Human-authored
annotations and interval-level summary records are stored as JSON.**
This single rule dictates every file format below.

## File map

| File | Format | Typical size | Writer | Reader |
| --- | --- | --- | --- | --- |
| `<video>.track_runner.config.yaml` | YAML | 250-450 B | `tr_config.write_config` | `tr_config.load_config` |
| `<video>.track_runner.seeds.json` | JSON | 5-50 KB | `state_io.write_seeds` | `state_io.load_seeds` |
| `<video>.track_runner.interval_scores.json` | JSON | ~100 KB | `state_io.write_solver_diagnostics` | `state_io.load_diagnostics` |
| `<video>.track_runner.torso_box_coords.npz` | NPZ | 100 KB - 3 MB | `state_io.write_torso_box_coords` | `state_io.load_torso_box_coords` |
| `<video>.track_runner.camera_motion.npz` | NPZ | 150-300 KB | `camera_motion.save_motion_cache` | `camera_motion.load_motion_cache` |
| `<video>.track_runner.agreement_debug.json` | JSON | varies | `state_io.write_agreement_debug_sidecar` | manual |
| `track_runner.config.yaml` (root) | YAML | ~250 B | hand-edited | merged under every per-video config |
| `archive/` | directory | varies | hand-kept or migration tool | none |

Function names were retained across the cleanup for callsite
compatibility even where the on-disk filename changed:
`load_diagnostics`/`write_solver_diagnostics` operate on
`interval_scores.json`, and `load_intervals`/`write_intervals` were
replaced by new `load_torso_box_coords`/`write_torso_box_coords`
because the underlying format changed from JSON to NPZ.

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
| `track_runner_seeds` | int | Header; required value is `3`. Loader accepts legacy `2` and migrates on next write. |
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
Unknown keys outside that set and the strip-list are tolerated in
memory but discarded at write time. Read tolerant, write strict.

### Load-time behavior

[state_io.load_seeds](../track_runner/state_io.py):
1. If the file does not exist, return
   `{track_runner_seeds: 3, seeds: []}`.
2. Accept headers 2 (legacy) or 3 (canonical); reject others.
3. For every seed: migrate legacy `obstructed` status (keep as
   `approximate` if `torso_box` present, drop otherwise), strip the
   legacy field set, re-derive cx/cy/w/h from torso_box in memory
   (skipped for `not_in_frame` seeds which carry no box).
4. Sort by `frame_index`.

### Why seeds files are now small

Legacy per-seed `histogram` fields (`(30, 32)` float64 arrays, ~11 KB
of JSON each) dominated pre-migration file sizes. The 20 MB files
observed before cleanup drop to ~5-50 KB in canonical v3 (~150-200 B
per seed). The one-shot migration tool
(`tools/_migrate_tr_config.py`) handles existing files.

## Torso-box-coords NPZ

File: `<video>.track_runner.torso_box_coords.npz`. This is the **per-interval
solved-result store of per-frame trajectory geometry that refine mode and
the encoder consume.** Reader `state_io.load_torso_box_coords`, writer
`state_io.write_torso_box_coords`.

### Top-level keys (NPZ)

| Key | Type | Notes |
| --- | --- | --- |
| `schema_version` | int32 | Current writer emits `10`; readers accept `{10}` (per `tr_schema.SUPPORTED_ARTIFACT_SCHEMAS["torso_box_coords"]`). The uint16 coordinate layout has been fixed since v10. Method-only changes (residual sampling, walker DP) keep this number fixed and refresh stale values with `solve`. |
| `manifest` | bytes (JSON-encoded) | List of per-interval entries mapping fingerprint to an `array_index` plus `start_frame`/`end_frame`. |
| `i<k>_cx`, `i<k>_cy`, `i<k>_w`, `i<k>_h` | uint16 arrays | Per-interval blended-interval-path arrays (the combined FWD+BWD output trajectory); `<k>` is the manifest's `array_index` for that interval. Array length equals `end_frame - start_frame + 1`. Pixel-snapped integers, range [0, 65535]. |
| `video_identity` | bytes (JSON-encoded) | Optional; same shape as elsewhere. |
| `solve_complete` | bool | Whether the solve completed vs. was interrupted. |

### In-memory shape

`load_torso_box_coords` reassembles the legacy shape consumers expect:

```python
{
    "track_runner_intervals": 2,
    "solved_intervals": {
        "<fingerprint>": {
            "start_frame": int,
            "end_frame": int,
            "blended_path": [
                # blended interval path: combined FWD+BWD output
                # trajectory for this interval. Output artifact only;
                # never used for FWD/BWD agreement scoring.
                {"cx": int, "cy": int, "w": int, "h": int},
                ...
            ],
        },
    },
    "video_identity": {...},
    "solve_complete": bool,
}
```

This preserves `stitch_trajectories` and every other iteration site
unchanged; only the read site changes.

### Explicitly not stored

- `interval_score` -- lives exclusively in `interval_scores.json`.
- `forward_path`, `backward_path` -- the per-pass forward and
  backward interval paths are computed during solve and produce the
  blended output, but are not persisted to disk.
- Per-frame extras (`conf`, `source`, `blend_flag`, `occlusion_risk`,
  `blob_gate`) -- not read by production code from a loaded store;
  dropped at write.

### Fingerprint format

Computed by `state_io.interval_fingerprint(seed_start, seed_end,
solver_tag)`. Derived `cx/cy/w/h` from the bracketing seeds are
serialized with `:.2f`; the two-decimal format is a fingerprint
component, not a claim that seeds carry subpixel precision.

```
49|1635.00|754.50|64.00|81.00|56|1630.50|756.50|69.00|87.00||blob_snap/v1/a0.600/...
```

### Reuse semantics

Solve computes a fingerprint for each (start-seed, end-seed,
solver-tag) triple. Matching entries are reused from the store;
others are solved and added. Refine uses the same fingerprint so its
store hits align with solve's. Deleting the file forces a full
re-solve; nothing else depends on it.

## Interval scores JSON

File: `<video>.track_runner.interval_scores.json`. **Sole owner of
interval scoring.** Reader `state_io.load_diagnostics`, writer
`state_io.write_solver_diagnostics`. Function names retained for
callsite compatibility even though the on-disk filename changed from
the old `.diagnostics.json`.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `track_runner_diagnostics` | int | Header; accepted values `2` (legacy, migrated on load) and `3` (current). |
| `fps` | float | Video fps, rounded to 6 decimals. |
| `intervals` | list | Per-interval scoring entries. |
| `cyclical_prior` | dict or null | Optional: period-detection result. |
| `race_phase` | dict | Optional: race-start frame detection. |
| `video_identity` | dict | Optional. |

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
`torso_box_coords.npz`. This file is exclusively the scoring summary
consumed by review tooling.

## Camera motion NPZ

File: `<video>.track_runner.camera_motion.npz`. Single durable solved
artifact per video; motion-model identity lives inside the file. Reader
`camera_motion.load_motion_cache`, writer
`camera_motion.save_motion_cache`.

### Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `motion_model` | bytes (UTF-8) | One of `fixed_zoom`, `discrete_zoom`, `continuous_zoom`. Staleness determined by comparing persisted model against the current configuration. |
| `video_identity_basename` | bytes (UTF-8) | Basename of the source video. |
| `frame_count` | int64 | Frame count from video probe. |

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
- On the next run, the loader reads `motion_model` from the stored
  artifact and compares against the current config-derived motion model.
  On mismatch, the file is treated as absent, the estimator runs, and
  `save_motion_cache` atomically overwrites.
- Reused by solve, refine, encode, and the UI. All readers access the
  single canonical filename directly.

## tr_config/archive

Holds hand-kept backups plus the one-shot migration tool's archive
directory (`tr_config/archive/pre_cleanup_<YYYYMMDD>/`). No code path
reads `tr_config/archive/`. Safe to delete for disk reclamation once
you have confirmed the new formats work end-to-end.

## Migration from legacy formats

The one-shot script `tools/_migrate_tr_config.py` canonicalizes seeds
and archives every other legacy file:

- Seeds canonicalization: drops rows with `source != "human"` under
  C7, then rewrites through the v3 canonical writer.
- Intervals/diagnostics/camera-motion legacy files: moved to
  `tr_config/archive/pre_cleanup_<YYYYMMDD>/`. No translation; the
  next solve / analyze / precompute_camera_motion regenerates under
  the new format.
- Debug sidecars: never touched (they did not exist pre-migration).

Default is dry-run; use `--apply` to act. Fingerprint-drift refusal
guards against files with stored cx/cy/w/h that disagree with
torso_box-derived geometry; `--accept-drift` opts in to rewrite
anyway.

Once the migration has run successfully, the script may be deleted
(`rm tools/_migrate_tr_config.py`).

## FAQ

- **Why is my seeds.json no longer 20 MB?** The legacy `histogram`
  field is gone under C6. The canonical four-field schema is
  ~150-200 B per seed.
- **Where is the camera-motion track stored?** In
  `<video>.track_runner.camera_motion.npz`. Keys are `dx`, `dy`,
  `quality` (plus `scale` for discrete/continuous zoom), and
  artifact-identity metadata (`motion_model`,
  `video_identity_basename`, `frame_count`).
- **Can I delete `tr_config/archive/`?** Yes. Nothing reads it.
- **What happens if I hand-edit a per-video config YAML?** The next
  run reloads it; per-video values override the merged defaults.
- **Does re-saving seeds.json introduce legacy fields?** No.
  `write_seeds` is strict: it emits only the canonical four fields
  regardless of what the in-memory dict carries.

## Related docs

- [TRACK_RUNNER_CONTRACT.md](TRACK_RUNNER_CONTRACT.md) -- hard
  invariants; C6 (appearance banned) and C7 (human-only seeds) drive
  most of the schema cleanup.
- [TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md) -- overall
  philosophy, including how seeds, intervals, and motion fit together.
- [CHANGELOG.md](CHANGELOG.md) -- the 2026-04-21 entries record
  the tr_config storage cleanup patches and size deltas.
