#!/usr/bin/env bash
# Крок 9: FAIL_RATE = 0,15, R воркерів, 0,7 · X(R), 300 с; у середині прогону — сценарій Ф5:
# L = t_cpu / 2. Два прогони: Ф5 без heartbeat і Ф5 з heartbeat.
. "$(dirname "$0")/lib.sh"
RATE=$(rate 0.7); LF5=$(python3 scripts/calc.py lease)
for CASE in "step9_nohb 0" "step9_hb 1"; do
  set -- $CASE; RUN=$1; HB=$2
  rm -rf results/$RUN results/${RUN}_phase1; mkdir -p results/$RUN results/${RUN}_phase1
  say "FAIL_RATE = 0,15; λ = $RATE задач/с, 300 с; на 150-й секунді L = t_cpu / 2 = $LF5 с, HEARTBEAT = $HB"
  clean; workers $R FAIL_RATE=0.15
  sampler_start $RUN; T0=$(date +%s)
  gen $RUN -rate "$RATE" -dur 300 > results/$RUN/gen.out &
  GEN=$!
  sleep 148
  say "перед сценарієм: поди першої половини прогону (L = 10 с, heartbeat)"
  echo '$ kubectl get pods -l app=worker'; kubectl get pods -l app=worker | tee results/${RUN}_phase1/pods.txt
  wlogs ${RUN}_phase1
  date +%s > results/$RUN/scenario_unix.txt
  echo "\$ kubectl set env deployment/worker LEASE_SECONDS=$LF5 HEARTBEAT=$HB   # $(ts), сценарій Ф5"
  kubectl set env deployment/worker LEASE_SECONDS=$LF5 HEARTBEAT=$HB
  kubectl rollout status deployment/worker --timeout=120s | tail -1 || true
  date +%s > results/$RUN/scenario_done_unix.txt
  wait $GEN; cat results/$RUN/gen.out
  say "кінець генерації: поди другої половини прогону"
  echo '$ kubectl get pods -l app=worker'; kubectl get pods -l app=worker
  qt "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status"
  wait_empty 180; sampler_stop
  collect $RUN "$T0"
  qt "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status"
  qt "SELECT attempts, count(*) FROM tasks GROUP BY attempts ORDER BY attempts"
  qt "SELECT count(*) AS tasks_with_more_than_one_result FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d"
  echo "\$ рядки журналу про нуль оновлених рядків: перша половина / друга половина"
  echo "  перша:  $(cat results/${RUN}_phase1/log_*.log 2>/dev/null | grep -Ec 'онов(ив|ило) 0 рядків' || true)"
  echo "  друга:  $( (cat results/$RUN/log_*.log 2>/dev/null; gzip -dc results/$RUN/log_*.log.gz 2>/dev/null) | grep -Ec 'онов(ив|ило) 0 рядків' || true)"
done
