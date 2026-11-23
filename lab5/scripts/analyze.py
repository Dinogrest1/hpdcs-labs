#!/usr/bin/env python3
"""Аналіз ЛР 5 з файлів results/raw/*.json (лише стандартна бібліотека і matplotlib).

Читає сирі результати прогонів, бере медіану трьох повторів кожної величини і пише
таблиці 1–6 умови та допоміжні таблиці в results/tables/*.md, results/summary.json і графіки screens/chart_*.png.
Жодне число таблиць не вводиться вручну.
"""
import glob
import json
import math
import os
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "results", "raw")
TAB = os.path.join(ROOT, "results", "tables")
SCR = os.path.join(ROOT, "screens")
os.makedirs(TAB, exist_ok=True)
os.makedirs(SCR, exist_ok=True)
plt.rcParams.update({"font.size": 10})
LOCKS = {"global": "Глобальний замок", "rw": "RWLock"}


def load(pattern):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(RAW, pattern)))]


def med(xs):
    return statistics.median(xs)


def fmt(x, d=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.{d}f}".replace(",", " ")


def fint(x):
    return f"{round(x):,}".replace(",", " ")


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write(name, text):
    open(os.path.join(TAB, name), "w", encoding="utf-8").write(text.rstrip() + "\n")


def ratio(rw, g):
    """RW / G; якщо G = 0 — різниця RW − G (правило умови)."""
    if g == 0:
        return "різниця " + fmt(rw - g, 4)
    return fmt(rw / g, 3)


runs = {lk: load(f"run_{lk}_*.json") for lk in LOCKS}
x1 = {lk: load(f"x1_{lk}_*.json") for lk in LOCKS}
cs = {lk: load(f"cs_{lk}_*.json") for lk in LOCKS}
clock = load("clock.json")
for lk in LOCKS:
    assert len(runs[lk]) == 3 and len(x1[lk]) == 3 and len(cs[lk]) == 3, f"{lk}: очікується 3 прогони"
n = runs["global"][0]["n"]
nw, nr = runs["global"][0]["n_w"], runs["global"][0]["n_r"]
D = runs["global"][0]["d_s"]


def role_med(lk, role, key):
    return med([r[role][key] for r in runs[lk]])


S = {}   # зведення для summary.json
for lk in LOCKS:
    s = {}
    for role in ("write", "read"):
        key = {"write": "w", "read": "r"}[role]
        d = {k: role_med(lk, role, k) for k in
             ("n_ops", "x_ops_per_s", "x_per_thread", "p50_ns", "p99_ns", "p999_ns", "max_wait_ns", "w", "cv",
              "mean_tau_ns", "sum_wait_ns")}
        d["h"] = d["p99_ns"] / d["p50_ns"]
        d["phi"] = d["x_ops_per_s"] / med([r["x_total"] for r in runs[lk]])
        s[key] = d
    s["X"] = med([r["x_total"] for r in runs[lk]])
    s["N"] = med([r["write"]["n_ops"] + r["read"]["n_ops"] for r in runs[lk]])
    s["w_all"] = med([(r["write"]["sum_wait_ns"] + r["read"]["sum_wait_ns"]) / 1e9 / (n * D) for r in runs[lk]])
    s["max_wait_read_ns"] = role_med(lk, "read", "max_wait_ns")
    s["X1"] = med([r["x_total"] for r in x1[lk]])
    s["X1_runs"] = [r["x_total"] for r in x1[lk]]
    s["t_cs_w_ns"] = med([c["t_cs_w_mean_ns"] for c in cs[lk]])
    s["t_cs_r_ns"] = med([c["t_cs_r_mean_ns"] for c in cs[lk]])
    s["threads"] = [med([r["threads"][i]["ops"] for r in runs[lk]]) for i in range(n)]
    s["inv_ok"] = sum(1 for r in runs[lk] if r["invariants_ok"])
    s["viol"] = sum(r["violations_last_total"] + r["violations_extra_total"] for r in runs[lk])
    S[lk] = s
G, RW = S["global"], S["rw"]
tcs_w, tcs_r = G["t_cs_w_ns"], G["t_cs_r_ns"]
tcs_bar = (nw * tcs_w + nr * tcs_r) / n            # нс
bound = 1e9 / tcs_bar                              # оп/с
rho = G["X"] * tcs_bar / 1e9

# ---- Таблиця 1 ----
goversion = runs["global"][0]["go_version"]
ncpu = runs["global"][0]["num_cpu"]
inv_total = sum(S[l]["inv_ok"] for l in LOCKS)
viol_total = sum(S[l]["viol"] for l in LOCKS)
write("table1.md", table(
    ["n", "n_w", "n_r", "Мова і версія", "Логічних ядер", "X_1, оп/с", "Інваріанти після прогонів кроків 2 і 3"],
    [[n, nw, nr, f"Go {goversion.replace('go', '')}", ncpu,
      f"{fint(G['X1'])} (глобальний замок), {fint(RW['X1'])} (RWLock)",
      f"виконано в {inv_total} з 6 прогонів; порушень last: {viol_total}"]]))

