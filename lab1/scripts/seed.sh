#!/usr/bin/env bash
# Заносить 100 записів перед серією; id у сценарії беруться з діапазону 1-100.
set -eu
ch=(video audio chat); st=(scheduled in_progress completed cancelled)
for i in $(seq 1 100); do
  curl -s -o /dev/null -X POST http://localhost:8080/telemed-sessions -H 'Content-Type: application/json' \
    -d "{\"patient_code\":\"P-$(printf %06d $i)\",\"doctor\":\"Лікар $((i%7+1))\",\"channel\":\"${ch[$((i%3))]}\",\"started_at\":\"2026-10-0$((i%9+1))T09:00:00+03:00\",\"duration_min\":$(( (i%18+1)*5 )),\"status\":\"${st[$((i%4))]}\"}"
done
curl -s "http://localhost:8080/telemed-sessions?limit=1" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("seeded, total =",d["total"],"first id =",d["items"][0]["id"])'
