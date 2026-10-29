#!/usr/bin/env bash
# kubectl scale до N реплік і очікування: N готових подів, N кінцевих точок Service, N цілей UP у Prometheus.
set -eu
N=$1
kubectl scale deployment/web --replicas="$N" >/dev/null
if ! kubectl rollout status deployment/web --timeout=180s >/dev/null; then
  echo "ПОМИЛКА: за 180 с не всі $N подів готові:"; kubectl get pods -l app=web -o wide; exit 1
fi
ready=$(kubectl get deployment web -o jsonpath='{.status.readyReplicas}')
[ "$ready" = "$N" ] || { echo "ПОМИЛКА: готових подів $ready замість $N"; kubectl get pods -l app=web -o wide; exit 1; }
until [ "$(kubectl get endpointslices -l kubernetes.io/service-name=web -o jsonpath='{range .items[*].endpoints[?(@.conditions.ready==true)]}x{end}' | wc -c | tr -d ' ')" = "$N" ]; do sleep 1; done
until [ "$(python3 scripts/promq.py 'count(up{pod=~"web-.*"} == 1)')" = "$N" ] \
   && [ "$(python3 scripts/promq.py 'count(up{pod=~"web-.*"})')" = "$N" ]; do sleep 1; done
sleep 10   # дати cAdvisor і HPA-метрикам нові поди
echo "web: $N готових подів, $N кінцевих точок, $N цілей UP"
kubectl get pods -l app=web -o custom-columns=POD:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready --no-headers | sort -k2
