"""Аналіз серії ЛР 4 офлайн з файлів results/ (таблиці бази, журнали і знімки /metrics воркерів,
посекундний стан бази, ряди Prometheus, вивід генератора).
Пише results/summary.json, таблиці results/tables/*.md і діаграми screens/chart_*.png.
"""
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from calc import (RES, R, db_seconds, dup, log_lines, results_per_task, rd, step_stats,  # noqa: E402
                  tasks, throughput)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TAB = os.path.join(RES, "tables")
SCR = os.path.join(ROOT, "screens")
os.makedirs(TAB, exist_ok=True)
os.makedirs(SCR, exist_ok=True)
T_POLL = 0.050
F = 0.15
WS = [1, 2, R]
ZERO = re.compile(r"онов(?:ив|ило) 0 рядків")
plt.rcParams.update({"font.size": 10})


def fnum(x, d=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.{d}f}".replace(",", " ")


def pct(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def write(name, text):
    open(os.path.join(TAB, name), "w", encoding="utf-8").write(text.rstrip() + "\n")


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def gen(run):
    return json.load(open(rd(run, "gen.json")))


def prom(run, name):
    """Ряд Prometheus [(t, v)]; для рядів з кількома мітками — сума."""
    p = json.load(open(rd(run, "prom.json")))
    acc = {}
    for r in p["series"][name]:
        for t, v in r["values"]:
            acc[float(t)] = acc.get(float(t), 0.0) + float(v)
    return sorted(acc.items())


def rate_between(points, a, b):
    """Середня швидкість зростання лічильника між моментами a і b (за найближчими точками)."""
    pa = [p for p in points if p[0] <= a] or points[:1]
    pb = [p for p in points if p[0] >= b] or points[-1:]
    (ta, va), (tb, vb) = pa[-1], pb[0]
    return (vb - va) / (tb - ta) if tb > ta else None


def wq(rows):
    return [t["started"] - t["created"] for t in rows if t["started"] is not None]


def q99(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.99 * len(xs)) - 1)] if xs else None


summary = {}

# ---------- крок 2: t_io і t_cpu ----------
tm = json.load(open(rd("step2", "timing.json")))
s2 = step_stats("step2")
summary["step2"] = tm
x1_pred = 1 / ((tm["t_io_ms"] + tm["t_cpu_ms"]) / 1e3 + T_POLL / 2)
write("step2.md", table(
    ["Величина", "Значення, мс", "Звідки"],
    [["задач", tm["tasks"], "`worker_task_duration_seconds_count{step=\"process\"}`"],
     ["fetch (GET запису від web)", fnum(tm["t_fetch_ms"], 2), "`_sum / _count`, step=\"fetch\""],
     ["store (завершення і запис результату)", fnum(tm["t_store_ms"], 2), "step=\"store\""],
     ["**t_io** = fetch + store", f"**{fnum(tm['t_io_ms'], 2)}**", ""],
     ["**t_cpu** = process", f"**{fnum(tm['t_cpu_ms'], 2)}**", "step=\"process\""],
     ["захоплення з задачею (claim)", fnum(tm["t_claim_ms"], 2), "step=\"claim\""],
     ["порожні захоплення за прогін", s2["claims"]["empty"], "`worker_claims_total{result=\"empty\"}`"]]))

# ---------- кроки 3–4: дублі ----------
dup_rows, dup_chart = [], []
for run, name in (("step3_poll50", "наївне, T_poll = 50 мс"), ("step3_poll1", "наївне, T_poll = 1 мс"),
                  ("step4_skip_locked", "FOR UPDATE SKIP LOCKED, 1 мс"), ("step4_for_update", "FOR UPDATE, 1 мс")):
    d = dup(run)
    st = step_stats(run)
    mx = max((r["lock_waiters"] for r in db_seconds(run)), default=0)
    mxl = max((r["locks_not_granted"] for r in db_seconds(run)), default=0)
    summary[run] = {"dup": d, "claims": st["claims"], "claim_p99_ms": st["steps"]["claim"]["p99_ms"],
                    "claim_all_p99_ms": st["steps"]["claim_all"]["p99_ms"], "lock_max": mx, "not_granted_max": mxl}
    dup_rows.append([name, d["n"], d["results"], d["n_dup_table"], f"{d['D_table']:.4f}", d["done_lines"],
                     d["n_dup_logs"], f"{d['D_logs']:.4f}", fnum(st["steps"]["claim"]["p99_ms"], 2),
                     st["claims"]["empty"], int(mx), int(mxl)])
    dup_chart.append((name, d["D_table"]))
