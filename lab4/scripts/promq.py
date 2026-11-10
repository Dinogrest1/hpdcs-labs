"""Одне значення миттєвого запиту Prometheus: python3 scripts/promq.py 'sum(up{job="web"})'"""
import json
import sys
import urllib.parse
import urllib.request

url = "http://localhost:9090/api/v1/query?" + urllib.parse.urlencode({"query": sys.argv[1]})
try:
    r = json.load(urllib.request.urlopen(url, timeout=3))["data"]["result"]
    print(int(float(r[0]["value"][1])) if r else 0)
except Exception:
    print(-1)
