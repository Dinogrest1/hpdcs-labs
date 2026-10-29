#!/usr/bin/env bash
# Один прогін: ./scripts/steprun.sh <прогін> <параметри генератора...>; потім дамп Prometheus.
set -eu
run=$1; shift
kubectl get pods -l app=web -o custom-columns=POD:.metadata.name,NODE:.spec.nodeName --no-headers > "results/pods_$run.txt"
./scripts/lg.sh -url 'http://localhost:8080/telemed-sessions/{id}' -ids 1-100 -conns 40 -per-conn 100 \
  -out "/results/$run" -label "$run" "$@" 2>&1
sleep 20   # cAdvisor оновлює CFS-лічильники раз на 10–15 с
python3 scripts/promdump.py "$run"
