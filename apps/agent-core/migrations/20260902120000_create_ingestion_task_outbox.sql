BEGIN;

CREATE TABLE agent_core.ingestion_task_outbox (
    id UUID PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    attempt_id UUID NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ NULL,

    CONSTRAINT ingestion_task_outbox_event_unique
        UNIQUE (task_id, event_type, attempt_id)
);

COMMIT;