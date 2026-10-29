#!/usr/bin/env bash
# Крок 3: дані переживають видалення пода бази.
set -eu
q() { kubectl exec db-0 -- psql -U telemed -d telemed -tAc "$1"; }
echo '$ kubectl get statefulset,pod,pvc,svc -l app=db -o wide'
kubectl get statefulset/db pod/db-0 svc/db -o wide; kubectl get pvc
echo '$ kubectl get pod db-0 -o jsonpath={.spec.nodeName}'; kubectl get pod db-0 -o jsonpath='{.spec.nodeName}'; echo
echo '$ psql: SHOW max_connections'; q "SHOW max_connections"
echo '$ kubectl logs job/migrate'; kubectl logs job/migrate | tail -3
echo "под до: $(kubectl get pod db-0 -o jsonpath='uid={.metadata.uid} старт={.status.startTime}')"
echo "кількість записів до: $(q 'SELECT count(*) FROM telemed_sessions'), max(id) = $(q 'SELECT max(id) FROM telemed_sessions')"
echo '$ kubectl delete pod db-0'; kubectl delete pod db-0
sleep 3
until kubectl get pod db-0 >/dev/null 2>&1; do sleep 1; done
kubectl wait --for=condition=ready pod/db-0 --timeout=120s
echo "под після: $(kubectl get pod db-0 -o jsonpath='uid={.metadata.uid} старт={.status.startTime}')"
echo "кількість записів після: $(q 'SELECT count(*) FROM telemed_sessions'), max(id) = $(q 'SELECT max(id) FROM telemed_sessions')"
