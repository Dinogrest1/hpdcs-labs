#!/usr/bin/env bash
# Крок 7: закон Літтла для черги. R воркерів; 0,7 · X(R) протягом 180 с, далі 1,3 · X(R) протягом 120 с.
. "$(dirname "$0")/lib.sh"
for CASE in "07 0.7 180" "13 1.3 120"; do
  set -- $CASE; RUN=step7_$1; RATE=$(rate "$2"); DUR=$3
  rm -rf results/$RUN; mkdir -p results/$RUN
  say "λ = $2 · X($R) = $RATE задач/с, $DUR с"
  clean; workers $R
  snap $RUN before; sampler_start $RUN; T0=$(date +%s)
  gen $RUN -rate "$RATE" -dur "$DUR"
  qt "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status"
  wait_empty 600; sampler_stop
  collect $RUN "$T0"
  echo "\$ python3 scripts/calc.py stat $RUN"; python3 scripts/calc.py stat $RUN
done
