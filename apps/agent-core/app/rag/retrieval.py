from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.ranking import HybridSearchResult, fuse_search_results
from app.rag.vector_store import search_knowledge_chunks_by_keyword, search_similar_knowledge_chunks

MAX_SEMANTIC_DISTANCE = 0.30


def is_heading_only(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith("# ")


# 混合检索
async def retrieve_knowledge(
    engine: AsyncEngine,
    embeddings: Embeddings,
    query: str,
    *,
    limit: int = 3,
    embedding_model: str,
    visible_visibilities: tuple[str, ...] = ("PUBLIC",),
) -> list[HybridSearchResult]:
    """执行语义和关键词两条检索分支，再融合为稳定排序结果。"""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty or whitespace")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    # 异步生成查询向量
    query_embedding = await embeddings.aembed_query(normalized_query)

    # 异步执行语义检索
    semantic_results = await search_similar_knowledge_chunks(
        engine,
        query_embedding,
        limit=limit,
        embedding_model=embedding_model,
        visible_visibilities=visible_visibilities,
    )

    # 异步执行关键词检索
    keyword_results = await search_knowledge_chunks_by_keyword(
        engine,
        normalized_query,
        limit=limit,
        visible_visibilities=visible_visibilities,
    )

    qualified_semantic_results = [
        result
        for result in semantic_results
        if result.distance <= MAX_SEMANTIC_DISTANCE and not is_heading_only(result.chunk.content)
    ]

    qualified_keyword_results = [
        chunk for chunk in keyword_results if not is_heading_only(chunk.content)
    ]

    # 将两份检索结果融合为最终排名
    return fuse_search_results(
        qualified_semantic_results,
        qualified_keyword_results,
        limit=limit,
    )
