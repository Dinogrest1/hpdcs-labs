# Спільні налаштування кроків: venv, кількість потоків, запуск torchrun.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY=.venv/bin/python
TR=.venv/bin/torchrun
# n_th = ⌊C_phys / P_max⌋ для всіх прогонів; torchrun сам виставив би OMP_NUM_THREADS=1, тому задаємо явно
export OMP_NUM_THREADS=$(( $(sysctl -n hw.physicalcpu) / 2 ))
# gloo і torchrun на macOS визначають адресу за іменем машини, яке мережа перетворює на довгу ip6.arpa-назву:
# рандеву зависало. Явно фіксуємо loopback.
export GLOO_SOCKET_IFNAME=lo0
export RUN_ID="lab6-$(date +%s)"
R=7                       # повторів кожної конфігурації вимірів швидкості
RUNS=results/runs
mkdir -p "$RUNS"

# one <P> <аргументи train.py> — P процесів через torchrun, P = 0 — один процес без torch.distributed
one() {
  local P=$1; shift
  if [ "$P" = 0 ]; then $PY train.py "$@"
  else $TR --nnodes=1 --nproc_per_node="$P" --rdzv-backend=static --master-addr=127.0.0.1 --master-port=29500 \
           --node-rank=0 train.py "$@" 2>&1 | grep -v 'Redirects are currently not supported'
  fi
}

# bg — знімок фонового навантаження на початку кроку (другий вибір top показує поточне, а не середнє від старту)
bg() { echo "# Фонове навантаження на початку кроку"; uptime; top -l 2 -n 5 -o cpu -stats pid,command,cpu | tail -6; echo; }
