#!/usr/bin/env bash
# Крок 2: P_max = 2 процеси, ручна синхронізація, еквівалентність, DDP, no_sync, навчання з DDP.
set -eu -o pipefail
source "$(dirname "$0")/env.sh"
{
bg
echo "# 2.1 Еквівалентність: g в одному процесі, ḡ на двох (ручна синхронізація), перший батч з B = 192"
one 0 --task grad_single --outdir "$RUNS" --tag s2_grad_single
one 2 --task equiv --sync manual --b 96 --outdir "$RUNS" --tag s2_equiv
echo
echo "# 2.2 50 кроків з ручною синхронізацією, суми ваг у float64"
one 2 --task sync50 --sync manual --b 96 --outdir "$RUNS" --tag s2_sync50
echo
echo "# 2.3 Швидкість: ручна синхронізація, DDP, DDP з no_sync; $R повторів по колу"
for r in $(seq 1 $R); do
  one 2 --task bench --sync manual --b 96 --tag s2_manual_r$r
  one 2 --task bench --sync ddp --b 96 --tag s2_ddp_r$r
  one 2 --task bench --sync ddp --ns --b 96 --tag s2_ddpns_r$r
done
echo
echo "# 2.4 Навчання з DDP на 2 процесах: батч процесу 96, B = 192, η = 0,02, 3 епохи"
one 2 --task train --sync ddp --b 96 --tag s2_ddp_train
} 2>&1 | tee results/step2.log
