#!/usr/bin/env bash
# Крок 5: контрольні точки кожні K_c = 10 кроків, відмова rank 1 на початку кроку s_f = 25, відновлення.
set -eu -o pipefail
source "$(dirname "$0")/env.sh"
CK=results/step5
{
bg
if lsof -nP -iTCP:29500 >/dev/null 2>&1; then echo "порт 29500 зайнятий"; exit 1; fi
rm -rf "$CK" "$CK"_nofail; mkdir -p "$CK" "$CK"_nofail
echo "# Прогін з відмовою: torchrun --max-restarts=1, статична адреса групи"
$TR --nnodes=1 --nproc_per_node=2 --max-restarts=1 --rdzv-backend=static \
    --master-addr=127.0.0.1 --master-port=29500 --node-rank=0 \
    train.py --task train --sync ddp --b 96 --lr 0.02 --tag s5_fail \
    --ckpt-dir "$CK" --ckpt-every 10 --fail-step 25 2>&1 | grep -v 'Redirects are currently not supported'
echo
echo "# Файл-позначка (час відмови, секунди Unix) і журнал подій"
cat "$CK/failed.flag"; echo
cat "$CK/events.jsonl"
echo
echo "# Контроль: ті самі контрольні точки без відмови (чи змінюють вони навчання)"
$TR --nnodes=1 --nproc_per_node=2 --rdzv-backend=static --master-addr=127.0.0.1 --master-port=29500 \
    --node-rank=0 train.py --task train --sync ddp --b 96 --lr 0.02 --tag s5_ckpt_nofail \
    --ckpt-dir "$CK"_nofail --ckpt-every 10 2>&1 | grep -v 'Redirects are currently not supported'
} 2>&1 | tee results/step5.log
