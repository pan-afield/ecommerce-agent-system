from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260825090000_create_knowledge_chunks.sql"
)
LOCAL_EMBEDDING_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260831090000_migrate_local_embeddings.sql"
)


def test_knowledge_chunks_migration_declares_pgvector_column() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql
    assert "embedding vector(1024)" in sql


def test_knowledge_chunks_migration_does_not_create_vector_search_index_yet() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "using hnsw" not in sql
    assert "using ivfflat" not in sql


def test_local_embedding_migration_updates_dimension_and_records_model() -> None:
    sql = LOCAL_EMBEDDING_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "ALTER COLUMN embedding TYPE vector(1024)" in sql
    assert "USING embedding::vector(1024)" in sql
    assert "ADD COLUMN embedding_model VARCHAR(100) NOT NULL" in sql


def test_local_embedding_migration_does_not_assign_a_fake_model_default() -> None:
    sql = LOCAL_EMBEDDING_MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "embedding_model varchar(100) default" not in sql
