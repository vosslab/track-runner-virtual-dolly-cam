# Plan: prepare mode creating a fast-read working video

## Context

Scattered (random-access) frame reads on 4K HEVC Main-10 HDR sources cost 130-575 ms per call
single-process and median 2.6 s under 7-worker load, versus 6.4 ms/frame sequential
(`common_tools/README.md` lines 32-156, `docs/TROUBLESHOOTING.md` lines 5-40). Every
decode-heavy path pays the HEVC Main-10 decode cost; any scattered seek is catastrophic.

New `prepare` pipeline mode creates a derived working video (the "fast-read video") sitting
beside the original: same resolution, same frame count and equivalent frame timing, H.264
8-bit yuv420p, GOP 30, 3D-denoised, video-only. Simple mental model (user decisions):

- **prepare makes the working video. All working modes decode from it.**
- **encode uses the original for final quality.**
- **All Track Runner state stays keyed to the original video.** The fast-read video shares
  the original's state set; it is a derived working file, never a second project identity.
- **No sidecar / registration file.** The fast-read video is discovered by deterministic
  path derived from the original path, and structurally validated live when needed. The
  deterministic filename IS the registration. No persisted `.json` bookkeeping (also the
  C13-friendly choice: no stored settings hashes or identity snapshots to go stale).

Working modes: `setup`, `seed`, `edit`, `target`, `solve`, `refine`, `analyze` (including
Stage-1 camera motion inside solve). Final output mode: `encode` -> original. The user
accepts the quality change from CRF 23 + hqdn3d + SDR conversion for all working purposes;
structural validity is the only gate. Stage-1 camera motion intentionally uses the fast-read
video when available: a deliberate product decision favoring working-pipeline speed over
exact pixel equivalence (phase correlation is pixel-statistic dependent; the M3 A/B report
documents the impact).

Recommended workflow (docs update): optional but recommended for 4K HEVC sources --
`prepare -> setup -> seed -> solve -> target/refine -> analyze -> encode`.

Frame-identity contract: the fast-read video preserves frame identity, geometry, frame count,
and equivalent frame timing (fps is a reported field; the real invariant is the frame-index
to time mapping). It does NOT preserve exact pixel values (8-bit conversion, HDR tone change,
hqdn3d denoise).

User-facing phrase: "prepare creates a fast-read video beside the original video. The
fast-read video keeps the same frame size and timing, but uses a codec layout intended to
make OpenCV frame reads faster."

Baseline transcode command (user-selected: CRF 23, hqdn3d on):

```bash
ffmpeg -i INPUT.mkv \
    -map 0:v:0 -an \
    -vf "hqdn3d,format=yuv420p" \
    -c:v libx264 \
    -preset veryfast \
    -crf 23 \
    -g 30 \
    OUTPUT.fastread.mkv
```

Path layout (deterministic, all stems are the ORIGINAL stem):

```
original:  Lyra-Wheeling-IMG_3912.mkv
fast-read: Lyra-Wheeling-IMG_3912.fastread.mkv     (same directory)
state:     Lyra-Wheeling-IMG_3912.track_runner.*   (all .json/.npz, keyed to original)
```

Settled: CRF does not control keyframe spacing (GOP does); `-g 30` only GOP control; no
`-keyint_min` / `-sc_threshold 0`; `format=yuv420p` last in filter chain. Settings are fixed
constants in `fastread_video.py` (no per-video settings storage; if defaults change later,
structural validation still governs and the user recreates with `--force`). No denoise
tuning UI. Naming: mode `prepare`; artifact "fast-read video"; suffix `.fastread.mkv`. Do
not reopen naming, CRF, denoise, mode placement, role policy, or the no-sidecar decision.

## Objectives

- New `prepare` CLI mode creates the fast-read video beside the original at the deterministic path.
- All per-video state remains keyed to the original video; the fast-read video shares that state set.
- A single resolution call routes every mode's decode: working modes decode from the fast-read video when it exists at the deterministic path and is structurally valid, otherwise from the original; encode and identity always use the original.
- A present-but-invalid fast-read video fails loudly with the remedy (re-run `prepare --force`, or delete the fast-read video); an absent fast-read video falls back to original with no warning.
- M3 measures whether OpenCV random-seek time improves at least 3x on the representative clip; M1/M2 acceptance does not depend on this measurement. File size and encode time reported, not gated.
- M3 A/B solve report documents the quality impact of fast-read decode (informational, not a gate).

## Design philosophy

