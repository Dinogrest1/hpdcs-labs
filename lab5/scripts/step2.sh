#!/usr/bin/env bash
# Крок 2: глобальний замок. t_cs,w і t_cs,r одним потоком, потім 3 прогони по D секунд на n потоках.
source "$(dirname "$0")/lib.sh"
build
for i in $(seq 1 $REPS); do
  echo "\$ bin/ringbench -mode cs -lock global -run $i"
  "$BIN" -mode cs -lock global -run $i -out "$RAW/cs_global_$i.json"
done
for i in $(seq 1 $REPS); do
  echo
  echo "\$ bin/ringbench -mode run -lock global -n $N -d $D -warm $WARM -run $i"
  load_line
  "$BIN" -mode run -lock global -n $N -d $D -warm $WARM -run $i -out "$RAW/run_global_$i.json"
  sleep $PAUSE
done
