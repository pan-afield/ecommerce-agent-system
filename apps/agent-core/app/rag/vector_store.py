from dataclasses import dataclass

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EMBEDDING_DIMENSIONS, EmbeddedKnowledgeChunk


class RagVectorDimensionError(RuntimeError):
    """数据库向量列维度与应用配置不一致。"""


class RagEmbeddingModelConflictError(RuntimeError):
    """知识库中存在由其他 embedding 模型生成的向量。"""


SELECT_EMBEDDING_DIMENSION_STATEMENT = text(
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
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped
    """
)

DELETE_ALL_KNOWLEDGE_CHUNKS_STATEMENT = text("DELETE FROM agent_core.knowledge_chunks")

LOCK_EMBEDDING_MODEL_STATEMENT = text(
    """
    SELECT pg_advisory_xact_lock(
        hashtext('agent_core.knowledge_chunks.embedding_model')
    )
    """
)

SELECT_EMBEDDING_MODELS_STATEMENT = text(
    """
    SELECT DISTINCT embedding_model
    FROM agent_core.knowledge_chunks
    WHERE embedding IS NOT NULL
    """
)

STORE_EMBEDDED_CHUNK_STATEMENT = text(
    """
    INSERT INTO agent_core.knowledge_chunks (
        chunk_id,
        source_id,
        page_number,
        chunk_index,
        content,
        embedding,
        embedding_model
    )
    VALUES (
        :chunk_id,
        :source_id,
        :page_number,
        :chunk_index,
        :content,
        :embedding,
        :embedding_model
    )
    ON CONFLICT (chunk_id) DO NOTHING
    RETURNING chunk_id
    """
).bindparams(bindparam("embedding", type_=Vector(EMBEDDING_DIMENSIONS)))


async def store_embedded_knowledge_chunks(
    engine: AsyncEngine,
    embedded_chunks: list[EmbeddedKnowledgeChunk],
) -> int:
    """批量插入向量化知识块，并返回实际新增的知识块数量。"""
    if not embedded_chunks:
        return 0

    incoming_models = {embedded_chunk.embedding_model for embedded_chunk in embedded_chunks}
    if len(incoming_models) != 1:
        raise RagEmbeddingModelConflictError(
            "one ingestion batch must use exactly one embedding model"
        )

    incoming_model = next(iter(incoming_models))

    count = 0
    async with engine.begin() as connection:
        # advisory lock 让不同导入任务在检查模型和写入之间不会互相穿插。
        await connection.execute(LOCK_EMBEDDING_MODEL_STATEMENT)

        result = await connection.execute(SELECT_EMBEDDING_MODELS_STATEMENT)
        stored_models = set(result.scalars().all())

        if stored_models and stored_models != {incoming_model}:
            raise RagEmbeddingModelConflictError(
                "stored embeddings use a different embedding model"
            )
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
                    "embedding_model": embedded_chunk.embedding_model,
                },
            )
            count += 1 if result.scalar_one_or_none() is not None else 0

    return count


async def replace_all_embedded_knowledge_chunks(
    engine: AsyncEngine,
    embedded_chunks: list[EmbeddedKnowledgeChunk],
) -> int:
    """在一个事务中删除并重建全部向量，失败时由事务回滚删除。"""
    if not embedded_chunks:
        raise ValueError("replacement chunks must not be empty")

    incoming_models = {embedded_chunk.embedding_model for embedded_chunk in embedded_chunks}
    if len(incoming_models) != 1:
        raise RagEmbeddingModelConflictError(
            "one replacement batch must use exactly one embedding model"
        )

    count = 0
    async with engine.begin() as connection:
        # 删除和所有插入共享一个事务；任一步失败都会回滚整次重建。
        await connection.execute(LOCK_EMBEDDING_MODEL_STATEMENT)
        await connection.execute(DELETE_ALL_KNOWLEDGE_CHUNKS_STATEMENT)

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
                    "embedding_model": embedded_chunk.embedding_model,
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
    embedding_model: str,
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
        AND embedding_model = :embedding_model
        ORDER BY distance ASC, chunk_id ASC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("query_embedding", type_=Vector(EMBEDDING_DIMENSIONS)),
    )

    async with engine.connect() as connection:
        dimension_result = await connection.execute(SELECT_EMBEDDING_DIMENSION_STATEMENT)
        stored_dimensions = dimension_result.scalar_one_or_none()

        if stored_dimensions != EMBEDDING_DIMENSIONS:
            raise RagVectorDimensionError(
                "knowledge embedding column dimension mismatch: "
                f"expected {EMBEDDING_DIMENSIONS}, got {stored_dimensions}"
            )

        result = await connection.execute(
            search_statement,
            {
                "query_embedding": query_embedding,
                "limit": limit,
                "embedding_model": embedding_model,
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
    """按大小写不敏感的内容包含关系检索，作为混合检索的关键词分支。"""
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