The fast-read video is a derived working file discovered by deterministic path and validated
live -- no registration file, no persisted bookkeeping. The rejected alternatives: (a) a
two-stage approval gate (user rejected as too conservative; accepted trade-off is a small,
known quality change for fast decode everywhere except final encode); (b) a sidecar JSON
recording settings/identity/probes (user rejected as unnecessary persistence; everything it
stored is derivable live, and stored metadata is exactly the fragile bookkeeping C13 warns
about). Scattered existence checks are rejected in favor of one `resolve_video_context()`
chokepoint (fix the design, not the symptom). Structural validity, computed fresh per run,
is the only gate and it fails loud (do-not-hide-bugs-with-defaults).

## Scope

- Add fast-read creation module shelling the baseline ffmpeg command, writing `<original stem>.fastread.mkv` beside the source.
- Add `tr_paths.fastread_video_path(original_video_path)` as the single source of the deterministic fast-read path.
- Add live structural validation (geometry, frame count, duration/timing, frame-read smoke) comparing fast-read against the original.
- Add `resolve_video_context(original_video_path)` returning a frozen `VideoContext` (fields `original_video_path`, `working_decode`, `final_encode`, `metadata_identity`; each selection carries path/role/using_fastread/reason); resolved once at CLI dispatch.
- Route ALL working modes (setup, seed, edit, target, solve incl. Stage-1, refine, analyze) through `working_decode.path`; encode through `final_encode.path`.
- Thread `original_video_path` and `decode_video_path` as separate, explicitly named variables; identity, state paths, output names use original only.
- Add user-visible logging: every mode banner names source video and decode video.
- End every `prepare` run with a status summary; step-progress CLI output with ffmpeg noise suppressed.
- Benchmark seek/sequential cost, file size, encode time; produce an informational solve A/B report on quality impact.

## Non-goals

- Replace or modify the original source video in any way.
- Persist any registration/sidecar/bookkeeping file for the fast-read video.
- Re-key any state file (seeds, trajectories, scores, caches) to the fast-read video.
- Use the fast-read video for `encode` final output.
- Gate working-mode routing on A/B results (structural validity is the only gate).
- Create the fast-read video automatically inside other modes -- `prepare` is the only creator; user-run.
- Change FrameReader decode strategies or the Stage-4 residual pre-pass design.
- Change frame rate, resolution, or frame count.
- Build a denoise tuning UI; build batch/multi-video fast-read management.
- Treat file size as a pass/fail gate (reported only).

## Current state summary

- `common_tools/frame_reader.py:259-551` -- `FrameReader` wraps `cv2.VideoCapture`; `.mkv`-only (lines 315-319) so `.fastread.mkv` is compatible; strategy-0 sequential fast path vs strategy-1 seek.
- `common_tools/probe_video.py` -- `probe_video` is the metadata probe primitive; new code uses it, not direct `cv2.VideoCapture`.
- `track_runner/tr_video_identity.py:58-129` -- video identity records; identity stays computed from the ORIGINAL video only.
- Contract C13 (quoted): "Do not use a config_hash field for bookkeeping or diagnostics. It is too fragile." Fragile-value examples: `basename`, `size_bytes`. The no-sidecar design eliminates stored bookkeeping entirely; validation compares live-probed geometry/frame_count/duration/timing of fast-read vs original, never container size or basename.
- `track_runner/tr_paths.py` -- `_data_file_path(input_file, suffix)` keys all state to the original basename; `fastread_video_path()` helper goes here.
- `track_runner/cli_args.py:301-453` + `track_runner/cli.py:2656-2699` -- subcommand registration and if/elif mode dispatch; new mode = parser + `_mode_prepare()` + dispatch arm. Entry point confirmed: `track_runner/track_runner.py`; invocation order `-i VIDEO <subcommand>` per `docs/USAGE.md`.
- `track_runner/solver_workers.py:120-125` -- each worker opens its own `FrameReader`; decode path enters via `WorkerContext` / `make_pool` initargs.
- `track_runner/encoder.py`, `track_runner/video_io.py` -- existing ffmpeg subprocess patterns to imitate (long-lived `Popen`). `common_tools/frame_filters.py:269-292` maps filters for post-crop encode output -- different use case; `prepare` builds its own `-vf` string.
- Mode docs live in `docs/MODES.md` + per-mode pages under `docs/modes/` (SETUP/SEED/SOLVE/...).
- No fast-read/mezzanine concept exists in code or docs; `docs/TROUBLESHOOTING.md` names GOP rewrite as a known last-resort idea.

## Architecture boundaries and ownership

