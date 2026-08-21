# Troubleshooting

Known symptoms, causes, and next steps for common issues.

## Solve or refine rejects an artifact

**Symptom:** A command reports a stale, malformed, or unsupported torso-box or
interval-score artifact.

**Cause:** This repository reads only its current artifact layouts. It does not
migrate prior layouts or partially reuse malformed derived data.

**Mitigation:** Remove the derived interval-score and torso-box artifacts for
that video, then run `solve` from the current seeds. Do not reuse seeds for a
different source video; annotate fresh seeds instead.

## 4K HEVC solve runs are slow

**Symptom:** A solve on a 4K HEVC source takes much longer than expected.

**Cause:** HEVC encodes with a group-of-pictures (GOP) structure. Decoding
a random frame requires the decoder to seek back to the nearest preceding
keyframe and decode forward to the target frame. The walker evaluates
scattered frames, so repeated seeks can dominate runtime on long-GOP media.

Auto-bin (bin_factor 4 at 3840 px width) does not reduce decode cost.
Binning resizes the frame after decode; the seek itself and the keyframe
decode forward are unchanged by any bin factor.

**Mitigation (available now):** Run `prepare` before `setup` to create a fast-read
H.264 working copy beside the original. All working modes then decode from the
fast-read video automatically. See
[PREPARE.md](modes/PREPARE.md) for the full procedure, role policy,
and rollback instructions.

```bash
python3 track_runner.py prepare   # one-time per source file
python3 track_runner.py setup ...
```

**Status:** The production walker uses a byte-bounded sequential residual
pre-pass in [`residual_pre_pass.py`](../track_runner/residual_pre_pass.py).

**User action:** Run `prepare` first (see above). Runs on 4K HEVC sources without
the fast-read video are slow but will complete correctly.

**Background:** Future recordings in a codec with short keyframe intervals
(for example H.264 with a 30-frame GOP) seek faster because less forward
decode work is required per random-access request. This is offered as
context only; the durable fix is `prepare`, not a recording codec change.
