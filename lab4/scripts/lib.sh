# Спільні функції кроків серії ЛР 4. Підключається через `. scripts/lib.sh`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
R=5                                   # найбільша кількість воркерів (R з ЛР 3)
URL='http://localhost:8080/telemed-sessions/{id}/tasks'
# Остаточний режим воркера і параметри варіанта 26; workers() задає всі змінні явно.
DEFAULTS="POLL_MS=50 LEASE_SECONDS=10 FAIL_RATE=0 CLAIM_MODE=skip_locked STORE_MODE=idempotent LEASE_ENABLED=1 HEARTBEAT=1 CLAIM_ORDER=created GRACE_SECONDS=25"

ts()  { date '+%F %T'; }
say() { echo "== $(ts) $*"; }
q()   { kubectl exec db-0 -- psql -U telemed -d telemed -X -tA -c "$1"; }       # одне значення
qt()  { echo "psql> $1"; kubectl exec db-0 -- psql -U telemed -d telemed -X -c "$1"; }   # таблицею, з текстом запиту
qf()  { kubectl exec -i db-0 -- psql -U telemed -d telemed -X -v ON_ERROR_STOP=1 -f - < "$1"; }
wpods()    { kubectl get pods -l app=worker --field-selector=status.phase=Running -o jsonpath='{.items[*].metadata.name}'; }
wmetrics() { kubectl get --raw "/api/v1/namespaces/default/pods/$1:9000/proxy/metrics" 2>/dev/null; }

# workers <W> [KEY=VALUE ...] — зупинити пул, задати режим, запустити W воркерів і дочекатися їх.
workers() {
  local w=$1; shift
  local merged=() d k v o pod
  for d in $DEFAULTS; do
    k=${d%%=*}; v=$d
    for o in "$@"; do [ "${o%%=*}" = "$k" ] && v=$o; done
    merged+=("$v")
  done
  kubectl scale deployment/worker --replicas=0 >/dev/null
  kubectl wait --for=delete pod -l app=worker --timeout=120s >/dev/null 2>&1 || true
  kubectl set env deployment/worker "${merged[@]}" >/dev/null
  echo "\$ kubectl set env deployment/worker ${merged[*]}"
  echo "\$ kubectl scale deployment/worker --replicas=$w"
  [ "$w" = 0 ] && return 0
  kubectl scale deployment/worker --replicas="$w" >/dev/null
  kubectl rollout status deployment/worker --timeout=180s >/dev/null
  until [ "$(wpods | wc -w | tr -d ' ')" = "$w" ]; do sleep 0.5; done
  for pod in $(wpods); do
    until wmetrics "$pod" | grep -Eq '^worker_claims_total\{result="(empty|task)"\} [1-9]'; do sleep 0.3; done
  done
  kubectl get pods -l app=worker -o custom-columns=POD:.metadata.name,NODE:.spec.nodeName,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount --no-headers
}

# snap <прогін> <before|after> — знімок /metrics кожного воркера (лише ряди worker_*).
snap() {
  local pod; mkdir -p "results/$1"
  for pod in $(wpods); do wmetrics "$pod" | grep '^worker_' > "results/$1/metrics_$2_$pod.txt" || true; done
}

# Посекундний запис стану бази в results/<прогін>/db_seconds.csv.
sampler_start() {
  mkdir -p "results/$1"
  echo "unix,lock_waiters,locks_not_granted,xacts,queued,running,done,failed" > "results/$1/db_seconds.csv"
  kubectl exec db-0 -- sh -c "$(cat scripts/db_sampler.sh)" >> "results/$1/db_seconds.csv" 2>/dev/null &
  SAMPLER=$!
}
# Чекати лише процес запису: інші фонові процеси кроку (генератор, kubectl logs -f) мають свій час.
sampler_stop() { kubectl exec db-0 -- rm -f /tmp/sampler.run; wait "$SAMPLER" 2>/dev/null || true; }

# gen <прогін> <параметри генератора> — генератор у контейнері з мережею хоста ВМ (як у ЛР 3).
gen() {
  local run=$1; shift; mkdir -p "results/$run"
  docker run --rm --network host -v "$PWD/results":/results telemed4-loadgen:lab4 \
    -mode tasks -url "$URL" -ids 1-100 -out "/results/$run/gen" -label "$run" "$@"
}

