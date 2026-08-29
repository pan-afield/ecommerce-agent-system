from dataclasses import dataclass

from app.rag.ranking import HybridSearchResult


@dataclass(frozen=True)
class KnowledgeCitation:
    source_id: str
    chunk_id: str
    page_number: int | None
    content: str
    score: float


def build_knowledge_citations(
    results: list[HybridSearchResult],
) -> list[KnowledgeCitation]:
    """根据融合搜索结果构建知识引用列表。"""
    citations: list[KnowledgeCitation] = []

    for result in results:
        chunk = result.chunk
        citations.append(
            KnowledgeCitation(
                source_id=chunk.source_id,
                chunk_id=chunk.chunk_id,
                page_number=chunk.page_number,
                content=chunk.content,
                score=result.score,
            )
        )

    return citations


def format_knowledge_citations(
    citations: list[KnowledgeCitation],
) -> str:
    if not citations:
        return ""

    formatted_citations: list[str] = []

    for index, citation in enumerate(citations, start=1):
        page_info = f"，第 {citation.page_number} 页" if citation.page_number is not None else ""
        formatted_citations.append(
            f"[{index}] 来源：{citation.source_id}{page_info}\n"
            f"chunk_id: {citation.chunk_id}\n"
            f"{citation.content}"
        )

    return "\n\n".join(formatted_citations)