- **Fast-read creation** owns the ffmpeg transcode. New module `track_runner/fastread_video.py`. Never touches seeds or solved state. Logs the full ffmpeg command in `--verbose`; on failure shows short error + stderr tail and deletes partial output.
- **Deterministic discovery (no sidecar).** `tr_paths.fastread_video_path(original_video_path)` -> `<original stem>.fastread.mkv` is the ONLY source of the fast-read path, always computed from the original video path. The file's presence at this path is its registration; `.fastread.mkv` files anywhere else are not discovered.
- **One shared state namespace.** All Track Runner `.json` and `.npz` artifacts are keyed to the ORIGINAL video; the fast-read video shares that state set. Path-namespace tests (WP-P3) assert all artifact paths use the original stem (`Lyra-Wheeling-IMG_3912.track_runner.*`).
- **Video selection** owns the original-vs-fast-read decision, centralized in ONE resolution call. `fastread_video.resolve_video_context(original_video_path)` runs once at CLI dispatch and returns a frozen context; modes receive the context (`get_video_path_for_role`-style internals stay private to `fastread_video.py`):

  ```python
  VideoContext:
      original_video_path          # canonical; state, identity, output names
      working_decode: VideoSelection    # fast-read iff present + structurally valid, else original
      final_encode: VideoSelection      # always original
      metadata_identity: VideoSelection # always original

  VideoSelection:
      path, role, using_fastread, reason
  ```

  Resolution logic: (1) compute expected fast-read path from the original path; (2) absent -> original; (3) present -> structural validation against the live-probed original; (4) valid -> fast-read for `working_decode`; (5) invalid -> raise with remedy. Validation happens exactly once per CLI run; a valid `VideoContext` IS the authorization for working-mode FrameReaders to use `working_decode.path` for that run -- modes use the already-resolved context and do not revalidate.

  Fixed `reason` strings (used in logs and tests): `valid_fastread`, `no_fastread_original`, `final_encode_original`, `metadata_identity_original`. The invalid case RAISES (no selection returned); the error names the fast-read path, the failed check, and the remedy ("re-run prepare --force, or delete the fast-read video to use the original").
- **Core invariant (positive form, for coder briefs):**
  - Use `original_video_path` for state, identity, config, cache, output naming, and final encode.
  - Use `decode_video_path` for FrameReader and frame-loading primitives in working modes.
  - Use `VideoContext` to carry both paths from CLI dispatch into mode code.
  - `fastread_video.py` owns discovery, structural validation, and decode-path selection; `tr_paths` owns all paths, computed from the original video path.
- **Role policy** (canonical statement):
  - Working modes `setup`, `seed`, `edit`, `target`, `solve` (all stages incl. Stage-1 camera motion), `refine`, `analyze`: `working_decode` -> fast-read when present + structurally valid; else original.
  - `encode`: `final_encode` -> always original.
  - identity hashing / state paths / output names: `metadata_identity` -> always original.
  - `setup` rule (explicit): setup may DISPLAY/decode fast-read frames, but writes config/state under the original video identity and original path -- the fast-read video is never recorded as the configured source.
  - `analyze` reports (console, YAML, HTML) include both `canonical_source` and `decode_source` fields (exactly these names).
- **Selection semantics** (exact):
  - expected fast-read path absent: working modes use original, no warning or error; banner still reports `decode video: original`.
  - expected fast-read path present + structurally valid: working modes use fast-read, reason `valid_fastread`.
  - expected fast-read path present + structurally invalid: raise loudly with remedy.
  - `final_encode` / `metadata_identity`: always original.
- **Dual-path naming rule (post-M2, HARD acceptance criterion):** in routed code touching frame decoding or state paths, replace ambiguous `video_path` parameters with `original_video_path` or `decode_video_path` matched to the function's responsibility. `video_path` is ambiguous once two physical videos exist.
- **Mode mapping table** (acceptance reference for WP-P4):

  | Mode | Decodes from | Writes / keys state under |
  | --- | --- | --- |
  | prepare | original | fast-read video at deterministic path |
  | setup, seed, edit, target | `working_decode.path` | config/seeds/state under `original_video_path` |
  | solve, refine | `working_decode.path` (Stage-1 + worker FrameReaders) | caches/state/output names under `original_video_path` |
  | analyze | `working_decode.path` | reports keyed to original; include `canonical_source` + `decode_source` |
  | encode | `final_encode.path` (always original) | output naming keyed to `original_video_path` |
- **Banner format (short):**
  - `source video: Lyra-Wheeling-IMG_3912.mkv`
  - `decode video: Lyra-Wheeling-IMG_3912.fastread.mkv` (or `decode video: original`)
  - Full probe details stay in debug output, not banners.
- **Debug artifacts:** any report, overlay, or debug artifact rendered from fast-read frames includes `decode_source` in its metadata or header.

### Mapping (milestones / workstreams -> components / patches)