# ---- Таблиці 2 і 3 ----
def role_rows(lk, with_cs):
    s = S[lk]
    rows = []
    for key, name in (("w", "Запис"), ("r", "Читання")):
        d = s[key]
        row = [name, fint(d["n_ops"]), fint(d["x_ops_per_s"]), fmt(d["p50_ns"] / 1e3, 3), fmt(d["p99_ns"] / 1e3, 1),
               fmt(d["max_wait_ns"] / 1e6, 3), fmt(d["w"], 4), fmt(d["cv"], 4)]
        if with_cs:
            row.append(fmt((tcs_w if key == "w" else tcs_r) / 1e3, 4))
        rows.append(row)
    tot = ["Разом", fint(s["N"]), fint(s["X"]), "—", "—", "—", fmt(s["w_all"], 4), "—"]
    if with_cs:
        tot.append(fmt(tcs_bar / 1e3, 4) + " (t̄_cs)")
    rows.append(tot)
    return rows

head = ["Роль", "Операцій за D", "X, оп/с", "p50, мкс", "p99, мкс", "Найбільший t_wait, мс", "w", "cv"]
write("table2.md", table(head + ["t_cs, мкс"], role_rows("global", True)))
write("table3.md", table(head, role_rows("rw", False)))

# ---- Таблиця 4 ----
rows = []
for i in range(n):
    rows.append([i + 1, "запис" if (i + 1) % 2 else "читання", fint(G["threads"][i]), fint(RW["threads"][i])])
write("table4.md", table(["Потік", "Роль", "Глобальний замок", "RWLock"], rows))

# ---- Таблиця 5 ----
S_G, S_RW = G["X"] / G["X1"], RW["X"] / RW["X1"]
Sw = RW["w"]["x_ops_per_s"] / G["w"]["x_ops_per_s"]
Sr = RW["r"]["x_ops_per_s"] / G["r"]["x_ops_per_s"]
Ssum = RW["X"] / G["X"]
write("table5.md", table(["Метрика", "Формула", "Значення"], [
    ["S(n), глобальний замок", "X(G) / X_1", fmt(S_G, 3)],
    ["E(n), глобальний замок", "S(n) / n", fmt(S_G / n, 4)],
    ["S(n), RWLock", "X(RW) / X_1", fmt(S_RW, 3)],
    ["E(n), RWLock", "S(n) / n", fmt(S_RW / n, 4)],
    ["S_w", "X_w(RW) / X_w(G)", fmt(Sw, 4)],
    ["S_r", "X_r(RW) / X_r(G)", fmt(Sr, 3)],
    ["S_Σ", "X(RW) / X(G)", fmt(Ssum, 3)],
    ["ρ, завантаженість глобального замка", "X(G) · t̄_cs", fmt(rho, 3)],
]))
write("table5b.md", table(["Величина", "Значення"], [
    ["t_cs,w (глобальний замок, один потік), нс", fmt(tcs_w, 1)],
    ["t_cs,r (глобальний замок, один потік), нс", fmt(tcs_r, 1)],
    ["t̄_cs = (n_w · t_cs,w + n_r · t_cs,r) / n, нс", fmt(tcs_bar, 2)],
    ["межа 1 / t̄_cs, оп/с", fint(bound)],
    ["X(G), оп/с", fint(G["X"])],
    ["X(G) / (1 / t̄_cs)", fmt(G["X"] / bound, 4)],
    ["1 / X(G), нс на операцію", fmt(1e9 / G["X"], 1)],
    ["X_1(G), оп/с", fint(G["X1"])],
    ["X_1(G) · t̄_cs (один потік, без конкуренції)", fmt(G["X1"] * tcs_bar / 1e9, 3)],
]))

# ---- Таблиця 6 ----
def metric_rows():
    spec = [
        ("X, оп/с", "x_ops_per_s", lambda v: fint(v)),
        ("x, на один потік ролі, оп/с", "x_per_thread", lambda v: fint(v)),
        ("p50, мкс", "p50_ns", lambda v: fmt(v / 1e3, 3)),
        ("p99, мкс", "p99_ns", lambda v: fmt(v / 1e3, 1)),
        ("h = p99 / p50", "h", lambda v: fmt(v, 1)),
        ("Найбільший t_wait, мс", "max_wait_ns", lambda v: fmt(v / 1e6, 3)),
        ("w", "w", lambda v: fmt(v, 4)),
        ("cv", "cv", lambda v: fmt(v, 4)),
        ("φ, частка ролі в усіх операціях", "phi", lambda v: fmt(v, 4)),
    ]
    out = []
    for name, key, f in spec:
        gw, gr, rw_, rr = G["w"][key], G["r"][key], RW["w"][key], RW["r"][key]
        scale = 1e6 if key == "max_wait_ns" else 1
        out.append([name, f(gw), f(gr), f(rw_), f(rr), ratio(rw_, gw), ratio(rr, gr)])
    return out
