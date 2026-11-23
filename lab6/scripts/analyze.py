#!/usr/bin/env python3
"""Таблиці 1–6 і графіки ЛР 6 з сирих результатів results/runs/*.json, *.csv і results/step5/.

Запуск: .venv/bin/python scripts/analyze.py
Пише results/tables/*.md, screens/chart_*.png і results/summary.json. Жодних чисел, введених вручну.
"""
import csv
import glob
import json
import os
import statistics as st

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNS = os.path.join(ROOT, "results", "runs")
TAB = os.path.join(ROOT, "results", "tables")
SCR = os.path.join(ROOT, "screens")
os.makedirs(TAB, exist_ok=True)
os.makedirs(SCR, exist_ok=True)
SUMMARY = {}


def J(tag):
    with open(os.path.join(RUNS, tag + ".json"), encoding="utf-8") as f:
        return json.load(f)


def reps(prefix):
    """Усі повтори <prefix>_r1, _r2, ..."""
    out = []
    for p in sorted(glob.glob(os.path.join(RUNS, prefix + "_r[0-9]*.json")),
                    key=lambda s: int(s.rsplit("_r", 1)[1][:-5])):
        with open(p, encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def csv_rows(tag):
    with open(os.path.join(RUNS, tag + ".csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def final_losses(tag):
    """step -> (epoch, loss); для повторених після відновлення кроків лишається останнє значення."""
    d = {}
    for r in csv_rows(tag):
        d[int(r["step"])] = (int(r["epoch"]), float(r["loss"]))
    return d


def fmt(x, nd=2):
    if x is None:
        return "−"
    return f"{x:.{nd}f}".replace(".", ",").replace("-", "−")


def fsci(x):
    s = f"{x:.2e}".replace(".", ",").replace("-", "−")
    m, e = s.split("e")
    return f"{m}·10^{int(e.replace('−', '-'))}^"


def table(path, header, rows):
    with open(os.path.join(TAB, path), "w", encoding="utf-8") as f:
        f.write("| " + " | ".join(header) + " |\n")
        f.write("|" + "|".join("---" for _ in header) + "|\n")
        for r in rows:
            f.write("| " + " | ".join(str(c) for c in r) + " |\n")


def M(runs, key):
    return st.median(r[key] for r in runs)


# ---------------- Таблиця 1: стенд ----------------
stand = J("stand")
table("t1_stand.md",
      ["Процесор", "Фізичних ядер C_phys", "Логічних ядер", "Памʼять, ГБ", "ОС", "Python", "PyTorch", "n_th"],
      [[f"{stand['cpu']} ({stand['c_perf']} продуктивних + {stand['c_eff']} енергоефективних)", stand["c_phys"],
        stand["c_logical"], fmt(stand["mem_gb"], 0), stand["os"], stand["python"], stand["torch"], stand["n_th"]]])
table("t1_dataset.md",
      ["Датасет", "K", "C", "c1 / c2 / h", "n_params", "M, МіБ", "N", "B", "P_max"],
      [[stand["dataset"], stand["K"], stand["C"], " / ".join(map(str, stand["widths"])), stand["n_params"],
        fmt(stand["M_mib"], 2), stand["N"], stand["B"], stand["P_max"]]])
c1, c2, h = stand["widths"]
C, K = stand["C"], stand["K"]
terms = [("9·C·c1", 9 * C * c1), ("c1", c1), ("9·c1·c2", 9 * c1 * c2), ("c2", c2), ("49·c2·h", 49 * c2 * h), ("h", h),
         ("h·K", h * K), ("K", K)]
assert sum(v for _, v in terms) == stand["n_params"]
table("t1_nparams.md", ["Доданок формули", "Значення"],
      [[n, v] for n, v in terms] + [["Сума (збігається з сумою numel усіх 8 тензорів)", stand["n_params"]]])
table("t1_tensors.md", ["Тензор", "Елементів", "Розмір, МіБ"],
      [[n, v, fmt(4 * v / 2 ** 20, 3)] for n, v in stand["tensor_sizes"].items()])

# ---------------- повтори вимірів ----------------
cfg = {
    "single": reps("s1_single_bench"),
    "manual": reps("s2_manual"), "ddp2": reps("s2_ddp"), "ns2": reps("s2_ddpns"),
    "s3p1": reps("s3_p1"), "s3p2": reps("s3_p2"), "s3p1ns": reps("s3_p1ns"), "s3p2ns": reps("s3_p2ns"),
    "s4p1": reps("s4_p1"),
}
names = {"single": "1 процес без distributed, b = 192", "manual": "2 процеси, ручна синхронізація, b = 96",
         "ddp2": "2 процеси, DDP, b = 96 (крок 2)", "ns2": "2 процеси, DDP no_sync, b = 96 (крок 2)",
         "s3p1": "1 процес, DDP, b = 192", "s3p2": "2 процеси, DDP, b = 96 (крок 3)",
         "s3p1ns": "1 процес, DDP no_sync, b = 192", "s3p2ns": "2 процеси, DDP no_sync, b = 96 (крок 3)",
         "s4p1": "1 процес, DDP, b = 96"}
for k, v in cfg.items():
    assert len(v) >= 3, f"мало повторів {k}"

# розкид повторів
rows = []
for k, v in cfg.items():
    ts = [r["t_step_ms"] for r in v]
    xs = [r["X"] for r in v]
    rows.append([names[k], len(v), fmt(st.median(ts)), f"{fmt(min(ts))} – {fmt(max(ts))}",
                 fmt(st.median(xs), 1), f"{fmt(min(xs), 1)} – {fmt(max(xs), 1)}",
                 fmt(100 * (max(ts) - min(ts)) / st.median(ts), 1)])
table("t_spread.md", ["Конфігурація", "Повторів", "t_step (медіана повторів), мс", "t_step мін – макс, мс",
                      "X (медіана), прикл./с", "X мін – макс", "Розкид t_step, % від медіани"], rows)

# фази кроку
rows = []
for k, v in cfg.items():
    rows.append([names[k]] + [fmt(M(v, key)) for key in ("t_fwd_ms", "t_bwd_ms", "t_sync_ms", "t_opt_ms", "t_step_ms", "t_load_ms")])
table("t_phases.md", ["Конфігурація", "t_fwd, мс", "t_bwd, мс", "t_sync, мс", "t_opt, мс", "t_step, мс",
                      "Читання батча, мс (поза t_step)"], rows)

# X за годинником (з читанням батча) проти X за сумою кроків
rows = [[names[k], fmt(M(v, "X"), 1), fmt(M(v, "X_wall"), 1),
         fmt(100 * (M(v, "X") - M(v, "X_wall")) / M(v, "X"), 1)] for k, v in cfg.items()]
table("t_xwall.md", ["Конфігурація", "X за сумою 50 кроків, прикл./с", "X за годинником 50 кроків разом із читанням батча",
                     "Різниця, %"], rows)

# ---------------- Таблиця 2 ----------------
B = stand["B"]
t_single, X_single = M(cfg["single"], "t_step_ms"), M(cfg["single"], "X")
t_manual, X_manual, sync_manual = M(cfg["manual"], "t_step_ms"), M(cfg["manual"], "X"), M(cfg["manual"], "t_sync_ms")
t_ddp, X_ddp, t_ns, X_ns = M(cfg["ddp2"], "t_step_ms"), M(cfg["ddp2"], "X"), M(cfg["ns2"], "t_step_ms"), M(cfg["ns2"], "X")
comm_ddp = t_ddp - t_ns
table("t2_sync.md", ["Режим", "t_step, мс", "Час обміну на крок, мс", "Частка кроку в обміні", "X, прикл./с"],
      [["1 процес, батч B", fmt(t_single), "0", "0", fmt(X_single, 1)],
       ["P_max процесів, ручна синхронізація", fmt(t_manual), fmt(sync_manual), fmt(sync_manual / t_manual, 3), fmt(X_manual, 1)],
       ["P_max процесів, DDP", fmt(t_ddp), fmt(comm_ddp), fmt(comm_ddp / t_ddp, 3), fmt(X_ddp, 1)],
       ["P_max процесів, DDP, no_sync", fmt(t_ns), "0", "0", fmt(X_ns, 1)]])
SUMMARY["table2"] = {"t_single": t_single, "X_single": X_single, "t_manual": t_manual, "t_sync": sync_manual,
                     "c_manual": sync_manual / t_manual, "t_ddp": t_ddp, "t_ns": t_ns, "t_comm_exp": comm_ddp,
                     "c_ddp": comm_ddp / t_ddp, "X_ddp": X_ddp, "X_ns": X_ns, "X_manual": X_manual}

eq = J("s2_equiv")
sy = J("s2_sync50")
tr1, trd = J("s1_single_train"), J("s2_ddp_train")
table("t2_quality.md",
      ["max \\|g − ḡ\\| / max \\|g\\|", "Різниця сум ваг між процесами після 50 кроків", "ACC, 1 процес", "ACC, DDP на P_max",
       "AUC, 1 процес", "AUC, DDP на P_max"],
      [[fsci(eq["max_ratio"]), fsci(sy["weight_sum_diff"]) if sy["weight_sum_diff"] else "0", fmt(tr1["acc"], 4), fmt(trd["acc"], 4),
        fmt(tr1["auc"], 4), fmt(trd["auc"], 4)]])
table("t2_equiv_tensors.md",
      ["Тензор", "max \\|g\\|", "\\|g − ḡ\\| / max \\|g\\|", "Контроль: \\|g − g_0\\| / max \\|g\\| (градієнт лише процесу 0)"],
      [[n, fsci(eq["max_abs_g"][n]), fsci(eq["per_tensor"][n]), fmt(eq["control_local_vs_full"][n], 3)] for n in eq["per_tensor"]])
sums = sy["weight_sums"]
table("t2_sums.md", ["Процес", "Сума ваг після 50 кроків (float64)"], [[i, repr(s)] for i, s in enumerate(sums)])
SUMMARY["equiv"] = {"max_ratio": eq["max_ratio"], "control": eq["control_max_ratio"], "sum_diff": sy["weight_sum_diff"],
                    "train_sum_diff_ddp": trd["weight_sum_diff"]}

# парні різниці t_step − t_step,ns за номером повтору (повтори чергуються, тому парні значення близькі за часом)
rows = []
for lab, a_, b_ in (("2 процеси, крок 2: DDP − no_sync", "ddp2", "ns2"), ("2 процеси, крок 3: DDP − no_sync", "s3p2", "s3p2ns"),
                    ("1 процес, крок 3: DDP − no_sync", "s3p1", "s3p1ns")):
    d = [x["t_step_ms"] - y["t_step_ms"] for x, y in zip(cfg[a_], cfg[b_])]
    rows.append([lab, len(d), fmt(st.median(d)), f"{fmt(min(d))} – {fmt(max(d))}", sum(1 for v in d if v < 0)])
table("t_paired.md", ["Пара", "Повторів", "Медіана парних різниць, мс", "мін – макс, мс", "Кількість відʼємних різниць"], rows)

# ---------------- Таблиця 3 ----------------
t1, t1n, X1 = M(cfg["s3p1"], "t_step_ms"), M(cfg["s3p1ns"], "t_step_ms"), M(cfg["s3p1"], "X")
t2, t2n, X2 = M(cfg["s3p2"], "t_step_ms"), M(cfg["s3p2ns"], "t_step_ms"), M(cfg["s3p2"], "X")
S2 = X2 / X1
E2 = S2 / 2
e2 = (1 / S2 - 1 / 2) / (1 - 1 / 2)
c1_, c2_ = (t1 - t1n) / t1, (t2 - t2n) / t2
table("t3_strong.md", ["P", "b", "t_step, мс", "t_step,ns, мс", "X, прикл./с", "S", "E", "e", "c"],
      [[1, 192, fmt(t1), fmt(t1n), fmt(X1, 1), "1", "1", "−", fmt(c1_, 3)],
       [2, 96, fmt(t2), fmt(t2n), fmt(X2, 1), fmt(S2, 3), fmt(E2, 3), fmt(e2, 3), fmt(c2_, 3)]])
SUMMARY["table3"] = {"t1": t1, "t1ns": t1n, "X1": X1, "t2": t2, "t2ns": t2n, "X2": X2, "S": S2, "E": E2, "e": e2,
                     "c1": c1_, "c2": c2_}

# ---------------- Таблиця 4 ----------------
tw1, Xw1 = M(cfg["s4p1"], "t_step_ms"), M(cfg["s4p1"], "X")
Sw, Ew = X2 / Xw1, X2 / Xw1 / 2
table("t4_weak.md", ["P", "B_P", "t_step, мс", "X, прикл./с", "S_w", "E_w"],
      [[1, 96, fmt(tw1), fmt(Xw1, 1), "1", "1"], [2, 192, fmt(t2), fmt(X2, 1), fmt(Sw, 3), fmt(Ew, 3)]])
SUMMARY["table4"] = {"t1": tw1, "X1": Xw1, "t2": t2, "X2": X2, "S_w": Sw, "E_w": Ew}

# розклад втрати ефективності: обмін чи конкуренція процесів за ядра і памʼять
X1ns, X2ns = M(cfg["s3p1ns"], "X"), M(cfg["s3p2ns"], "X")
table("t_decomp.md", ["Величина", "Значення"],
      [["S = X(2) / X(1) із синхронізацією (таблиця 3)", fmt(S2, 3)],
       ["S без обміну = X_ns(2) / X_ns(1), обидва прогони з no_sync", fmt(X2ns / X1ns, 3)],
       ["t_step, 1 процес, b = 96, DDP (таблиця 4), мс", fmt(tw1)],
       ["t_step,ns, 2 процеси, b = 96, без обміну (таблиця 3), мс", fmt(t2n)],
       ["Уповільнення кроку b = 96 від появи другого процесу без жодного обміну, мс", fmt(t2n - tw1)],
       ["те саме, % від t_step одного процесу", fmt(100 * (t2n - tw1) / tw1, 1)],
       ["t_comm,exp, 2 процеси, b = 96, мс", fmt(t2 - t2n)],
       ["E_w за відношенням t_step(P = 1) / t_step(P = 2) (в таблиці 4 E_w рахується за X, тому число трохи інше)", fmt(tw1 / t2, 3)],
       ["E_w без обміну = t_step(P = 1) / t_step,ns(P = 2)", fmt(tw1 / t2n, 3)]])
SUMMARY["decomp"] = {"S_ns": X2ns / X1ns, "slowdown_ms": t2n - tw1, "slowdown_pct": 100 * (t2n - tw1) / tw1,
                     "Ew_ns": tw1 / t2n}

# ---------------- Таблиця 5 ----------------
p1, p2, p2lr = J("s4_p1_train"), trd, J("s4_p2_lrP_train")
modes = [("P = 1", p1, 96, "η₀"), ("P = P_max, з кроку 2", p2, 192, "η₀"), ("P = P_max", p2lr, 192, "η_P")]
table("t5_quality.md", ["Режим", "B_P", "η", "Кроків оптимізатора за n_ep епох", "ACC", "AUC",
                        "Найбільша втрата за останню епоху"],
      [[n, bp, f"{fmt(r['lr'], 2)} ({eta})", r["steps_total"], fmt(r["acc"], 4), fmt(r["auc"], 4),
        fmt(r["max_loss_last_epoch"], 4)] for n, r, bp, eta in modes])
nfo = stand["n_e"]
table("t5_steps.md", ["Режим", "n_e за формулою ⌊⌈N / P⌉ / b⌋", "n_e фактичний (len(DataLoader))", "Кроків за 3 епохи: 3 · n_e",
                      "Кроків виконано"],
      [["P = 1, b = 96", nfo["P=1,b=96"], p1["n_e"], 3 * nfo["P=1,b=96"], p1["steps_total"]],
       ["P = 2, b = 96, η₀", nfo["P=2,b=96"], p2["n_e"], 3 * nfo["P=2,b=96"], p2["steps_total"]],
       ["P = 2, b = 96, η_P", nfo["P=2,b=96"], p2lr["n_e"], 3 * nfo["P=2,b=96"], p2lr["steps_total"]],
       ["P = 1, b = 192 (крок 1)", nfo["P=1,b=192"], tr1["n_e"], 3 * nfo["P=1,b=192"], tr1["steps_total"]]])
L = {t: final_losses(t) for t in ("s1_single_train", "s2_ddp_train", "s4_p1_train", "s4_p2_lrP_train")}


def span(d, lo, hi):
    return [v for s, (e, v) in sorted(d.items()) if lo <= s <= hi]


rows = []
for n, tag in (("P = 1, b = 96, η₀", "s4_p1_train"), ("P = 2, B = 192, η₀", "s2_ddp_train"),
               ("P = 2, B = 192, η_P", "s4_p2_lrP_train")):
    d = L[tag]
    nsteps = len(d)
    e0 = [v for s, (e, v) in d.items() if e == 0]
    e2_ = [v for s, (e, v) in d.items() if e == 2]
    rows.append([n, fmt(d[1][1], 4), fmt(max(e0), 4), fmt(st.mean(e0), 4), fmt(st.mean(e2_), 4), fmt(d[nsteps][1], 4),
                 "так" if any(v != v for _, v in d.values()) else "ні"])
table("t5_loss_stats.md", ["Режим", "Втрата кроку 1", "Найбільша втрата за епоху 1", "Середня втрата епохи 1",
                          "Середня втрата епохи 3", "Втрата останнього кроку", "Є NaN"], rows)
SUMMARY["table5"] = {n: {"acc": r["acc"], "auc": r["auc"], "steps": r["steps_total"], "max_last": r["max_loss_last_epoch"]}
                     for n, r, _, _ in modes}

# ---------------- Таблиця 6 ----------------
ev = [json.loads(l) for l in open(os.path.join(ROOT, "results", "step5", "events.jsonl"), encoding="utf-8")]
fail = next(e for e in ev if e["event"] == "fail")
ckpts_before = [e for e in ev if e["event"] == "ckpt" and e["attempt"] == 0]
ck_last = ckpts_before[-1]
resume = next(e for e in ev if e["event"] == "resume")
first = next(e for e in ev if e["event"] == "first_step_after_resume")
starts1 = [e for e in ev if e["event"] == "proc_start" and e["attempt"] == 1]
rows5 = csv_rows("s5_fail")
done0 = max(int(r["step"]) for r in rows5 if r["attempt"] == "0")
redo = done0 - ck_last["step"]
t_f, t_c = fail["t"], ck_last["t"]
t_r = first["t"] - t_f
t_lost = (t_f - t_c) + t_r
flag_t = float(open(os.path.join(ROOT, "results", "step5", "failed.flag")).read())
table("t6_failure.md", ["K_c", "s_f", "Крок останньої контрольної точки", "Кроків виконано повторно", "t_lost, с"],
      [[stand["K_c"], stand["s_f"], ck_last["step"], redo, fmt(t_lost, 2)]])
# кроки 21..24 до відмови: їх тривалість за журналом спроби 0
t_steps0 = [float(r["t_step"]) for r in rows5 if r["attempt"] == "0" and ck_last["step"] < int(r["step"]) <= done0]
step_ts = {int(r["step"]): float(r["t_end"]) for r in rows5 if r["attempt"] == "0"}
t_redo_work = t_f - step_ts[ck_last["step"]]
sc = [int(r["step"]) for r in rows5 if r["attempt"] == "1"]
step_t1 = [float(r["t_step"]) for r in rows5 if r["attempt"] == "1" and int(r["step"]) <= ck_last["step"] + redo]
table("t6_components.md", ["Складова", "Значення, с"],
      [["t_f − t_c: від запису контрольної точки (крок 20) до відмови", fmt(t_f - t_c, 3)],
       ["   з них виконання кроків 21–24 до відмови (від кінця кроку 20 до відмови)", fmt(t_redo_work, 3)],
       ["t_r: від відмови до завершення першого кроку після відновлення (крок 21)", fmt(t_r, 3)],
       ["   відмова → старт нових процесів (час початку процесу, спроба 1)", fmt(min(e["t_proc_start"] for e in starts1) - t_f, 3)],
       ["   старт процесу → група створена, контрольна точка прочитана (подія resume)", fmt(resume["t"] - min(e["t_proc_start"] for e in starts1), 3)],
       ["   resume → кінець кроку 21", fmt(first["t"] - resume["t"], 3)],
       ["t_lost = (t_f − t_c) + t_r", fmt(t_lost, 3)],
       ["Для порівняння: 4 повторні кроки за тривалістю кроків спроби 1", fmt(sum(step_t1), 3)],
       ["Час файла-позначки − час події fail (контроль годинника), с", fmt(flag_t - t_f, 6)]])
SUMMARY["table6"] = {"ckpt_step": ck_last["step"], "redo": redo, "t_lost": t_lost, "t_f_minus_t_c": t_f - t_c, "t_r": t_r,
                     "done_before_fail": done0}

Lf, Ln, Lc = final_losses("s5_fail"), final_losses("s2_ddp_train"), final_losses("s5_ckpt_nofail")
sf = stand["s_f"]
f_json, c_json = J("s5_fail"), J("s5_ckpt_nofail")
steps_w = list(range(sf - 2, sf + 3))
table("t6_loss.md", ["Крок"] + [f"s_f − 2 = {steps_w[0]}", f"s_f − 1 = {steps_w[1]}", f"s_f = {steps_w[2]}",
                                f"s_f + 1 = {steps_w[3]}", f"s_f + 2 = {steps_w[4]}", "ACC після n_ep епох"],
      [["Втрата, прогін з відмовою"] + [fmt(Lf[s][1], 6) for s in steps_w] + [fmt(f_json["acc"], 4)],
       ["Втрата, прогін без відмови"] + [fmt(Ln[s][1], 6) for s in steps_w] + [fmt(trd["acc"], 4)]])
a0 = {int(r["step"]): float(r["loss"]) for r in rows5 if r["attempt"] == "0"}
a1 = {int(r["step"]): float(r["loss"]) for r in rows5 if r["attempt"] == "1"}
table("t6_loss_detail.md", ["Крок", "Спроба 0 (до відмови)", "Спроба 1 (після відновлення)", "Без відмови (крок 2)",
                            "Спроба 1 − без відмови"],
      [[s, fmt(a0[s], 6) if s in a0 else "−", fmt(a1[s], 6) if s in a1 else "−", fmt(Ln[s][1], 6),
        fsci(a1[s] - Ln[s][1]) if s in a1 and a1[s] != Ln[s][1] else ("0" if s in a1 else "−")]
       for s in range(ck_last["step"] - 1, sf + 4)])


def maxdiff(x, y, lo=1):
    steps = [s for s in x if s in y and s >= lo]
    return max(abs(x[s][1] - y[s][1]) for s in steps), sum(1 for s in steps if x[s][1] != y[s][1]), len(steps)


rows = []
for name_, x, y, lo in (("прогін з відмовою (остаточна траєкторія) і без відмови (крок 2)", Lf, Ln, 1),
                        ("  те саме, лише кроки після відновлення (21…)", Lf, Ln, ck_last["step"] + 1),
                        ("прогін з контрольними точками без відмови і без відмови (крок 2)", Lc, Ln, 1),
                        ("1 процес B = 192 (крок 1) і DDP P = 2 (крок 2)", L["s1_single_train"], Ln, 1)):
    md, nd, n = maxdiff(x, y, lo)
    rows.append([name_, n, nd, fsci(md) if md else "0"])
table("t6_equal.md", ["Порівняння втрат за кроками", "Кроків", "Кроків з відмінною втратою (float32)", "Найбільша |різниця|"], rows)
SUMMARY["table6_loss"] = {"acc_fail": f_json["acc"], "acc_nofail": trd["acc"], "acc_ckpt_nofail": c_json["acc"],
                          "maxdiff_fail_vs_nofail": maxdiff(Lf, Ln)[0], "maxdiff_after_resume": maxdiff(Lf, Ln, 21)[0],
                          "maxdiff_single_vs_ddp": maxdiff(L["s1_single_train"], Ln)[0]}

# ---------------- графіки ----------------
plt.rcParams.update({"font.size": 11, "figure.dpi": 150})

fig, ax = plt.subplots(figsize=(7, 4.4))
Pm = [1, 2]
xs_med = [X1, X2]
lo = [min(r["X"] for r in cfg["s3p1"]), min(r["X"] for r in cfg["s3p2"])]
hi = [max(r["X"] for r in cfg["s3p1"]), max(r["X"] for r in cfg["s3p2"])]
ax.plot([0, 2.2], [0, 2.2 * X1], "--", color="gray", label="ідеальна пряма P · X(1)")
ax.errorbar(Pm, xs_med, yerr=[[m - l for m, l in zip(xs_med, lo)], [h_ - m for m, h_ in zip(xs_med, hi)]], fmt="o-",
            color="C0", capsize=4, label="виміряна X(P): медіана, мін–макс повторів")
for p, x in zip(Pm, xs_med):
    ax.annotate(f"{x:.0f}", (p, x), textcoords="offset points", xytext=(6, -14))
ax.set_xlim(0.5, 2.3)
ax.set_xticks([1, 2])
ax.set_ylim(0, 2.2 * X1 * 1.02)
ax.set_xlabel("Кількість процесів P")
ax.set_ylabel("Пропускна здатність X, прикл./с")
ax.set_title("Strong scaling, B = 192, DDP, n_th = 7 на процес")
ax.grid(alpha=0.3)
ax.legend(loc="upper left")
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_xp.png"))
plt.close(fig)

fig, axs = plt.subplots(1, 2, figsize=(12, 4.4))
styles = [("P = 1, b = 96, η₀", "s4_p1_train", 96, "C0"), ("P = 2, B = 192, η₀", "s2_ddp_train", 192, "C1"),
          ("P = 2, B = 192, η_P = 0,04", "s4_p2_lrP_train", 192, "C3")]
for name_, tag, bp, col in styles:
    d = L[tag]
    steps = sorted(d)
    ne = stand["n_e"]["P=1,b=96"] if tag == "s4_p1_train" else stand["n_e"]["P=2,b=96"]
    axs[0].plot(steps, [d[s][1] for s in steps], color=col, lw=1.2, label=name_)
    axs[1].plot([s / ne for s in steps], [d[s][1] for s in steps], color=col, lw=1.2, label=name_)
axs[0].set_xlabel("Крок оптимізатора")
axs[1].set_xlabel("Епохи")
for a in axs:
    a.set_ylabel("Втрата на кроці")
    a.grid(alpha=0.3)
axs[0].legend()
axs[0].set_title("За номером кроку")
axs[1].set_title("За кількістю пройдених епох")
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_loss_modes.png"))
plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4.4))
lo_s, hi_s = 8, 40
ax.plot([s for s in range(lo_s, hi_s + 1) if s in Ln], [Ln[s][1] for s in range(lo_s, hi_s + 1) if s in Ln], "-", color="C0",
        lw=4, alpha=0.35, label="без відмови (крок 2)")
ax.plot([s for s in a0 if lo_s <= s <= hi_s], [a0[s] for s in a0 if lo_s <= s <= hi_s], "s", color="gray", ms=5,
        label="з відмовою, спроба 0 (до відмови)")
ax.plot([s for s in a1 if lo_s <= s <= hi_s], [a1[s] for s in a1 if lo_s <= s <= hi_s], ".-", color="C3", lw=1,
        label="з відмовою, спроба 1 (після відновлення)")
for s in range(stand["K_c"], hi_s + 1, stand["K_c"]):
    ax.axvline(s, color="green", ls=":", lw=1)
ax.axvline(sf, color="red", ls="--", lw=1.2)
ax.text(sf + 0.3, ax.get_ylim()[1] * 0.98, f"відмова, s_f = {sf}", color="red", va="top")
ax.text(stand["K_c"] + 0.3, ax.get_ylim()[0] + 0.02, "контрольні точки", color="green", rotation=90, va="bottom")
ax.set_xlabel("Крок оптимізатора")
ax.set_ylabel("Втрата на кроці")
ax.set_title("Втрата навколо кроку відмови")
ax.grid(alpha=0.3)
ax.legend(loc="upper right", bbox_to_anchor=(1, 0.88))
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_loss_fail.png"))
plt.close(fig)

fig, ax = plt.subplots(figsize=(9, 4.4))
order = ["single", "s3p1", "s3p1ns", "s3p2", "s3p2ns", "manual", "ddp2", "ns2"]
for i, k in enumerate(order):
    ts = [r["t_step_ms"] for r in cfg[k]]
    ax.plot([i] * len(ts), ts, "o", color="C0", alpha=0.6)
    ax.plot([i - 0.25, i + 0.25], [st.median(ts)] * 2, "-", color="C3")
ax.set_xticks(range(len(order)))
ax.set_xticklabels(["1 проц.\nбез dist.\nb=192", "1 проц.\nDDP\nb=192", "1 проц.\nno_sync\nb=192", "2 проц.\nDDP\nb=96",
                    "2 проц.\nno_sync\nb=96", "2 проц.\nручна\nb=96", "2 проц.\nDDP\n(крок 2)", "2 проц.\nno_sync\n(крок 2)"],
                   fontsize=8)
ax.set_ylabel("t_step, мс (медіана 50 кроків одного прогону)")
ax.set_title("Повтори вимірів: точки — окремі прогони, лінія — медіана")
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(SCR, "chart_repeats.png"))
plt.close(fig)

with open(os.path.join(ROOT, "results", "summary.json"), "w", encoding="utf-8") as f:
    json.dump(SUMMARY, f, ensure_ascii=False, indent=1)

# ---------------- підсумок у термінал ----------------
print("Таблиця 2:", json.dumps(SUMMARY["table2"], ensure_ascii=False, indent=1))
print("Еквівалентність:", json.dumps(SUMMARY["equiv"], ensure_ascii=False))
print("Таблиця 3:", json.dumps(SUMMARY["table3"], ensure_ascii=False))
print("Таблиця 4:", json.dumps(SUMMARY["table4"], ensure_ascii=False))
print("Розклад:", json.dumps(SUMMARY["decomp"], ensure_ascii=False))
print("Таблиця 5:", json.dumps(SUMMARY["table5"], ensure_ascii=False))
print("Таблиця 6:", json.dumps(SUMMARY["table6"], ensure_ascii=False))
print("Втрати 6:", json.dumps(SUMMARY["table6_loss"], ensure_ascii=False))
for t in sorted(os.listdir(TAB)):
    print("results/tables/" + t)
