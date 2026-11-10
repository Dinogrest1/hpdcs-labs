#!/usr/bin/env bash
# Крок 8: коректна зупинка за SIGTERM. Під прогоном 0,7 · X(R) — kubectl rollout restart deployment/worker.
# Другий прогін з GRACE_SECONDS=0 показує гілку повернення задачі в чергу: воркер не чекає завершення.
. "$(dirname "$0")/lib.sh"
RATE=$(rate 0.7)
for CASE in "step8 25" "step8_grace0 0"; do
  set -- $CASE; RUN=$1; GRACE=$2
  rm -rf results/$RUN; mkdir -p results/$RUN
  say "λ = 0,7 · X($R) = $RATE задач/с, 60 с; GRACE_SECONDS = $GRACE; на 20-й секунді rollout restart"
  clean; workers $R GRACE_SECONDS=$GRACE
  OLD=$(wpods)
  for P in $OLD; do kubectl logs -f "$P" > "results/$RUN/exit_$P.log" 2>/dev/null & done
  kubectl get pods -l app=worker -w --no-headers -o custom-columns='POD:.metadata.name,PHASE:.status.phase,EXIT:.status.containerStatuses[0].state.terminated.exitCode,REASON:.status.containerStatuses[0].state.terminated.reason' > results/$RUN/watch.txt 2>/dev/null &
  WATCH=$!
  snap $RUN before; sampler_start $RUN; T0=$(date +%s)
  gen $RUN -rate "$RATE" -dur 60 > results/$RUN/gen.out &
  GEN=$!
  sleep 21
  echo "\$ kubectl rollout restart deployment/worker   # $(ts)"
  kubectl rollout restart deployment/worker
  date +%s > results/$RUN/restart_unix.txt
  kubectl rollout status deployment/worker --timeout=180s | tail -1
  wait $GEN; cat results/$RUN/gen.out
  wait_empty 300; sampler_stop
  kill $WATCH 2>/dev/null || true; sleep 1
  collect $RUN "$T0"
  echo '$ kubectl get pods -l app=worker -w -o custom-columns=POD,PHASE,EXIT,REASON | grep -v "<none>"   # коди завершення старих подів'
  grep -v '<none> *<none>' results/$RUN/watch.txt | sort -u
  echo '$ tail -n 2 журналів старих подів (kubectl logs -f, запущений до перезапуску)'
  for P in $OLD; do echo "--- $P"; grep -E 'SIGTERM|повернуто в чергу|вихід з кодом' "results/$RUN/exit_$P.log" | tail -3; done
  echo "\$ grep -c 'повернуто в чергу' results/$RUN/exit_*.log"
  cat results/$RUN/exit_*.log | grep -c 'повернуто в чергу' || true
  qt "SELECT count(*) AS tasks, count(*) FILTER (WHERE status = 'done') AS done, count(*) FILTER (WHERE status = 'failed') AS failed, count(*) FILTER (WHERE attempts > 1) AS attempts_gt_1 FROM tasks"
  qt "SELECT count(*) AS result_rows, (SELECT count(*) FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d) AS tasks_with_more_than_one_result FROM task_results"
  qt "SELECT id, status, attempts, token, worker_id FROM tasks WHERE attempts > 1 ORDER BY id LIMIT 10"
done
