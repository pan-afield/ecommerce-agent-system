CREATE SCHEMA IF NOT EXISTS agent_core;

CREATE TABLE agent_core.ingestion_tasks (
    id VARCHAR(64) PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ingestion_tasks_id_not_blank
        CHECK (btrim(id) <> ''),
    CONSTRAINT ingestion_tasks_status_valid
        CHECK (status IN ('PENDING'))
);