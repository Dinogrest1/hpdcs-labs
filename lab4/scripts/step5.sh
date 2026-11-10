#!/usr/bin/env bash
# Крок 5: lease, heartbeat, fencing token, ідемпотентний запис. 2 воркери, 100 задач;
# один воркер зупинено сигналом STOP на 2 · L = 20 с і відновлено сигналом CONT.
. "$(dirname "$0")/lib.sh"
RUN=step5; rm -rf results/$RUN; mkdir -p results/$RUN
L=10
clean; workers 2
V=$(wpods | cut -d' ' -f1)
echo "зупинятиметься воркер $V"
echo "\$ kubectl exec $V -- sh -c 'kill -STOP 1; sleep 1; grep State /proc/1/status; kill -CONT 1'"
kubectl exec "$V" -- sh -c 'kill -STOP 1; sleep 1; grep State /proc/1/status; kill -CONT 1'
echo "# процес лишився в стані S: ядро не доставляє STOP процесу з PID 1 зсередини його простору PID."
echo "# Той самий сигнал надсилається з вузла, де воркер не є PID 1."
snap $RUN before; sampler_start $RUN; T0=$(date +%s)
gen $RUN -count 100 -rate 100 > results/$RUN/gen.out &
GEN=$!
until [ "$(q "SELECT count(*) FROM tasks WHERE status = 'done'")" -ge 10 ]; do sleep 0.1; done
stop_proc "$V" STOP
TSTOP=$(date +%s.%N)
say "воркер зупинено; задача, яка була в нього в обробці (до)"
kubectl exec "$V" -- grep State /proc/1/status
qt "SELECT id, status, attempts, token, worker_id, lease_until FROM tasks WHERE status = 'running' AND worker_id = '$V'"
TID=$(q "SELECT id FROM tasks WHERE status = 'running' AND worker_id = '$V' LIMIT 1")
wait "$GEN"; cat results/$RUN/gen.out
[ -n "$TID" ] || { echo "на момент зупинки у воркера не було задачі в обробці: повторіть крок"; stop_proc "$V" CONT; sampler_stop; exit 1; }
sleep $((L + 5))
say "через $((L + 5)) с після зупинки: lease сплив, задачу повернуто в чергу і забрано"
qt "SELECT id, status, attempts, token, worker_id FROM tasks WHERE id = $TID"
# до відновлення рівно 2 · L від моменту зупинки
sleep "$(python3 -c "import time; print(max(0, $TSTOP + 2 * $L - time.time()))")"
stop_proc "$V" CONT
echo "воркер був зупинений $(python3 -c "import time; print(round(time.time() - $TSTOP, 1))") с"
sleep 3
say "через 3 с після відновлення (після)"
kubectl exec "$V" -- grep State /proc/1/status
qt "SELECT id, status, attempts, token, worker_id, round(extract(epoch FROM finished_at - created_at)::numeric, 2) AS seconds_to_done FROM tasks WHERE id = $TID"
qt "SELECT task_id, token, worker_id, result FROM task_results WHERE task_id = $TID"
qt "SELECT count(*) AS result_rows_for_task FROM task_results WHERE task_id = $TID"
wait_empty 60; sampler_stop
collect $RUN "$T0"
echo "\$ grep 'task=$TID ' results/$RUN/log_*.log   # журнали обох воркерів про цю задачу"
grep -H "task=$TID " results/$RUN/log_*.log | sed "s|results/$RUN/log_||"
qt "SELECT status, count(*), max(attempts) AS max_attempts_used FROM tasks GROUP BY status"
qt "SELECT count(*) AS result_rows, count(DISTINCT task_id) AS tasks FROM task_results"
echo "$TID $V" > results/$RUN/victim.txt
echo "\$ python3 scripts/calc.py stat $RUN"; python3 scripts/calc.py stat $RUN
