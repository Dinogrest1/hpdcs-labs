#!/usr/bin/env bash
# Крок 3: RWLock (sync.RWMutex). Три прогони по D секунд на n потоках; t_cs для довідки.
source "$(dirname "$0")/lib.sh"
build
for i in $(seq 1 $REPS); do
  echo "\$ bin/ringbench -mode cs -lock rw -run $i"
  "$BIN" -mode cs -lock rw -run $i -out "$RAW/cs_rw_$i.json"
done
for i in $(seq 1 $REPS); do
  echo
  echo "\$ bin/ringbench -mode run -lock rw -n $N -d $D -warm $WARM -run $i"
  load_line
  "$BIN" -mode run -lock rw -n $N -d $D -warm $WARM -run $i -out "$RAW/run_rw_$i.json"
  sleep $PAUSE
done
