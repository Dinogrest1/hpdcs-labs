# Telemed sessions: CRUD-сервіс (ЛР 1, варіант 26)

Ресурс `/telemed-sessions`: FastAPI + asyncpg, PostgreSQL 16, балансувальник nginx.

## Запуск

```bash
cd lab1                         # усі команди — з папки лаби
cp .env.example .env            # один раз: задати POSTGRES_PASSWORD
docker compose up -d --build    # стенд з одним вебінстансом
WEB_REPLICAS=2 docker compose up -d --build   # те саме з двома
curl localhost:8080/healthz
```

Назовні публікується лише порт 8080 балансувальника.

## Структура

| Шлях | Що це |
|---|---|
| `app/main.py` | сервіс: 6 ендпоінтів, валідація, `/healthz` |
| `app/healthcheck.py` | healthcheck контейнера без curl/wget |
| `db/schema.sql` | схема, накочується автоматично через `docker-entrypoint-initdb.d` |
| `Dockerfile`, `.dockerignore` | багатоетапна збірка, фінальна стадія `python:3.12-slim` |
| `docker-compose.yml` | db + web (N реплік) + lb (nginx) + loadgen (profile `tools`) |
| `nginx/nginx.conf` | round-robin між репліками, `/noop` для перевірки генератора |
| `loadgen/loadgen.py` | генератор навантаження: відкритий і закритий контур |
| `scripts/` | перевірка CRUD, вимірювання, серія навантаження |
| `results/` | сирі результати прогонів (`*.json`, `*.raw.csv.gz`) і логи |
| `REPORT.md` | звіт |

## Навантаження

```bash
./scripts/series.sh 1    # серія на одному інстансі + перевірка генератора + закритий контур
./scripts/series.sh 2    # серія на двох інстансах + розподіл запитів
```

Один прогін вручну:

```bash
docker compose --profile tools run --rm loadgen open \
  --url 'http://lb/telemed-sessions/{id}' --ids 1-100 --rate 500 --duration 30 --warmup 10
```
