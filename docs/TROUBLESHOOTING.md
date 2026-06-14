# Troubleshooting

Known symptoms, causes, and next steps for common issues.

## Solve or refine rejects an existing schema v11-v14 artifact

**Symptom:** After the `SCHEMA_VERSION` rollback to 10, a command reports an
existing schema v11/v12/v13/v14 artifact as stale, or `refine`/loaders raise an
unsupported-schema error pointing to a torso-box artifact.

**Cause:** v11-v14 were method-only or diagnostic bumps that stored nothing new
(see [docs/TR_SCHEMA_VERSION_HISTORY.md](TR_SCHEMA_VERSION_HISTORY.md)). They are
no longer accepted as current solver artifacts. Artifacts stamped v11-v14 must be
regenerated under the current method.

**Mitigation:** Run `solve`. It clears the stale artifact and re-solves from
seeds; no manual deletion is needed. Run `solve` before `refine` after the
rollback, since `refine` does not force a full re-solve (contract C7).

## Walker / corpus runs on 4K HEVC sources take hours

**Symptom:** A single video in a corpus walk takes hours to complete
(for example, Lyra-Wheeling-IMG_3912, 3840 x 2160 at 119.94 fps, HEVC,
finished in 6 h 11 m on a 20-interval sample).

**Cause:** HEVC encodes with a group-of-pictures (GOP) structure. Decoding
a random frame requires the decoder to seek back to the nearest preceding
keyframe and decode forward to the target frame. On a 4K HEVC source with
a 119-frame GOP, each random-access seek costs approximately 450-550 ms
single-process, and a median of roughly 2.6 s under 7-way parallel solve
load, versus 6-14 ms for a sequential read. The walker batch path issues
scattered seeks across the file -- one per observation site per frame --
so many small seeks compound into hours. See
[common_tools/README.md](../common_tools/README.md) for the measured
strategy table and per-offset cost breakdown.

Auto-bin (bin_factor 4 at 3840 px width) does not reduce decode cost.
Binning resizes the frame after decode; the seek itself and the keyframe
decode forward are unchanged by any bin factor.

**Mitigation (available now):** Run `prepare` before `setup` to create a fast-read
H.264 working copy beside the original. All working modes then decode from the
fast-read video automatically, reducing per-frame cost to 6-14 ms. See
[docs/modes/PREPARE.md](modes/PREPARE.md) for the full procedure, role policy,
and rollback instructions.

```bash
python3 track_runner.py prepare   # one-time per source file
python3 track_runner.py setup ...
```

**Status (sequential pre-pass fix):** The longer-term access-pattern fix (audit
P16/P17: sequential pre-pass plus cache-guard narrowing in the walker batch path)
is parked in
[active_plans/active/blob_walk_v2_fix_phase_roadmap.md](active_plans/active/blob_walk_v2_fix_phase_roadmap.md)
with the Lyra-Wheeling corpus-120 run as trigger evidence.

**User action:** Run `prepare` first (see above). Runs on 4K HEVC sources without
the fast-read video are slow but will complete correctly.

**Background:** Future recordings in a codec with short keyframe intervals
(for example H.264 with a 30-frame GOP) seek faster because less forward
decode work is required per random-access request. This is offered as
context only; the durable fix is `prepare`, not a recording codec change.