write("dup.md", table(["Захоплення", "n", "Рядків у task_results", "n_dup (таблиця)", "D (таблиця)",
                       "Рядків done у журналах", "n_dup (журнали)", "D (журнали)", "p99 захоплення, мс",
                       "Порожніх захоплень", "Найбільше Lock у pg_stat_activity", "Найбільше granted = false"],
                      dup_rows))
fig, ax = plt.subplots(figsize=(8, 3.8))
ax.barh([n for n, _ in dup_chart][::-1], [d for _, d in dup_chart][::-1], color=["#4c78a8", "#4c78a8", "#e45756", "#e45756"])
for i, (_, d) in enumerate(dup_chart[::-1]):
    ax.text(d + 0.005, i, f"{d:.3f}", va="center")
ax.set_xlabel("D = n_dup / n (за таблицею task_results)"); ax.set_xlim(0, max(d for _, d in dup_chart) * 1.2 + 0.01)
ax.set_title(f"Кроки 3–4: частка дублів, {R} воркерів, 300 задач на 100 за секунду")
fig.tight_layout(); fig.savefig(os.path.join(SCR, "chart_dup.png"), dpi=120); plt.close(fig)

# крок 4, контрольний прогін з непорожньою чергою
full = []
for run, name in (("step4b_skip_locked", "FOR UPDATE SKIP LOCKED"), ("step4b_for_update", "FOR UPDATE")):
    if not os.path.exists(rd(run, "tasks.csv")):
        continue
    d = dup(run)
    st = step_stats(run)
    x = throughput(run)
    dbs = db_seconds(run)
    lock_secs = sum(1 for r in dbs if r["lock_waiters"] > 0)
    summary[run] = {"dup": d, "claims": st["claims"], "claim_p99_ms": st["steps"]["claim"]["p99_ms"],
                    "claim_mean_ms": st["steps"]["claim"]["mean_ms"], "X": x["X"],
                    "lock_max": max((r["lock_waiters"] for r in dbs), default=0), "lock_seconds": lock_secs,
                    "seconds": len(dbs)}
    full.append([name, d["n"], f"{d['D_table']:.4f}", fnum(x["X"]), fnum(st["steps"]["claim"]["mean_ms"], 2),
                 fnum(st["steps"]["claim"]["p99_ms"], 2), st["claims"]["empty"],
                 int(summary[run]["lock_max"]), f"{lock_secs} з {len(dbs)}"])
if full:
    write("dup_full.md", table(["Захоплення (3000 задач у черзі)", "n", "D", "X, задач/с", "Захоплення: середнє, мс",
                                "p99 захоплення, мс", "Порожніх захоплень", "Найбільше Lock у pg_stat_activity",
                                "Секунд з Lock > 0"], full))

# ---------- крок 5 ----------
tid, victim = open(rd("step5", "victim.txt")).read().split()
tid = int(tid)
t5 = {t["id"]: t for t in tasks("step5")}[tid]
lines5 = [(pod, ln) for pod, ln in log_lines("step5") if f"task={tid} " in ln]
summary["step5"] = {"task": tid, "victim": victim, "after": {k: t5[k] for k in ("status", "attempts", "token", "worker_id")},
                    "results": results_per_task("step5")[tid], "log": lines5,
                    "lost": step_stats("step5")["tasks"]["lost"], "seconds_to_done": t5["finished"] - t5["created"]}

# ---------- крок 6: X(W) ----------
x6 = {}
for W in WS:
    run = f"step6_w{W}"
    x = throughput(run)
    st = step_stats(run)
    db = [r for r in db_seconds(run) if x["from"] <= r["unix"] <= x["to"]]
    tps = (db[-1]["xacts"] - db[0]["xacts"]) / (db[-1]["unix"] - db[0]["unix"]) if len(db) > 1 else None
    cpu = rate_between(prom(run, "db_cpu"), x["from"], x["to"])
    wcpu = rate_between(prom(run, "worker_cpu"), x["from"], x["to"])
    thr = rate_between(prom(run, "worker_throttled"), x["from"], x["to"])
    per = rate_between(prom(run, "worker_periods"), x["from"], x["to"])
    x6[W] = {"X": x["X"], "std": x["std"], "window_s": x["window_s"], "drain_s": x["drain_s"], "done": x["done"],
             "claim_p99_ms": st["steps"]["claim"]["p99_ms"], "tps": tps, "db_cpu": cpu,
             "worker_cpu": wcpu, "throttled": thr / per if thr is not None and per else None,
             "process_ms": st["steps"]["process"]["mean_ms"], "per_second": x["per_second"]}

