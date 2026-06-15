#!/bin/sh

. source_me.sh

for json in tr_config/*.seeds.json; do
  #continue
  stem="$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"
  video="TRACK_VIDEOS/${stem}.mkv"

  if [ ! -s "$video" ]; then
    continue
  fi
  echo "================================="
  echo "$video"
  echo $(file -LIb "$video")
  #./track_runner/track_runner.py --workers 1 -i $video prepare
  echo "================================="
  ./track_runner/track_runner.py --workers 2 -i $video solve --yes --auto-bin
  sleep 2
done

for json in tr_config/*.seeds.json; do
  stem="$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"
  video="TRACK_VIDEOS/${stem}.mkv"

  if [ ! -s "$video" ]; then
    continue
  fi
  echo "================================="
  echo "$video"
  echo "================================="
  file "$video"
  ./track_runner/track_runner.py -i $video solve --upgrade --auto-bin
  sleep 5
done
