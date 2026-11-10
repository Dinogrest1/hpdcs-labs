#!/usr/bin/env bash
# Крок 10, Д1: захоплення в порядку priority DESC, created_at. Задачі трьох пріоритетів порівну,
# інтенсивність 1,3 · X(R), 120 с; W_q за кожним пріоритетом.
. "$(dirname "$0")/lib.sh"
RUN=step10_d1; RATE=$(rate 1.3)
rm -rf results/$RUN; mkdir -p results/$RUN
say "Д1: CLAIM_ORDER=priority, λ = 1,3 · X($R) = $RATE задач/с, 120 с, пріоритети 1, 2, 3 по колу"
clean; workers $R CLAIM_ORDER=priority
echo '$ psql: EXPLAIN підзапиту захоплення'
qt "EXPLAIN SELECT id FROM tasks WHERE status = 'queued' ORDER BY priority DESC, created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
snap $RUN before; sampler_start $RUN; T0=$(date +%s)
gen $RUN -rate "$RATE" -dur 120 -priorities 1,2,3
say "кінець генерації: черга за пріоритетами"
qt "SELECT priority, count(*) FILTER (WHERE status = 'queued') AS queued, count(*) FILTER (WHERE status = 'done') AS done, count(*) AS total FROM tasks GROUP BY priority ORDER BY priority DESC"
wait_empty 600; sampler_stop
collect $RUN "$T0"
qt "SELECT priority, count(*) AS tasks, round(avg(extract(epoch FROM started_at - created_at))::numeric, 3) AS wq_avg_s, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)))::numeric, 3) AS wq_p50_s, round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM started_at - created_at)))::numeric, 3) AS wq_p99_s FROM tasks GROUP BY priority ORDER BY priority DESC"