X1 = x6[1]["X"]
C = {W: x6[W]["X"] / X1 for W in WS}
u = {W: W - 1 for W in WS}
v = {W: W * (W - 1) for W in WS}
y = {W: W / C[W] - 1 for W in WS}
fit = [W for W in WS if W > 1]
S_ = lambda f: sum(f(w) for w in fit)  # noqa: E731
Suu, Svv, Suv = S_(lambda w: u[w] ** 2), S_(lambda w: v[w] ** 2), S_(lambda w: u[w] * v[w])
Suy, Svy = S_(lambda w: u[w] * y[w]), S_(lambda w: v[w] * y[w])
den = Suu * Svv - Suv ** 2
sigma, kappa = (Svv * Suy - Suv * Svy) / den, (Suu * Svy - Suv * Suy) / den
note = ""
if sigma < 0:
    sigma, kappa, note = 0.0, Svy / Svv, "σ < 0 → σ = 0, κ = Σvy/Σv²"
elif kappa < 0:
    sigma, kappa, note = Suy / Suu, 0.0, "κ < 0 → κ = 0, σ = Σuy/Σu²"
w_star = math.sqrt((1 - sigma) / kappa) if kappa > 0 and sigma < 1 else None
summary["step6"] = {"X": {W: {k: v_ for k, v_ in x6[W].items() if k != "per_second"} for W in WS},
                    "C": C, "sigma": sigma, "kappa": kappa, "W_star": w_star, "note": note, "X1_pred": x1_pred,
                    "X1_pred_no_poll": 1 / ((tm["t_io_ms"] + tm["t_cpu_ms"]) / 1e3)}
write("xw.md", table(
    ["W", "X(W), задач/с", "Розкид (σ посекундної кількості)", "Вікно, с", "Спорожнення черги, с", "p99 захоплення, мс",
     "Транзакцій бази за секунду", "CPU пода бази, ядер", "CPU воркерів разом, ядер", "Дроселювання воркерів", "process, мс"],
    [[W, fnum(r["X"]), fnum(r["std"]), fnum(r["window_s"]), fnum(r["drain_s"]), fnum(r["claim_p99_ms"], 2),
      fnum(r["tps"], 0), fnum(r["db_cpu"], 3), fnum(r["worker_cpu"], 2), pct(r["throttled"]), fnum(r["process_ms"], 1)]
     for W, r in x6.items()]))
write("usl.md", table(["W", "X(W)", "C(W)", "u = W − 1", "v = W(W − 1)", "y = W/C − 1", "У підборі"],
                      [[W, fnum(x6[W]["X"]), f"{C[W]:.3f}", u[W], v[W], f"{y[W]:.4f}", "так" if W in fit else "ні"]
                       for W in WS]))
fig, ax = plt.subplots(figsize=(8, 5))
ax.errorbar(WS, [x6[W]["X"] for W in WS], yerr=[x6[W]["std"] for W in WS], fmt="o", capsize=5,
            label="виміряне X(W) ± розкид")
ww = [i / 10 for i in range(10, 81)]
ax.plot(ww, [X1 * w / (1 + sigma * (w - 1) + kappa * w * (w - 1)) for w in ww],
        label=f"закон: σ = {sigma:.4f}, κ = {kappa:.5f}")
ax.plot(ww, [X1 * w for w in ww], ":", color="gray", label="лінійне X(1) · W")
ax.axhline(x1_pred, ls="--", color="#e45756", lw=1, label=f"1 / (t_io + t_cpu + T_poll/2) = {x1_pred:.1f}")
ax.set_xlabel("W, воркерів"); ax.set_ylabel("X(W), задач/с"); ax.set_xticks(range(1, 9)); ax.legend(fontsize=8)
ax.set_title("Крок 6: пропускна здатність пулу з непорожньою чергою")
fig.tight_layout(); fig.savefig(os.path.join(SCR, "chart_xw.png"), dpi=120); plt.close(fig)
XR = x6[R]["X"]


def done_per_second(rows, t0, t1):
    c = Counter(int(t["finished"] - t0) for t in rows if t["finished"] and t0 <= t["finished"] < t1 and t["status"] == "done")
    return [c.get(i, 0) for i in range(int(t1 - t0))]