write("table6.md", table(["Показник", "Глобальний замок: запис", "Глобальний замок: читання", "RWLock: запис",
                          "RWLock: читання", "RW / G: запис", "RW / G: читання"], metric_rows()))

# ---- Допоміжні таблиці ----
rr = []
for lk in LOCKS:
    for r in runs[lk]:
        rr.append([LOCKS[lk], r["run"], fint(r["write"]["n_ops"]), fint(r["read"]["n_ops"]), fint(r["x_total"]),
                   fmt(r["write"]["p50_ns"] / 1e3, 3), fmt(r["write"]["p99_ns"] / 1e3, 1),
                   fmt(r["read"]["p50_ns"] / 1e3, 3), fmt(r["read"]["p99_ns"] / 1e3, 1),
                   fmt(r["write"]["max_wait_ns"] / 1e6, 3), fmt(r["write"]["w"], 4), fmt(r["read"]["w"], 4),
                   fmt(r["write"]["cv"], 4), fmt(r["read"]["cv"], 4),
                   "так" if r["invariants_ok"] else "НІ"])
write("runs.md", table(["Замок", "Прогін", "N_w", "N_r", "X, оп/с", "p50 запису, мкс", "p99 запису, мкс",
                        "p50 читання, мкс", "p99 читання, мкс", "t_wait,max, мс", "w_w", "w_r", "cv_w", "cv_r",
                        "Інваріанти"], rr))

x1rows = []
for lk in LOCKS:
    for r in x1[lk]:
        x1rows.append([LOCKS[lk], r["run"], fint(r["write"]["n_ops"]), fint(r["read"]["n_ops"]), fint(r["x_total"]),
                       "так" if r["invariants_ok"] else "НІ"])
write("x1.md", table(["Замок", "Прогін", "Записів за D", "Читань за D", "X_1, оп/с", "Інваріанти"], x1rows))

csrows = []
for lk in LOCKS:
    for c in cs[lk]:
        csrows.append([LOCKS[lk], c["run"], fmt(c["t_cs_w_mean_ns"], 1), fmt(c["t_cs_r_mean_ns"], 1),
                       fmt(c["tau_w_mean_ns"], 1), fmt(c["tau_r_mean_ns"], 1), fmt(c["t_cs_w_median_ns"], 1)])
write("cs.md", table(["Замок", "Прогін", "t_cs,w середній, нс", "t_cs,r середній, нс", "τ запису середня, нс",
                      "τ читання середня, нс", "t_cs,w медіана, нс"], csrows))

c0 = clock[0]
write("clock.md", table(["Вимір", "нс на виклик"], [
    ["time.Since(t0): монотонний годинник, 1 позначка стенду", fmt(c0["time_since_ns"], 1)],
    ["time.Now() з UnixNano (стінний і монотонний годинники)", fmt(c0["time_now_ns"], 1)],
    ["3 позначки на операцію (t_call, t_acq, t_ret)", fmt(c0["three_stamps_ns"], 1)],
]))

inv = []
for lk in LOCKS:
    for r in runs[lk]:
        f = r["final"]
        inv.append([LOCKS[lk], r["run"], r["violations_last_total"], r["violations_extra_total"],
                    fint(f["total_appends"]), fint(f["final_seq"]), f["size"], f["expected_size"],
                    "так" if f["last16_ok"] else "НІ", "так" if f["scan_ok"] else "НІ"])
write("invariants.md", table(["Замок", "Прогін", "Порушень last", "Додаткових порушень", "Усього append",
                              "Останній seq", "Розмір", "Очікуваний розмір", "last(16) кінцевого стану", "Обхід буфера"], inv))

# ---- Графіки ----
C = {"global": "#c0392b", "rw": "#2471a3"}
xs = list(range(1, n + 1))

fig, ax = plt.subplots(figsize=(9, 4.2))
wd = 0.38
ax.bar([x - wd / 2 for x in xs], G["threads"], wd, color=C["global"], label="Глобальний замок")
ax.bar([x + wd / 2 for x in xs], RW["threads"], wd, color=C["rw"], label="RWLock")
ax.set_yscale("log")
ax.set_xticks(xs)
ax.set_xticklabels([f"{x}\n{'зап.' if x % 2 else 'чит.'}" for x in xs])
ax.set_xlabel("Потік і його роль")
ax.set_ylabel("Операцій за D = 10 с (логарифмічна шкала)")
ax.set_title("Операції кожного потоку: глобальний замок і RWLock (медіана трьох прогонів)")
ax.set_ylim(top=max(G["threads"]) * 6)
ax.legend(ncol=2, loc="upper center")
ax.grid(axis="y", alpha=.3)
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_threads.png"), dpi=130)
plt.close(fig)

