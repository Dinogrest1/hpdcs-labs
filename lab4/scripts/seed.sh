#!/usr/bin/env bash
# Засів: 100 записів через POST localhost:8080/telemed-sessions (NodePort web); id у сценарії — 1..100.
# Якщо записи 1..100 уже є (кластер лишився з ЛР 3), нічого не вставляє і лише показує їх.
set -eu
cd "$(dirname "$0")/.."
q() { kubectl exec db-0 -- psql -U telemed -d telemed -X -tA -c "$1"; }
have=$(q "SELECT count(*) FROM telemed_sessions WHERE id BETWEEN 1 AND 100")
echo "записів з id 1..100 до засіву: $have"
if [ "$have" != 100 ]; then
./scripts/vmsh.sh '
ch="video audio chat"; st="scheduled in_progress completed cancelled"
i=1
while [ $i -le 100 ]; do
  c=$(echo $ch | cut -d" " -f$((i%3+1))); s=$(echo $st | cut -d" " -f$((i%4+1)))
  curl -s -o /dev/null -X POST http://localhost:8080/telemed-sessions -H "Content-Type: application/json" \
    -d "{\"patient_code\":\"P-$(printf %06d $i)\",\"doctor\":\"Лікар $((i%7+1))\",\"channel\":\"$c\",\"started_at\":\"2026-10-0$((i%9+1))T09:00:00+03:00\",\"duration_min\":$(( (i%18+1)*5 )),\"status\":\"$s\"}"
  i=$((i+1))
done'
fi
echo '$ psql: SELECT count(*), min(id), max(id) FROM telemed_sessions WHERE id BETWEEN 1 AND 100'
q "SELECT count(*), min(id), max(id) FROM telemed_sessions WHERE id BETWEEN 1 AND 100"
echo '$ psql: SELECT channel, count(*) FROM telemed_sessions WHERE id BETWEEN 1 AND 100 GROUP BY channel ORDER BY channel'
q "SELECT channel, count(*) FROM telemed_sessions WHERE id BETWEEN 1 AND 100 GROUP BY channel ORDER BY channel"
echo '$ curl "localhost:8080/telemed-sessions?limit=2"'
./scripts/vmsh.sh 'curl -s "http://localhost:8080/telemed-sessions?limit=2"; echo'
