#!/usr/bin/env bash
# Крок 4: weak scaling (b = 96, P = 1 і 2) і якість за різних глобальних батчів.
set -eu -o pipefail
source "$(dirname "$0")/env.sh"
{
bg
echo "# Weak scaling: b = 96; P = 1 (DDP через torchrun), $R повторів. Рядок P = 2 береться з кроку 3"
for r in $(seq 1 $R); do
  one 1 --task bench --sync ddp --b 96 --tag s4_p1_r$r
done
echo
echo "# Навчання: P = 1, b = 96, η = 0,02 (без torch.distributed)"
one 0 --task train --sync none --b 96 --lr 0.02 --tag s4_p1_train
echo
echo "# Навчання: P = 2, b = 96 (B = 192), η_P = 0,02 · 2 = 0,04 (лінійне правило, B_0 = b)"
one 2 --task train --sync ddp --b 96 --lr 0.04 --tag s4_p2_lrP_train
} 2>&1 | tee results/step4.log
