CREATE SCHEMA IF NOT EXISTS agent_core;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_core.knowledge_chunks (
    chunk_id CHAR(64) PRIMARY KEY,
    source_id VARCHAR(255) NOT NULL,
    page_number INTEGER,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    embedding vector(1024),

    CONSTRAINT knowledge_chunks_page_number_positive
        CHECK (page_number IS NULL OR page_number > 0),
    CONSTRAINT knowledge_chunks_chunk_index_non_negative
        CHECK (chunk_index >= 0),
    CONSTRAINT knowledge_chunks_content_not_blank
        CHECK (length(btrim(content)) > 0)
);

CREATE INDEX knowledge_chunks_source_location_idx
ON agent_core.knowledge_chunks (
    source_id,
    page_number,
    chunk_index
);