# wait_empty [тайм-аут, с] — чекати, поки не лишиться задач у queued і running.
wait_empty() {
  local limit=${1:-300} t0=$SECONDS n
  while :; do
    n=$(q "SELECT count(*) FROM tasks WHERE status IN ('queued', 'running')")
    [ "$n" = 0 ] && { echo "черга порожня: queued = 0, running = 0 (чекали $((SECONDS - t0)) с)"; return 0; }
    [ $((SECONDS - t0)) -ge "$limit" ] && { echo "за $limit с черга не спорожніла: лишилось $n задач у queued і running"; return 0; }
    sleep 0.5
  done
}

# dump <прогін> — таблиці tasks і task_results у CSV (час — секунди епохи). Стовпчик result (jsonb)
# вивантажується окремо для перших 20 задач, щоб файли довгих прогонів не розросталися.
dump() {
  mkdir -p "results/$1"
  kubectl exec db-0 -- psql -U telemed -d telemed -X -c "\\copy (SELECT id, record_id, status, seed, priority, attempts, max_attempts, token, worker_id, round(extract(epoch FROM created_at)::numeric, 6) AS created, round(extract(epoch FROM started_at)::numeric, 6) AS started, round(extract(epoch FROM finished_at)::numeric, 6) AS finished FROM tasks ORDER BY id) TO STDOUT WITH CSV HEADER" > "results/$1/tasks.csv"
  kubectl exec db-0 -- psql -U telemed -d telemed -X -c "\\copy (SELECT task_id, token, worker_id, round(extract(epoch FROM created_at)::numeric, 6) AS created FROM task_results ORDER BY task_id, created_at) TO STDOUT WITH CSV HEADER" > "results/$1/task_results.csv"
  kubectl exec db-0 -- psql -U telemed -d telemed -X -c "\\copy (SELECT task_id, token, worker_id, result FROM task_results ORDER BY task_id, created_at LIMIT 20) TO STDOUT WITH CSV HEADER" > "results/$1/task_results_sample.csv"
}

# wlogs <прогін> — журнал кожного пода worker з тому emptyDir на вузлі: файл містить рядки всіх
# екземплярів контейнера, тоді як kubectl logs віддає лише поточний і попередній.
wlogs() {
  local pod uid node; mkdir -p "results/$1"
  kubectl get pods -l app=worker -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.uid} {.spec.nodeName}{"\n"}{end}' |
  while read -r pod uid node; do
    [ -n "$node" ] || continue
    docker exec "$node" cat "/var/lib/kubelet/pods/$uid/volumes/kubernetes.io~empty-dir/logs/worker.log" \
      > "results/$1/log_$pod.log" 2>/dev/null || true
  done
  # журнали довгих прогонів стискаються; аналіз читає і .log, і .log.gz
  find "results/$1" -name 'log_*.log' -size +1024k -exec gzip -f {} +
}

# stop_proc <под> <STOP|CONT> — сигнал процесу воркера з простору PID вузла (див. крок 5).
stop_proc() {
  local node cid pid
  node=$(kubectl get pod "$1" -o jsonpath='{.spec.nodeName}')
  cid=$(kubectl get pod "$1" -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
  pid=$(docker exec "$node" crictl inspect -o go-template --template '{{.info.pid}}' "$cid")
  echo "\$ docker exec $node kill -$2 $pid   # PID процесу воркера $1 на вузлі"
  docker exec "$node" kill "-$2" "$pid"
}

# rate <множник> — множник · X(R) з прогону кроку 6, задач за секунду
rate() { python3 scripts/calc.py rate "$1"; }

# collect <прогін> <unix початку> — усе, з чого рахується прогін.
collect() {
  snap "$1" after
  kubectl get pods -l app=worker -o wide > "results/$1/pods.txt"
  wlogs "$1"
  dump "$1"
  python3 scripts/promdump.py "$1" "$2" "$(date +%s)"
}

clean() { q "TRUNCATE tasks CASCADE" >/dev/null; }
