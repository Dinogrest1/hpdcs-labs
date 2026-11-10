"""Розрахунки за файлами одного прогону (results/<прогін>/); ті самі функції використовує analyze.py.

python3 scripts/calc.py stat   <прогін>      лічильники і тривалості кроків воркерів (різниця знімків /metrics)
python3 scripts/calc.py timing <прогін>      t_io, t_cpu → results/<прогін>/timing.json
python3 scripts/calc.py x      <прогін>      X за останні 30 с до спорожнення черги
python3 scripts/calc.py rate   <множник>     множник · X(R) з results/step6_w5
python3 scripts/calc.py lease                L = t_cpu / 2 для сценарію Ф5, секунд
python3 scripts/calc.py dup    <прогін>      D за таблицею результатів і за журналами воркерів
"""
import csv
import glob
import gzip
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RES = os.path.join(ROOT, "results")
R = 5
WINDOW = 30          # X(W) — за останні 30 с до спорожнення черги
LINE = re.compile(r'^(\w+?)(?:_(bucket|sum|count))?(?:\{(.*)\})? ([-+0-9.eEinfa]+)$')


def rd(run, name):
    return os.path.join(RES, run, name)


def parse_metrics(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.startswith("#"):
            continue
        key, _, val = line.rstrip("\n").rpartition(" ")
        out[key] = float(val)
    return out


def metrics_delta(run):
    """Сума по всіх воркерах: (після − до) для кожного ряду; воркер без знімка «до» рахується від нуля."""
    total = defaultdict(float)
    for after in glob.glob(rd(run, "metrics_after_*.txt")):
        before = after.replace("metrics_after_", "metrics_before_")
        b = parse_metrics(before) if os.path.exists(before) else {}
        for k, v in parse_metrics(after).items():
            total[k] += v - b.get(k, 0.0)
    return total


def counter(m, name, **labels):
    lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return m.get(f"{name}{{{lab}}}", 0.0)


def hist(m, step):
    """Гістограма кроку: [(верхня межа, накопичена кількість)], сума, кількість."""
    buckets = []
    for k, v in m.items():
        mt = re.match(r'worker_task_duration_seconds_bucket\{le="([^"]+)",step="%s"\}' % step, k)
        if mt:
            buckets.append((float(mt.group(1).replace("+Inf", "inf")), v))
    buckets.sort()
    return (buckets, counter(m, "worker_task_duration_seconds_sum", step=step),
            counter(m, "worker_task_duration_seconds_count", step=step))


def quantile(buckets, q):
    """Квантиль за гістограмою з лінійною інтерполяцією всередині бакета (як histogram_quantile)."""
    if not buckets or buckets[-1][1] <= 0:
        return None
    rank = q * buckets[-1][1]
    lo, prev = 0.0, 0.0
    for le, c in buckets:
        if c >= rank:
            if math.isinf(le):
                return lo
            return lo + (le - lo) * (rank - prev) / (c - prev) if c > prev else le
        lo, prev = le, c
    return lo


def merged_claim_hist(m):
    """Усі команди захоплення разом: з задачею і порожні."""
    a, sa, ca = hist(m, "claim")
    b, sb, cb = hist(m, "claim_empty")
    d = defaultdict(float)
    for le, c in a + b:
        d[le] += c
    return sorted(d.items()), sa + sb, ca + cb


def step_stats(run):
    m = metrics_delta(run)
    out = {"tasks": {r: int(counter(m, "worker_tasks_total", result=r)) for r in ("done", "failed", "lost")},
           "claims": {r: int(counter(m, "worker_claims_total", result=r)) for r in ("task", "empty")},
           "steps": {}}
    for step in ("reap", "claim", "claim_empty", "fetch", "process", "store"):
        b, s, c = hist(m, step)
        out["steps"][step] = {"count": int(c), "mean_ms": s / c * 1e3 if c else None,
                              "p99_ms": quantile(b, .99) * 1e3 if c else None}
    b, s, c = merged_claim_hist(m)
    out["steps"]["claim_all"] = {"count": int(c), "mean_ms": s / c * 1e3 if c else None,
                                 "p99_ms": quantile(b, .99) * 1e3 if c else None}
    return out


def tasks(run):
    rows = []
    for r in csv.DictReader(open(rd(run, "tasks.csv"))):
        for k in ("id", "record_id", "priority", "attempts", "token"):
            r[k] = int(r[k])
        for k in ("created", "started", "finished"):
            r[k] = float(r[k]) if r[k] else None
        rows.append(r)
    return rows


def results_per_task(run):
    c = Counter()
    for r in csv.DictReader(open(rd(run, "task_results.csv"))):
        c[int(r["task_id"])] += 1
    return c


def log_lines(run):
    for path in sorted(glob.glob(rd(run, "log_*.log")) + glob.glob(rd(run, "log_*.log.gz"))):
        pod = os.path.basename(path)[4:].split(".log")[0]
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield pod, line.rstrip("\n")


def dup(run):
    """D = n_dup / n двома способами: за таблицею результатів і за рядками done у журналах."""
    n = len(tasks(run))
    by_table = sum(1 for v in results_per_task(run).values() if v > 1)
    done = Counter()
    for _, line in log_lines(run):
        mt = re.search(r" task=(\d+) done ", line)
        if mt:
            done[int(mt.group(1))] += 1
    by_logs = sum(1 for v in done.values() if v > 1)
    return {"n": n, "results": sum(results_per_task(run).values()), "n_dup_table": by_table,
            "D_table": by_table / n if n else None, "done_lines": sum(done.values()),
            "n_dup_logs": by_logs, "D_logs": by_logs / n if n else None}


def db_seconds(run):
    rows = []
    for r in csv.DictReader(open(rd(run, "db_seconds.csv"))):
        try:
            rows.append({k: float(v) for k, v in r.items()})
        except (ValueError, TypeError):
            pass                     # обірваний останній рядок
    return rows


def throughput(run):
    """X: завершені задачі за секунду у вікні останніх 30 с до завершення останньої задачі."""
    fin = sorted(t["finished"] for t in tasks(run) if t["status"] == "done" and t["finished"])
    end = fin[-1]
    start = max(end - WINDOW, fin[0])
    per = Counter(int((t - start)) for t in fin if start < t <= end)
    secs = int(round(end - start))
    counts = [per.get(i, 0) for i in range(secs)]
    return {"X": len([t for t in fin if start < t <= end]) / (end - start), "std": statistics.pstdev(counts),
            "window_s": end - start, "from": start, "to": end, "drain_s": end - fin[0], "done": len(fin),
            "per_second": counts}


def timing(run):
    s = step_stats(run)["steps"]
    t = {"t_fetch_ms": s["fetch"]["mean_ms"], "t_store_ms": s["store"]["mean_ms"],
         "t_io_ms": s["fetch"]["mean_ms"] + s["store"]["mean_ms"], "t_cpu_ms": s["process"]["mean_ms"],
         "t_claim_ms": s["claim"]["mean_ms"], "tasks": s["process"]["count"]}
    json.dump(t, open(rd(run, "timing.json"), "w"), indent=1)
    return t


def main():
    cmd = sys.argv[1]
    if cmd == "stat":
        s = step_stats(sys.argv[2])
        print(f"worker_tasks_total:  done = {s['tasks']['done']}, failed = {s['tasks']['failed']}, lost = {s['tasks']['lost']}")
        print(f"worker_claims_total: task = {s['claims']['task']}, empty = {s['claims']['empty']}")
        for k, v in s["steps"].items():
            if v["count"]:
                print(f"  {k:12s} n = {v['count']:6d}  середнє = {v['mean_ms']:8.3f} мс  p99 = {v['p99_ms']:8.3f} мс")
    elif cmd == "timing":
        t = timing(sys.argv[2])
        print(f"задач: {t['tasks']}; t_io = fetch + store = {t['t_fetch_ms']:.2f} + {t['t_store_ms']:.2f} = "
              f"{t['t_io_ms']:.2f} мс; t_cpu = {t['t_cpu_ms']:.2f} мс")
    elif cmd == "x":
        x = throughput(sys.argv[2])
        print(f"X = {x['X']:.1f} задач/с за останні {x['window_s']:.1f} с (розкид {x['std']:.1f}); "
              f"черга спорожніла за {x['drain_s']:.1f} с, завершено {x['done']}")
    elif cmd == "rate":
        print(f"{float(sys.argv[2]) * throughput(f'step6_w{R}')['X']:.1f}")
    elif cmd == "lease":
        print(f"{json.load(open(rd('step2', 'timing.json')))['t_cpu_ms'] / 2 / 1e3:.4f}")
    elif cmd == "dup":
        d = dup(sys.argv[2])
        print(f"n = {d['n']}; рядків у task_results: {d['results']}; n_dup за таблицею = {d['n_dup_table']}, "
              f"D = {d['D_table']:.4f}; рядків done у журналах: {d['done_lines']}; "
              f"n_dup за журналами = {d['n_dup_logs']}, D = {d['D_logs']:.4f}")
        mx = max((r["lock_waiters"] for r in db_seconds(sys.argv[2])), default=0)
        print(f"найбільше рядків pg_stat_activity з wait_event_type = 'Lock' за прогін: {int(mx)}")


if __name__ == "__main__":
    main()
