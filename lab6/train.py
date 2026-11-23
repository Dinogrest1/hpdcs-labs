#!/usr/bin/env python3
"""ЛР 6, варіант 26. Розподілене навчання за даними на CPU: BloodMNIST (Д2), ширина Ш3, P_max = 2.

Один скрипт, режим вибирається параметрами:
  --task info          стенд, n_params, M, n_e
  --task train         навчання n_ep епох, втрата кожного кроку, ACC, AUC (також з контрольними точками і відмовою)
  --task bench         10 кроків прогріву і 50 вимірюваних (t_step, t_sync, X)
  --task grad_single   градієнт g на першому батчі в одному процесі
  --task equiv         ḡ на P_max процесах (ручна синхронізація) і порівняння з g
  --task sync50        50 кроків з ручною синхронізацією, суми ваг у float64, all_gather
  --sync none|manual|ddp, --ns (прямий і зворотний прохід у model.no_sync())
Запуск у кількох процесах: torchrun --standalone --nproc_per_node=P train.py ...
"""
import time

T_PROC_START = time.time()          # початок процесу: для оцінки t_r після відновлення

import argparse
import json
import os
import platform
import subprocess
import sys
from contextlib import nullcontext

import numpy as np
import torch
import torch.distributed as dist
import torch.nn.functional as F
from torch import nn
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, DistributedSampler, TensorDataset

# ---------------- параметри варіанта 26 ----------------
VARIANT = {"dataset": "Д2", "width": "Ш3", "P_max": 2, "B": 192, "K_c": 10, "s_f": 25,
           "N": 7000, "n_ep": 3, "lr0": 0.02, "momentum": 0.9, "seed": 0}
WARMUP, MEASURED = 10, 50

# ---------------- модель і дані: дослівно з умови ----------------
DATASETS = {  # позначка: (файл, класів K, каналів C)
    "Д1": ("dermamnist", 7, 3),
    "Д2": ("bloodmnist", 8, 3),
    "Д3": ("organamnist", 11, 1),
    "Д4": ("organcmnist", 11, 1),
    "Д5": ("pathmnist", 9, 3),
    "Д6": ("octmnist", 4, 1),
}
WIDTHS = {"Ш1": (16, 32, 128), "Ш2": (32, 64, 256), "Ш3": (64, 128, 512)}


def load_split(path, split, n=None):
    """Розбиття split ("train" або "test") файлу .npz як TensorDataset."""
    d = np.load(path)
    x = d[f"{split}_images"][:n]          # (N, 28, 28) або (N, 28, 28, 3), uint8
    y = d[f"{split}_labels"][:n].reshape(-1)   # (N, 1) -> (N,)
    if x.ndim == 3:
        x = x[:, None, :, :]              # (N, 1, 28, 28)
    else:
        x = x.transpose(0, 3, 1, 2)       # (N, 3, 28, 28)
    x = torch.from_numpy(np.ascontiguousarray(x)).float()
    x = (x / 255 - 0.5) / 0.5
    return TensorDataset(x, torch.from_numpy(y.astype(np.int64)))


