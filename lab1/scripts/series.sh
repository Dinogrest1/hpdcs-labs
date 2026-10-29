#!/usr/bin/env bash
# Серія вимірювань під фіксованою інтенсивністю для заданої кількості вебінстансів.
#   ./scripts/series.sh 1      # один інстанс (+ перевірка генератора і закритий контур)
#   ./scripts/series.sh 2      # два інстанси за балансувальником
set -eu
N=${1:?replicas}
LEVELS=${LEVELS:-"10 100 500 6000"}   # 10/100/500 за умовою + 6000: рівень перевантаження
REPS=${REPS:-3}
cd "$(dirname "$0")/.."
URL='http://lb/telemed-sessions/{id}'

echo "== стенд з $N інстансами (томи порожні)"
docker compose down -v >/dev/null 2>&1
WEB_REPLICAS=$N docker compose up -d >/dev/null 2>&1
until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8080/healthz)" = 200 ]; do sleep 0.2; done
docker compose ps --format '{{.Name}} {{.Status}}'
./scripts/seed.sh

if [ "$N" = 1 ]; then
  echo "== перевірка генератора на /noop (nginx return 200, сервіс не задіяний)"
  for r in 10 100 500 2000 8000; do
    [ -f results/noop_${r}.json ] && continue
    ./scripts/lg.sh open --url http://lb/noop --rate $r --label "noop" \
      --out /results/noop_${r}.json 2>/dev/null | python3 scripts/brief.py noop
  done
fi

for r in $LEVELS; do
  for k in $(seq 1 "$REPS"); do
    [ -f results/open_n${N}_${r}_rep${k}.json ] && continue   # уже знятий прогін не повторюємо
    ./scripts/lg.sh open --url "$URL" --ids 1-100 --rate $r --label "open r=$N" \
      --out /results/open_n${N}_${r}_rep${k}.json 2>/dev/null | python3 scripts/brief.py "n=$N rep$k"
    sleep 3
  done
done

if [ "$N" = 1 ]; then
  CLR=${CLOSED_LEVEL:-6000}
  C=$(python3 - "$CLR" <<'EOF'
import json, statistics, sys
v = [json.load(open(f"results/open_n1_{sys.argv[1]}_rep{k}.json"))["active_mean"] for k in (1, 2, 3)]
print(round(statistics.median(v)))
EOF
)
  echo "== закритий контур на рівні $CLR: concurrency = $C (середня кількість одночасних запитів у відкритому контурі)"
  for k in 1; do   # умова: один прогін у закритому контурі
    ./scripts/lg.sh closed --url "$URL" --ids 1-100 --concurrency $C --label "closed r=1" \
      --out /results/closed_n1_c${C}_rep${k}.json 2>/dev/null | python3 scripts/brief.py "closed rep$k"
    sleep 3
  done
fi

if [ "$N" = 2 ]; then
  echo "== розподіл запитів між інстансами"
  docker compose ps web --format '{{.Name}}' | while read c; do
    echo "$c $(docker inspect "$c" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}')"
  done | tee results/instances_n2.txt
  # у nginx:alpine access.log — це симлінк на stdout контейнера, тому читаємо docker logs
  docker compose logs --no-log-prefix lb 2>/dev/null | grep -o 'upstream=[0-9.:]*' | sort | uniq -c \
    | tee results/lb_distribution_n2.txt
  for i in $(seq 1 10); do curl -s -D - -o /dev/null localhost:8080/telemed-sessions/1 | grep -i x-instance; done \
    | tee results/x_instance_n2.txt
fi
