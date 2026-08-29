from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260825090000_create_knowledge_chunks.sql"
)


def test_knowledge_chunks_migration_declares_pgvector_column() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql
    assert "embedding vector(1536)" in sql


def test_knowledge_chunks_migration_does_not_create_vector_search_index_yet() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "using hnsw" not in sql
    assert "using ivfflat" not in sql
