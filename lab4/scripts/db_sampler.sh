# Виконується в поді бази (kubectl exec db-0 -- sh -c "$(cat scripts/db_sampler.sh)"):
# раз на секунду один рядок CSV, поки існує /tmp/sampler.run.
#   unix, рядки pg_stat_activity з wait_event_type = 'Lock', рядки pg_locks з granted = false,
#   лічильник транзакцій бази (commit + rollback), задачі за статусами.
touch /tmp/sampler.run
while [ -f /tmp/sampler.run ]; do
  psql -U telemed -d telemed -X -tA -F, -c "
    SELECT round(extract(epoch FROM clock_timestamp())::numeric, 3),
           (SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'),
           (SELECT count(*) FROM pg_locks WHERE NOT granted),
           (SELECT xact_commit + xact_rollback FROM pg_stat_database WHERE datname = 'telemed'),
           (SELECT count(*) FROM tasks WHERE status = 'queued'),
           (SELECT count(*) FROM tasks WHERE status = 'running'),
           (SELECT count(*) FROM tasks WHERE status = 'done'),
           (SELECT count(*) FROM tasks WHERE status = 'failed')"
  sleep 1
done
