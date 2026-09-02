BEGIN;

ALTER TABLE agent_core.ingestion_tasks
DROP CONSTRAINT ingestion_tasks_status_valid;

ALTER TABLE agent_core.ingestion_tasks
ADD CONSTRAINT ingestion_tasks_status_valid
CHECK (status IN ('PENDING', 'RUNNING'));

COMMIT;