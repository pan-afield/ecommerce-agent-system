import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import create_database_engine
from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk
from app.rag.vector_store import (
    search_similar_knowledge_chunks,
    store_embedded_knowledge_chunks,
    try_store_embedded_knowledge_chunk,
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
        embedding=[0.25] * 1536,
    )


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
        assert await try_store_embedded_knowledge_chunk(engine, chunk) is True
        assert await try_store_embedded_knowledge_chunk(engine, chunk) is False

        results = await search_similar_knowledge_chunks(
            engine,
            [0.25] * 1536,
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
