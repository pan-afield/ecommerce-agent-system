BEGIN;

ALTER TABLE agent_core.ingestion_task_outbox
ADD COLUMN publish_attempt_id UUID,
ADD COLUMN publishing_at TIMESTAMPTZ;

COMMIT;