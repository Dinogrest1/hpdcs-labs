#!/usr/bin/env bash
# Один ступінчастий прогін: ./scripts/steprun.sh <прогін> <параметри генератора...>
# Паралельно знімає docker stats, після прогону зберігає ряди Prometheus.
set -eu
run=$1; shift
rm -f "results/stats_$run.jsonl"
./scripts/statsloop.sh "results/stats_$run.jsonl" & sp=$!
./scripts/lg.sh -out "/results/$run" -label "$run" "$@" 2>&1
kill $sp; wait $sp 2>/dev/null || true
sleep 6   # останній збір Prometheus після кінця прогону
python3 scripts/promdump.py "$run"
