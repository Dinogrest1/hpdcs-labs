#!/usr/bin/env bash
# Крок 6: пропускна здатність пулу на W = 1, 2 і R воркерах з непорожньою чергою.
# Черга заповнюється при зупинених воркерах, далі kubectl scale до W; X(W) — за останні 30 с до спорожнення.
# NQ задач на воркер: за 400 задач за умовою черга спорожніє за 3–10 с, і вікна 30 с не існує.
. "$(dirname "$0")/lib.sh"
NQ=${NQ:-2000}
for W in 1 2 $R; do
  RUN=step6_w$W; rm -rf results/$RUN; mkdir -p results/$RUN
  say "W = $W: у черзі $((NQ * W)) задач"
  clean; workers 0 >/dev/null
  gen $RUN -count $((NQ * W)) -rate 500
  qt "SELECT status, count(*) FROM tasks GROUP BY status"
  sampler_start $RUN; T0=$(date +%s)
  echo "\$ kubectl scale deployment/worker --replicas=$W"
  kubectl scale deployment/worker --replicas=$W >/dev/null
  kubectl rollout status deployment/worker --timeout=180s >/dev/null
  wait_empty 600
  sleep 20                      # cAdvisor оновлює лічильники CPU раз на 10–15 с
  sampler_stop
  collect $RUN "$T0"
  echo "\$ python3 scripts/calc.py x $RUN"; python3 scripts/calc.py x $RUN
  echo "\$ python3 scripts/calc.py stat $RUN"; python3 scripts/calc.py stat $RUN
done
