"""Воркер черги задач (ЛР 4, варіант 26).

Цикл: повернути в чергу задачі зі спливлим lease → захопити задачу → отримати запис від web →
обробити синтетичний зріз → записати результат. Одна задача за раз; паралелізм дає кількість воркерів.

Варіанти захоплення і запису лишені в коді й перемикаються змінними середовища:
  CLAIM_MODE   naive | for_update | skip_locked
  STORE_MODE   plain | idempotent
  LEASE_ENABLED, HEARTBEAT   повернення задач зі спливлим lease і продовження lease
"""
import http.client
import json
import logging
import math
import os
import random
import signal
import socket
import sys
import threading
import time

import psycopg
from prometheus_client import Counter, Gauge, Histogram, start_http_server

WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname()
DSN = "host={h} port={p} user={u} password={pw} dbname={d} connect_timeout=3".format(
    h=os.environ["DB_HOST"], p=os.environ.get("DB_PORT", "5432"), u=os.environ["DB_USER"],
    pw=os.environ["DB_PASSWORD"], d=os.environ["DB_NAME"])
WEB_HOST = os.environ.get("WEB_HOST", "web")
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))
RESOURCE = os.environ.get("RESOURCE", "/telemed-sessions")

LEASE_S = float(os.environ.get("LEASE_SECONDS", "10"))        # L
POLL_S = float(os.environ.get("POLL_MS", "50")) / 1000        # T_poll
FAIL_RATE = float(os.environ.get("FAIL_RATE", "0"))
CLAIM_MODE = os.environ.get("CLAIM_MODE", "skip_locked")
STORE_MODE = os.environ.get("STORE_MODE", "idempotent")
CLAIM_ORDER = os.environ.get("CLAIM_ORDER", "created")        # created | priority (Д1)
LEASE_ENABLED = os.environ.get("LEASE_ENABLED", "1") == "1"
HEARTBEAT = os.environ.get("HEARTBEAT", "1") == "1"
GRACE_S = float(os.environ.get("GRACE_SECONDS", "25"))        # менше за terminationGracePeriodSeconds = 30
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9000"))
LOG_FILE = os.environ.get("LOG_FILE", "")

S = 256                                                       # зріз S × S
Q_LO = float(os.environ.get("Q_LO", "0.05"))
Q_HI = float(os.environ.get("Q_HI", "0.95"))
LEVELS = 4096

handlers = [logging.StreamHandler(sys.stdout)]
if LOG_FILE:
    # Копія журналу у файлі на томі emptyDir: вона переживає перезапуск контейнера,
    # а kubectl logs зберігає лише поточний і попередній екземпляри.
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s",
                    datefmt="%H:%M:%S", handlers=handlers)
log = logging.getLogger("worker")

BUCKETS = (0.0005, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05, 0.075,
           0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0)
TASKS = Counter("worker_tasks_total", "Задачі за результатом", ["result"])           # done | failed | lost
CLAIMS = Counter("worker_claims_total", "Спроби захоплення за результатом", ["result"])  # task | empty
# step: claim — команда захоплення, що повернула задачу; claim_empty — порожнє захоплення;
# reap — повернення задач зі спливлим lease; fetch, process, store — три кроки задачі.
DURATION = Histogram("worker_task_duration_seconds", "Тривалість кроків", ["step"], buckets=BUCKETS)
IN_FLIGHT = Gauge("worker_tasks_in_flight", "Задачі в обробці цим воркером")
for r in ("done", "failed", "lost"):
    TASKS.labels(r)
for r in ("task", "empty"):
    CLAIMS.labels(r)

ORDER = "priority DESC, created_at" if CLAIM_ORDER == "priority" else "created_at"

# Наївне захоплення (крок 3): дві окремі команди без транзакції і без блокувань.
SQL_NAIVE_SELECT = f"SELECT id FROM tasks WHERE status = 'queued' ORDER BY {ORDER} LIMIT 1"
SQL_NAIVE_UPDATE = """
UPDATE tasks SET status = 'running', worker_id = %s, token = token + 1,
       attempts = attempts + 1, started_at = now()
 WHERE id = %s
RETURNING id, record_id, seed, token, attempts"""

