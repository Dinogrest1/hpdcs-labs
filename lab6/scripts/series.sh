#!/usr/bin/env bash
# Усі кроки по порядку. На машині не має бути іншого навантаження.
set -eu
cd "$(dirname "$0")/.."
./scripts/step1.sh
./scripts/step2.sh
./scripts/step3.sh
./scripts/step4.sh
./scripts/step5.sh
