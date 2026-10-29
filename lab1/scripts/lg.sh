#!/usr/bin/env bash
# Обгортка: запускає генератор у контейнері в мережі frontend (поряд із балансувальником).
exec docker compose --profile tools run --rm -T loadgen "$@"
