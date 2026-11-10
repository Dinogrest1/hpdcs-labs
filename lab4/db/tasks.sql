-- Міграція ЛР 4: черга задач обробки і таблиця результатів.
-- Накочується Job migrate-tasks (k8s/migrate-tasks-job.yaml); IF NOT EXISTS робить повторний запуск безпечним.

CREATE TABLE IF NOT EXISTS tasks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id     BIGINT       NOT NULL REFERENCES telemed_sessions (id) ON DELETE CASCADE,
    status        VARCHAR(16)  NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'done', 'failed')),
    seed          BIGINT       NOT NULL,
    -- Д1: пріоритет 1…3, більший береться раніше; у кроках 1–9 усі задачі мають 2.
    priority      SMALLINT     NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    attempts      INTEGER      NOT NULL DEFAULT 0,
    max_attempts  INTEGER      NOT NULL DEFAULT 3,
    token         BIGINT       NOT NULL DEFAULT 0,
    lease_until   TIMESTAMPTZ,
    worker_id     TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);

-- Захоплення шукає найстаршу задачу в статусі queued: WHERE status = 'queued' ORDER BY created_at LIMIT 1.
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks (status, created_at);
-- Д1: той самий пошук у порядку priority DESC, created_at.
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority_created ON tasks (status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS task_results (
    task_id     BIGINT      NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    token       BIGINT      NOT NULL,
    worker_id   TEXT        NOT NULL,
    result      JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT task_results_pkey PRIMARY KEY (task_id)
);