class Net(nn.Module):
    def __init__(self, C, c1, c2, h, K):
        super().__init__()
        self.features = nn.Sequential(
            # 28 x 28 -> 14 x 14
            nn.Conv2d(C, c1, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            # 14 x 14 -> 7 x 7
            nn.Conv2d(c1, c2, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.head = nn.Sequential(
            nn.Flatten(), nn.Linear(7 * 7 * c2, h), nn.ReLU(),
            nn.Linear(h, K),
        )

    def forward(self, x):
        return self.head(self.features(x))


def n_params_formula(C, c1, c2, h, K):
    return 9 * C * c1 + c1 + 9 * c1 * c2 + c2 + 49 * c2 * h + h + h * K + K


# ---------------- допоміжне ----------------
def sysctl(name, default=None):
    try:
        return subprocess.check_output(["sysctl", "-n", name], text=True).strip()
    except Exception:
        return default


def c_phys():
    v = sysctl("hw.physicalcpu")
    return int(v) if v else (os.cpu_count() or 1)


def events(a, **kw):
    """Журнал подій (контрольні точки, відмова, відновлення): рядок JSON, дописується атомарно."""
    if not a.ckpt_dir:
        return
    kw["t"] = time.time()
    kw["attempt"] = int(os.environ.get("TORCHELASTIC_RESTART_COUNT", 0))
    fd = os.open(os.path.join(a.ckpt_dir, "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.write(fd, (json.dumps(kw, ensure_ascii=False) + "\n").encode())
    os.close(fd)


def build(a, rank, world):
    """Дані, семплер, завантажувач, модель, оптимізатор."""
    name, K, C = DATASETS[VARIANT["dataset"]]
    c1, c2, h = WIDTHS[VARIANT["width"]]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{name}.npz")
    train = load_split(path, "train", n=VARIANT["N"])
    torch.manual_seed(VARIANT["seed"])        # до створення моделі
    model = Net(C, c1, c2, h, K)
    n_params = sum(p.numel() for p in model.parameters())
    assert n_params == n_params_formula(C, c1, c2, h, K)
    # DistributedSampler з явними num_replicas і rank працює й без init_process_group
    sampler = DistributedSampler(train, num_replicas=world, rank=rank, shuffle=True, seed=0)
    loader = DataLoader(train, batch_size=a.b, sampler=sampler, drop_last=True)
    return path, name, K, C, (c1, c2, h), train, model, n_params, sampler, loader


def wrap(model, a):
    """Ручна синхронізація: broadcast ваг з процесу 0; DDP розсилає параметри сам."""
    if a.sync == "manual":
        for p in model.parameters():
            dist.broadcast(p.data, src=0)
        return model
    if a.sync == "ddp":
        return DDP(model)
    return model


def evaluate(model, test):
    """ACC і AUC на тестовій частині (AUC: «один проти решти», середнє за класами)."""
    from sklearn.metrics import roc_auc_score
    model.eval()
    probs = []
    with torch.no_grad():
        for i in range(0, len(test), 1000):
            probs.append(torch.softmax(model(test.tensors[0][i:i + 1000]), dim=1))
    prob = torch.cat(probs).numpy()
    y = test.tensors[1].numpy()
    acc = float((prob.argmax(1) == y).mean())
    auc = float(roc_auc_score(y, prob, multi_class="ovr", average="macro"))
    model.train()
    return acc, auc


def weight_sum(model):
    return float(sum(p.detach().double().sum().item() for p in model.parameters()))


def gather_sums(model, world):
    """Суми всіх ваг у float64 з усіх процесів через all_gather; повертає список і max − min."""
    s = torch.tensor([weight_sum(model)], dtype=torch.float64)
    if world == 1:
        return [float(s[0])], 0.0
    parts = [torch.zeros(1, dtype=torch.float64) for _ in range(world)]
    dist.all_gather(parts, s)
    v = [float(t[0]) for t in parts]
    return v, max(v) - min(v)


def do_step(model, opt, x, y, a, world):
    """Один крок навчання. Повертає втрату і тривалості фаз, секунди."""
    pc = time.perf_counter
    t0 = pc()
    opt.zero_grad(set_to_none=True)
    with (model.no_sync() if a.ns else nullcontext()):
        loss = F.cross_entropy(model(x), y)
        t1 = pc()
        loss.backward()
    t2 = pc()
    t_sync = 0.0
    if a.sync == "manual":
        ts0 = t_last = pc()
        for p in model.parameters():
            dist.all_reduce(p.grad, op=dist.ReduceOp.SUM)
            t_last = pc()                    # момент повернення з останнього all_reduce
            p.grad /= world
        t_sync = t_last - ts0
    t3 = pc()
    opt.step()
    t4 = pc()
    return loss.detach(), {"fwd": t1 - t0, "bwd": t2 - t1, "sync": t_sync, "opt": t4 - t3, "step": t4 - t0}


def batch_stream(loader, sampler):
    epoch = 0
    while True:
        sampler.set_epoch(epoch)
        for x, y in loader:
            yield epoch, x, y
        epoch += 1


def med(v):
    return float(np.median(v))


def save_json(a, name, obj):
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def common_meta(a, rank, world):
    return {"tag": a.tag, "task": a.task, "sync": a.sync, "ns": a.ns, "world": world, "b": a.b,
            "B_global": a.b * world, "lr": a.lr, "threads": torch.get_num_threads(),
            "omp_num_threads_env": os.environ.get("OMP_NUM_THREADS")}


# ---------------- режими ----------------
def task_info(a):
    name, K, C = DATASETS[VARIANT["dataset"]]
    c1, c2, h = WIDTHS[VARIANT["width"]]
    model = Net(C, c1, c2, h, K)
    n_params = sum(p.numel() for p in model.parameters())
    assert n_params == n_params_formula(C, c1, c2, h, K)
    N, b, P_max = VARIANT["N"], VARIANT["B"] // VARIANT["P_max"], VARIANT["P_max"]
    cp = c_phys()
    info = {
        "cpu": sysctl("machdep.cpu.brand_string"), "c_phys": cp, "c_logical": int(sysctl("hw.logicalcpu", 0)),
        "c_perf": int(sysctl("hw.perflevel0.physicalcpu", 0)), "c_eff": int(sysctl("hw.perflevel1.physicalcpu", 0)),
        "mem_gb": int(sysctl("hw.memsize", 0)) / 2 ** 30,
        "os": "macOS " + platform.mac_ver()[0], "python": platform.python_version(), "torch": torch.__version__,
        "n_th": cp // P_max, "dataset": f"{VARIANT['dataset']} {name}", "K": K, "C": C,
        "widths": [c1, c2, h], "n_params": n_params, "M_bytes": 4 * n_params, "M_mib": 4 * n_params / 2 ** 20,
        "n_tensors": len(list(model.parameters())),
        "tensor_sizes": {n: p.numel() for n, p in model.named_parameters()},
        "N": N, "B": VARIANT["B"], "P_max": P_max, "b": b, "K_c": VARIANT["K_c"], "s_f": VARIANT["s_f"],
        "n_ep": VARIANT["n_ep"], "lr0": VARIANT["lr0"],
        "n_e": {"P=1,b=192": (N // 1) // 192, "P=2,b=96": -(-N // 2) // 96, "P=1,b=96": (N // 1) // 96},
        "test_size": len(np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{name}.npz"))["test_labels"]),
    }
    save_json(a, "stand", info)
    print(json.dumps(info, ensure_ascii=False, indent=1))


def task_train(a, rank, world):
    path, name, K, C, widths, train, model, n_params, sampler, loader = build(a, rank, world)
    test = load_split(path, "test")
    model = wrap(model, a)
    inner = model.module if isinstance(model, DDP) else model
    opt = torch.optim.SGD(model.parameters(), lr=a.lr, momentum=VARIANT["momentum"])
    n_e = len(loader)
    start_epoch = start_i = step = 0
    resumed = False
    ck = os.path.join(a.ckpt_dir, "ckpt.pt") if a.ckpt_dir else None
    flag = os.path.join(a.ckpt_dir, "failed.flag") if a.ckpt_dir else None
    events(a, event="proc_start", rank=rank, t_proc_start=T_PROC_START)
    if ck and os.path.exists(ck):
        st = torch.load(ck, map_location="cpu")
        inner.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        start_epoch, start_i, step = st["epoch"], st["step_in_epoch"], st["step"]
        resumed = True
        if rank == 0:
            events(a, event="resume", from_step=step, epoch=start_epoch, step_in_epoch=start_i)
    os.makedirs(a.outdir, exist_ok=True)
    csv_path = os.path.join(a.outdir, a.tag + ".csv")
    fcsv = None
    if rank == 0:
        fcsv = open(csv_path, "a" if resumed else "w", encoding="utf-8")
        if not resumed:
            fcsv.write("attempt,step,epoch,i,loss,t_step,t_end\n")
    attempt = int(os.environ.get("TORCHELASTIC_RESTART_COUNT", 0))
    if world > 1:
        dist.barrier()
    t_train0 = time.perf_counter()
    first_after_resume = resumed
    for epoch in range(start_epoch, VARIANT["n_ep"]):
        sampler.set_epoch(epoch)
        for i, (x, y) in enumerate(loader):
            if epoch == start_epoch and i < start_i:
                continue                      # пропуск уже виконаних батчів епохи
            step += 1
            if a.fail_step and step == a.fail_step and rank == world - 1 and not os.path.exists(flag):
                t_f = time.time()
                with open(flag, "w") as f:
                    f.write(repr(t_f))        # позначка: після перезапуску відмова не повторюється
                events(a, event="fail", step=step, rank=rank)
                sys.stdout.flush()
                os._exit(1)
            loss, ph = do_step(model, opt, x, y, a, world)
            if world > 1:
                dist.all_reduce(loss)
                loss /= world                 # втрата на всьому батчі B: середнє втрат процесів
            if rank == 0:
                fcsv.write(f"{attempt},{step},{epoch},{i},{loss.item():.9g},{ph['step']:.6f},{time.time():.6f}\n")
                fcsv.flush()
                if first_after_resume:
                    events(a, event="first_step_after_resume", step=step)
                    first_after_resume = False
            if a.ckpt_every and step % a.ckpt_every == 0:
                if rank == 0:
                    state = {"model": inner.state_dict(), "opt": opt.state_dict(), "epoch": epoch,
                             "step_in_epoch": i + 1, "step": step}
                    torch.save(state, os.path.join(a.ckpt_dir, "ckpt.tmp"))
                    os.replace(os.path.join(a.ckpt_dir, "ckpt.tmp"), ck)
                    events(a, event="ckpt", step=step)
                if world > 1:
                    dist.barrier()
            if a.max_steps and step >= a.max_steps:
                break
        if a.max_steps and step >= a.max_steps:
            break
    t_train = time.perf_counter() - t_train0
    sums, diff = gather_sums(inner, world)
    if rank == 0:
        fcsv.close()
        rows = [l.split(",") for l in open(csv_path, encoding="utf-8").read().splitlines()[1:]]
        last = {}                             # для повторених кроків лишається значення після відновлення
        for r in rows:
            last[int(r[1])] = (int(r[2]), float(r[4]))
        acc, auc = evaluate(inner, test)
        loss_last = [v for e, v in last.values() if e == VARIANT["n_ep"] - 1]
        res = common_meta(a, rank, world)
        res.update({"n_params": n_params, "n_e": n_e, "steps_total": step, "steps_logged": len(last),
                    "acc": acc, "auc": auc, "max_loss_last_epoch": max(loss_last) if loss_last else None,
                    "final_loss": last[max(last)][1], "t_train_s": t_train, "resumed": resumed,
                    "weight_sums": sums, "weight_sum_diff": diff,
                    "ckpt_every": a.ckpt_every, "fail_step": a.fail_step})
        save_json(a, a.tag, res)
        print(f"[{a.tag}] P={world} b={a.b} lr={a.lr}: n_e={n_e}, кроків {step}, ACC={acc:.4f}, AUC={auc:.4f}, "
              f"max втрата останньої епохи {max(loss_last, default=float("nan")):.4f}, різниця сум ваг {diff:.3e}, час навчання {t_train:.1f} с")
    if world > 1:
        dist.barrier()


def task_bench(a, rank, world):
    path, name, K, C, widths, train, model, n_params, sampler, loader = build(a, rank, world)
    model = wrap(model, a)
    opt = torch.optim.SGD(model.parameters(), lr=a.lr, momentum=VARIANT["momentum"])
    stream = batch_stream(loader, sampler)
    recs, loads = [], []
    wall0 = None
    if world > 1:
        dist.barrier()
    for k in range(WARMUP + MEASURED):
        tl = time.perf_counter()
        epoch, x, y = next(stream)
        tl = time.perf_counter() - tl
        if k == WARMUP:
            if world > 1:
                dist.barrier()                # процеси починають вимірювання разом
            wall0 = time.perf_counter()
        loss, ph = do_step(model, opt, x, y, a, world)
        if k >= WARMUP:
            recs.append(ph)
            loads.append(tl)
    wall = time.perf_counter() - wall0
    t_sum = sum(r["step"] for r in recs)
    B = a.b * world
    by_rank = [None] * world
    if world > 1:
        dist.all_gather_object(by_rank, med([r["step"] for r in recs]))
    else:
        by_rank = [med([r["step"] for r in recs])]
    if rank == 0:
        res = common_meta(a, rank, world)
        res.update({"n_params": n_params,
                    "t_step_ms": 1e3 * med([r["step"] for r in recs]),
                    "t_fwd_ms": 1e3 * med([r["fwd"] for r in recs]),
                    "t_bwd_ms": 1e3 * med([r["bwd"] for r in recs]),
                    "t_sync_ms": 1e3 * med([r["sync"] for r in recs]),
                    "t_opt_ms": 1e3 * med([r["opt"] for r in recs]),
                    "t_load_ms": 1e3 * med(loads),
                    "t_step_mean_ms": 1e3 * float(np.mean([r["step"] for r in recs])),
                    "t_step_ms_by_rank": [1e3 * v for v in by_rank],
                    "t_total_s": t_sum, "X": MEASURED * B / t_sum,
                    "wall_s": wall, "X_wall": MEASURED * B / wall,
                    "steps_ms": [1e3 * r["step"] for r in recs]})
        if isinstance(model, DDP):
            try:
                d = model._get_ddp_logging_data()
                res["ddp_bucket_sizes"] = d.get("bucket_sizes")
                res["ddp_num_buckets"] = d.get("num_buckets_reduced")
            except Exception as e:           # pragma: no cover
                res["ddp_bucket_sizes"] = str(e)
        save_json(a, a.tag, res)
        print(f"[{a.tag}] sync={a.sync}{' ns' if a.ns else ''} P={world} b={a.b}: t_step={res['t_step_ms']:.2f} мс "
              f"(fwd {res['t_fwd_ms']:.2f}, bwd {res['t_bwd_ms']:.2f}, sync {res['t_sync_ms']:.2f}, opt {res['t_opt_ms']:.2f}), "
              f"X={res['X']:.1f} прикл./с, завантаження батча {res['t_load_ms']:.2f} мс")
    if world > 1:
        dist.barrier()


def first_batch_indices(train, B, world, rank, b):
    """Приклади першого батча епохи 0: процес r бере позиції r, r + P, ... перших B позицій перестановки."""
    s = DistributedSampler(train, num_replicas=world, rank=rank, shuffle=True, seed=0)
    s.set_epoch(0)
    return list(s)[:b]


def grads_on(model, train, idx):
    model.zero_grad(set_to_none=True)
    x, y = train.tensors[0][idx], train.tensors[1][idx]
    F.cross_entropy(model(x), y).backward()
    return {n: p.grad.detach().clone() for n, p in model.named_parameters()}


def task_grad_single(a):
    path, name, K, C, widths, train, model, n_params, sampler, loader = build(a, 0, 1)
    B = VARIANT["B"]
    idx = first_batch_indices(train, B, 1, 0, B)
    g = grads_on(model, train, idx)
    g2 = grads_on(model, train, idx)          # повтор у тому самому процесі: відтворюваність
    rep = max(float((g[n] - g2[n]).abs().max()) / float(g[n].abs().max()) for n in g)
    os.makedirs(a.outdir, exist_ok=True)
    torch.save({"g": g, "idx": idx}, os.path.join(a.outdir, "g_single.pt"))
    print(f"[grad_single] 1 процес, батч {B}: градієнт збережено; повторний обрахунок у тому самому процесі: "
          f"max відношення різниці {rep:.3e}; перші індекси батча {idx[:6]}")


def task_equiv(a, rank, world):
    path, name, K, C, widths, train, model, n_params, sampler, loader = build(a, rank, world)
    model = wrap(model, a)                    # ручна: broadcast ваг з процесу 0
    b = a.b
    idx = first_batch_indices(train, VARIANT["B"], world, rank, b)
    g_loc = grads_on(model, train, idx)       # локальний градієнт g_r
    gbar = {}
    for n, p in model.named_parameters():
        dist.all_reduce(p.grad, op=dist.ReduceOp.SUM)
        p.grad /= world
        gbar[n] = p.grad.detach().clone()
    if rank == 0:
        ref = torch.load(os.path.join(a.outdir, "g_single.pt"))
        g = ref["g"]
        per, ctl = {}, {}
        for n in g:
            den = float(g[n].abs().max())
            per[n] = float((g[n] - gbar[n]).abs().max()) / den
            ctl[n] = float((g[n] - g_loc[n]).abs().max()) / den     # контроль: градієнт лише процесу 0
        res = common_meta(a, rank, world)
        res.update({"per_tensor": per, "max_ratio": max(per.values()), "control_local_vs_full": ctl,
                    "control_max_ratio": max(ctl.values()),
                    "max_abs_g": {n: float(g[n].abs().max()) for n in g},
                    "indices_rank0_first": idx[:6]})
        save_json(a, a.tag, res)
        print(f"[equiv] max |g − ḡ| / max |g| за тензорами = {res['max_ratio']:.3e}")
        for n in g:
            print(f"   {n:22s} max|g| = {res['max_abs_g'][n]:.4e}   |g − ḡ|/|g| = {per[n]:.3e}   "
                  f"(контроль: g_0 замість ḡ: {ctl[n]:.3e})")
    dist.barrier()


def task_sync50(a, rank, world):
    path, name, K, C, widths, train, model, n_params, sampler, loader = build(a, rank, world)
    model = wrap(model, a)
    opt = torch.optim.SGD(model.parameters(), lr=a.lr, momentum=VARIANT["momentum"])
    stream = batch_stream(loader, sampler)
    for k in range(50):
        epoch, x, y = next(stream)
        do_step(model, opt, x, y, a, world)
    sums, diff = gather_sums(model, world)
    if rank == 0:
        res = common_meta(a, rank, world)
        res.update({"steps": 50, "weight_sums": sums, "weight_sum_diff": diff})
        save_json(a, a.tag, res)
        print(f"[sync50] суми ваг (float64) за процесами: {[repr(s) for s in sums]}; max − min = {diff:.3e}")
    dist.barrier()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["info", "train", "bench", "grad_single", "equiv", "sync50"])
    ap.add_argument("--sync", default="none", choices=["none", "manual", "ddp"])
    ap.add_argument("--ns", action="store_true", help="прямий і зворотний прохід у model.no_sync()")
    ap.add_argument("--b", type=int, default=VARIANT["B"] // VARIANT["P_max"], help="батч процесу")
    ap.add_argument("--lr", type=float, default=VARIANT["lr0"])
    ap.add_argument("--tag", default="run")
    ap.add_argument("--outdir", default="results/runs")
    ap.add_argument("--ckpt-every", type=int, default=0)
    ap.add_argument("--fail-step", type=int, default=0)
    ap.add_argument("--ckpt-dir", default="")
    ap.add_argument("--max-steps", type=int, default=0, help="налагодження: зупинити навчання після стількох кроків")
    a = ap.parse_args()

    n_th = c_phys() // VARIANT["P_max"]       # n_th = ⌊C_phys / P_max⌋, однаково для всіх прогонів
    torch.set_num_threads(n_th)
    if a.task == "info":
        return task_info(a)
    rank, world = 0, 1
    if a.sync != "none":
        # --rdzv-backend=static створює TCPStore один раз і лишає його між перезапусками, тому адреси gloo з
        # попередньої спроби лишаються в сховищі, і перезапущені процеси чекають на мертвий порт.
        # Параметр group_name у PyTorch ігнорується, тому кожній спробі дається власний простір ключів через PrefixStore.
        attempt = os.environ.get("TORCHELASTIC_RESTART_COUNT", "0")
        store = dist.TCPStore(os.environ["MASTER_ADDR"], int(os.environ["MASTER_PORT"]),
                              int(os.environ["WORLD_SIZE"]), is_master=False)
        store = dist.PrefixStore(f"{os.environ.get('RUN_ID', 'run')}-attempt{attempt}", store)
        dist.init_process_group(backend="gloo", store=store, rank=int(os.environ["RANK"]),
                                world_size=int(os.environ["WORLD_SIZE"]))
        rank, world = dist.get_rank(), dist.get_world_size()
    if a.task == "train":
        task_train(a, rank, world)
    elif a.task == "bench":
        task_bench(a, rank, world)
    elif a.task == "grad_single":
        task_grad_single(a)
    elif a.task == "equiv":
        task_equiv(a, rank, world)
    elif a.task == "sync50":
        task_sync50(a, rank, world)
    if world > 1:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
