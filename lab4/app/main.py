"""CRUD-сервіс телемедичних консультацій (варіант 26, ресурс /telemed-sessions) з метриками Prometheus
і чергою задач обробки (ЛР 4): створення задачі, стан задачі з результатом, перелік задач."""

import asyncio
import json
import logging
import os
import random
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import asyncpg
from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("telemed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Імʼя екземпляра: POD_NAME (у Kubernetes — через Downward API), інакше hostname контейнера.
INSTANCE = os.environ.get("POD_NAME") or socket.gethostname()
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
LIMIT_DEFAULT = 20
LIMIT_MAX = 100
ID_MAX = 2**63 - 1

# Метрики реєструються один раз під час імпорту модуля, оновлюються в MetricsMiddleware.
# Межі бакетів — набір Г2 варіанта 26 (мс → с, за конвенцією Prometheus).
BUCKETS = (0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0)
REQUESTS = Counter("http_requests_total", "Завершені HTTP-запити", ["method", "path", "code"])
DURATION = Histogram("http_request_duration_seconds", "Тривалість обробки запиту в сервісі",
                     ["method", "path"], buckets=BUCKETS)
IN_PROGRESS = Gauge("http_requests_in_progress", "Запити в обробці")
# ЛР 4: довжина черги і кількість задач в обробці; оновлюються запитом до бази кожні 5 с.
TASKS_QUEUED = Gauge("tasks_queued", "Задачі в статусі queued")
TASKS_RUNNING = Gauge("tasks_running", "Задачі в статусі running")
TASKS_REFRESH_S = 5.0

Channel = Literal["video", "audio", "chat"]
Status = Literal["scheduled", "in_progress", "completed", "cancelled"]
TaskStatus = Literal["queued", "running", "done", "failed"]


class SessionIn(BaseModel):
    """Тіло POST і PUT. PUT замінює запис повністю, тому всі поля обовʼязкові."""

    model_config = ConfigDict(extra="forbid")

    patient_code: str = Field(pattern=r"^[A-Za-z0-9-]{4,32}$")
    doctor: str = Field(min_length=1, max_length=100)
    channel: Channel
    started_at: datetime
    duration_min: int = Field(strict=True)
    status: Status

    @field_validator("doctor")
    @classmethod
    def doctor_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("started_at")
    @classmethod
    def started_at_has_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a timezone offset, e.g. 2026-10-10T12:00:00+03:00")
        return v

    @field_validator("duration_min")
    @classmethod
    def duration_range(cls, v: int) -> int:
        # Правило варіанта 26: тривалість більша за нуль і не більша за 90 хвилин.
        if not 0 < v <= 90:
            raise ValueError("must be greater than 0 and not greater than 90 minutes")
        return v


class TaskIn(BaseModel):
    """Необовʼязкове тіло POST …/tasks. Д1: пріоритет 1…3, більший обробляється раніше."""

    model_config = ConfigDict(extra="forbid")

    priority: int = Field(2, ge=1, le=3, strict=True)


COLUMNS = "id, patient_code, doctor, channel, started_at, duration_min, status, created_at, updated_at"


TASK_COLUMNS = ("id, record_id, status, seed, priority, attempts, max_attempts, token, "
                "lease_until, worker_id, created_at, started_at, finished_at")


def row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k in ("started_at", "created_at", "updated_at"):
        d[k] = d[k].isoformat()
    return d


def task_to_dict(row: asyncpg.Record) -> dict:
    d = {k: row[k] for k in TASK_COLUMNS.split(", ")}
    for k in ("lease_until", "created_at", "started_at", "finished_at"):
        d[k] = d[k].isoformat() if d[k] is not None else None
    return d


async def refresh_task_gauges(app: FastAPI) -> None:
    """Раз на 5 с одним запитом читає кількість задач у статусах queued і running."""
    while True:
        p = app.state.pool
        if p is not None:
            try:
                row = await p.fetchrow(
                    """SELECT count(*) FILTER (WHERE status = 'queued')  AS queued,
                              count(*) FILTER (WHERE status = 'running') AS running
                       FROM tasks WHERE status IN ('queued', 'running')""")
                TASKS_QUEUED.set(row["queued"])
                TASKS_RUNNING.set(row["running"])
            except (OSError, asyncpg.PostgresError) as e:
                log.warning("tasks gauges not refreshed: %s", e)
        await asyncio.sleep(TASKS_REFRESH_S)


async def connect_forever(app: FastAPI) -> None:
    """Підключається до бази з повторами; поки пулу немає, /healthz віддає 503."""
    delay = 0.2
    while True:
        try:
            app.state.pool = await asyncpg.create_pool(
                host=os.environ["DB_HOST"],
                port=int(os.environ.get("DB_PORT", "5432")),
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                database=os.environ["DB_NAME"],
                # Розмір пулу — найбільша кількість зʼєднань екземпляра; понад пул зʼєднань немає.
                min_size=min(2, POOL_SIZE),
                max_size=POOL_SIZE,
            )
            log.info("connected to database, instance=%s pool=%d", INSTANCE, POOL_SIZE)
            return
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("database not ready (%s), retry in %.1fs", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = None
    task = asyncio.create_task(connect_forever(app))
    gauges = asyncio.create_task(refresh_task_gauges(app))
    yield
    # Сюди uvicorn доходить після SIGTERM, коли запити в обробці вже завершені.
    task.cancel()
    gauges.cancel()
    if app.state.pool is not None:
        await app.state.pool.close()
    log.info("graceful shutdown complete, pool closed, instance=%s", INSTANCE)


app = FastAPI(title="Telemed sessions", lifespan=lifespan)


class MetricsMiddleware:
    """Чистий ASGI-обробник: метрики запиту і заголовок X-Pod без накладних витрат BaseHTTPMiddleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        code = [500]
        pod = INSTANCE.encode()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                code[0] = message["status"]
                message["headers"] = list(message.get("headers", [])) + [(b"x-pod", pod)]
            await send(message)

        IN_PROGRESS.inc()
        t0 = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dt = time.perf_counter() - t0
            IN_PROGRESS.dec()
            # Шаблон маршруту (/telemed-sessions/{session_id}), а не конкретний id:
            # FastAPI кладе знайдений маршрут у scope["route"].
            route = scope.get("route")
            path = route.path if route is not None else "unmatched"
            method = scope["method"]
            REQUESTS.labels(method, path, str(code[0])).inc()
            DURATION.labels(method, path).observe(dt)


app.add_middleware(MetricsMiddleware)


def error(status: int, message: str, details: list | None = None) -> JSONResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = []
    for e in exc.errors():
        loc = [str(p) for p in e["loc"] if p not in ("body",)]
        details.append({"field": ".".join(loc) or "body", "problem": e["msg"]})
    return error(400, "validation failed", details)


@app.exception_handler(asyncpg.PostgresConnectionError)
@app.exception_handler(ConnectionError)
async def db_down_handler(request: Request, exc: Exception):
    return error(503, "database unavailable")


def pool(request: Request) -> asyncpg.Pool:
    p = request.app.state.pool
    if p is None:
        raise ConnectionError("pool not ready")
    return p


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/livez")
async def livez():
    # Лише «процес живий»: база не перевіряється, щоб її збій не перезапускав сервіс.
    return {"status": "alive", "instance": INSTANCE}


@app.get("/healthz")
async def healthz(request: Request):
    p = request.app.state.pool
    if p is None:
        return error(503, "database not connected yet")
    try:
        await asyncio.wait_for(p.fetchval("SELECT 1"), timeout=1.0)
    except Exception as e:  # будь-яка помилка бази робить сервіс неготовим
        return error(503, f"database check failed: {type(e).__name__}")
    return {"status": "ok", "instance": INSTANCE}


@app.post("/telemed-sessions", status_code=201)
async def create_session(body: SessionIn, request: Request):
    row = await pool(request).fetchrow(
        f"""INSERT INTO telemed_sessions
              (patient_code, doctor, channel, started_at, duration_min, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {COLUMNS}""",
        body.patient_code, body.doctor, body.channel, body.started_at, body.duration_min, body.status,
    )
    return row_to_dict(row)


@app.get("/telemed-sessions")
async def list_sessions(
    request: Request,
    limit: int = Query(LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
    offset: int = Query(0, ge=0, le=ID_MAX),
    channel: Channel | None = Query(None),
):
    p = pool(request)
    if channel is None:
        total = await p.fetchval("SELECT count(*) FROM telemed_sessions")
        rows = await p.fetch(
            f"SELECT {COLUMNS} FROM telemed_sessions ORDER BY id LIMIT $1 OFFSET $2", limit, offset
        )
    else:
        total = await p.fetchval("SELECT count(*) FROM telemed_sessions WHERE channel = $1", channel)
        rows = await p.fetch(
            f"SELECT {COLUMNS} FROM telemed_sessions WHERE channel = $1 ORDER BY id LIMIT $2 OFFSET $3",
            channel, limit, offset,
        )
    return {"items": [row_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/telemed-sessions/{session_id}")
async def get_session(request: Request, session_id: int = Path(ge=1, le=ID_MAX)):
    row = await pool(request).fetchrow(f"SELECT {COLUMNS} FROM telemed_sessions WHERE id = $1", session_id)
    if row is None:
        return error(404, f"telemed session {session_id} not found")
    return row_to_dict(row)


@app.put("/telemed-sessions/{session_id}")
async def replace_session(body: SessionIn, request: Request, session_id: int = Path(ge=1, le=ID_MAX)):
    row = await pool(request).fetchrow(
        f"""UPDATE telemed_sessions
            SET patient_code = $2, doctor = $3, channel = $4, started_at = $5,
                duration_min = $6, status = $7, updated_at = now()
            WHERE id = $1
            RETURNING {COLUMNS}""",
        session_id, body.patient_code, body.doctor, body.channel, body.started_at, body.duration_min, body.status,
    )
    if row is None:
        return error(404, f"telemed session {session_id} not found")
    return row_to_dict(row)


@app.delete("/telemed-sessions/{session_id}", status_code=204)
async def delete_session(request: Request, session_id: int = Path(ge=1, le=ID_MAX)):
    deleted = await pool(request).fetchval(
        "DELETE FROM telemed_sessions WHERE id = $1 RETURNING id", session_id
    )
    if deleted is None:
        return error(404, f"telemed session {session_id} not found")
    return Response(status_code=204)


# ---- ЛР 4: черга задач обробки ----

@app.post("/telemed-sessions/{session_id}/tasks", status_code=202)
async def create_task(request: Request, session_id: int = Path(ge=1, le=ID_MAX), body: TaskIn | None = None):
    # seed генерує сервіс: 1 … 2^31 − 1, початковий стан генератора зрізу.
    seed = random.randint(1, 2**31 - 1)
    priority = body.priority if body is not None else 2
    # INSERT … SELECT: якщо запису немає, не вставляється жодного рядка, і відповідь 404 —
    # одним запитом, без окремої перевірки існування.
    row = await pool(request).fetchrow(
        f"""INSERT INTO tasks (record_id, seed, priority)
            SELECT id, $2, $3 FROM telemed_sessions WHERE id = $1
            RETURNING {TASK_COLUMNS}""",
        session_id, seed, priority,
    )
    if row is None:
        return error(404, f"telemed session {session_id} not found")
    return task_to_dict(row)


@app.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: int = Path(ge=1, le=ID_MAX)):
    cols = ", ".join("t." + c for c in TASK_COLUMNS.split(", "))
    row = await pool(request).fetchrow(
        f"""SELECT {cols}, r.task_id AS r_task_id, r.token AS r_token, r.worker_id AS r_worker_id,
                   r.result AS r_result, r.created_at AS r_created_at
            FROM tasks t LEFT JOIN task_results r ON r.task_id = t.id
            WHERE t.id = $1 LIMIT 1""",
        task_id,
    )
    if row is None:
        return error(404, f"task {task_id} not found")
    d = task_to_dict(row)
    d["result"] = None
    if row["r_task_id"] is not None:
        d["result"] = {"token": row["r_token"], "worker_id": row["r_worker_id"],
                       "result": json.loads(row["r_result"]), "created_at": row["r_created_at"].isoformat()}
    return d


@app.get("/tasks")
async def list_tasks(
    request: Request,
    limit: int = Query(LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
    offset: int = Query(0, ge=0, le=ID_MAX),
    status: TaskStatus | None = Query(None),
):
    p = pool(request)
    if status is None:
        total = await p.fetchval("SELECT count(*) FROM tasks")
        rows = await p.fetch(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id LIMIT $1 OFFSET $2", limit, offset)
    else:
        total = await p.fetchval("SELECT count(*) FROM tasks WHERE status = $1", status)
        rows = await p.fetch(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE status = $1 ORDER BY id LIMIT $2 OFFSET $3",
            status, limit, offset,
        )
    return {"items": [task_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}
