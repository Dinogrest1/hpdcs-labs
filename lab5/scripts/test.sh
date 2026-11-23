#!/usr/bin/env bash
# Крок 3 (перевірка): go vet і тести, зокрема з детектором гонок; тести також перевіряють, що last() не змінює структуру.
source "$(dirname "$0")/lib.sh"
cd "$LAB/bench"
echo '$ go vet ./...'
"$GO" vet ./...
echo '$ go test -race -count=1 -v ./...'
"$GO" test -race -count=1 -v ./...
echo '$ go test -count=1 -v ./...   (без детектора гонок)'
"$GO" test -count=1 -v ./...