def timeline(run, title, out, marks=(), extra=None):
    g = gen(run)
    rows = tasks(run)
    t0 = g["t0_unix"]
    t1 = max([t["finished"] for t in rows if t["finished"]] + [g["end_unix"]]) + 3
    fig, ax = plt.subplots(figsize=(9, 4.4))
    for name, lab, col in (("tasks_queued", "tasks_queued", "#4c78a8"), ("tasks_running", "tasks_running", "#f58518")):
        pts = [(t - t0, v_) for t, v_ in prom(run, name) if t0 - 5 <= t <= t1]
        ax.step([p[0] for p in pts], [p[1] for p in pts], where="post", label=lab, color=col)
    ax2 = ax.twinx()
    d = done_per_second(rows, t0, t1)
    ax2.plot(range(len(d)), d, color="#54a24b", lw=0.8, alpha=0.8, label="завершено задач за секунду")
    ax2.set_ylabel("завершено задач за секунду", color="#54a24b")
    for t, lab in marks:
        ax.axvline(t - t0, color="#e45756", ls="--", lw=1.2)
        ax.text(t - t0 + 1, ax.get_ylim()[1] * 0.92, lab, color="#e45756", fontsize=8)
    if extra:
        extra(ax, t0)
    ax.set_xlabel("секунди від початку прогону"); ax.set_ylabel("задач (вимірювачі web, збір 5 с)")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    ax.set_title(title)
    fig.tight_layout(); fig.savefig(os.path.join(SCR, out), dpi=120); plt.close(fig)


# ---------- крок 7: закон Літтла ----------
g = gen("step7_07")
rows7 = tasks("step7_07")
a, b = g["t0_unix"] + 60, g["t0_unix"] + 180
win = [t for t in rows7 if a <= t["created"] < b]
lam = len(win) / (b - a)
Wq = statistics.fmean(wq(win))
Lq_prom = [v_ for t, v_ in prom("step7_07", "tasks_queued") if a <= t <= b]
Lq_db = [r["queued"] for r in db_seconds("step7_07") if a <= r["unix"] <= b]
Lq = statistics.fmean(Lq_prom)
summary["step7"] = {"rate_set": g["rate"], "lambda": lam, "Wq_s": Wq, "lambda_Wq": lam * Wq, "Lq": Lq,
                    "Lq_points": len(Lq_prom), "Lq_db": statistics.fmean(Lq_db), "Lq_db_points": len(Lq_db),
                    "diff": (Lq - lam * Wq) / (lam * Wq), "diff_db": (statistics.fmean(Lq_db) - lam * Wq) / (lam * Wq),
                    "Wq_p99_s": q99(wq(win)), "XR": XR}
g13 = gen("step7_13")
rows13 = sorted(tasks("step7_13"), key=lambda t: t["created"])
end_q = [r for r in db_seconds("step7_13") if r["unix"] <= g13["last_sent_unix"] + 0.5]
end_qp = [v_ for t, v_ in prom("step7_13", "tasks_queued") if t <= g13["last_sent_unix"] + 0.5]
gen_rows13 = [t for t in rows13 if t["created"] <= g13["last_sent_unix"] + 0.5]
x13 = sum(1 for t in rows13 if t["finished"] and g13["t0_unix"] + 30 <= t["finished"] < g13["last_sent_unix"]) / (g13["last_sent_unix"] - g13["t0_unix"] - 30)
summary["step7_13"] = {"rate_set": g13["rate"], "created": len(rows13),
                       "lambda": len(gen_rows13) / (g13["last_sent_unix"] - g13["t0_unix"]),
                       "queue_end_db": end_q[-1]["queued"] if end_q else None,
                       "queue_end_prom": end_qp[-1] if end_qp else None,
                       "Wq_first100": statistics.fmean(wq(rows13[:100])), "Wq_last100": statistics.fmean(wq(rows13[-100:])),
                       "X_during": x13, "growth_pred": (len(gen_rows13) / (g13["last_sent_unix"] - g13["t0_unix"]) - x13) * (g13["last_sent_unix"] - g13["t0_unix"])}
s7, s713 = summary["step7"], summary["step7_13"]
write("little.md", table(["Величина", "Значення", "Звідки"], [
    ["X(R), задач/с", fnum(XR), "крок 6, W = 5"],
    ["задана інтенсивність 0,7 · X(R)", fnum(s7["rate_set"]), "параметр генератора"],
    ["λ, задач/с", fnum(s7["lambda"], 2), "задачі з created_at в останніх 120 с / 120"],
    ["W_q, с", fnum(s7["Wq_s"], 4), "середнє started_at − created_at цих задач"],
    ["**λ · W_q**", f"**{fnum(s7['lambda_Wq'], 3)}**", ""],
    ["**L_q виміряне**", f"**{fnum(s7['Lq'], 3)}**", f"середнє tasks_queued у Prometheus, {s7['Lq_points']} точок по 5 с"],
    ["розбіжність (L_q − λW_q) / λW_q", pct(s7["diff"]), ""],
    ["L_q за посекундним записом бази", fnum(s7["Lq_db"], 3), f"`count(*) WHERE status = 'queued'`, {s7['Lq_db_points']} точок"],
    ["розбіжність для нього", pct(s7["diff_db"]), ""],
    ["p99 W_q, с", fnum(s7["Wq_p99_s"], 4), ""]]))
