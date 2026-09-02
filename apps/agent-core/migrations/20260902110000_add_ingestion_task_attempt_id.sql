BEGIN;

ALTER TABLE agent_core.ingestion_tasks
ADD COLUMN attempt_id UUID;

COMMIT;