#!/usr/bin/env bash
# Встановлює N екземплярів web, перезапускає балансувальник (щоб він отримав нові адреси)
# і чекає, поки всі N будуть healthy і в стані UP у Prometheus.
set -eu
N=$1
docker compose up -d --scale web="$N" --no-recreate web >/dev/null 2>&1
until [ "$(docker compose ps web --format '{{.Health}}' | grep -c '^healthy$')" = "$N" ]; do sleep 1; done
docker compose restart lb >/dev/null 2>&1
until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8088/healthz)" = 200 ]; do sleep 0.5; done
until [ "$(python3 scripts/promq.py 'sum(up{job="web"})')" = "$N" ] \
   && [ "$(python3 scripts/promq.py 'count(up{job="web"})')" = "$N" ]; do sleep 1; done
echo "web: $N екземпляри healthy, у Prometheus $N цілей UP, lb перезапущено"
