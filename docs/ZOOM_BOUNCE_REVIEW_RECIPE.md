# Zoom-bounce review recipe

How to combine the four assessment tools to evaluate a corpus of
encoded variant outputs and decide whether the Step 3.6 fit-to-source
ratchet hypothesis explains the residual zoom bounce reported after
the 2026-05-02 crop changes.

The investigation plan that scopes this work is at
`/Users/vosslab/.claude/plans/declarative-shimmying-brooks.md`. The
hypothesis under test is described in
[docs/CHANGELOG.md](CHANGELOG.md) (entry 2026-05-02) and in
[docs/TRACK_RUNNER_DESIGN.md](TRACK_RUNNER_DESIGN.md).

## Required corpus layout

The user produces a directory tree like:

```
output_smoke/zoom_bounce/
    baseline/
        IMG_3702.mkv
        IMG_3707.mkv
        IMG_3823.mkv
        IMG_3830.mkv
        pixel_zoom_comparison.csv
    path_a/
        IMG_3702.mkv
        ...
        pixel_zoom_comparison.csv
    alpha_025/
        ...
```

`baseline/` is required and acts as the reference. Other variant
sub-directories are optional; the ranking tool ignores variants whose
videos do not appear in `baseline/`.

Each `pixel_zoom_comparison.csv` is the output of
[tools/assess_pixel_zoom.py](../tools/assess_pixel_zoom.py) in batch
mode against that variant's directory.

## Workflow

### Step 1: Run assess_pixel_zoom.py per variant

For each variant sub-directory:

```
source source_me.sh && python tools/assess_pixel_zoom.py \
    -d output_smoke/zoom_bounce/baseline/ -p '*.mkv'
source source_me.sh && python tools/assess_pixel_zoom.py \
    -d output_smoke/zoom_bounce/path_a/ -p '*.mkv'
source source_me.sh && python tools/assess_pixel_zoom.py \
    -d output_smoke/zoom_bounce/alpha_025/ -p '*.mkv'
```

Each run writes `pixel_zoom_comparison.csv` in the variant directory.

### Step 2: Rank the variants

```
source source_me.sh && python tools/rank_zoom_variants.py \
    -d output_smoke/zoom_bounce/
```

Produces `output_smoke/zoom_bounce/RANKING.md` with per-video deltas
versus baseline and a per-variant `win` / `tie` / `loss` verdict on
the default metric `bounce_rate_per_s`.

To rank on a different metric, e.g. `zoom_velocity_log_p95`:

```
source source_me.sh && python tools/rank_zoom_variants.py \
    -d output_smoke/zoom_bounce/ -m zoom_velocity_log_p95
```

For metrics where higher is better (e.g. `valid_frame_fraction`),
add `-H`:

```
source source_me.sh && python tools/rank_zoom_variants.py \
    -d output_smoke/zoom_bounce/ -m valid_frame_fraction -H
```

### Step 3: Find hotspots in the worst-ranked baseline videos

After RANKING.md identifies which baseline videos are worst, focus
visual review on the worst windows of those videos:

```
source source_me.sh && python tools/find_zoom_hotspots.py \
    -i output_smoke/zoom_bounce/baseline/IMG_3707.mkv \
    -n 5 -w 5 -c
```

Writes `output_smoke/zoom_bounce/baseline/IMG_3707.hotspots.md`
listing the top-5 hotspot windows by smoothed bounce intensity, and
extracts `IMG_3707.hotspot_<rank>.mkv` clips around each (15-second
clips per the `-w 5` window doubled by the centered span).

### Step 4: Test the Step 3.6 ratchet hypothesis

For each baseline video that scored badly, correlate per-frame bounce
with runner-to-source-edge distance using the solved-interval data
that lives next to the SOURCE video (not the encoded output):

```
source source_me.sh && python tools/correlate_bounce_with_edge.py \
    -i output_smoke/zoom_bounce/baseline/IMG_3707.mkv \
    -s ~/nsh/track-runner-virtual-dolly-cam/TRACK_VIDEOS/Hononega-Varsity_4x400m-IMG_3707.mkv
```

Writes the scatter plot PNG and a per-quartile bounce intensity table
in markdown. A positive Spearman correlation (rho > 0.4) is the
signature of edge-driven bounce: as the runner approaches a source
edge, bounce intensity rises. A near-zero correlation rejects the
ratchet hypothesis.

If the solved-interval artifacts live in a non-default location, pass
`-d /path/to/data_dir`.