write("overload.md", table(["Величина", "Значення"], [
    ["задана інтенсивність 1,3 · X(R), задач/с", fnum(s713["rate_set"])],
    ["створено задач за 120 с", s713["created"]],
    ["X під час прогону (без перших 30 с), задач/с", fnum(s713["X_during"])],
    ["довжина черги на кінець генерації (посекундний запис бази)", fnum(s713["queue_end_db"], 0)],
    ["те саме, tasks_queued (оновлюється раз на 5 с)", fnum(s713["queue_end_prom"], 0)],
    ["W_q перших 100 задач, с", fnum(s713["Wq_first100"], 3)],
    ["W_q останніх 100 задач, с", fnum(s713["Wq_last100"], 3)]]))
timeline("step7_07", f"Крок 7: λ = 0,7 · X(R) = {s7['rate_set']:.0f} задач/с, {R} воркерів", "chart_step7_07.png",
         marks=[(a, "початок вікна 120 с")])
timeline("step7_13", f"Крок 7: λ = 1,3 · X(R) = {s713['rate_set']:.0f} задач/с, {R} воркерів", "chart_step7_13.png",
         marks=[(g13["last_sent_unix"], "кінець генерації")])

# ---------- крок 8: SIGTERM ----------
def step8(run):
    rows = tasks(run)
    codes = {}
    for line in open(rd(run, "watch.txt")):
        p = line.split()
        if len(p) >= 4 and p[2] != "<none>":
            codes[p[0]] = (p[2], p[3])
    returned = release0 = 0
    exits = {}
    for f in glob.glob(rd(run, "exit_*.log")):
        txt = open(f, encoding="utf-8").read()
        returned += len(re.findall(r"повернуто в чергу \(оновлено рядків: 1\)", txt))
        release0 += len(re.findall(r"повернуто в чергу \(оновлено рядків: 0\)", txt))
        exits[os.path.basename(f)[5:-4]] = "вихід з кодом 0" in txt
    rp = results_per_task(run)
    restart = int(open(rd(run, "restart_unix.txt")).read())
    # задачі, виконані двічі: рядки done у журналах старих (exit_*) і нових (log_*) подів
    done_lines = Counter()
    for f in glob.glob(rd(run, "exit_*.log")) + glob.glob(rd(run, "log_*.log")):
        for m in re.finditer(r" task=(\d+) done ", open(f, encoding="utf-8").read()):
            done_lines[int(m.group(1))] += 1
    twice = sorted(k for k, c in done_lines.items() if c > 1)
    return {"codes": codes, "returned_by_workers": returned, "release_zero_rows": release0, "exit_line": exits,
            "failed": sum(1 for t in rows if t["status"] == "failed"),
            "done": sum(1 for t in rows if t["status"] == "done"), "tasks": len(rows),
            "multi_results": sum(1 for v_ in rp.values() if v_ > 1), "attempts_gt1": sum(1 for t in rows if t["attempts"] > 1),
            "restart": restart, "done_twice": twice,
            "gt1_ids": [t["id"] for t in rows if t["attempts"] > 1]}


S8 = ("step8_before_fix", "step8_before_fix_grace0", "step8_fix1", "step8_fix1_grace0", "step8", "step8_grace0")
s8 = {run: step8(run) for run in S8
      if os.path.exists(rd(run, "tasks.csv"))}
summary["step8"] = s8
rows8 = []
for run, lab in (("step8_before_fix", "до виправлення, GRACE_SECONDS = 25"),
                 ("step8_before_fix_grace0", "до виправлення, GRACE_SECONDS = 0"),
                 ("step8_fix1", "блокування захоплення, GRACE_SECONDS = 25"),
                 ("step8_fix1_grace0", "блокування захоплення, GRACE_SECONDS = 0"),
                 ("step8", "остаточний код, GRACE_SECONDS = 25"), ("step8_grace0", "остаточний код, GRACE_SECONDS = 0")):
    if run not in s8:
        continue
    r = s8[run]
    cc = Counter(f"{c} ({why})" for c, why in r["codes"].values())
    rows8.append([lab, ", ".join(f"{k} × {v_}" for k, v_ in sorted(cc.items())) or "—",
                  f"{r['returned_by_workers']} (+ {r['release_zero_rows']} з 0 рядків)",
                  r["failed"], r["multi_results"], r["attempts_gt1"],
                  ", ".join(map(str, r["gt1_ids"])) or "—", len(r["done_twice"]), f"{r['done']} / {r['tasks']}"])
