#!/usr/bin/env bash
# Заносить 100 записів через localhost:8080 (NodePort web); id у сценарії — 1..100.
./scripts/vmsh.sh '
ch="video audio chat"; st="scheduled in_progress completed cancelled"
i=1
while [ $i -le 100 ]; do
  c=$(echo $ch | cut -d" " -f$((i%3+1))); s=$(echo $st | cut -d" " -f$((i%4+1)))
  curl -s -o /dev/null -X POST http://localhost:8080/telemed-sessions -H "Content-Type: application/json" \
    -d "{\"patient_code\":\"P-$(printf %06d $i)\",\"doctor\":\"Лікар $((i%7+1))\",\"channel\":\"$c\",\"started_at\":\"2026-10-0$((i%9+1))T09:00:00+03:00\",\"duration_min\":$(( (i%18+1)*5 )),\"status\":\"$s\"}"
  i=$((i+1))
done
curl -s "http://localhost:8080/telemed-sessions?limit=1"; echo'
