#!/usr/bin/env bash
# Перевірка: дані переживають down/up, а після down -v база порожня.
set -u
R=http://localhost:8080/telemed-sessions
wait200() { until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8080/healthz)" = 200 ]; do sleep 0.2; done; }
total() { curl -s "$R?limit=1" | python3 -c 'import sys,json;print("total =",json.load(sys.stdin)["total"])'; }
B='{"patient_code":"P-777","doctor":"Петренко","channel":"chat","started_at":"2026-10-11T09:00:00+03:00","duration_min":15,"status":"scheduled"}'
echo "\$ POST x2"; for i in 1 2; do curl -s -o /dev/null -w '<- HTTP %{http_code}\n' -X POST $R -H 'Content-Type: application/json' -d "$B"; done
echo "\$ GET /telemed-sessions?limit=1"; total
echo "\$ docker compose down"; docker compose down 2>&1 | tail -1
echo "\$ docker compose up -d"; docker compose up -d >/dev/null 2>&1; wait200
echo "\$ GET /telemed-sessions?limit=1   # після down/up"; total
echo "\$ docker compose stop db; GET /healthz"; docker compose stop db >/dev/null 2>&1
curl -s -w '\n<- HTTP %{http_code}\n' localhost:8080/healthz
echo "\$ docker compose start db; GET /healthz"; docker compose start db >/dev/null 2>&1; sleep 2; wait200
curl -s -w '\n<- HTTP %{http_code}\n' localhost:8080/healthz
echo "\$ docker compose down -v"; docker compose down -v 2>&1 | grep -i volume
echo "\$ docker compose up -d"; docker compose up -d >/dev/null 2>&1; wait200
echo "\$ GET /telemed-sessions?limit=1   # після down -v/up"; total