| Milestone / Workstream | Component | Expected patches |
| --- | --- | --- |
| M1 / WS-VALIDATE | `tr_paths.py` helper + live structural validation in `fastread_video.py` | 1 |
| M1 / WS-CREATE | `fastread_video.create_fastread()`, `prepare` subcommand in `cli_args.py` + `cli.py`, progress UI + status summary | 1-2 |
| M1 / WS-TESTS | `tests/test_fastread_video.py` (new) | 1 |
| M2 / WS-ROLE | `resolve_video_context` + `VideoContext`/`VideoSelection`, call sites in `cli.py`, UI controllers, `solver_workers.py`, banners | 1-2 |
| M3 / WS-BENCH | `tools/benchmark_fastread_video.py` (new), report doc | 1 |
| M3 / WS-AB | informational solve A/B report | 1 |

## Milestone plan

| M | Title | Summary | Goal |
| --- | --- | --- | --- |
| M1 | Create and validate fast-read video | New `prepare` mode transcodes source to the deterministic `.fastread.mkv` path and validates it live | A valid fast-read video exists beside the original; nothing reads it yet |
| M2 | Route all working modes through fast-read | Single dispatch-time `VideoContext`; every working mode decodes fast-read when valid; encode/identity stay original; loud invalid path; banner logging | prepare is immediately useful: one command switches the whole working pipeline to fast decode |
| M3 | Benchmark and quality report | Seek/size/encode-time benchmark; informational solve A/B documenting quality impact of CRF 23 + hqdn3d | Measured 3x+ seek win on representative clip; quality impact documented |

### Milestone: M1 create and validate fast-read video

- Depends on: none
- Workstreams: WS-VALIDATE, WS-CREATE, WS-TESTS
- Entry criteria: plan approved. Validation semantics frozen in this plan; WS-CREATE and WS-TESTS may draft in parallel; WP-P1 (validation) INTEGRATES FIRST, then WP-P2/WP-P3 rebase on the actual helpers.
- Exit criteria:
  - `source source_me.sh && python3 track_runner/track_runner.py -i VIDEO.mkv prepare` produces `VIDEO.fastread.mkv` at the deterministic path.
  - Live structural validation passes on a real 4K HEVC clip: width/height/frame_count exact vs live-probed original; duration within small tolerance; fps tolerance based on probe precision (rational timing, handles 119.94-style values); BEST-EFFORT ffprobe timestamp comparison of first/middle/last frames within container-precision tolerance -- when timestamp extraction is unreliable for the container/codec pair, fall back to exact frame count + duration tolerance and note the fallback in console/debug output; FrameReader opens fast-read and reads frames 0, frame_count//2, frame_count-1. Metadata probes use `common_tools.probe_video.probe_video`; smoke reads use `common_tools.frame_reader.FrameReader`.
  - Idempotency: existing fast-read that validates -> skip transcode (status says so); existing fast-read that fails validation -> raise and suggest `--force`; `--force` -> recreate.
  - Every `prepare` run ends with the status summary: fast-read path, structural validity, validation-fallback note (`none` or the fallback used), next action lines "all working modes will now decode from the fast-read video" and "encode will still use the original video".
  - Obvious follow-ons completed: `pytest tests/ -k fastread` green, pyflakes clean, `docs/CHANGELOG.md` entry written.
- Parallel-plan ready: yes (WS-VALIDATE, WS-CREATE, WS-TESTS concurrent; WP-P1 integrates first).

### Milestone: M2 route all working modes through fast-read

- Depends on: M1 exit (artifact + validation live).
- Workstreams: WS-ROLE
- Entry criteria: M1 exit criteria met.
- Exit criteria:
  - `resolve_video_context` is the only place deciding original vs fast-read (discovery + validation happen exactly once per run); verified by behavior-focused tests and code review; `fastread_video.py` owns all selection logic (docs/CLI help/log strings may mention "fastread" freely).
  - Selection semantics implemented exactly per Architecture section and mode mapping table.
  - All working modes routed; dual-path naming rule enforced; workers receive `working_decode.path` through `WorkerContext` (no per-worker re-validation); encode reads `final_encode.path`.
  - Every mode banner logs source video + decode video.
  - Regression guard on a non-prepared video: state filenames unchanged, geometry output unchanged, no new warnings except the decode-source banner.
  - Obvious follow-ons completed: focused tests pass, changelog entry, `docs/USAGE.md` + `docs/MODES.md` + `docs/modes/PREPARE.md` written (workflow shows `prepare` as optional-recommended first step for 4K HEVC).
- Parallel-plan ready: no (single routing lane touching dispatch/banner code; one coder; planned 4a/4b patch split below).

### Milestone: M3 benchmark and quality report

