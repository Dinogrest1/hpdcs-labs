#!/usr/bin/env bash
# Крок 3: strong scaling з DDP, B = 192 = const, P = 1 і 2 (P_max = 2); P = 1 теж через torchrun.
set -eu -o pipefail
source "$(dirname "$0")/env.sh"
{
bg
echo "# Strong scaling: B = 192; $R повторів по колу: DDP і DDP з no_sync для P = 1 (b = 192) і P = 2 (b = 96)"
for r in $(seq 1 $R); do
  one 1 --task bench --sync ddp --b 192 --tag s3_p1_r$r
  one 2 --task bench --sync ddp --b 96 --tag s3_p2_r$r
  one 1 --task bench --sync ddp --ns --b 192 --tag s3_p1ns_r$r
  one 2 --task bench --sync ddp --ns --b 96 --tag s3_p2ns_r$r
done
} 2>&1 | tee results/step3.log
