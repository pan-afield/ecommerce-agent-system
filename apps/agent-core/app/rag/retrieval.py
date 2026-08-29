from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.ranking import HybridSearchResult, fuse_search_results
from app.rag.vector_store import search_knowledge_chunks_by_keyword, search_similar_knowledge_chunks


# 混合检索
async def retrieve_knowledge(
    engine: AsyncEngine,
    embeddings: Embeddings,
    query: str,
    *,
    limit: int = 3,
) -> list[HybridSearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty or whitespace")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    # 异步生成查询向量
    query_embedding = await embeddings.aembed_query(normalized_query)

    # 异步执行语义检索和关键词检索
    semantic_results = await search_similar_knowledge_chunks(
        engine,
        query_embedding,
        limit=limit,
    )

    # 异步执行关键词检索
    keyword_results = await search_knowledge_chunks_by_keyword(
        engine,
        normalized_query,
        limit=limit,
    )

    # 将两份检索结果融合为最终排名
    return fuse_search_results(
        semantic_results,
        keyword_results,
        limit=limit,
    )
