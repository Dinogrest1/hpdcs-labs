#!/usr/bin/env bash
# Уся серія ЛР 3 одним запуском (після ./scripts/deploy.sh):
#   засів, кроки 3–5 (перевірки), крок 7 (ступінчасті прогони на 1, 2, 3, 5 репліках),
#   крок 10 (профіль зі стрибком на 5 фіксованих репліках), крок 11 (той самий профіль з HPA).
set -euo pipefail   # pipefail: помилка scale.sh не ховається за | tee
cd "$(dirname "$0")/.."
# Повторний запуск пропускає вже зняті частини (за файлами в results/), тож збій коштує
# щонайбільше одного прогону. Після кожного прогону результати комітяться локально.
done_() { [ -f "results/$1" ]; }
checkpoint() {
  git -C .. add "lab3/results" >/dev/null 2>&1 && \
  git -C .. -c user.name="Bogdan Tovstenko" -c user.email="bogdan.tovstenko@kmbs.ua" \
    commit -q -m "ЛР3: результати, $1" >/dev/null 2>&1 || true
  # Пауза: ./scripts/pause.sh створює results/.pause — серія виходить після щойно завершеного прогону.
  if [ -f results/.pause ]; then
    rm -f results/.pause
    echo "== $(date '+%F %T') пауза після «$1»; продовження: ./scripts/series.sh"
    exit 0
  fi
}
rows() { kubectl exec db-0 -- psql -U telemed -d telemed -tAc "SELECT count(*) FROM telemed_sessions WHERE id <= 100" 2>/dev/null || echo 0; }
if ! done_ step5_monitoring.log; then
echo "== $(date '+%F %T') засів"
echo '$ ./scripts/seed.sh'; ./scripts/seed.sh
echo "== $(date '+%F %T') крок 4: CRUD через localhost:8080"
./scripts/check_crud.sh | tee results/step4_crud.log
echo "== $(date '+%F %T') крок 3: збереження даних після видалення пода бази"
./scripts/check_db.sh | tee results/step3_db.log
echo "== $(date '+%F %T') крок 5: моніторинг"
./scripts/check_monitoring.sh | tee results/step5_monitoring.log
checkpoint "кроки 3-5"
else
echo "== $(date '+%F %T') продовження серії: засів і кроки 3-5 уже зроблені"
fi

# Якщо кластер створено заново після вимкнення, база порожня: засіяти перед прогонами.
if [ "$(rows)" != 100 ]; then
  echo "== $(date '+%F %T') у базі немає записів 1..100 — засів"; ./scripts/seed.sh
fi

for N in 1 2 3 5; do
  done_ "prom_n$N.json" && { echo "== крок 7: $N — уже знято, пропущено"; continue; }
  echo "== $(date '+%F %T') крок 7: $N реплік(и)"
  ./scripts/scale.sh "$N" | tee "results/scale_n$N.log"
  ./scripts/steprun.sh "n$N" -step-dur 30 -window 20
  checkpoint "прогін n$N"
done

read X1 X5 LOW JUMP < <(python3 - <<'PY'
import json
x = lambda n: max(s["achieved_rps"] for s in json.load(open(f"results/n{n}.json"))["steps"])
x1, x5 = x(1), x(5)
print(round(x1, 1), round(x5, 1), round(0.3 * x1), round(min(2 * x1, 0.9 * x5)))
PY
)
echo "X(1) = $X1, X(5) = $X5; профіль: 30 с × $LOW, 60 с × $JUMP (= min(2·X(1), 0.9·X(5))), 90 с × $LOW"
PROFILE="30:$LOW,60:$JUMP,90:$LOW"

if ! done_ prom_fixed5.json; then
echo "== $(date '+%F %T') крок 10: профіль на 5 фіксованих репліках"
./scripts/scale.sh 5
./scripts/steprun.sh fixed5 -mode profile -profile "$PROFILE" -window 999   # підсумок відрізка — за весь відрізок
checkpoint "прогін fixed5"
fi

if ! done_ prom_hpa.json; then
echo "== $(date '+%F %T') крок 11: той самий профіль з HPA"
kubectl delete hpa web --ignore-not-found >/dev/null
./scripts/scale.sh 1
kubectl apply -f k8s/hpa.yaml
ok=0
while [ $ok -lt 60 ]; do   # одна репліка 60 с поспіль
  cur=$(kubectl get hpa web -o jsonpath='{.status.currentReplicas}')
  tgt=$(kubectl get hpa web -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}')
  if [ "$cur" = 1 ] && [ -n "$tgt" ]; then ok=$((ok+5)); else ok=0; fi
  sleep 5
done
echo '$ kubectl get hpa web'; kubectl get hpa web
./scripts/steprun.sh hpa -mode profile -profile "$PROFILE" -window 999
sleep 40
echo '$ kubectl get hpa web'; kubectl get hpa web
echo '$ kubectl describe hpa web | events'; kubectl describe hpa web | sed -n '/Events:/,$p'
kubectl delete -f k8s/hpa.yaml
checkpoint "прогін hpa"
fi
echo "== $(date '+%F %T') серію завершено"
