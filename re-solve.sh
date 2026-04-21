#!/bin/sh

. source_me.sh

for json in tr_config/*.seeds.json; do
  video="TRACK_VIDEOS/$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"

  if [ ! -s "$video" ]; then
    continue
  fi

  file "$video"
  #./track_runner/track_runner.py --debug --debug-tracks -i $video solve
  ./track_runner/track_runner.py --debug-tracks -i $video solve
  sleep 1
done