- Depends on: M2 exit (routing live).
- Workstreams: WS-BENCH, WS-AB
- Entry criteria: M2 exit criteria met.
- Exit criteria:
  - `tools/benchmark_fastread_video.py` reports scattered-seek median/p95, sequential ms/frame, file size, and encode time for original vs fast-read; `--report-only` for exploration. 3x speedup is the target for the representative HEVC clip and fails only inside this script, never in pytest. Speedup always reported.
  - Informational solve A/B report (original-decode vs fastread-decode, same video + seeds): same frame count solved; failed-interval counts, severe-interval counts, FWD/BWD p50/p90, confidence-tier counts all reported (existing scoring/review outputs reused); an `image_evaluator` agent compares overlay renders of representative hard intervals (existing review/debug overlays where possible; report lists the exact image files reviewed). Findings documented; routing is NOT gated on the result -- if regression is material, the report recommends a CRF/filter adjustment to the user.
  - Obvious follow-ons completed: reports filed under `docs/active_plans/reports/`, changelog entries, `common_tools/README.md` strategy-table note.
- Parallel-plan ready: yes (WS-BENCH and WS-AB independent; both need only M2 artifacts).

## Workstream breakdown

### Workstream: WS-VALIDATE (deterministic path + live structural validation)

- Owner: expert_coder
- Needs: validation semantics (this plan), `probe_video` + `FrameReader` APIs, `tr_paths` suffix pattern, C13 quote above.
- Provides: `tr_paths.fastread_video_path()`, `fastread_video.validate_fastread_structural(original_video_path, fastread_path)` (live comparison), raise-with-remedy error shape.
- Expected patches: 1.

### Workstream: WS-CREATE (prepare mode)

- Owner: coder
- Needs: ffmpeg command + path layout (Context), CLI registration pattern (`cli_args.py:301-453`), validation from WS-VALIDATE.
- Provides: `fastread_video.create_fastread()`, `prepare` subcommand, `_mode_prepare()` dispatch, step-progress UI, ffmpeg stderr spooling + summary, status summary function.
- Expected patches: 1-2.

### Workstream: WS-TESTS (M1 unit tests)

- Owner: coder
- Needs: frozen validation semantics.
- Provides: `tests/test_fastread_video.py` -- resolver/validation behavior with synthetic probe data + tmp_path (no real ffmpeg or video), path-namespace tests.
- Expected patches: 1.

### Workstream: WS-ROLE (role-based selection + routing)

- Owner: expert_coder
- Needs: M1 artifacts; reader open sites (`cli.py:905-980`, `solver_workers.py:120-125`).
- Provides: `VideoContext` + `VideoSelection` types, `resolve_video_context()` (single dispatch-time resolution), original/decode dual-path threading across all working modes, routing inventory, banner logging, path-leakage verification.
- Expected patches: 1-2. Planned split if the routing change grows: Patch 4a = VideoContext at CLI dispatch + solve/refine worker routing + banners; Patch 4b = setup/seed/edit/target UI frame-loading routing + analyze source fields + path-leakage validation.

### Workstream: WS-BENCH (benchmark)

- Owner: coder
- Needs: M2 routing; `common_tools/README.md` measurement methodology.
- Provides: `tools/benchmark_fastread_video.py` + numbers in report.
- Expected patches: 1.

### Workstream: WS-AB (informational solve A/B)

- Owner: coder
- Needs: M2 routing; one corpus video with seeds; image_evaluator agent for hard-interval visual comparison.
- Provides: A/B report under `docs/active_plans/reports/`.
- Expected patches: 1.

## Work packages

### Work package: WP-P1 deterministic path + live structural validation

- Owner: expert_coder
- Touch points: `track_runner/tr_paths.py`, `track_runner/fastread_video.py` (new).
- Depends on: none (semantics frozen in this plan).
- Acceptance criteria: `tr_paths.fastread_video_path(original_video_path)` returns `<original stem>.fastread.mkv` beside the source and is the only source of that path; `validate_fastread_structural(original_video_path, fastread_path)` live-probes BOTH files via `probe_video` and checks width/height/frame_count exact, duration within small tolerance, fps tolerance derived from probe precision, best-effort first/middle/last timestamp alignment (fallback to frame count + duration with a console/debug note), FrameReader open + reads at 0 / mid / last on the fast-read file; failures raise `RuntimeError` naming the fast-read path, the failed check, and the remedy. Comparison uses live-probed geometry/timing only (no basename, no size_bytes, no stored metadata -- C13).
- Verification commands: `pytest tests/test_fastread_video.py`.
- Obvious follow-ons: changelog, pyflakes.

### Work package: WP-P2 fast-read creation mode

