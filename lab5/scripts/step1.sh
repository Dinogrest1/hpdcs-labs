#!/usr/bin/env bash
# Крок 1: вартість вимірювання часу, t_cs і X_1 (один потік по черзі пише і читає, n_w : n_r = 6 : 6 = 1 : 1).
source "$(dirname "$0")/lib.sh"
build
echo '$ bin/ringbench -mode clock'
"$BIN" -mode clock -out "$RAW/clock.json"
for lk in global rw; do
  for i in $(seq 1 $REPS); do
    echo
    echo "\$ bin/ringbench -mode x1 -lock $lk -n $N -d $D -warm $WARM -run $i"
    load_line
    "$BIN" -mode x1 -lock $lk -n $N -d $D -warm $WARM -run $i -out "$RAW/x1_${lk}_$i.json"
    sleep $PAUSE
  done
done
