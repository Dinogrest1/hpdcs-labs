#!/usr/bin/env bash
# Крок 9: найбільша пауза між перезапусками контейнера (CrashLoopBackOff) на вузлах worker — 1 с замість 300 с.
# Без цього за FAIL_RATE = 0,15 пауза подвоюється з кожним падінням (10, 20, 40, 80 с, …), і пул за 2 хвилини
# стоїть у CrashLoopBackOff. Змінюється KubeletConfiguration (crashLoopBackOff.maxContainerRestartPeriod) і
# kubelet перезапускається; поди на вузлі при цьому не перезапускаються.
#   ./scripts/kubelet_backoff.sh 1s      задати
#   ./scripts/kubelet_backoff.sh off     повернути як було
set -eu
V=${1:-1s}
for N in telemed-worker telemed-worker2; do
  docker exec "$N" sh -c "
    cfg=/var/lib/kubelet/config.yaml
    [ -f \$cfg.orig ] || cp \$cfg \$cfg.orig
    cp \$cfg.orig \$cfg
    if [ '$V' != off ]; then
      sed -i 's/^crashLoopBackOff: {}\$/crashLoopBackOff:\n  maxContainerRestartPeriod: $V/' \$cfg
      grep -q '^featureGates:' \$cfg || printf 'featureGates:\n  KubeletCrashLoopBackOffMax: true\n' >> \$cfg
    fi
    systemctl restart kubelet"
  echo "\$ docker exec $N grep -A1 -E '^(crashLoopBackOff|featureGates)' /var/lib/kubelet/config.yaml"
  docker exec "$N" grep -A1 -E '^(crashLoopBackOff|featureGates)' /var/lib/kubelet/config.yaml
done
sleep 5
for N in telemed-worker telemed-worker2; do
  echo "\$ docker exec $N systemctl is-active kubelet: $(docker exec $N systemctl is-active kubelet)"
done
kubectl wait --for=condition=Ready node/telemed-worker node/telemed-worker2 --timeout=60s
