#!/usr/bin/env bash
# Крок 3: коректна зупинка під навантаженням на двох екземплярах.
# Генератор 40 с тримає 1000 запитів/с; на ~20-й секунді docker stop зупиняє один екземпляр.
set -eu
./scripts/scale.sh 2
victim=$(docker compose ps web --format '{{.Name}}' | sort | tail -1)
echo "\$ docker compose ps web"; docker compose ps web --format '{{.Name}} {{.Status}}'
./scripts/lg.sh -mode profile -profile 40:1000 -out /results/graceful -label graceful >/tmp/graceful_lg.txt 2>&1 & lg=$!
sleep 22
t_stop=$(date +%s)
echo "\$ docker stop $victim      # unix $t_stop"
docker stop "$victim"
t_done=$(date +%s)
echo "зупинка зайняла $((t_done - t_stop)) с"
echo "\$ docker inspect --format '{{.State.ExitCode}}' $victim"
docker inspect --format '{{.State.ExitCode}}' "$victim"
wait $lg; cat /tmp/graceful_lg.txt | grep -v Container
echo "\$ docker logs $victim | tail -6"
docker logs "$victim" 2>&1 | tail -6
echo "# генератор, 10 с навколо зупинки (±5 с від docker stop):"
python3 - "$t_stop" <<'PY'
import csv, sys
t = int(sys.argv[1])
rows = [r for r in csv.DictReader(open("results/graceful.seconds.csv")) if t - 5 <= int(r["unix"]) < t + 5]
print("unix        sent  2xx  non2xx  conn_err  p99_ms")
for r in rows:
    print(f'{r["unix"]}  {r["sent"]:>4} {r["achieved_2xx"]:>4}  {r["non2xx"]:>6}  {r["conn_errors"]:>8}  {r["p99_ms"]:>6}')
bad = sum(int(r["non2xx"]) + int(r["conn_errors"]) for r in rows)
print(f"за 10 с: відповідей 2xx = {sum(int(r['achieved_2xx']) for r in rows)}, не 2xx і помилок зʼєднання = {bad}")
PY
