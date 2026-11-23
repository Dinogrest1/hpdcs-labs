#!/usr/bin/env bash
# Крок 1: стенд, n_params і M, навчання в одному процесі без torch.distributed, виміри t_step і X(1).
set -eu -o pipefail
source "$(dirname "$0")/env.sh"
{
bg
echo "# Стенд"
sysctl machdep.cpu.brand_string hw.physicalcpu hw.logicalcpu hw.perflevel0.physicalcpu hw.perflevel1.physicalcpu hw.memsize
sw_vers -productVersion
$PY --version
echo "OMP_NUM_THREADS=$OMP_NUM_THREADS"
echo
echo "# Параметри мережі, M, n_e"
$PY train.py --task info --outdir "$RUNS"
echo
echo "# Навчання в одному процесі без torch.distributed: батч B = 192, η = 0,02, 3 епохи"
$PY train.py --task train --sync none --b 192 --tag s1_single_train
echo
echo "# Швидкість: 10 кроків прогріву + 50 вимірюваних, $R повторів"
for r in $(seq 1 $R); do
  $PY train.py --task bench --sync none --b 192 --tag s1_single_bench_r$r
done
} 2>&1 | tee results/step1.log
