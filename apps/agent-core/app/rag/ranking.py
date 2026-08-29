from dataclasses import dataclass

from app.rag.chunking import KnowledgeChunk
from app.rag.vector_store import SemanticSearchResult

RRF_RANK_CONSTANT = 60


@dataclass(frozen=True)
class HybridSearchResult:
    """表示融合搜索结果及其综合相关性分数。"""

    chunk: KnowledgeChunk
    score: float


def fuse_search_results(
    semantic_results: list[SemanticSearchResult],
    keyword_results: list[KnowledgeChunk],
    *,
    limit: int = 3,
) -> list[HybridSearchResult]:
    """把语义检索和关键词检索的两份排名合并成一份稳定的最终排名。"""

    # 第一步：校验返回数量，避免产生无意义或不符合预期的结果。
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    # 第二步：按知识块 ID 收集唯一知识块，并为每个知识块初始化累计分数。
    chunks: dict[str, KnowledgeChunk] = {}
    scores: dict[str, float] = {}

    # 第三步：遍历语义检索结果，按照其排名累加 RRF 分数。
    for rank, result in enumerate(semantic_results, start=1):
        chunk = result.chunk
        chunks.setdefault(chunk.chunk_id, chunk)
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (RRF_RANK_CONSTANT + rank)

    # 第四步：遍历关键词检索结果，继续累加另一条检索路径的 RRF 分数。
    for rank, chunk in enumerate(keyword_results, start=1):
        chunks.setdefault(chunk.chunk_id, chunk)
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (RRF_RANK_CONSTANT + rank)

    # 第五步：按综合分数降序排列；分数相同时按 ID 排序以保证结果稳定。
    ranked_chunk_ids = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], chunk_id),
    )

    # 第六步：截取前 limit 个知识块，并组装为统一的融合搜索结果。
    return [
        HybridSearchResult(
            chunk=chunks[chunk_id],
            score=scores[chunk_id],
        )
        for chunk_id in ranked_chunk_ids[:limit]
    ]
