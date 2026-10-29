-- Схема накочується автоматично: файл змонтовано в /docker-entrypoint-initdb.d,
-- postgres виконує його один раз при ініціалізації порожнього тому.

CREATE TABLE IF NOT EXISTS telemed_sessions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_code  VARCHAR(32)  NOT NULL CHECK (patient_code ~ '^[A-Za-z0-9-]{4,32}$'),
    doctor        VARCHAR(100) NOT NULL CHECK (length(btrim(doctor)) > 0),
    channel       VARCHAR(16)  NOT NULL CHECK (channel IN ('video', 'audio', 'chat')),
    started_at    TIMESTAMPTZ  NOT NULL,
    duration_min  SMALLINT     NOT NULL CHECK (duration_min > 0 AND duration_min <= 90),
    status        VARCHAR(16)  NOT NULL CHECK (status IN ('scheduled', 'in_progress', 'completed', 'cancelled')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Фільтр переліку йде за каналом, сортування за id: складений індекс
-- обслуговує і WHERE channel = $1, і ORDER BY id LIMIT/OFFSET без сортування.
CREATE INDEX IF NOT EXISTS idx_telemed_sessions_channel_id
    ON telemed_sessions (channel, id);
