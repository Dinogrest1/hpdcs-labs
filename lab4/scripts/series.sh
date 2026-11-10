#!/usr/bin/env bash
# Уся серія ЛР 4 одним запуском (після ./scripts/deploy.sh). Вивід кожного кроку — у results/<крок>.log.
#   ./scripts/series.sh            усі кроки
#   ./scripts/series.sh 6 7        лише названі кроки
set -euo pipefail
cd "$(dirname "$0")/.."
STEPS=${*:-seed 1 2 3 4 4b 5 6 7 8 9 10}
for S in $STEPS; do
  echo "== $(date '+%F %T') крок $S"
  if [ "$S" = seed ]; then ./scripts/seed.sh 2>&1 | tee results/seed.log; continue; fi
  # Крок 9: без короткої паузи перезапуску пул за FAIL_RATE = 0,15 стоїть у CrashLoopBackOff (див. звіт)
  [ "$S" = 9 ] && ./scripts/kubelet_backoff.sh 1s 2>&1 | tee results/kubelet_backoff.log
  "./scripts/step$S.sh" 2>&1 | tee "results/step$S.log"
done
# після серії — остаточний режим воркерів
bash -c '. scripts/lib.sh; workers 1 >/dev/null'
echo "== $(date '+%F %T') серію завершено"
