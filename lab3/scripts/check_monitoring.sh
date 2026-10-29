#!/usr/bin/env bash
# Крок 5: ряди запитів для кожного пода web, CFS-лічильники контейнера web, replicas_ready, kubectl top.
echo '$ kubectl get pods -A -o wide | grep -E "prometheus|state-metrics|pgexp|metrics-server|db-0"'
kubectl get pods -A -o custom-columns=NS:.metadata.namespace,POD:.metadata.name,NODE:.spec.nodeName,STATUS:.status.phase --no-headers \
  | grep -E "prometheus-mon|state-metrics|pgexp|metrics-server|db-0|operator"
for q in 'sum by (pod) (http_requests_total{pod=~"web-.*"})' \
         'sum by (pod) (container_cpu_cfs_periods_total{container="web"})' \
         'sum by (pod) (container_cpu_cfs_throttled_periods_total{container="web"})' \
         'kube_deployment_status_replicas_ready{deployment="web"}' \
         'pg_up'; do
  echo "\$ PromQL: $q"
  curl -s -G localhost:9090/api/v1/query --data-urlencode "query=$q" | python3 -c '
import sys, json
for r in json.load(sys.stdin)["data"]["result"]:
    m = {k: v for k, v in r["metric"].items() if k in ("pod", "deployment", "instance")}
    print("  ", m, "=", r["value"][1])'
done
echo '$ curl localhost:9090/api/v1/targets | інтервал збору подів web'
curl -s localhost:9090/api/v1/targets | python3 -c '
import sys, json
for t in json.load(sys.stdin)["data"]["activeTargets"]:
    if t["labels"].get("pod", "").startswith("web-"):
        print("  ", t["labels"]["pod"], t["health"], "scrapeInterval", t["scrapeInterval"])'
echo '$ kubectl top pods'; kubectl top pods
echo '$ kubectl top nodes'; kubectl top nodes
