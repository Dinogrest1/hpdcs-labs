"""Генератор навантаження з фіксованою швидкістю надходження (відкритий контур)
і, для порівняння, із фіксованою кількістю одночасних запитів (закритий контур).

Відкритий контур: задана інтенсивність ділиться між процесами (за кількістю ядер).
Процес i відправляє запити з номерами k*P + i у моменти start + (k*P + i) / rate,
обчислені наперед від початку прогону. Сам запит віддається пулу потоків, тож
цикл планування ніколи не чекає на відповідь. Затримка рахується від запланованого
моменту відправки, тому час очікування в черзі генератора теж потрапляє у виміри.

Приклади:
  python loadgen.py open   --url http://lb/telemed-sessions/{id} --rate 100
  python loadgen.py closed --url http://lb/telemed-sessions/{id} --concurrency 40
"""

import argparse
import gzip
import http.client
import json
import math
import multiprocessing as mp
import os
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

TIMEOUT = 10.0


class Client:
    """HTTP/1.1 з keep-alive: одне зʼєднання на потік."""

    def __init__(self, url_template: str, ids: list[int]):
        u = urlsplit(url_template)
        self.host, self.port = u.hostname, u.port or 80
        self.path = u.path
        self.ids = ids
        self.local = threading.local()

    def _conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT)
            self.local.conn = c
        return c

    def request(self) -> int:
        path = self.path.replace("{id}", str(random.choice(self.ids))) if self.ids else self.path
        c = self._conn()
        try:
            c.request("GET", path)
            r = c.getresponse()
            r.read()
            return r.status
        except Exception:
            c.close()
            self.local.conn = None
            return 0  # 0 = помилка транспорту або таймаут, рахується як не-2xx


def open_worker(idx, procs, rate, start, warmup, duration, threads, url, ids, q):
    client = Client(url, ids)
    total = warmup + duration
    n_total = int(math.floor(rate * total))
    records = []          # (sched_rel, end_rel, status)
    lock = threading.Lock()
    inflight = [0]        # відправлені (заплановані) − завершені, разом із чергою пулу
    active = [0]          # запити, що реально на дроті (потік пулу чекає відповідь)
    lag_max = [0.0]
    samples = []
    active_samples = []

    def do(sched_abs):
        with lock:
            active[0] += 1
        status = client.request()
        end = time.monotonic()
        with lock:
            inflight[0] -= 1
            active[0] -= 1
            records.append((sched_abs - start, end - start, status))

    def sampler():
        # Кількість запитів у системі = відправлені − завершені, знімається кожні 5 мс
        # у вимірювальному вікні (без прогріву).
        while True:
            now = time.monotonic() - start
            if now >= total:
                return
            if now >= warmup:
                samples.append(inflight[0])
                active_samples.append(active[0])
            time.sleep(0.005)

    pool = ThreadPoolExecutor(max_workers=threads)
    threading.Thread(target=sampler, daemon=True).start()
    k = idx
    while k < n_total:
        target = start + k / rate
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            lag_max[0] = max(lag_max[0], -delay)
        with lock:
            inflight[0] += 1
        pool.submit(do, target)
        k += procs
    pool.shutdown(wait=True)
    q.put({"records": records, "inflight_mean": statistics.fmean(samples) if samples else 0.0,
           "active_mean": statistics.fmean(active_samples) if active_samples else 0.0,
           "lag_max": lag_max[0]})


def closed_worker(n_threads, start, warmup, duration, url, ids, q):
    client = Client(url, ids)
    total = warmup + duration
    records = []
    lock = threading.Lock()

    def loop():
        while True:
            t0 = time.monotonic()
            if t0 - start >= total:
                return
            status = client.request()
            t1 = time.monotonic()
            with lock:
                records.append((t0 - start, t1 - start, status))

    while time.monotonic() < start:
        time.sleep(0.001)
    ts = [threading.Thread(target=loop) for _ in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    q.put({"records": records, "inflight_mean": float(n_threads), "active_mean": float(n_threads),
           "lag_max": 0.0})


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    i = min(len(sorted_vals) - 1, max(0, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[i]


def summarize(args, parts, warmup, duration):
    records = [r for p in parts for r in p["records"]]
    lo, hi = warmup, warmup + duration
    # Затримки: запити, заплановані (відкритий) / відправлені (закритий) у вимірювальному вікні.
    window = [r for r in records if lo <= r[0] < hi]
    lat = sorted((e - s) * 1000 for s, e, _ in window)
    non2xx = sum(1 for *_, st in window if not 200 <= st < 300)
    # Досягнута інтенсивність: відповіді, що завершились у вимірювальному вікні, за секунду.
    completed = sum(1 for _, e, _ in records if lo <= e < hi)
    return {
        "mode": args.mode,
        "url": args.url,
        "label": args.label,
        "target_rps": args.rate if args.mode == "open" else None,
        "concurrency": args.concurrency if args.mode == "closed" else None,
        "procs": args.procs,
        "threads_per_proc": args.threads,
        "warmup_s": warmup,
        "duration_s": duration,
        "sent_in_window": len(window),
        "achieved_rps": completed / duration,
        "p50_ms": pct(lat, 50),
        "p95_ms": pct(lat, 95),
        "p99_ms": pct(lat, 99),
        "mean_ms": statistics.fmean(lat) if lat else float("nan"),
        "non2xx_share": non2xx / len(window) if window else float("nan"),
        "inflight_mean": sum(p["inflight_mean"] for p in parts),
        "active_mean": sum(p["active_mean"] for p in parts),
        "generator_lag_max_ms": max(p["lag_max"] for p in parts) * 1000,
    }, window


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["open", "closed"])
    ap.add_argument("--url", required=True, help="шаблон URL, {id} замінюється на id з --ids")
    ap.add_argument("--ids", default="", help="діапазон id, напр. 1-100")
    ap.add_argument("--rate", type=float, default=100.0, help="запитів за секунду (open)")
    ap.add_argument("--concurrency", type=int, default=10, help="одночасних запитів (closed)")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--warmup", type=float, default=10.0)
    ap.add_argument("--procs", type=int, default=os.cpu_count())
    ap.add_argument("--threads", type=int, default=64, help="потоків у пулі кожного процесу")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="", help="куди записати JSON-підсумок")
    args = ap.parse_args()

    ids = []
    if args.ids:
        a, b = map(int, args.ids.split("-"))
        ids = list(range(a, b + 1))

    q = mp.Queue()
    start = time.monotonic() + 0.5
    if args.mode == "open":
        procs = [mp.Process(target=open_worker, args=(i, args.procs, args.rate, start, args.warmup,
                                                       args.duration, args.threads, args.url, ids, q))
                 for i in range(args.procs)]
    else:
        per = [args.concurrency // args.procs + (1 if i < args.concurrency % args.procs else 0)
               for i in range(args.procs)]
        procs = [mp.Process(target=closed_worker, args=(n, start, args.warmup, args.duration,
                                                         args.url, ids, q))
                 for n in per if n > 0]
    for p in procs:
        p.start()
    parts = [q.get() for _ in procs]
    for p in procs:
        p.join()

    summary, window = summarize(args, parts, args.warmup, args.duration)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        with gzip.open(args.out.replace(".json", ".raw.csv.gz"), "wt") as f:
            f.write("sched_or_send_s,end_s,status\n")
            for s, e, st in sorted(window):
                f.write(f"{s:.6f},{e:.6f},{st}\n")


if __name__ == "__main__":
    main()
