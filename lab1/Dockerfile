# syntax=docker/dockerfile:1

# ---- Стадія 1: збірка залежностей ----
FROM python:3.12-slim AS builder
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Спершу лише requirements.txt: шар із залежностями перебудовується
# тільки коли змінюється перелік пакетів, а не при кожній правці коду.
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- Стадія 2: фінальний образ без компіляторів ----
FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY app/main.py app/healthcheck.py ./
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=2s --timeout=2s --start-period=5s --retries=3 \
    CMD ["python", "/srv/healthcheck.py"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
