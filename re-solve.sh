#!/bin/sh

. source_me.sh

run_solve() {
  "$@" > "$log" 2>&1
  status=$?
  cat "$log"
  return "$status"
}

for json in tr_config/*.seeds.json; do
  stem="$(basename "$json" | sed 's/\.track_runner\.seeds\.json$//')"
  video="TRACK_VIDEOS/${stem}.mkv"

  if [ "$#" -gt 0 ]; then
    selected=0
    for requested_stem in "$@"; do
      if [ "$stem" = "$requested_stem" ]; then
        selected=1
      fi
    done
    if [ "$selected" -eq 0 ]; then
      continue
    fi
  fi

  if [ ! -s "$video" ]; then
    continue
  fi
  log="solve_${stem}.log"
  echo "================================="
  echo "$video"
  file -LIb "$video"
  echo "================================="
  ./track_runner/track_runner.py --workers 1 -i "$video" prepare || exit $?
  run_solve ./track_runner/track_runner.py --workers 1 -i "$video" solve --yes || exit $?
done