write("sigterm.md", table(["Прогін", "Коди завершення старих контейнерів", "Повернуто в чергу воркерами (команда оновила 1 рядок)",
                           "failed", "Задач з > 1 результатом", "attempts > 1", "id задач з attempts > 1",
                           "Виконано двічі (рядки done у журналах)", "done / усього"], rows8))
if "step8" in s8:
    timeline("step8", "Крок 8: kubectl rollout restart deployment/worker під 0,7 · X(R)", "chart_step8.png",
             marks=[(s8["step8"]["restart"], "rollout restart")])

# ---------- крок 9: FAIL_RATE і Ф5 ----------
s9 = {}
for run, hb in (("step9_nohb", False), ("step9_hb", True), ("step9default_nohb", False), ("step9default_hb", True)):
    if not os.path.exists(rd(run, "tasks.csv")):
        continue
    g = gen(run)
    rows = tasks(run)
    sc = int(open(rd(run, "scenario_unix.txt")).read())
    scd = int(open(rd(run, "scenario_done_unix.txt")).read())
    att = Counter(t["attempts"] for t in rows)
    rp = results_per_task(run)
    # Частки attempts — серед задач, що дійшли до кінцевого стану (done або failed). За падінь пул не встигає
    # за λ, черга росте, і задачі, створені після сценарію, не встигають обробитися; тому фази розділено
    # за моментом завершення задачі (finished_at), а не створення.
    fin = [t for t in rows if t["status"] in ("done", "failed")]
    before = [t for t in fin if t["finished"] < sc]
    after = [t for t in fin if t["finished"] >= scd]

    def share(rs, cond):
        return sum(1 for t in rs if cond(t)) / len(rs) if rs else None

    z1 = sum(1 for _, ln in log_lines(f"{run}_phase1") if ZERO.search(ln))
    z_all = sum(1 for _, ln in log_lines(run) if ZERO.search(ln))
    hb0 = {ph: sum(1 for _, ln in log_lines(r_) if "heartbeat оновив 0 рядків" in ln) for ph, r_ in (("1", f"{run}_phase1"), ("2", run))}
    lost = {ph: sum(1 for _, ln in log_lines(r_) if "завершення оновило 0 рядків" in ln) for ph, r_ in (("1", f"{run}_phase1"), ("2", run))}
    reaps = {ph: sum(1 for _, ln in log_lines(r_) if "lease сплив" in ln) for ph, r_ in (("1", f"{run}_phase1"), ("2", run))}
    crashes1 = sum(1 for _, ln in log_lines(f"{run}_phase1") if "вбудоване падіння" in ln)
    crashes2 = sum(1 for _, ln in log_lines(run) if "вбудоване падіння" in ln)
    # перезапуски: перша половина — поди до сценарію, друга — поди після (стовпчик RESTARTS)
    def restarts(path):
        n = 0
        for line in open(path):
            p = line.split()
            if p and p[0].startswith("worker-") and len(p) >= 4:
                n += int(p[3])
        return n
    r1 = restarts(rd(f"{run}_phase1", "pods.txt"))
    r2 = restarts(rd(run, "pods.txt"))
    done_rows = [t for t in rows if t["status"] == "done" and t["finished"]]
    x_before = sum(1 for t in done_rows if g["t0_unix"] + 30 <= t["finished"] < sc) / (sc - g["t0_unix"] - 30)
    x_after = sum(1 for t in done_rows if scd + 10 <= t["finished"] < g["last_sent_unix"]) / max(1, g["last_sent_unix"] - scd - 10)
    s9[run] = {"heartbeat": hb, "done": sum(1 for t in rows if t["status"] == "done"), "tasks": len(rows), "rate_set": g["rate"], "attempts": dict(sorted(att.items())),
               "status": dict(Counter(t["status"] for t in rows)),
               "finished": len(fin), "unfinished": len(rows) - len(fin),
               "attempts_finished": dict(sorted(Counter(t["attempts"] for t in fin).items())),
               "share_gt1": share(fin, lambda t: t["attempts"] > 1), "share_failed": share(fin, lambda t: t["status"] == "failed"),
               "before": {"n": len(before), "gt1": share(before, lambda t: t["attempts"] > 1),
                          "failed": share(before, lambda t: t["status"] == "failed"), "n_gt1": sum(1 for t in before if t["attempts"] > 1)},
               "after": {"n": len(after), "gt1": share(after, lambda t: t["attempts"] > 1),
                         "failed": share(after, lambda t: t["status"] == "failed"), "n_gt1": sum(1 for t in after if t["attempts"] > 1)},
               "multi_results": sum(1 for v_ in rp.values() if v_ > 1), "zero_rows_phase1": z1, "zero_rows_phase2": z_all,
               "crashes_phase1": crashes1, "crashes_phase2": crashes2, "restarts_phase1": r1, "restarts_phase2": r2,
               "X_before": x_before, "X_after": x_after, "scenario": sc, "scenario_done": scd,
               "hb_zero": hb0, "store_lost": lost, "reaps": reaps}
    timeline(run, f"Крок 9{' (пауза перезапуску за замовчуванням)' if 'default' in run else ''}: FAIL_RATE = 0,15, "
                  f"{g['rate']:.0f} задач/с; Ф5 {'з heartbeat' if hb else 'без heartbeat'}",
             f"chart_{run}.png", marks=[(sc, "Ф5: L = t_cpu / 2")])
