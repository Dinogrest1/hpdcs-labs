"""Healthcheck контейнера без curl і wget: у python:3.12-slim їх немає."""
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
