BEGIN;

ALTER TABLE agent_core.ingestion_tasks
ADD COLUMN started_at TIMESTAMPTZ(3);

COMMIT;