summary["step9"] = s9
LF5 = tm["t_cpu_ms"] / 2
rows9 = []
for run in [x for x in ("step9default_nohb", "step9default_hb", "step9_nohb", "step9_hb") if x in s9]:
    r = s9[run]
    rows9.append([("пауза за замовчуванням, " if "default" in run else "пауза 1 с, ") +
                  ("з heartbeat" if r["heartbeat"] else "без heartbeat"), r["tasks"],
                  f"{r['restarts_phase1']} + {r['restarts_phase2']}",
                  ", ".join(f"{k}: {v_}" for k, v_ in r["attempts_finished"].items()),
                  ", ".join(f"{k}: {v_}" for k, v_ in sorted(r["status"].items())),
                  f"{pct(r['share_gt1'])} / {pct(F, 0)}", f"{pct(r['share_failed'], 2)} / {pct(F ** 3, 2)}",
                  r["multi_results"], fnum(r["X_before"]), fnum(r["X_after"]), fnum(XR)])
write("fail.md", table(["Прогін (CrashLoopBackOff, Ф5)", "Створено задач", "Перезапусків (до + після сценарію)",
                        "attempts: задач (лише done і failed)", "Статуси після прогону",
                        "attempts > 1: виміряне / f", "failed: виміряне / f³",
                        "Задач з > 1 результатом", "X до сценарію, задач/с", "X після сценарію, задач/с",
                        "X(R) без падінь, задач/с"], rows9))
rows9b = []
for run in [x for x in ("step9_nohb", "step9_hb") if x in s9]:
    r = s9[run]
    for ph, k, lab, z, cr in (("before", "1", f"{'з' if r['heartbeat'] else 'без'} heartbeat: до сценарію, L = 10 с, heartbeat",
                               r["zero_rows_phase1"], r["crashes_phase1"]),
                              ("after", "2", f"{'з' if r['heartbeat'] else 'без'} heartbeat: після, L = {LF5:.1f} мс, heartbeat {'є' if r['heartbeat'] else 'немає'}",
                               r["zero_rows_phase2"], r["crashes_phase2"])):
        p = r[ph]
        rows9b.append([lab, p["n"], f"{p['n_gt1']} ({pct(p['gt1'])})", pct(F, 0), pct(p["failed"], 2), pct(F ** 3, 2),
                       cr, r["reaps"][k], f"{z} ({r['hb_zero'][k]} heartbeat + {r['store_lost'][k]} завершення)", 0])
write("f5.md", table(["Фаза прогону", "Задач завершено (done або failed)", "attempts > 1", "Очікувано за f", "failed",
                      "Очікувано f³", "Вбудованих падінь", "Повернень зі спливлим lease",
                      "Рядків «0 оновлених рядків»", "Очікувано рядків «0 рядків» лише від FAIL_RATE"], rows9b))

# ---------- крок 10: Д1 ----------
g10 = gen("step10_d1")
rows10 = tasks("step10_d1")
d1 = {}
for p in (3, 2, 1):
    rs = [t for t in rows10 if t["priority"] == p]
    w = wq(rs)
    d1[p] = {"n": len(rs), "mean": statistics.fmean(w), "p50": statistics.median(w), "p99": q99(w), "max": max(w)}