# Захоплення однією командою (крок 4). FOR UPDATE блокує знайдений рядок до кінця команди;
# SKIP LOCKED пропускає рядки, які вже заблокував інший воркер.
SQL_CLAIM = """
UPDATE tasks SET status = 'running', worker_id = %s, token = token + 1,
       lease_until = now() + %s * interval '1 second', attempts = attempts + 1, started_at = now()
 WHERE id = (SELECT id FROM tasks WHERE status = 'queued'
              ORDER BY {order} LIMIT 1 FOR UPDATE{skip})
RETURNING id, record_id, seed, token, attempts"""
SQL_CLAIM_SKIP = SQL_CLAIM.format(order=ORDER, skip=" SKIP LOCKED")
SQL_CLAIM_WAIT = SQL_CLAIM.format(order=ORDER, skip="")

# Повернення задач зі спливлим lease (крок 5); після max_attempts захоплень — failed.
SQL_REAP = """
UPDATE tasks
   SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
       finished_at = CASE WHEN attempts >= max_attempts THEN now() END,
       worker_id = NULL
 WHERE status = 'running' AND lease_until < now()
RETURNING id, status, attempts, token"""

SQL_HEARTBEAT = "UPDATE tasks SET lease_until = now() + %s * interval '1 second' WHERE id = %s AND token = %s"
# failed — кінцевий стан: воркер із чинним токеном не повертає таку задачу в done.
SQL_DONE = "UPDATE tasks SET status = 'done', finished_at = now() WHERE id = %s AND token = %s AND status <> 'failed'"
SQL_RESULT = """INSERT INTO task_results (task_id, token, worker_id, result) VALUES (%s, %s, %s, %s)
ON CONFLICT (task_id) DO NOTHING"""
SQL_DONE_PLAIN = "UPDATE tasks SET status = 'done', finished_at = now() WHERE id = %s"
SQL_RESULT_PLAIN = "INSERT INTO task_results (task_id, token, worker_id, result) VALUES (%s, %s, %s, %s)"
# Повернення задачі в чергу після SIGTERM. Умова status = 'running' потрібна понад умову за токеном:
# головний потік може встигнути завершити задачу (done) між рішенням сторожа і цією командою, і без неї
# done знову ставав би queued, а задача виконувалась би вдруге.
SQL_RELEASE = "UPDATE tasks SET status = 'queued', worker_id = NULL WHERE id = %s AND token = %s AND status = 'running'"

stopping = False                # отримано SIGTERM: нових задач не брати. Простий прапорець, а не Event:
                                # обробник сигналу виконується в головному потоці й не має брати блокувань.
finished = threading.Event()    # головний цикл завершився
current = None                  # (id, token) задачі в обробці; читають heartbeat і сторож зупинки
# Перевірка прапорця зупинки, захоплення і запис current виконуються під цим блокуванням; сторож бере його
# перед читанням current. Інакше SIGTERM між перевіркою і захопленням дає задачу, про яку сторож не знає.
claim_lock = threading.Lock()


class Lost(Exception):
    """Команда з токеном воркера оновила нуль рядків: задача вже не його."""


def connect(role):
    """Зʼєднання в режимі autocommit; повторює спроби, поки база недоступна."""
    delay, first = 0.2, True
    while True:
        try:
            conn = psycopg.connect(DSN, autocommit=True, application_name=f"{WORKER_ID}/{role}")
            if not first:
                log.info("зʼєднання з базою відновлено (%s)", role)
            return conn
        except psycopg.OperationalError as e:
            first = False
            log.warning("база недоступна (%s): %s; повтор через %.1f с", role, str(e).strip(), delay)
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def process(seed):
    """Точкова операція за гістограмою зрізу S × S; цикли Python без бібліотек поза інтерпретатором."""
    n = S * S
    pixels = [0] * n
    hist = [0] * LEVELS
    x = seed
    for i in range(n):
        x = (1103515245 * x + 12345) % 2147483648      # x(i+1) = (a · x(i) + c) mod 2^31
        p = x >> 19                                    # 12 старших бітів із 31
        pixels[i] = p
        hist[p] += 1
    # Квантиль рівня q — найменший рівень яскравості, на якому накопичена частка пікселів >= q.
    lo_rank, hi_rank = Q_LO * n, Q_HI * n
    p_lo = p_hi = None
    cum = 0
    for level in range(LEVELS):
        cum += hist[level]
        if p_lo is None and cum >= lo_rank:
            p_lo = level
        if cum >= hi_rank:
            p_hi = level
            break
    width = p_hi - p_lo
    if width <= 0:
        raise ValueError(f"вироджене вікно яскравості: p_lo={p_lo}, p_hi={p_hi}")
    removed = 0
    total = 0
    total_sq = 0
    for p in pixels:
        if p < p_lo or p > p_hi:
            removed += 1                               # p' = 0, піксель вилучено
        else:
            v = (2 * 4095 * (p - p_lo) + width) // (2 * width)   # округлення до найближчого цілого
            total += v
            total_sq += v * v
    kept = n - removed
    mean = total / kept
    std = math.sqrt(max(total_sq / kept - mean * mean, 0.0))
    return {"p_lo": p_lo, "p_hi": p_hi, "removed": removed, "mean": round(mean, 3), "std": round(std, 3)}


