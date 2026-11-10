#!/usr/bin/env bash
# Крок 4: захоплення однією командою. Спершу FOR UPDATE SKIP LOCKED, далі FOR UPDATE без SKIP LOCKED;
# серія кроку 3 з T_poll = 1 мс. Первинний ключ task_results повернуто.
. "$(dirname "$0")/lib.sh"
workers 0 >/dev/null
echo '$ psql -f db/step4_restore_pk.sql'; qf db/step4_restore_pk.sql
for MODE in skip_locked for_update; do
  RUN=step4_$MODE; rm -rf results/$RUN; mkdir -p results/$RUN
  say "захоплення $MODE, T_poll = 1 мс, $R воркерів"
  clean; workers $R CLAIM_MODE=$MODE STORE_MODE=plain LEASE_ENABLED=0 HEARTBEAT=0 POLL_MS=1
  snap $RUN before; sampler_start $RUN; T0=$(date +%s)
  gen $RUN -count 300 -rate 100
  wait_empty 120; sampler_stop
  collect $RUN "$T0"
  qt "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'done') AS done FROM tasks"
  qt "SELECT count(*) AS result_rows, (SELECT count(*) FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d) AS n_dup FROM task_results"
  echo "\$ python3 scripts/calc.py dup $RUN"; python3 scripts/calc.py dup $RUN
  echo "\$ python3 scripts/calc.py stat $RUN"; python3 scripts/calc.py stat $RUN
  echo "\$ sort -t, -k2 -nr results/$RUN/db_seconds.csv | head -3   # unix, Lock у pg_stat_activity, granted = false у pg_locks, …"
  sed 1d results/$RUN/db_seconds.csv | sort -t, -k2 -nr | head -3
done