### Step 5: Characterize bounce timescale

For one or two baseline videos, check whether bounce energy sits above
or below the EMA cutoff:

```
source source_me.sh && python tools/spectrum_zoom_bounce.py \
    -i output_smoke/zoom_bounce/baseline/IMG_3707.mkv
```

Writes the power-spectrum PNG. Energy peaks below the EMA cutoff
(annotated as a red dashed line) point at slow bounce that the EMA
is not designed to attenuate (sustained ratchet behavior, slow
drift). Energy peaks above the cutoff suggest residual high-frequency
leakage; investigate source-content motion and per-frame integer
rounding.

The default `--tau-frames 6` matches
`crop_post_smooth_size_strength=0.15` (current default). Override
with `-t 4` for the higher alpha=0.25 variant or `-t 13` for the
old alpha=0.05 baseline.

### Step 6 (optional): Side-by-side visual review

Generate a side-by-side movie for direct A/B comparison without
window-switching during playback:

```
ffmpeg -i output_smoke/zoom_bounce/baseline/IMG_3707.mkv \
       -i output_smoke/zoom_bounce/path_a/IMG_3707.mkv \
       -filter_complex hstack \
       -c:v libx264 -crf 18 \
       sxs_IMG_3707.mkv
```

For portrait sources use `vstack` instead of `hstack`. For three-way
comparison, chain two filters:

```
ffmpeg -i baseline.mkv -i path_a.mkv -i path_b.mkv \
       -filter_complex "[0:v][1:v]hstack[t];[t][2:v]hstack" \
       -c:v libx264 -crf 18 \
       sxs_3way.mkv
```

The hotspot clips from Step 3 are good inputs to side-by-side review
since they are short and contain the worst regions.

## Reading the results

What confirms the Step 3.6 fit-to-source ratchet hypothesis:

- `correlate_bounce_with_edge.py` reports Spearman rho >= 0.4 on at
  least two baseline videos.
- The per-quartile table in `*.bounce_edge.md` shows monotonically
  increasing bounce intensity as the edge gap quartile shrinks (Q4
  furthest, Q1 nearest).
- `spectrum_zoom_bounce.py` shows dominant energy below the EMA
  cutoff annotation: bounce is in a band the EMA cannot remove.
- `rank_zoom_variants.py` shows Path A (clamp removed) wins clearly
  on `bounce_rate_per_s` versus baseline.
- Path B (one-sided EMA after the second re-fit) wins by a similar
  margin if it was included.
- Alpha-only control (alpha=0.25) does NOT win; small reduction at
  best. Confirms that raising alpha alone cannot fix the asymmetry.

What rejects the hypothesis:

- Spearman rho near zero across multiple videos.
- Per-quartile table flat: bounce intensity does not depend on edge
  proximity.
- Spectrum dominant frequencies sit above the EMA cutoff (residual
  is high-frequency leakage, not slow ratchet).
- Path A does NOT win, or wins by a smaller margin than alpha-only.
- Visual review on the hotspot clips finds that the bouncy regions
  align with source-content motion (camera pans, runners crossing
  scene cuts) rather than crop-edge proximity.

The four artifacts together (RANKING.md + per-variant ranking, two
correlator outputs, two spectrum outputs, hotspot clips) are the
inputs to `output_smoke/zoom_bounce/EVIDENCE/DECISION.md` per
Milestone 3 of the investigation plan.

## Caveats

- Source content motion (camera pans, hand-held shake, scene cuts)
  shows up in `assess_pixel_zoom.py` as scale change. Hotspots near a
  source pan are not necessarily crop-pipeline bounce. Cross-check
  the source-video timeline.
- The pre-encode tool
  [tools/analyze_crop_path_stability.py](../tools/analyze_crop_path_stability.py)
  measures `height_jerk_p95` directly on the crop rectangles
  (no rendered-pixel involvement). It can be used as a sanity cross-
  check, but the encoder applies integer rounding and ffmpeg may
  introduce small additional jitter; rendered-pixel measurement is
  the authoritative bounce signal.
- Hotspot windows are picked greedily with non-max suppression. A
  long sustained bounce region may produce one hotspot that
  represents the whole region, not multiple peaks within it.
- `correlate_bounce_with_edge.py` treats the runner's torso bbox as
  a rigid rectangle. Edge gap is the minimum of four sides. Negative
  values mean the runner extends past the source edge (rare but
  possible on edge-of-frame intervals).
