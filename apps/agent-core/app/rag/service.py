from dataclasses import dataclass

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.citations import (
    KnowledgeCitation,
    build_knowledge_citations,
    format_knowledge_citations,
)
from app.rag.retrieval import retrieve_knowledge


@dataclass(frozen=True)
class RagContext:
    query: str
    citations: list[KnowledgeCitation]


async def build_rag_context(
    engine: AsyncEngine,
    embeddings: Embeddings,
    query: str,
    *,
    limit: int = 3,
) -> RagContext:
    results = await retrieve_knowledge(
        engine,
        embeddings,
        query,
        limit=limit,
    )

    citations = build_knowledge_citations(results)
    return RagContext(query=query.strip(), citations=citations)


def build_rag_prompt(
    query: str,
    citations: list[KnowledgeCitation],
) -> str:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty or whitespace")

    formatted_citations = format_knowledge_citations(citations)

    if formatted_citations:
        return (
            "请根据以下企业知识库证据回答用户问题。\n"
            "如果证据不足，请明确说明无法从知识库确认，不要编造。\n\n"
            f"知识库证据：\n{formatted_citations}\n\n"
            f"用户问题：\n{normalized_query}"
        )

    return f"当前没有检索到可用的企业知识库证据。\n\n用户问题：\n{normalized_query}"
