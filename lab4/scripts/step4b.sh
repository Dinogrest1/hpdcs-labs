#!/usr/bin/env bash
# Крок 4, додатковий прогін: ті самі два режими захоплення з непорожньою чергою.
# За 100 задач/с серії кроку 3 черга майже весь час порожня, і воркери рідко змагаються за той самий рядок.
# Тут перед стартом воркерів у черзі 3000 задач: усі 5 воркерів щоразу йдуть до найстаршого рядка.
. "$(dirname "$0")/lib.sh"
for MODE in skip_locked for_update; do
  RUN=step4b_$MODE; rm -rf results/$RUN; mkdir -p results/$RUN
  say "захоплення $MODE, T_poll = 1 мс, $R воркерів, 3000 задач у черзі до старту"
  clean; workers 0 >/dev/null
  kubectl set env deployment/worker CLAIM_MODE=$MODE STORE_MODE=plain LEASE_ENABLED=0 HEARTBEAT=0 POLL_MS=1 >/dev/null
  echo "\$ kubectl set env deployment/worker CLAIM_MODE=$MODE STORE_MODE=plain LEASE_ENABLED=0 HEARTBEAT=0 POLL_MS=1"
  gen $RUN -count 3000 -rate 500
  sampler_start $RUN; T0=$(date +%s)
  echo "\$ kubectl scale deployment/worker --replicas=$R"
  kubectl scale deployment/worker --replicas=$R >/dev/null
  kubectl rollout status deployment/worker --timeout=180s >/dev/null
  wait_empty 300; sampler_stop
  collect $RUN "$T0"
  qt "SELECT count(*) AS result_rows, (SELECT count(*) FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d) AS n_dup FROM task_results"
  echo "\$ python3 scripts/calc.py dup $RUN"; python3 scripts/calc.py dup $RUN
  echo "\$ python3 scripts/calc.py x $RUN"; python3 scripts/calc.py x $RUN
  echo "\$ python3 scripts/calc.py stat $RUN"; python3 scripts/calc.py stat $RUN
  echo "\$ sort -t, -k2 -nr results/$RUN/db_seconds.csv | head -3   # unix, Lock у pg_stat_activity, granted = false у pg_locks, …"
  sed 1d results/$RUN/db_seconds.csv | sort -t, -k2 -nr | head -3
done
