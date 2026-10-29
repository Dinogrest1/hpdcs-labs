#!/usr/bin/env bash
# Запускає генератор контейнером у мережі frontend, поряд із балансувальником.
exec docker compose --profile tools run --rm -T loadgen "$@"
