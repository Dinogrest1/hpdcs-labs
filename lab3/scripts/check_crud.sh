#!/usr/bin/env bash
# Крок 4: повний цикл CRUD через localhost:8080 (NodePort) і под з заголовка X-Pod на кожен запит.
./scripts/vmsh.sh '
B=http://localhost:8080/telemed-sessions
show() { echo "$1"; grep -i -E "^HTTP/|^x-pod" /tmp/h | tr -d "\r"; cat /tmp/b; echo; echo; }
curl -s -D /tmp/h -o /tmp/b -X POST $B -H "Content-Type: application/json" -d "{\"patient_code\":\"P-CRUD01\",\"doctor\":\"Коваленко О.П.\",\"channel\":\"video\",\"started_at\":\"2026-10-28T10:00:00+02:00\",\"duration_min\":30,\"status\":\"scheduled\"}"
show "> POST $B"
ID=$(sed -E "s/.*\"id\":([0-9]+).*/\1/" /tmp/b)
curl -s -D /tmp/h -o /tmp/b $B/$ID;                                   show "> GET $B/$ID"
curl -s -D /tmp/h -o /tmp/b -X PUT $B/$ID -H "Content-Type: application/json" -d "{\"patient_code\":\"P-CRUD01\",\"doctor\":\"Коваленко О.П.\",\"channel\":\"audio\",\"started_at\":\"2026-10-28T10:00:00+02:00\",\"duration_min\":45,\"status\":\"completed\"}"
show "> PUT $B/$ID"
curl -s -D /tmp/h -o /tmp/b "$B?channel=audio&limit=2";              show "> GET $B?channel=audio&limit=2"
curl -s -D /tmp/h -o /tmp/b -X DELETE $B/$ID;                         show "> DELETE $B/$ID"
curl -s -D /tmp/h -o /tmp/b $B/$ID;                                   show "> GET $B/$ID"
'
