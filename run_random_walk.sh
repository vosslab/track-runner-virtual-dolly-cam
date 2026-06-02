#!/usr/bin/env bash
# run_random_walk.sh
# Walk random visible-both intervals for EACH video listed in
# data/outdoor_corpus.txt (SAMPLE_N per video), then report per-video
# in-box motion-heat fractions.
# Does NOT require arguments; constants are hardcoded (single-purpose runner).
# Run directly: ./run_random_walk.sh
#
# For a reproducible selection, set RANDOM_SEED to an integer below.

# -e + pipefail for loud failure; NOT -u (nounset): `source source_me.sh` pulls
# in profile scripts (e.g. bash_completion) that reference interactive-only vars
# like $PS1, which are unset in this non-interactive shell and would abort.
set -eo pipefail

# Resolve repo root via git; never derive from cwd per repo convention.
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Constants -- single-purpose runner, no flags needed.
CORPUS_FILE="data/outdoor_corpus.txt"
OUTPUT_DIR="corpus_walk"
SAMPLE_N=20
# Set to an integer for a reproducible sample; leave empty for a fresh sample.
RANDOM_SEED=""

# Build the optional --random-seed argument. A plain string (not an array) so
# the empty case word-splits to nothing (portable to old bash, no empty-array
# expansion edge cases).
SEED_FLAG=""
if [ -n "$RANDOM_SEED" ]; then
    SEED_FLAG="--random-seed $RANDOM_SEED"
fi

# -----------------------------------------------------------------------
echo "=== RANDOM-SAMPLE WALK ==="
echo "Corpus:  $CORPUS_FILE"
echo "Output:  $OUTPUT_DIR"
echo "Mode:    $SAMPLE_N random visible-both intervals per video"
echo ""

# make_walk_html_v2.py is video-agnostic: it walks ONE video per invocation.
# Loop over each corpus line, skipping comment (#) and blank lines and trimming
# any trailing whitespace, passing one video at a time.
grep -vE '^[[:space:]]*(#|$)' "$CORPUS_FILE" | sed 's/[[:space:]]*$//' | while read -r video_path; do
    echo "--- walking $video_path ---"
    source source_me.sh && python3 tools/blob_walk_v2/make_walk_html_v2.py \
        --walk \
        -v "$video_path" \
        -n "$SAMPLE_N" \
        -o "$OUTPUT_DIR" \
        $SEED_FLAG
done

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
