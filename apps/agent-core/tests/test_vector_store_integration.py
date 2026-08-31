import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import create_database_engine
from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk
from app.rag.vector_store import (
    RagEmbeddingModelConflictError,
    replace_all_embedded_knowledge_chunks,
    search_similar_knowledge_chunks,
    store_embedded_knowledge_chunks,
)


def embedded_chunk(source_id: str, chunk_id: str, content: str) -> EmbeddedKnowledgeChunk:
    return EmbeddedKnowledgeChunk(
        chunk=KnowledgeChunk(
            chunk_id=chunk_id,
            source_id=source_id,
            page_number=1,
            chunk_index=0,
            content=content,
        ),
        embedding=[0.25] * 1024,
        embedding_model="test-model",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_column_uses_configured_dimensions() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT attribute.atttypmod
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS table_info
                      ON table_info.oid = attribute.attrelid
                    JOIN pg_namespace AS schema_info
                      ON schema_info.oid = table_info.relnamespace
                    WHERE schema_info.nspname = 'agent_core'
                      AND table_info.relname = 'knowledge_chunks'
                      AND attribute.attname = 'embedding'
                    """
                )
            )

        assert result.scalar_one() == 1024
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_store_round_trip_and_idempotency() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    source_id = f"integration-{uuid4()}"
    chunk_id = ("a" * 63) + "1"
    chunk = embedded_chunk(source_id, chunk_id, "集成测试退款政策。")

    try:
        assert await store_embedded_knowledge_chunks(engine, [chunk]) == 1
        assert await store_embedded_knowledge_chunks(engine, [chunk]) == 0

        results = await search_similar_knowledge_chunks(
            engine,
            [0.25] * 1024,
            embedding_model="test-model",
            limit=1,
        )

        assert results[0].chunk.chunk_id == chunk_id
        assert results[0].chunk.source_id == source_id
        assert results[0].chunk.content == "集成测试退款政策。"
        assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.knowledge_chunks "
                    "WHERE source_id = :source_id"
                ),
                {"source_id": source_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_store_rebuild_rolls_back_to_previous_model_on_failure() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    original = embedded_chunk("original-policy", "c" * 64, "原有退款政策。")
    replacement = embedded_chunk("new-policy", "d" * 64, "新版退款政策。")
    replacement = EmbeddedKnowledgeChunk(
        chunk=replacement.chunk,
        embedding=replacement.embedding,
        embedding_model="new-model",
    )
    invalid = embedded_chunk("new-policy", "e" * 64, "   ")
    invalid = EmbeddedKnowledgeChunk(
        chunk=invalid.chunk,
        embedding=invalid.embedding,
        embedding_model="new-model",
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM agent_core.knowledge_chunks"))

        assert await store_embedded_knowledge_chunks(engine, [original]) == 1

        with pytest.raises(IntegrityError):
            await replace_all_embedded_knowledge_chunks(engine, [replacement, invalid])

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT source_id, content, embedding_model
                    FROM agent_core.knowledge_chunks
                    ORDER BY chunk_id
                    """
                )
            )
            rows = [tuple(row) for row in result.all()]

        assert rows == [("original-policy", "原有退款政策。", "test-model")]
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM agent_core.knowledge_chunks"))
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_ingestions_cannot_mix_embedding_models() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    first = embedded_chunk("model-a-policy", "f" * 64, "模型 A 的政策。")
    second = embedded_chunk("model-b-policy", "0" * 64, "Model B policy.")
    second = EmbeddedKnowledgeChunk(
        chunk=second.chunk,
        embedding=second.embedding,
        embedding_model="other-model",
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM agent_core.knowledge_chunks"))

        results = await asyncio.gather(
            store_embedded_knowledge_chunks(engine, [first]),
            store_embedded_knowledge_chunks(engine, [second]),
            return_exceptions=True,
        )

        successes = [result for result in results if isinstance(result, int)]
        conflicts = [
            result
            for result in results
            if isinstance(result, RagEmbeddingModelConflictError)
        ]
        assert successes == [1]
        assert len(conflicts) == 1

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT DISTINCT embedding_model
                    FROM agent_core.knowledge_chunks
                    """
                )
            )
            stored_models = set(result.scalars().all())

        assert stored_models in ({"test-model"}, {"other-model"})
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM agent_core.knowledge_chunks"))
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_store_batch_rolls_back_on_constraint_failure() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    source_id = f"integration-rollback-{uuid4()}"
    first = embedded_chunk(source_id, ("b" * 63) + "1", "第一条集成测试政策。")
    invalid = embedded_chunk(source_id, ("b" * 63) + "2", "   ")

    try:
        with pytest.raises(IntegrityError):
            await store_embedded_knowledge_chunks(engine, [first, invalid])

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT count(*) FROM agent_core.knowledge_chunks "
                    "WHERE source_id = :source_id"
                ),
                {"source_id": source_id},
            )
            assert result.scalar_one() == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.knowledge_chunks "
                    "WHERE source_id = :source_id"
                ),
                {"source_id": source_id},
            )
        await engine.dispose()
