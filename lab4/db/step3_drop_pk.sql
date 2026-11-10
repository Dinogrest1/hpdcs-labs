-- Крок 3: первинний ключ task_results тимчасово замінено звичайним індексом,
-- щоб другий результат тієї самої задачі записався і дубль було видно в таблиці.
TRUNCATE tasks CASCADE;
ALTER TABLE task_results DROP CONSTRAINT IF EXISTS task_results_pkey;
CREATE INDEX IF NOT EXISTS idx_task_results_task_id ON task_results (task_id);
