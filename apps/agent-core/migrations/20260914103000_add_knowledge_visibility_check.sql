ALTER TABLE agent_core.knowledge_chunks
ADD CONSTRAINT knowledge_chunks_visibility_allowed
CHECK (visibility IN ('PUBLIC', 'SUPPORT', 'ADMIN'));