summary["step10"] = {"rate_set": g10["rate"], "by_priority": d1, "tasks": len(rows10)}
write("d1.md", table(["priority", "Задач", "W_q середнє, с", "W_q p50, с", "W_q p99, с", "W_q найбільше, с"],
                     [[p, r["n"], fnum(r["mean"], 3), fnum(r["p50"], 3), fnum(r["p99"], 3), fnum(r["max"], 3)]
                      for p, r in d1.items()]))
fig, ax = plt.subplots(figsize=(9, 4.4))
t0 = g10["t0_unix"]
for p, col in ((3, "#54a24b"), (2, "#4c78a8"), (1, "#e45756")):
    rs = sorted((t for t in rows10 if t["priority"] == p and t["started"]), key=lambda t: t["created"])
    ax.plot([t["created"] - t0 for t in rs], [t["started"] - t["created"] for t in rs], ".", ms=1.5, color=col,
            label=f"priority {p}")
ax.axvline(g10["last_sent_unix"] - t0, color="gray", ls="--", lw=1)
ax.set_xlabel("момент створення задачі, с від початку прогону"); ax.set_ylabel("W_q = started_at − created_at, с")
ax.set_title(f"Д1: W_q за пріоритетом, λ = 1,3 · X(R) = {g10['rate']:.0f} задач/с"); ax.legend(markerscale=8, fontsize=8)
fig.tight_layout(); fig.savefig(os.path.join(SCR, "chart_d1.png"), dpi=120); plt.close(fig)

json.dump(summary, open(os.path.join(RES, "summary.json"), "w"), indent=1, ensure_ascii=False, default=str)

# ---------- вивід ----------
print(f"Крок 2: t_io = {tm['t_io_ms']:.2f} мс, t_cpu = {tm['t_cpu_ms']:.2f} мс (20 задач)")
for run in ("step3_poll50", "step3_poll1", "step4_skip_locked", "step4_for_update"):
    s = summary[run]
    print(f"{run:18s} D(таблиця) = {s['dup']['D_table']:.4f}  D(журнали) = {s['dup']['D_logs']:.4f}  "
          f"p99 claim = {s['claim_p99_ms']:.2f} мс  порожніх = {s['claims']['empty']}  Lock max = {int(s['lock_max'])}")
s5 = summary["step5"]
print(f"Крок 5: task={s5['task']} → {s5['after']}, результатів {s5['results']}, lost у воркерів = {s5['lost']}")
for W in WS:
    r = x6[W]
    print(f"Крок 6: W = {W}: X = {r['X']:.1f} ± {r['std']:.1f}, p99 claim = {r['claim_p99_ms']:.2f} мс, "
          f"TPS = {r['tps']:.0f}, CPU бази = {r['db_cpu']:.3f}, process = {r['process_ms']:.1f} мс")
print(f"        C = {', '.join(f'{W}: {C[W]:.3f}' for W in WS)}; σ = {sigma:.4f}, κ = {kappa:.5f}, W* = {w_star}  {note}")
print(f"        X(1) = {X1:.1f} проти 1/(t_io + t_cpu + T_poll/2) = {x1_pred:.1f}")
print(f"Крок 7: λ = {s7['lambda']:.2f}, W_q = {s7['Wq_s']:.4f} с, λW_q = {s7['lambda_Wq']:.3f}, L_q = {s7['Lq']:.3f} "
      f"({pct(s7['diff'])}); за записом бази {s7['Lq_db']:.3f} ({pct(s7['diff_db'])})")
print(f"        1,3·X(R): черга на кінець {s713['queue_end_db']}, W_q перші/останні 100: "
      f"{s713['Wq_first100']:.3f} / {s713['Wq_last100']:.3f} с")
for run, r in s8.items():
    print(f"Крок 8 {run}: коди {dict(Counter(c for c, _ in r['codes'].values()))}, повернуто {r['returned_by_workers']}, "
          f"failed {r['failed']}, >1 результату {r['multi_results']}, attempts>1 {r['attempts_gt1']}")
for run, r in s9.items():
    print(f"Крок 9 {run}: attempts {r['attempts']}, статуси {r['status']}, >1 результату {r['multi_results']}, "
          f"до: >1 {pct(r['before']['gt1'])} failed {pct(r['before']['failed'], 2)}; після: >1 {pct(r['after']['gt1'])} "
          f"failed {pct(r['after']['failed'], 2)}; «0 рядків» {r['zero_rows_phase1']} / {r['zero_rows_phase2']}; "
          f"X {r['X_before']:.1f} / {r['X_after']:.1f}")
for p, r in d1.items():
    print(f"Д1: priority {p}: n = {r['n']}, W_q середнє {r['mean']:.3f} с, p50 {r['p50']:.3f}, p99 {r['p99']:.3f}")