- Owner: coder
- Touch points: `track_runner/fastread_video.py`, `track_runner/cli_args.py`, `track_runner/cli.py`.
- Depends on: WP-P1 (validation).
- Acceptance criteria: `prepare` transcodes via the exact baseline command (`-map 0:v:0 -an -vf "hqdn3d,format=yuv420p" -c:v libx264 -preset veryfast -crf 23 -g 30`); output at the deterministic path; non-zero exit raises short error + stderr tail; partial output deleted on failure; idempotency per M1 exit criteria (`--force` recreates).
- Progress display (coarse step progress, no frame-accurate ffmpeg parsing):

  ```
  Track Runner prepare
  source video:    Lyra-Wheeling-IMG_3912.mkv
  fast-read video: Lyra-Wheeling-IMG_3912.fastread.mkv
  settings:        crf 23, gop 30, filter hqdn3d,format=yuv420p
  [  0%] probing source video
  [  5%] checking existing fast-read video
  [ 10%] creating fast-read video with ffmpeg
  [ ... ] ffmpeg running, elapsed 00:30
  [ ... ] ffmpeg running, elapsed 01:00
  ffmpeg summary:
    frame=27372 fps=351 q=-1.0 Lsize=...
  [ 90%] validating fast-read video
  [100%] prepare complete
  ```

  Mechanics: launch ffmpeg with `subprocess.Popen`, spool stderr to a log buffer/temp file, suppress raw ffmpeg output from the terminal, poll the process and print a heartbeat line with elapsed time every 30 seconds (heartbeat prints only while ffmpeg is still running past 30 s). After successful completion DISPLAY the final 5-10 non-empty ffmpeg stderr lines under `ffmpeg summary` as context only -- structural validation supplies the authoritative frame count and timing checks. On failure print the final 30-60 stderr lines and delete the partial output. `--verbose` streams full ffmpeg command + full stderr. Frame-accurate percent is an optional later enhancement.
- Status summary (required end-of-run output): fast-read path, structural validity, validation-fallback note (`none` or fallback used), next action lines "all working modes will now decode from the fast-read video" / "encode will still use the original video".
- Verification commands: user-run `source source_me.sh && python3 track_runner/track_runner.py -i <clip>.mkv prepare` (ffmpeg beside source needs user approval per hook policy); `pytest tests/ -k fastread`.
- Obvious follow-ons: dispatch arm, changelog, pyflakes.

### Work package: WP-P3 M1 tests

- Owner: coder
- Touch points: `tests/test_fastread_video.py` (new).
- Depends on: WP-P1 semantics (frozen; may start from plan).
- Acceptance criteria: RESOLVER/VALIDATION-ONLY tests (routing integration belongs to WP-P4): structural validation pass/fail shapes with synthetic probe data, idempotency decision, and `resolve_video_context` behavior pinned per field -- `final_encode.path`/`metadata_identity.path` equal original always; `working_decode.path` equals original when fast-read absent (reason `no_fastread_original`); resolver raises when fast-read present but invalid; `working_decode.path` equals fast-read when valid (reason `valid_fastread`). Path-namespace tests: `fastread_video_path("Lyra-Wheeling-IMG_3912.mkv")` ends `Lyra-Wheeling-IMG_3912.fastread.mkv`; all Track Runner artifact path helpers produce original-stem names (`Lyra-Wheeling-IMG_3912.track_runner.*`). Synthetic dicts + tmp_path; no ffmpeg; each test < 1 s; behavioral asserts only per PYTEST_STYLE.
- Verification commands: `pytest tests/test_fastread_video.py`.
- Obvious follow-ons: changelog; delete any test a higher-level gate covers.

### Work package: WP-P4 role selection and routing

- Owner: expert_coder
- Touch points: `track_runner/fastread_video.py`, `track_runner/cli.py`, UI controllers, `track_runner/solver_workers.py`.
- Depends on: WP-P1, WP-P2.
- Acceptance criteria (positive form): CLI dispatch constructs `VideoContext` once via `resolve_video_context(original_video_path)`; each mode entry function accepts or constructs the `VideoContext` near the CLI boundary; deeper functions receive either `VideoContext` or explicit `original_video_path`/`decode_video_path` parameters matched to their responsibility (rename existing ambiguous `video_path` parameters in routed code accordingly); workers receive `working_decode.path` through `WorkerContext`; encode reads `final_encode.path`; banners log source video + decode video in every mode. REQUIRED DELIVERABLE -- routing inventory (part of implementation handoff; the work package is incomplete without it). Per-mode entry format: `mode / entry function / frame-loading function / canonical path variable / decode path variable / validation run`. Covers setup, seed, edit, target, solve Stage-1, solve/refine worker FrameReader construction, analyze, plus encode receiving `original_video_path` via `final_encode.path`; seed/edit/target entries must trace into the UI controller layer. `analyze` console/YAML/HTML outputs carry exactly the field names `canonical_source` and `decode_source`. Validation steps: (a) path-leakage -- run solve on a prepared video; every generated state/cache/output filename uses the original stem (no `.fastread` in any state filename); logs show decode video as `.fastread.mkv`; code-review note names the `tr_paths`/`tr_video_identity` call sites receiving `original_video_path` and the FrameReader call sites receiving `decode_video_path`; (b) setup boundary -- run setup on a prepared video; saved setup/config artifact references the original source path while the banner shows decode video as `.fastread.mkv`. Integration tests for routed modes live here (WP-P4), not in WP-P3.
- Verification commands: `pytest tests/ -k "fastread or walker_flag"`; manual `solve` on a clip with and without fast-read; regression guard checks from M2 exit criteria.
- Obvious follow-ons: `docs/USAGE.md`, `docs/MODES.md`, `docs/modes/PREPARE.md`, changelog.

