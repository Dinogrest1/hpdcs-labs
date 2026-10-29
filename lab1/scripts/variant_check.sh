#!/usr/bin/env bash
# Додаткова вимога варіанта 26: база у внутрішній мережі, порт на хост не публікується.
set -u
echo '$ docker compose ps --format "table {{.Service}}\t{{.Ports}}"'
docker compose ps --format "table {{.Service}}\t{{.Ports}}"
echo; echo '$ docker compose port db 5432'
docker compose port db 5432 2>&1 || true
echo; echo '$ docker network inspect telemed_backend --format "internal={{.Internal}}"'
docker network inspect telemed_backend --format 'internal={{.Internal}}'
DBIP=$(docker inspect telemed-db-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
echo; echo "# з хоста (macOS): спроба підключитися до бази"
echo "\$ nc -vz -G 2 localhost 5432 ; nc -vz -G 2 $DBIP 5432"
nc -vz -G 2 localhost 5432 2>&1 | grep -v '^$' | head -1 || true
nc -vz -G 2 $DBIP 5432 2>&1 | head -1 || true
echo "# хто слухає порти на хості: 5432 тримає локальний Homebrew postgres, а не стенд; 8080 — ssh-форвардер Colima"
echo "$ lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(5432|8080) '"
lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(5432|8080) ' | awk '{print $1, $2, $9}'
echo "\$ PGPASSWORD=*** psql -h localhost -p 5432 -U telemed -d telemed -c 'select count(*) from telemed_sessions'"
PGPASSWORD=$(grep POSTGRES_PASSWORD .env | cut -d= -f2) PGCONNECT_TIMEOUT=3 psql -h localhost -p 5432 -U telemed -d telemed -c 'select count(*) from telemed_sessions' 2>&1 | head -2
echo; echo "# з контейнера сервісу: підключення до db:5432 за іменем сервісу"
echo '$ docker compose exec web python -c "...asyncpg.connect(host=\"db\")...; SELECT version(), count(*)"'
docker compose exec -T web python -c '
import asyncio, asyncpg, os
async def m():
    c = await asyncpg.connect(host="db", user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"])
    print("connected:", (await c.fetchval("select version()"))[:40])
    print("rows in telemed_sessions:", await c.fetchval("select count(*) from telemed_sessions"))
asyncio.run(m())'
echo; echo "# база не має виходу назовні (internal-мережа)"
echo '$ docker compose exec db wget -T 3 -q -O- http://example.com'
docker compose exec -T db wget -T 3 -q -O- http://example.com 2>&1 | head -1
echo; echo '$ docker compose exec web id'
docker compose exec -T web id