class Web:
    """Постійне HTTP-зʼєднання до сервісу web."""

    def __init__(self):
        self.conn = None

    def get_record(self, record_id):
        for attempt in (1, 2):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection(WEB_HOST, WEB_PORT, timeout=5)
                self.conn.request("GET", f"{RESOURCE}/{record_id}")
                resp = self.conn.getresponse()
                body = resp.read()
                if resp.status != 200:
                    raise RuntimeError(f"GET {RESOURCE}/{record_id} -> {resp.status}")
                return json.loads(body)
            except (OSError, http.client.HTTPException):
                self.conn = None                      # зʼєднання закрив сервер: одна повторна спроба
                if attempt == 2:
                    raise


def heartbeat_loop():
    """Кожні L / 3 продовжує lease поточної задачі; умова за токеном."""
    conn = connect("heartbeat")
    while not finished.is_set():
        time.sleep(LEASE_S / 3)
        cur = current
        if cur is None:
            continue
        try:
            n = conn.execute(SQL_HEARTBEAT, (LEASE_S, cur[0], cur[1])).rowcount
        except psycopg.OperationalError as e:
            log.warning("heartbeat: зʼєднання з базою втрачено: %s", str(e).strip())
            conn = connect("heartbeat")
            continue
        if n == 0 and current == cur:
            log.warning("task=%d token=%d heartbeat оновив 0 рядків: lease втрачено, задачу забрав інший воркер",
                        cur[0], cur[1])


def shutdown_guard():
    """Після SIGTERM чекає завершення поточної задачі; якщо не встигла за GRACE_S — повертає її в чергу."""
    while not stopping:
        time.sleep(0.002)
    cur = current
    log.info("SIGTERM: нових задач не беру, задача в обробці: %s, чекаю до %.1f с",
             f"task={cur[0]}" if cur else "немає", GRACE_S)
    if finished.wait(GRACE_S):
        return
    with claim_lock:            # після цього головний цикл уже не захопить нової задачі
        cur = current
    if cur is not None:
        try:
            n = connect("shutdown").execute(SQL_RELEASE, cur).rowcount
            log.warning("task=%d token=%d не завершилась за %.1f с: повернуто в чергу (оновлено рядків: %d)",
                        cur[0], cur[1], GRACE_S, n)
        except psycopg.Error as e:
            log.error("task=%d: не вдалося повернути в чергу: %s", cur[0], e)
    log.info("вихід з кодом 0")
    logging.shutdown()
    os._exit(0)


def reap(conn):
    for tid, status, attempts, token in conn.execute(SQL_REAP).fetchall():
        log.info("task=%d lease сплив: статус %s (attempts=%d, token=%d)", tid, status, attempts, token)


def claim(conn):
    if CLAIM_MODE == "naive":
        row = conn.execute(SQL_NAIVE_SELECT).fetchone()
        if row is None:
            return None
        return conn.execute(SQL_NAIVE_UPDATE, (WORKER_ID, row[0])).fetchone()
    sql = SQL_CLAIM_SKIP if CLAIM_MODE == "skip_locked" else SQL_CLAIM_WAIT
    return conn.execute(sql, (WORKER_ID, LEASE_S)).fetchone()


