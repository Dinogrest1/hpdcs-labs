"""Зберігає ряди Prometheus за час прогону в results/prom_<прогін>.json,
щоб усі розрахунки й діаграми робились офлайн, без залежності від тому Prometheus.
python3 scripts/promdump.py <прогін>   (бере t0/end з results/<прогін>.json)"""
import json
import sys
import urllib.parse
import urllib.request

PROM = "http://localhost:9090"
ROUTE = 'path="/telemed-sessions/{session_id}"'
QUERIES = {
    # накопичені лічильники бакетів по екземплярах — для p99 сервера і М4
    "bucket": f"sum by (instance, le) (http_request_duration_seconds_bucket{{{ROUTE}}})",
    "requests": f"sum by (instance) (http_requests_total{{{ROUTE}}})",
    "cpu": "process_cpu_seconds_total{job=\"web\"}",
    "inprogress": "http_requests_in_progress{job=\"web\"}",
    "up": "up{job=\"web\"}",
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
# Сирі зразки з точними часовими мітками збору (range-вектор): швидкості рахуються по них,
# бо значення query_range прив'язані до сітки 5 с і можуть бути «старшими» за точку сітки до 5 с.
out["raw"] = {}
for name in ("cpu", "requests"):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode(
        {"query": f"{QUERIES[name]}[{end - start}s]" if name == "cpu" else
                  f"http_requests_total{{{ROUTE}}}[{end - start}s]", "time": end})
    data = json.load(urllib.request.urlopen(url))
    assert data["status"] == "success", data
    out["raw"][name] = data["data"]["result"]
json.dump(out, open(f"results/prom_{run}.json", "w"))
print(f"prom_{run}.json: " + ", ".join(f"{k}={len(v)} рядів" for k, v in out["series"].items()))
