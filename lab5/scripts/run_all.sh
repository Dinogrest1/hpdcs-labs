#!/usr/bin/env bash
# Увесь експеримент: тести, потім кроки 1–3 (виміри), потім аналіз (кроки 4–5).
# Вивід кожного кроку — в results/<крок>.log цілим одним запуском.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f results/.measurements_done
mkdir -p results/raw
./scripts/env.sh        2>&1 | tee results/env.log
./scripts/test.sh       2>&1 | tee results/test.log
./scripts/step1.sh      2>&1 | tee results/step1.log
./scripts/step2.sh      2>&1 | tee results/step2.log
./scripts/step3.sh      2>&1 | tee results/step3.log
touch results/.measurements_done
python3 scripts/analyze.py 2>&1 | tee results/analyze.log