### Work package: WP-P5 benchmark

- Owner: coder
- Touch points: `tools/benchmark_fastread_video.py` (new).
- Depends on: WP-P4.
- Acceptance criteria: prints scattered-seek median/p95, sequential ms/frame, file sizes, encode time for original vs fast-read; exits non-zero if fast-read scattered seek < 3x faster on the clip under test (script-only gate); `--report-only` collects numbers without failing; never runs under `pytest tests/`.
- Verification commands: `source source_me.sh && python3 tools/benchmark_fastread_video.py -i <clip>.mkv`.
- Obvious follow-ons: numbers into report + `common_tools/README.md` note, changelog.

### Work package: WP-P6 informational solve A/B report

- Owner: coder
- Touch points: report doc under `docs/active_plans/reports/`.
- Depends on: WP-P4.
- Acceptance criteria: same video + seeds solved twice (decode original vs fast-read). Reported: frame count solved, failed-interval counts, severe-interval counts, FWD/BWD p50/p90, confidence-tier counts (existing scoring/review outputs reused, no new quality terms). Visual: `image_evaluator` agent compares overlay renders of representative hard intervals from both runs (existing review/debug overlays where possible) and flags visible identity drift, box drift, or obvious tracking degradation; report lists the exact image files reviewed. Informational only -- no routing gate; if regression is material, report recommends CRF/filter adjustment to the user. Coder does not invent numeric pass/fail tolerances.
- Verification commands: two `solve` runs + image_evaluator review + report filed.
- Obvious follow-ons: changelog; propose CRF/filter adjustment if warranted.

## Data inventory

No persisted registration data. The fast-read video at the deterministic path is the only
artifact:

```
original:  Lyra-Wheeling-IMG_3912.mkv
fast-read: Lyra-Wheeling-IMG_3912.fastread.mkv
state:     Lyra-Wheeling-IMG_3912.track_runner.*   (unchanged; all keyed to original)
```

Everything previously considered for a sidecar (settings, source identity snapshot, probe
records, validation warnings) is derivable live: settings are fixed constants in
`fastread_video.py`; identity and geometry come from `probe_video` on both files at
resolution time; validation notes go to console/debug output. Structural validity =
live-probed geometry exact + duration/fps tolerance + best-effort timestamp alignment +
FrameReader smoke. Per C13: no stored bookkeeping at all.

Probe source: `common_tools.probe_video.probe_video` for metadata;
`common_tools.frame_reader.FrameReader` for smoke reads.

## Acceptance criteria and gates

- Per-patch gate: `pytest tests/ -k <touched-area>` green; `pytest tests/test_pyflakes_code_lint.py` green; changelog entry present.
- Integration gate (M2): full `solve` on a prepared clip completes; state filenames identical to a non-fast-read run; banner shows decode video; review confirms selection logic lives only in `fastread_video.py` (behavior tests, not string grep).
- Structural gate (automatic): live validation inside `resolve_video_context()`, once per run; a valid `VideoContext` authorizes fast-read decode for that run. This is the ONLY gate on routing.
- Quality reporting (M3): benchmark + informational A/B report filed; user sees reports; no blocking sign-off.

## Test and verification strategy

- Unit (pytest, fast lane): structural-validation logic, resolution semantics, path-namespace -- synthetic probe dicts and tmp_path, no ffmpeg, no real video.
- Tooling (`tools/`): benchmark script, run explicitly.
- E2E-style verification: fast-read creation on a real clip (user-run), solve A/B runs.
- Behavioral asserts only (e.g. `context.final_encode.path == original_path`), no key-list or length asserts per PYTEST_STYLE.
- Regression guard (precise): solve on a non-prepared video post-M2 -- state filenames unchanged, geometry output unchanged, no new warnings except the decode-source banner.

## Migration and compatibility policy

