"""Зберігає ряди Prometheus за час прогону в results/prom_<прогін>.json (розрахунки — офлайн).
python3 scripts/promdump.py <прогін>"""
import json
import sys
import urllib.parse
import urllib.request

PROM = "http://localhost:9090"
ROUTE = 'path="/telemed-sessions/{session_id}"'
QUERIES = {
    "bucket": f"sum by (pod, le) (http_request_duration_seconds_bucket{{{ROUTE}}})",
    "requests": f"sum by (pod, node) (http_requests_total{{{ROUTE}}})",
    "throttled": 'sum by (pod) (container_cpu_cfs_throttled_periods_total{container="web"})',
    "periods": 'sum by (pod) (container_cpu_cfs_periods_total{container="web"})',
    "cpu_usage": 'sum by (pod) (container_cpu_usage_seconds_total{container="web"})',
    "process_cpu": 'sum by (pod) (process_cpu_seconds_total{pod=~"web-.*"})',
    "inprogress": 'sum by (pod) (http_requests_in_progress{pod=~"web-.*"})',
    "ready": 'kube_deployment_status_replicas_ready{deployment="web"}',
    "desired": 'kube_horizontalpodautoscaler_status_desired_replicas{horizontalpodautoscaler="web"}',
    "pod_node": 'kube_pod_info{pod=~"web-.*"}',
    "up": 'up{pod=~"web-.*"}',
    "pg_conn": 'sum by (state) (pg_stat_activity_count{datname="telemed"})',
}

run = sys.argv[1]
meta = json.load(open(f"results/{run}.json"))
start, end = meta["t0_unix"] - 15, meta["end_unix"] + 15
out = {"run": run, "start": start, "end": end, "step_s": 5, "series": {}}
for name, q in QUERIES.items():
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": start, "end": end, "step": "5s"})
    data = json.load(urllib.request.urlopen(url))
    assert data["status"] == "success", data
    out["series"][name] = data["data"]["result"]
json.dump(out, open(f"results/prom_{run}.json", "w"))
print(f"prom_{run}.json: " + ", ".join(f"{k}={len(v)}" for k, v in out["series"].items()))
