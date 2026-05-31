#!/usr/bin/env bash
# run_random_walk.sh
# Walk 20 random visible-both intervals per video and report per-video
# in-box motion-heat fractions.
# Does NOT require arguments; constants are hardcoded (single-purpose runner).
# Run directly: ./run_random_walk.sh
#
# For a reproducible selection add: --random-seed 12345

set -euo pipefail

# Resolve repo root via git; never derive from cwd per repo convention.
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Constants -- single-purpose runner, no flags needed.
OUTPUT_DIR="output_smoke_random20"
JOBS=4

# -----------------------------------------------------------------------
echo "=== RANDOM-SAMPLE WALK ==="
echo "Output:  $OUTPUT_DIR"
echo "Workers: $JOBS"
echo "Mode:    20 random visible-both intervals per video"
echo ""

# Source the Python environment and run the windowed Viterbi walker using
# random visible-both interval sampling.  -n 20 sets the sample size per video.
source source_me.sh && python3 tools/blob_walk_v2/make_walk_html_v2.py \
    --walk \
    --random-sample \
    -n 20 \
    -o "$OUTPUT_DIR" \
    -j "$JOBS"

# -----------------------------------------------------------------------
echo ""
echo "=== PER-VIDEO HEAT REPORT ==="

# Loop over each immediate subdirectory of the output dir and print the
# in-box motion-heat fraction for that video.
for d in "$OUTPUT_DIR"/*/; do
    echo "--- $d ---"
    source source_me.sh && python3 tools/blob_walk_v2/check_render_manifest.py -i "$d"
done

echo ""
echo "=== DONE ==="
