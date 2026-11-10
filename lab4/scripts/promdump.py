"""Зберігає ряди Prometheus за час прогону в results/<прогін>/prom.json (розрахунки — офлайн).
python3 scripts/promdump.py <прогін> <unix початку> <unix кінця>"""
import json
import sys
import urllib.parse
import urllib.request

PROM = "http://localhost:9090"
QUERIES = {
    "tasks_queued": "max(tasks_queued)",
    "tasks_running": "max(tasks_running)",
    "worker_tasks": "sum by (pod, result) (worker_tasks_total)",
    "worker_claims": "sum by (pod, result) (worker_claims_total)",
    "done_rate": 'sum(rate(worker_tasks_total{result="done"}[15s]))',
    "in_flight": "sum(worker_tasks_in_flight)",
    "db_cpu": 'sum(container_cpu_usage_seconds_total{pod="db-0", container="postgres"})',
    "worker_cpu": 'sum by (pod) (container_cpu_usage_seconds_total{container="worker"})',
    "worker_throttled": 'sum by (pod) (container_cpu_cfs_throttled_periods_total{container="worker"})',
    "worker_periods": 'sum by (pod) (container_cpu_cfs_periods_total{container="worker"})',
    "restarts": 'sum by (pod) (kube_pod_container_status_restarts_total{container="worker"})',
    "db_xacts": 'sum(pg_stat_database_xact_commit{datname="telemed"} + pg_stat_database_xact_rollback{datname="telemed"})',
    "up": 'up{pod=~"worker-.*"}',
}

run, start, end = sys.argv[1], int(float(sys.argv[2])) - 15, int(float(sys.argv[3])) + 5
out = {"run": run, "start": start, "end": end, "step_s": 5, "series": {}}
for name, q in QUERIES.items():
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": start, "end": end, "step": "5s"})
    data = json.load(urllib.request.urlopen(url))
    assert data["status"] == "success", data
    out["series"][name] = data["data"]["result"]
json.dump(out, open(f"results/{run}/prom.json", "w"))
print(f"results/{run}/prom.json: " + ", ".join(f"{k}={len(v)}" for k, v in out["series"].items()))
