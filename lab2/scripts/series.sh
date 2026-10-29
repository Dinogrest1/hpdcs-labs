#!/usr/bin/env bash
# Уся серія ЛР 2 одним запуском, з порожніми томами:
#   крок 3 — коректна зупинка, крок 4 — Targets на 4 екземплярах,
#   крок 6 — ступінчастий прогін на /noop (межа генератора G),
#   крок 7 — ступінчасті прогони на 1, 2, 3, 4 екземплярах з межею G.
set -eu
cd "$(dirname "$0")/.."
# Повторний запуск пропускає вже зняті частини (за файлами results/), а стенд з порожніми томами
# піднімає лише на самому початку серії. FRESH=1 — почати все заново.
if [ "${FRESH:-0}" = 1 ] || [ ! -f results/noop.json ]; then
echo "== $(date '+%F %T') стенд з порожніми томами"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d --build >/dev/null 2>&1
until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8088/healthz)" = 200 ]; do sleep 0.5; done
docker compose ps --format '{{.Name}} {{.Status}}'
echo "\$ ./scripts/seed.sh"
./scripts/seed.sh
echo "\$ docker info  # ресурси ВМ Docker (Colima)"
docker info --format 'CPU: {{.NCPU}}, памʼять: {{.MemTotal}} байт, Docker {{.ServerVersion}}'

echo "== $(date '+%F %T') крок 3: коректна зупинка"
./scripts/graceful.sh | tee results/step3_graceful.log

echo "== $(date '+%F %T') крок 4: Targets при --scale web=4"
./scripts/scale.sh 4 | tee results/step4_targets.log

echo "== $(date '+%F %T') крок 6: /noop, межа генератора"
./scripts/steprun.sh noop -url http://lb/noop
else
echo "== $(date '+%F %T') продовження серії: кроки 3, 4, 6 уже зняті (results/noop.json є)"
docker compose up -d >/dev/null 2>&1
until [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8088/healthz)" = 200 ]; do sleep 0.5; done
fi
G=$(python3 -c "import json;print(round(max(s['achieved_rps'] for s in json.load(open('results/noop.json'))['steps'])))")
echo "G = $G запитів/с"

for N in 1 2 3 4; do
  [ -f "results/prom_n$N.json" ] && { echo "== крок 7: $N — уже знято, пропущено"; continue; }
  echo "== $(date '+%F %T') крок 7: $N екземпляр(и)"
  ./scripts/scale.sh "$N"
  ./scripts/steprun.sh "n$N" -url 'http://lb/telemed-sessions/{id}' -ids 1-100 -G "$G"
done
echo "== $(date '+%F %T') серію завершено"
