BEGIN;

ALTER TABLE agent_core.ingestion_tasks
ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE agent_core.ingestion_tasks
ADD CONSTRAINT ingestion_tasks_retry_count_non_negative
CHECK (retry_count >= 0);

COMMIT;