- Additive rollout: `prepare` is opt-in and user-run; routing activates the moment a structurally valid fast-read video exists at the deterministic path. Videos without one behave exactly as today.
- Backward compatibility: existing state artifacts, schema, and filenames unchanged; no new persisted artifact schema (no `SCHEMA_VERSION` impact -- the fast-read video is a plain video file).
- Legacy deletion criteria: none -- no legacy path exists.
- Rollback strategy: delete `<stem>.fastread.mkv` -- all modes fall back to original automatically on the next run. No other cleanup exists because nothing else is persisted.

## Risk register

| Risk | Impact | Trigger | Owner | Mitigation |
| --- | --- | --- | --- | --- |
| CRF 23 + hqdn3d + SDR changes pixel statistics enough to degrade solve quality | Lower tracking quality larger than expected | A/B report shows material regression | WP-P6 owner | informational A/B documents magnitude; report recommends CRF/filter adjustment if material |
| Source video replaced/re-remuxed while old fast-read remains | Working modes decode frames from a mismatched source | source edit after prepare | WP-P1 owner | live validation compares fast-read against the CURRENT original every run (frame count/geometry/duration); mismatch raises with remedy |
| Frame-count/timing drift in transcode | Off-by-one frame indexing pipeline-wide | VFR source or ffmpeg timestamp handling | WP-P1 owner | exact frame_count, duration tolerance, best-effort timestamp check, 0/mid/last reads; reject on mismatch |
| State accidentally keyed to fast-read basename | Duplicate seed/cache sets | call site passes decode path to `tr_paths` | WP-P4 owner | dual-path naming rule; `metadata_identity` always original; path-namespace + leakage tests |
| Context bypass: a call site opens FrameReader from `original_video_path` or re-runs discovery itself | Inconsistent decode behavior across modes | new/missed call site post-M2 | WP-P4 owner | routing inventory covers every frame-loading site; mode banners expose actual decode video; per-mode behavior tests; review confirms FrameReader call sites receive `decode_video_path` |
| Seeds drawn on denoised SDR frames vs encode on original | Subtle annotation/encode appearance mismatch | seed boxes on smoothed edges | WP-P4 owner | geometry is identical by contract; banners name decode video; A/B hard-interval review catches drift |
| Per-run validation cost (two probes + three frame reads) | Slower mode startup | every run on prepared videos | WP-P1 owner | probes are milliseconds; the three smoke reads are sequential strategy-0 reads on H.264 (cheap); acceptable overhead vs HEVC decode savings |
| ffmpeg writes beside source need user approval (hook scopes ffmpeg to /tmp) | Agent cannot run transcode unattended | M1 verification | WP-P2 owner | `prepare` is user-run; agent asks user to run the one ffmpeg command or approve passthrough |

## Rollout and release checklist

- [ ] M1 implemented: `prepare` mode + live validation + tests; changelog entry.
- [ ] M2 implemented: all working modes routed; manual solve smoke on one prepared clip; USAGE/MODES/PREPARE docs updated.
- [ ] M3 reports completed: benchmark numbers recorded; informational A/B report filed.
- [ ] Close plan: `git mv` plan doc to `docs/archive/`.

## Patch plan and reporting format

- Patch 1: WP-P1 deterministic path helper + live structural validation.
- Patch 2: WP-P2 `prepare` creation mode + progress UI + status summary.
- Patch 3: WP-P3 unit tests.
- Patch 4 (or 4a/4b per planned split): WP-P4 role selection + routing + logging + mode docs.
- Patch 5: WP-P5 benchmark tool.
- Patch 6: WP-P6 informational A/B report (docs-only).
- Reports use "Patch N" labels; each patch updates `docs/CHANGELOG.md` before handoff.

## Documentation close-out requirements

- Active plan: file this plan as `docs/active_plans/active/fastread_video_prepare_mode_plan.md` at execution start; dispatch packet includes `docs/TRACK_RUNNER_CONTRACT.md` so coders see C13 source text.
- docs/CHANGELOG.md entry: one per patch, categorized per REPO_STYLE day-block sections.
- docs/USAGE.md: `prepare` mode invocation, role policy, selection semantics; canonical workflow updated to show `prepare` as optional-but-recommended first step for 4K HEVC sources.
- docs/MODES.md: add `prepare` entry; new per-mode page `docs/modes/PREPARE.md` matching existing per-mode pages; check whether mode docs are generated (refresh script) before hand-editing.
- Archive / closure notes: on M3 completion, `git mv` plan to `docs/archive/`; benchmark + A/B reports stay in `docs/active_plans/reports/`.

## Open questions and decisions needed

- None -- no-sidecar discovery, role policy, naming, CRF, denoise, and path layout all settled above.
