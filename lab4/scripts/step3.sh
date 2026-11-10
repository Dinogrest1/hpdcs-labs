#!/usr/bin/env bash
# Крок 3: наївне захоплення (SELECT і окремий UPDATE), INSERT без ON CONFLICT, R воркерів,
# 300 задач на 100 за секунду; D для T_poll = 50 мс і 1 мс.
. "$(dirname "$0")/lib.sh"
workers 0 >/dev/null
echo '$ psql -f db/step3_drop_pk.sql'; qf db/step3_drop_pk.sql
for POLL in 50 1; do
  RUN=step3_poll$POLL; rm -rf results/$RUN; mkdir -p results/$RUN
  say "наївне захоплення, T_poll = $POLL мс, $R воркерів"
  clean; workers $R CLAIM_MODE=naive STORE_MODE=plain LEASE_ENABLED=0 HEARTBEAT=0 POLL_MS=$POLL
  snap $RUN before; sampler_start $RUN; T0=$(date +%s)
  gen $RUN -count 300 -rate 100
  wait_empty 120; sampler_stop
  collect $RUN "$T0"
  qt "SELECT count(*) AS n FROM tasks"
  qt "SELECT count(*) AS result_rows, count(DISTINCT task_id) AS tasks_with_result FROM task_results"
  qt "SELECT count(*) AS n_dup FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d"
  qt "SELECT task_id, count(*) AS results, array_agg(DISTINCT worker_id) AS workers FROM task_results GROUP BY task_id HAVING count(*) > 1 ORDER BY task_id LIMIT 3"
  echo "\$ cat results/$RUN/log_*.log | grep -o 'task=[0-9]* done' | sort | uniq -d | wc -l   # задачі з більше ніж одним рядком done"
  cat results/$RUN/log_*.log | grep -o 'task=[0-9]* done' | sort | uniq -d | wc -l
  echo "\$ python3 scripts/calc.py dup $RUN"; python3 scripts/calc.py dup $RUN
done
