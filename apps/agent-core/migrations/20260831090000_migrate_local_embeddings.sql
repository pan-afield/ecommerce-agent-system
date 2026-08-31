BEGIN;

ALTER TABLE agent_core.knowledge_chunks
ALTER COLUMN embedding TYPE vector(1024)
USING embedding::vector(1024);

ALTER TABLE agent_core.knowledge_chunks
ADD COLUMN embedding_model VARCHAR(100) NOT NULL;

COMMIT;