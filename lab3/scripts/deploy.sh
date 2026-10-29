#!/usr/bin/env bash
# Повне розгортання з нуля: кластер kind, мітка role=infra, образ, база, міграція, web, моніторинг.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "немає .env: cp .env.example .env і задайте POSTGRES_PASSWORD"; exit 1; }
PASS=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)

echo "== кластер"
if kind get clusters 2>/dev/null | grep -qx telemed; then
  echo "кластер telemed уже існує — продовжуємо з наступних кроків"
else
  kind create cluster --config k8s/kind-config.yaml --wait 120s
fi
kubectl label node telemed-control-plane role=infra --overwrite
kubectl get nodes -L role

echo "== образи сервісу і генератора"
docker build -q -t telemed3-loadgen:lab3 loadgen
docker build -q -t telemed3-web:lab3 .
kind load docker-image telemed3-web:lab3 --name telemed

echo "== база і міграція"
kubectl create secret generic db-secret --from-literal=POSTGRES_PASSWORD="$PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap schema --from-file=schema.sql=db/schema.sql --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/db.yaml
kubectl rollout status statefulset/db --timeout=180s
kubectl get job migrate >/dev/null 2>&1 || kubectl apply -f k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/migrate --timeout=180s
kubectl logs job/migrate | tail -3

echo "== моніторинг"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install mon prometheus-community/kube-prometheus-stack -f helm/kube-prometheus-stack.yaml --wait --timeout 10m
helm upgrade --install pgexp prometheus-community/prometheus-postgres-exporter -f helm/postgres-exporter.yaml --wait
helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system -f helm/metrics-server.yaml --wait

echo "== web"
kubectl apply -f k8s/web.yaml -f k8s/podmonitor.yaml
kubectl rollout status deployment/web --timeout=180s
kubectl get pods -o wide
