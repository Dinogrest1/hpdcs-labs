# ЛР 4. Синхронізація пула воркерів (варіант 26)

Сервіс `/telemed-sessions` з ЛР 3 у кластері kind, доповнений чергою задач у PostgreSQL і пулом воркерів.

## Параметри варіанта 26

| Параметр | Значення |
|---|---|
| Сутність / ресурс | телемедична консультація, `/telemed-sessions` |
| R (з ЛР 3) | 5 воркерів |
| Розмір зрізу S | 256 × 256 |
| q_lo / q_hi | 0,05 / 0,95 |
| T_poll | 50 мс |
| Lease L | 10 с, heartbeat кожні L / 3 |
| Сценарій відмови | Ф5: L = t_cpu / 2, прогін без heartbeat і з heartbeat |
| Додаткова вимога | Д1: поле `priority` 1…3, захоплення за `priority DESC, created_at` |

## Первинний проєкт (зафіксовано до коду)

**Схема.** Міграція `db/tasks.sql` накочується окремим Job `migrate-tasks` (той самий образ і спосіб, що Job `migrate` у ЛР 3).

- `tasks`: `id`, `record_id` (FK на `telemed_sessions`), `status` (`queued`, `running`, `done`, `failed`), `seed`, `priority` (1…3, для Д1; за замовчуванням 2), `attempts`, `max_attempts = 3`, `token = 0`, `lease_until`, `worker_id`, `created_at`, `started_at`, `finished_at`. Індекси: `(status, created_at)` за умовою і `(status, priority DESC, created_at)` для Д1.
- `task_results`: `task_id` — первинний ключ, `token`, `worker_id`, `result jsonb`, `created_at`. На кроці 3 ключ тимчасово замінюється звичайним індексом (`db/step3_drop_pk.sql`), на кроці 4 повертається (`db/step4_restore_pk.sql`).

**Сервіс web.** До коду ЛР 3 додаються `POST /telemed-sessions/{id}/tasks` (202 або 404; `seed` генерує сервіс, у тілі можна передати `priority`), `GET /tasks/{id}` (задача з результатом), `GET /tasks` (фільтр `status`, `limit`, `offset`), вимірювачі `tasks_queued` і `tasks_running`, які фонова корутина оновлює одним запитом до бази кожні 5 с.

**Воркер.** Окремий процес на Python з власним образом (`worker/`), одна задача за раз, синхронний драйвер psycopg. Обробка зрізу — цикли Python без NumPy. Цикл: захоплення → GET запису від web → обробка → запис результату. Усі варіанти захоплення лишаються в коді й перемикаються змінними середовища, щоб кожен крок можна було повторити тим самим образом:

| Змінна | Значення | Крок |
|---|---|---|
| `CLAIM_MODE` | `naive` (SELECT і окремий UPDATE), `for_update` (одна команда з FOR UPDATE), `skip_locked` (FOR UPDATE SKIP LOCKED) | 3, 4 |
| `STORE_MODE` | `plain` (INSERT без ON CONFLICT і окремий UPDATE), `idempotent` (одна транзакція: UPDATE за токеном, INSERT … ON CONFLICT DO NOTHING) | 3–4, 5 |
| `LEASE_ENABLED`, `HEARTBEAT` | повернення задач зі спливлим lease і heartbeat | 5, 9 |
| `LEASE_SECONDS`, `POLL_MS`, `FAIL_RATE`, `CLAIM_ORDER` | L, T_poll, імовірність падіння, порядок захоплення (`created` або `priority`) | варіант, 9, 10 |

Задачі зі спливлим lease повертає в чергу кожен воркер перед захопленням (другий із двох дозволених умовою способів): за L = t_cpu / 2 у сценарії Ф5 повернення раз на 5 с у web було б на два порядки рідшим за сам lease. Heartbeat — окремий потік з власним зʼєднанням до бази. `failed` — кінцевий стан.

Метрики воркера на порту 9000: `worker_tasks_total{result}`, `worker_claims_total{result}`, `worker_task_duration_seconds{step}`, `worker_tasks_in_flight`.

**Kubernetes.** Deployment `worker` без `replicas`, `requests.cpu 250m`, `limits.cpu 500m`, `limits.memory 512Mi`, `terminationGracePeriodSeconds 30`, `WORKER_ID` з імені пода через Downward API. Поди worker, як і web, стають лише на два вузли worker. PodMonitor для подів worker з інтервалом 5 с.

**Генератор.** Go-генератор з ЛР 3 з новим режимом `-mode tasks`: POST `/telemed-sessions/{id}/tasks` у відкритому контурі з заданою інтенсивністю для id 1…100, запис id створених задач у `<прогін>.tasks.csv`.

**Вимірювання.** Після кожного прогону таблиці `tasks` і `task_results` вивантажуються в CSV у `results/<прогін>/`, туди ж ідуть журнали воркерів, знімки `/metrics` кожного воркера до і після прогону, посекундний запис `pg_stat_activity`, `pg_locks` і лічильника транзакцій, ряди Prometheus. Усі числа звіту рахує `scripts/analyze.py` з цих файлів.

## Що змінилося під час реалізації

| Було в проєкті | Стало | Чому |
|---|---|---|
| зупинка воркера на кроці 5 командою `kubectl exec … -- kill -STOP 1` | сигнал STOP процесу воркера з вузла kind | процес воркера — PID 1 у своєму просторі PID, ядро не доставляє йому сигнал зсередини цього простору |
| журнал воркера лише в stdout | копія у файлі на томі `emptyDir` | `kubectl logs` зберігає лише два останні екземпляри контейнера, а на кроці 9 їх сотні |
| 400 задач у черзі на кроці 6 | 2000 · W задач | 400 задач закінчуються за 2–9 с, вікна 30 с до спорожнення черги немає |
| повернення задачі після SIGTERM: `… WHERE id = $1 AND token = $2` | `… AND status = 'running'` і блокування між перевіркою прапорця зупинки та захопленням | дві гонки, виявлені на кроці 8 (задача без власника до спливання lease; повернення в чергу вже завершеної задачі) |
| типова пауза перезапуску контейнера | `crashLoopBackOff.maxContainerRestartPeriod: 1s` на вузлах worker (`scripts/kubelet_backoff.sh`) | за FAIL_RATE = 0,15 пул за 2 хвилини стоїть у CrashLoopBackOff |

## Запуск

```bash
cd lab4
cp .env.example .env              # задати POSTGRES_PASSWORD
./scripts/deploy.sh               # кластер, образи, база, міграції, web, worker, моніторинг
./scripts/series.sh               # засів і кроки 1–10
python3 scripts/analyze.py
./scripts/destroy.sh              # повне видалення кластера
```
