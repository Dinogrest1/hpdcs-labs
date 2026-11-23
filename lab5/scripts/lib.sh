#!/usr/bin/env bash
# Спільні змінні й функції кроків. Підключається через `source`.
set -eu
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GO=/opt/homebrew/bin/go
[ -x "$GO" ] || GO="$(command -v go)"
BIN="$LAB/bin/ringbench"
RAW="$LAB/results/raw"
N=12          # потоків стенду (варіант 26)
D=10          # тривалість вимірювання, с
WARM=1        # прогрів, с
REPS=3        # повторів
PAUSE=5       # пауза між прогонами, с (щоб кожен прогін починався з тих самих умов)
mkdir -p "$RAW" "$LAB/results/tables" "$LAB/bin"

build() { ( cd "$LAB/bench" && "$GO" build -o "$BIN" . ); }

# load_line: навантаження машини перед прогоном (щоб у журналі було видно, що вона вільна)
load_line() { echo "# навантаження перед прогоном: $(uptime | sed 's/.*load averages*: //')"; }
