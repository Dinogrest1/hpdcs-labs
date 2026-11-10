-- Крок 4: первинний ключ task_results повернуто (таблиці перед цим очищено від дублів кроку 3).
TRUNCATE tasks CASCADE;
DROP INDEX IF EXISTS idx_task_results_task_id;
ALTER TABLE task_results DROP CONSTRAINT IF EXISTS task_results_pkey;
ALTER TABLE task_results ADD CONSTRAINT task_results_pkey PRIMARY KEY (task_id);
