# ЛР 3. Сервіс у Kubernetes з автомасштабуванням і без нього (варіант 26)

Сервіс `/telemed-sessions` з ЛР 2 (метрики Prometheus, `/livez`, `X-Pod`, коректна зупинка) у локальному кластері kind.

## Параметри варіанта 26

| Параметр | Значення |
|---|---|
| Кластер | kind: 1 control-plane (мітка `role=infra`) + 2 worker |
| R = 3 + (26 mod 3) | 5 |
| requests / limits CPU | 250m / 500m, `limits.memory: 512Mi` |
| HPA: цільове використання CPU | 70 % |
| Рівень стрибка | min(2 · X(1), 0.9 · X(5)) |
| `DB_POOL_SIZE` (з ЛР 2) | 10 |
| Додаткова вимога | Д1: поди web рівномірно по двох worker через `topologySpreadConstraints` |

## Первинний проєкт (зафіксовано до маніфестів)

**Розміщення.** На control-plane (`role=infra`, taint `control-plane:NoSchedule`) — база, Prometheus, kube-state-metrics, postgres-exporter, metrics-server; усі вони мають `nodeSelector: role=infra` і toleration на taint. Поди web не мають toleration, тому стають лише на два worker; `topologySpreadConstraints` (`maxSkew: 1`, `topologyKey: kubernetes.io/hostname`, `DoNotSchedule`) розкладає їх порівну.

**Обʼєкти.**

| Файл | Обʼєкти |
|---|---|
| `k8s/kind-config.yaml` | кластер `telemed`; `extraPortMappings` control-plane: 30080 → 8080 (web), 30090 → 9090 (Prometheus) |
| `k8s/db.yaml` | headless Service `db`, StatefulSet `db` (postgres:16-alpine, `max_connections=100`, PVC 1 Гі) |
| `k8s/migrate-job.yaml` | Job `migrate`: чекає `pg_isready`, виконує `db/schema.sql` з ConfigMap `schema` |
| `k8s/web.yaml` | ConfigMap `web-config` (DB_HOST, DB_POOL_SIZE=10, …), Deployment `web` **без `replicas`**, Service `web` NodePort 30080 |
| `k8s/podmonitor.yaml` | PodMonitor для подів web, інтервал 5 с |
| `k8s/hpa.yaml` | HPA за CPU 70 %, 1…5, `scaleDown.stabilizationWindowSeconds: 30` |
| `helm/*.yaml` | значення для kube-prometheus-stack, prometheus-postgres-exporter, metrics-server |

Secret `db-secret` команда розгортання створює з `.env` (`kubectl create secret … --from-literal`), у репозиторії і маніфестах пароля немає.

**Генератор.** Той самий Go-генератор з ЛР 2 плюс модель зʼєднань: 8 · R = 40 постійних зʼєднань, кожне закривається після 100 запитів і замінюється новим; сходинки 30 с, середні за останні 20 с. Генератор працює контейнером у мережі хоста ВМ Docker і звертається на `localhost:8080`, тобто проходить перенаправлення порту Docker і kube-proxy, як вимагає умова.

## Запуск

```bash
cd lab3
cp .env.example .env              # задати POSTGRES_PASSWORD
./scripts/deploy.sh               # кластер, образ, база, міграція, web, моніторинг
./scripts/series.sh               # прогони кроків 7, 10, 11
./scripts/destroy.sh              # повне видалення кластера
```

**Пауза і продовження.** `./scripts/pause.sh` — серія зупиниться після поточного прогону (кожен завершений прогін зберігається в `results/` і комітиться локально). `./scripts/series.sh` — продовження з наступного прогону. Після вимкнення компʼютера: `./scripts/deploy.sh` (створить кластер, якщо його немає), потім `./scripts/series.sh` (засіє базу, якщо вона порожня, і пропустить уже зняті прогони).
