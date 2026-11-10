# syntax=docker/dockerfile:1
# Образ воркера: окремий від web, без FastAPI і uvicorn.

# ---- Стадія 1: залежності у віртуальному середовищі ----
FROM python:3.12-slim AS builder
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- Стадія 2: фінальний образ ----
FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY worker.py ./
USER 10001
EXPOSE 9000
# exec-форма: python — PID 1 і сам отримує SIGTERM від kubelet.
CMD ["python", "/srv/worker.py"]
