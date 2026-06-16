#!/bin/sh

. source_me.sh

for json in tr_config/*.seeds.json; do
  #continue
  stem="$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"
  video="TRACK_VIDEOS/${stem}.mkv"

  if [ ! -s "$video" ]; then
    continue
  fi
  log="solve_${stem}.log"
  echo "================================="
  echo "$video"
  echo $(file -LIb "$video")
  #./track_runner/track_runner.py --workers 1 -i $video prepare
  echo "================================="
  ./track_runner/track_runner.py --workers 2 -i $video solve --yes --bin 1 2>&1 | tee "$log"
  sleep 20
done

for json in tr_config/*.seeds.json; do
  stem="$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"
  video="TRACK_VIDEOS/${stem}.mkv"

  if [ ! -s "$video" ]; then
    continue
  fi
  log="upgrade_${stem}.log"
  echo "================================="
  echo "$video"
  echo "================================="
  file "$video"
  ./track_runner/track_runner.py --workers 2 -i $video solve --upgrade --bin 1 2>&1 | tee "$log"
  sleep 5
done
