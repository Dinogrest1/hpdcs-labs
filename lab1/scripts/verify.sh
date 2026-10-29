#!/usr/bin/env bash
# Етап 9: повний цикл CRUD + перевірка валідації. Виводить кожен запит і відповідь.
set -u
B=${BASE:-http://localhost:8080}
R=$B/telemed-sessions

req() {  # метод url [тіло]
  echo "\$ curl -X $1 ${2#$B}${3:+ -d '$3'}"
  if [ -n "${3:-}" ]; then
    curl -s -X "$1" "$2" -H 'Content-Type: application/json' -d "$3" -w '\n<- HTTP %{http_code}\n'
  else
    curl -s -X "$1" "$2" -w '\n<- HTTP %{http_code}\n'
  fi
  echo
}

BODY='{"patient_code":"P-000123","doctor":"Коваленко О.П.","channel":"video","started_at":"2026-10-10T10:00:00+03:00","duration_min":30,"status":"scheduled"}'
echo "### 1. Створення"
OUT=$(curl -s -X POST "$R" -H 'Content-Type: application/json' -d "$BODY" -w '\n%{http_code}')
echo "\$ curl -X POST /telemed-sessions -d '$BODY'"; echo "$OUT" | sed '$d'; echo "<- HTTP $(echo "$OUT" | tail -1)"; echo
ID=$(echo "$OUT" | sed '$d' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "### 2. Перелік із фільтром за каналом"
req GET "$R?channel=video&limit=5&offset=0"
echo "### 3. Один запис за id=$ID"
req GET "$R/$ID"
echo "### 4. Повна заміна"
req PUT "$R/$ID" '{"patient_code":"P-000123","doctor":"Коваленко О.П.","channel":"audio","started_at":"2026-10-10T10:00:00+03:00","duration_min":45,"status":"completed"}'
echo "### 5. Видалення"
req DELETE "$R/$ID"
echo "### 6. Повторне видалення"
req DELETE "$R/$ID"
echo "### 7. Читання видаленого"
req GET "$R/$ID"
echo "### 8. Валідація: тривалість 0 і 91 хв, невідомий канал, зайве поле id"
req POST "$R" '{"patient_code":"P-000124","doctor":"Іваненко","channel":"video","started_at":"2026-10-10T10:00:00+03:00","duration_min":0,"status":"scheduled"}'
req POST "$R" '{"patient_code":"P-000124","doctor":"Іваненко","channel":"video","started_at":"2026-10-10T10:00:00+03:00","duration_min":91,"status":"scheduled"}'
req POST "$R" '{"patient_code":"P-000124","doctor":"Іваненко","channel":"fax","started_at":"2026-10-10T10:00:00+03:00","duration_min":20,"status":"scheduled"}'
req POST "$R" '{"id":5,"patient_code":"P-000124","doctor":"Іваненко","channel":"chat","started_at":"2026-10-10T10:00:00+03:00","duration_min":20,"status":"scheduled"}'
echo "### 9. PUT неіснуючого, некоректний limit"
req PUT "$R/999999" '{"patient_code":"P-000124","doctor":"Іваненко","channel":"chat","started_at":"2026-10-10T10:00:00+03:00","duration_min":20,"status":"scheduled"}'
req GET "$R?limit=1000"
