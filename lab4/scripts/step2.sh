#!/usr/bin/env bash
# Крок 2: воркер у Deployment, метрики, збір Prometheus, 20 задач на одному воркері, t_io і t_cpu.
. "$(dirname "$0")/lib.sh"
RUN=step2; rm -rf results/$RUN; mkdir -p results/$RUN
clean; workers 1
P=$(wpods)
echo '$ kubectl get deployment worker -o jsonpath (replicas у маніфесті, ресурси, terminationGracePeriodSeconds)'
kubectl get deployment worker -o jsonpath='{"resources: "}{.spec.template.spec.containers[0].resources}{"\nterminationGracePeriodSeconds: "}{.spec.template.spec.terminationGracePeriodSeconds}{"\n"}'
grep -c 'replicas' k8s/worker.yaml | sed 's/^/рядків з полем replicas у k8s\/worker.yaml (лише коментар): /'
echo "\$ kubectl exec $P -- env | grep ...   # WORKER_ID з імені пода; пароль не друкується"
kubectl exec "$P" -- env | grep -E '^(WORKER_ID|DB_HOST|WEB_HOST|RESOURCE|POLL_MS|LEASE_SECONDS|FAIL_RATE|CLAIM_MODE|STORE_MODE|LEASE_ENABLED|HEARTBEAT|Q_LO|Q_HI)=' | sort
sleep 12
echo '$ curl localhost:9090/api/v1/targets   # ціль worker у Prometheus'
curl -s localhost:9090/api/v1/targets | python3 -c '
import sys, json
for t in json.load(sys.stdin)["data"]["activeTargets"]:
    if t["labels"].get("pod", "").startswith(("worker-", "web-")):
        print("  ", t["labels"]["pod"], t["scrapeUrl"], t["health"], "scrapeInterval", t["scrapeInterval"])'
snap $RUN before; T0=$(date +%s)
echo '$ генератор: 20 задач, 20 за секунду'
gen $RUN -count 20 -rate 20
wait_empty 60
collect $RUN "$T0"
echo "\$ curl $P:9000/metrics | grep -E 'worker_(tasks|claims)_total|in_flight|_(sum|count)'"
grep -E '^worker_(tasks_total|claims_total|tasks_in_flight)|_(sum|count)\{' results/$RUN/metrics_after_$P.txt
echo '$ python3 scripts/calc.py timing step2'; python3 scripts/calc.py timing $RUN
echo "\$ журнал воркера (останні 6 рядків)"; tail -6 results/$RUN/log_$P.log
echo '$ head -4 results/step2/gen.tasks.csv   # ідентифікатори створених задач'; head -4 results/$RUN/gen.tasks.csv
ID=$(sed -n 2p results/$RUN/gen.tasks.csv | cut -d, -f6)
echo "\$ curl localhost:8080/tasks/$ID   # задача з результатом"
./scripts/vmsh.sh "curl -s http://localhost:8080/tasks/$ID; echo"
