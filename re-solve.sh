#!/bin/sh

. source_me.sh

for json in tr_config/*.seeds.json; do
  video="TRACK_VIDEOS/$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"

  if [ ! -s "$video" ]; then
    continue
  fi
  echo "================================="
  echo "$video"
  echo "================================="
  file "$video"
  #./track_runner/track_runner.py --debug --debug-tracks -i $video solve --yes
  ./track_runner/track_runner.py -i $video solve --yes --debug-paths
  sleep 1
done
