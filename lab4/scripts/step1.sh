#!/usr/bin/env bash
# Крок 1: міграція tasks і task_results, ендпоінти задач, вимірювачі tasks_queued і tasks_running.
. "$(dirname "$0")/lib.sh"
workers 0 >/dev/null; clean
echo '$ kubectl get job migrate migrate-tasks'; kubectl get job migrate migrate-tasks
echo '$ kubectl logs job/migrate-tasks'; kubectl logs job/migrate-tasks
echo '$ psql: \d tasks'; kubectl exec db-0 -- psql -U telemed -d telemed -X -c '\d tasks'
echo '$ psql: \d task_results'; kubectl exec db-0 -- psql -U telemed -d telemed -X -c '\d task_results'
./scripts/vmsh.sh '
B=http://localhost:8080
show() { echo "$1"; grep -i -E "^HTTP/|^x-pod" /tmp/h | tr -d "\r"; cat /tmp/b; echo; echo; }
curl -s -D /tmp/h -o /tmp/b -X POST $B/telemed-sessions/7/tasks;        show "> POST $B/telemed-sessions/7/tasks"
ID=$(sed -E "s/^\{\"id\":([0-9]+).*/\1/" /tmp/b)
curl -s -D /tmp/h -o /tmp/b -X POST $B/telemed-sessions/999999/tasks;   show "> POST $B/telemed-sessions/999999/tasks"
curl -s -D /tmp/h -o /tmp/b -X POST $B/telemed-sessions/8/tasks -H "Content-Type: application/json" -d "{\"priority\":3}"
show "> POST $B/telemed-sessions/8/tasks  {\"priority\":3}"
curl -s -D /tmp/h -o /tmp/b -X POST $B/telemed-sessions/8/tasks -H "Content-Type: application/json" -d "{\"priority\":7}"
show "> POST $B/telemed-sessions/8/tasks  {\"priority\":7}"
curl -s -D /tmp/h -o /tmp/b $B/tasks/$ID;                               show "> GET $B/tasks/$ID"
curl -s -D /tmp/h -o /tmp/b $B/tasks/999999999;                         show "> GET $B/tasks/999999999"
curl -s -D /tmp/h -o /tmp/b "$B/tasks?status=queued&limit=1&offset=1";  show "> GET $B/tasks?status=queued&limit=1&offset=1"
curl -s -D /tmp/h -o /tmp/b "$B/tasks?status=bogus";                    show "> GET $B/tasks?status=bogus"
sleep 6
echo "> GET $B/metrics | grep ^tasks_   # через 6 с після створення: вимірювачі оновлюються раз на 5 с"
curl -s $B/metrics | grep "^tasks_"
'