fig, axs = plt.subplots(1, 3, figsize=(10, 3.8))
for ax, (title, g, r_) in zip(axs, (("X_w, оп/с (запис)", G["w"]["x_ops_per_s"], RW["w"]["x_ops_per_s"]),
                                    ("X_r, оп/с (читання)", G["r"]["x_ops_per_s"], RW["r"]["x_ops_per_s"]),
                                    ("X, оп/с (разом)", G["X"], RW["X"]))):
    ax.bar(["Глобальний", "RWLock"], [g, r_], color=[C["global"], C["rw"]])
    for i, v in enumerate((g, r_)):
        ax.text(i, v, fint(v), ha="center", va="bottom", fontsize=8)
    ax.set_title(title, fontsize=10)
    ax.set_ylim(0, max(g, r_) * 1.15)
    ax.grid(axis="y", alpha=.3)
fig.suptitle(f"Швидкість: S_w = {fmt(Sw, 3)}, S_r = {fmt(Sr, 2)}, S_Σ = {fmt(Ssum, 3)}", fontsize=10)
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_throughput.png"), dpi=130)
plt.close(fig)


def hist_sum(lk, role, key):
    tot = [0] * 100
    for r in runs[lk]:
        for i, c in enumerate(r[role][key]):
            tot[i] += c
    s = sum(tot)
    return [c / s for c in tot]


def centers():
    return [10 ** ((b + 0.5) / 10) / 1e3 for b in range(100)]   # мкс


for key, fname, label in (("hist_tau", "chart_tau.png", "Тривалість операції τ, мкс"),
                          ("hist_wait", "chart_wait.png", "Час очікування замка t_wait, мкс")):
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, (role, rname) in zip(axs, (("write", "запис"), ("read", "читання"))):
        for lk in LOCKS:
            h = hist_sum(lk, role, key)
            ax.plot(centers(), h, marker=".", color=C[lk], label=LOCKS[lk])
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel(label)
        ax.set_title(f"Операції {rname}", fontsize=10)
        ax.grid(alpha=.3)
        ax.set_ylim(1e-7, 1)
    axs[0].set_ylabel("Частка операцій у корзині (корзин 10 на декаду)")
    axs[0].legend()
    fig.suptitle("Розподіл за сумою трьох прогонів, горизонтальна вісь логарифмічна", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(SCR, fname), dpi=130)
    plt.close(fig)

fig, ax = plt.subplots(figsize=(7, 4))
labels = ["Запис", "Читання"]
gv = [G["w"]["max_wait_ns"] / 1e6, G["max_wait_read_ns"] / 1e6]
rv = [RW["w"]["max_wait_ns"] / 1e6, RW["max_wait_read_ns"] / 1e6]
ax.bar([0 - .2, 1 - .2], gv, .4, color=C["global"], label="Глобальний замок")
ax.bar([0 + .2, 1 + .2], rv, .4, color=C["rw"], label="RWLock")
for i, (a, b) in enumerate(zip(gv, rv)):
    ax.text(i - .2, a, fmt(a, 2), ha="center", va="bottom", fontsize=8)
    ax.text(i + .2, b, fmt(b, 2), ha="center", va="bottom", fontsize=8)
ax.set_xticks([0, 1])
ax.set_xticklabels(labels)
ax.set_ylabel("Найбільший t_wait однієї операції, мс")
ax.set_title("Найбільше очікування замка (медіана трьох прогонів)")
ax.legend()
ax.grid(axis="y", alpha=.3)
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_wait_max.png"), dpi=130)
plt.close(fig)

# ---- summary.json і друк ----
summary = {"n": n, "n_w": nw, "n_r": nr, "D": D, "go": goversion, "num_cpu": ncpu, "G": G, "RW": RW,
           "t_cs_bar_ns": tcs_bar, "bound_ops_per_s": bound, "rho": rho,
           "S_G": S_G, "E_G": S_G / n, "S_RW": S_RW, "E_RW": S_RW / n, "S_w": Sw, "S_r": Sr, "S_sum": Ssum}
json.dump(summary, open(os.path.join(ROOT, "results", "summary.json"), "w"), indent=1, ensure_ascii=False)
for name in ("table1", "table2", "table3", "table4", "table5", "table5b", "table6", "x1", "cs", "clock", "runs", "invariants"):
    print(f"--- {name}")
    print(open(os.path.join(TAB, name + ".md"), encoding="utf-8").read())