def store(conn, tid, token, result):
    payload = json.dumps(result)
    if STORE_MODE == "plain":
        conn.execute(SQL_RESULT_PLAIN, (tid, token, WORKER_ID, payload))
        conn.execute(SQL_DONE_PLAIN, (tid,))
        return
    # Завершення задачі і запис результату — одна транзакція; чужий токен відкочує її цілком.
    with conn.transaction():
        if conn.execute(SQL_DONE, (tid, token)).rowcount == 0:
            raise Lost()
        conn.execute(SQL_RESULT, (tid, token, WORKER_ID, payload))


def run_task(conn, web, task):
    global current
    tid, record_id, seed, token, attempts = task
    IN_FLIGHT.set(1)
    try:
        t0 = time.perf_counter()
        record = web.get_record(record_id)
        t1 = time.perf_counter()
        result = process(seed)
        t2 = time.perf_counter()
        result["record_id"] = record["id"]
        if FAIL_RATE > 0 and random.random() < FAIL_RATE:
            log.error("task=%d token=%d вбудоване падіння після обробки (FAIL_RATE=%.2f): os._exit(1)",
                      tid, token, FAIL_RATE)
            logging.shutdown()
            os._exit(1)                                # без обробки сигналів і без закриття зʼєднань
        store(conn, tid, token, result)
        t3 = time.perf_counter()
        DURATION.labels("fetch").observe(t1 - t0)
        DURATION.labels("process").observe(t2 - t1)
        DURATION.labels("store").observe(t3 - t2)
        TASKS.labels("done").inc()
        log.info("task=%d done token=%d attempts=%d fetch=%.1fms process=%.1fms store=%.1fms",
                 tid, token, attempts, (t1 - t0) * 1e3, (t2 - t1) * 1e3, (t3 - t2) * 1e3)
    except Lost:
        TASKS.labels("lost").inc()
        log.warning("task=%d token=%d завершення оновило 0 рядків: задача вже не належить воркеру, "
                    "результат відкинуто", tid, token)
    except psycopg.OperationalError:
        raise                                          # перепідключення в головному циклі
    except Exception as e:  # помилка кроку: задача лишається running і повернеться після спливання lease
        TASKS.labels("failed").inc()
        log.error("task=%d token=%d помилка обробки: %s: %s", tid, token, type(e).__name__, e)
    finally:
        current = None
        IN_FLIGHT.set(0)


def on_signal(signum, frame):
    global stopping
    stopping = True


def main():
    global current
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    start_http_server(METRICS_PORT)
    log.info("старт worker=%s claim=%s store=%s order=%s lease=%s L=%gс heartbeat=%s T_poll=%gмс FAIL_RATE=%g",
             WORKER_ID, CLAIM_MODE, STORE_MODE, CLAIM_ORDER, LEASE_ENABLED, LEASE_S,
             HEARTBEAT and LEASE_ENABLED, POLL_S * 1e3, FAIL_RATE)
    threading.Thread(target=shutdown_guard, daemon=True).start()
    if LEASE_ENABLED and HEARTBEAT:
        # Обробка — цикл Python, що тримає GIL; типовий інтервал перемикання потоків 5 мс
        # затримував би heartbeat на короткому lease, тому його зменшено до 0,5 мс.
        sys.setswitchinterval(0.0005)
        threading.Thread(target=heartbeat_loop, daemon=True).start()
    conn = connect("main")
    web = Web()
    while not stopping:
        try:
            if LEASE_ENABLED:
                t0 = time.perf_counter()
                reap(conn)
                DURATION.labels("reap").observe(time.perf_counter() - t0)
            with claim_lock:
                if stopping:
                    break
                t0 = time.perf_counter()
                task = claim(conn)
                dt = time.perf_counter() - t0
                if task is not None:
                    current = (task[0], task[3])
            if task is None:
                CLAIMS.labels("empty").inc()
                DURATION.labels("claim_empty").observe(dt)
                time.sleep(POLL_S)
                continue
            CLAIMS.labels("task").inc()
            DURATION.labels("claim").observe(dt)
            run_task(conn, web, task)
        except psycopg.OperationalError as e:
            log.warning("зʼєднання з базою втрачено: %s; перепідключення", str(e).strip())
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(0.2)
            conn = connect("main")
    finished.set()
    conn.close()
    log.info("вихід з кодом 0")
    sys.exit(0)


if __name__ == "__main__":
    main()
