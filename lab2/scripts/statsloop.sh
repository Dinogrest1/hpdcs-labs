#!/usr/bin/env bash
# Знімає docker stats --no-stream у циклі (рядок JSON на контейнер з міткою часу) до зупинки.
out=$1
while :; do
  ts=$(date +%s)
  docker stats --no-stream --format '{{json .}}' | sed "s/^{/{\"ts\":$ts,/" >> "$out"
done
