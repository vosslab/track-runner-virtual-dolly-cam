#!/bin/sh

. source_me.sh

for json in tr_config/*.seeds.json; do
  #continue
  video="TRACK_VIDEOS/$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"

  if [ ! -s "$video" ]; then
    continue
  fi
  echo "================================="
  echo "$video"
  echo "================================="
  file "$video"
  ./track_runner/track_runner.py --workers 1 -i $video solve --yes
  sleep 5
done

for json in tr_config/*.seeds.json; do
  video="TRACK_VIDEOS/$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"

  if [ ! -s "$video" ]; then
    continue
  fi
  echo "================================="
  echo "$video"
  echo "================================="
  file "$video"
  ./track_runner/track_runner.py -i $video solve --upgrade
  sleep 5
done
