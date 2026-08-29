from dataclasses import dataclass

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EMBEDDING_DIMENSIONS, EmbeddedKnowledgeChunk

STORE_EMBEDDED_CHUNK_STATEMENT = text(
    """
    INSERT INTO agent_core.knowledge_chunks (
        chunk_id,
        source_id,
        page_number,
        chunk_index,
        content,
        embedding
    )
    VALUES (
        :chunk_id,
        :source_id,
        :page_number,
        :chunk_index,
        :content,
        :embedding
    )
    ON CONFLICT (chunk_id) DO NOTHING
    RETURNING chunk_id
    """
).bindparams(bindparam("embedding", type_=Vector(1536)))


async def try_store_embedded_knowledge_chunk(
    engine: AsyncEngine,
    embedded_chunk: EmbeddedKnowledgeChunk,
) -> bool:
    """尝试插入一个向量化知识块，重复的 ``chunk_id`` 不会重复写入。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            STORE_EMBEDDED_CHUNK_STATEMENT,
            {
                "chunk_id": embedded_chunk.chunk.chunk_id,
                "source_id": embedded_chunk.chunk.source_id,
                "page_number": embedded_chunk.chunk.page_number,
                "chunk_index": embedded_chunk.chunk.chunk_index,
                "content": embedded_chunk.chunk.content,
                "embedding": embedded_chunk.embedding,
            },
        )
    return result.scalar_one_or_none() is not None


async def store_embedded_knowledge_chunks(
    engine: AsyncEngine,
    embedded_chunks: list[EmbeddedKnowledgeChunk],
) -> int:
    """批量插入向量化知识块，并返回实际新增的知识块数量。"""
    if not embedded_chunks:
        return 0

    count = 0
    async with engine.begin() as connection:
        for embedded_chunk in embedded_chunks:
            result = await connection.execute(
                STORE_EMBEDDED_CHUNK_STATEMENT,
                {
                    "chunk_id": embedded_chunk.chunk.chunk_id,
                    "source_id": embedded_chunk.chunk.source_id,
                    "page_number": embedded_chunk.chunk.page_number,
                    "chunk_index": embedded_chunk.chunk.chunk_index,
                    "content": embedded_chunk.chunk.content,
                    "embedding": embedded_chunk.embedding,
                },
            )
            count += 1 if result.scalar_one_or_none() is not None else 0

    return count


@dataclass(frozen=True)
class SemanticSearchResult:
    """表示一次语义搜索命中的知识块及其向量距离。"""

    chunk: KnowledgeChunk
    distance: float


async def search_similar_knowledge_chunks(
    engine: AsyncEngine,
    query_embedding: list[float],
    *,
    limit: int = 3,
) -> list[SemanticSearchResult]:
    """按向量距离升序搜索知识块；``limit`` 必须大于零。"""
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    if len(query_embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"embedding dimension must be {EMBEDDING_DIMENSIONS}")
    search_statement = text(
        """
        SELECT
            chunk_id,
            source_id,
            page_number,
            chunk_index,
            content,
            embedding <=> :query_embedding AS distance
        FROM agent_core.knowledge_chunks
        WHERE embedding IS NOT NULL
        ORDER BY distance ASC, chunk_id ASC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("query_embedding", type_=Vector(1536)),
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            search_statement,
            {
                "query_embedding": query_embedding,
                "limit": limit,
            },
        )
        rows = result.mappings().all()
        return [
            SemanticSearchResult(
                chunk=KnowledgeChunk(
                    chunk_id=row["chunk_id"],
                    source_id=row["source_id"],
                    page_number=row["page_number"],
                    chunk_index=row["chunk_index"],
                    content=row["content"],
                ),
                distance=float(row["distance"]),
            )
            for row in rows
        ]


async def search_knowledge_chunks_by_keyword(
    engine: AsyncEngine,
    query: str,
    *,
    limit: int = 3,
) -> list[KnowledgeChunk]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty or whitespace")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    search_statement = text(
        """
        SELECT
            chunk_id,
            source_id,
            page_number,
            chunk_index,
            content
        FROM agent_core.knowledge_chunks
        WHERE strpos(lower(content), lower(:query)) > 0
        ORDER BY
            strpos(lower(content), lower(:query)) ASC,
            char_length(content) ASC,
            chunk_id ASC
        LIMIT :limit
        """
    )
    async with engine.connect() as connection:
        result = await connection.execute(
            search_statement,
            {
                "query": normalized_query,
                "limit": limit,
            },
        )
        rows = result.mappings().all()
        return [
            KnowledgeChunk(
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                page_number=row["page_number"],
                chunk_index=row["chunk_index"],
                content=row["content"],
            )
            for row in rows
        ]
