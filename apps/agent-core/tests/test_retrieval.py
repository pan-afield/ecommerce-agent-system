from typing import cast

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

import app.rag.retrieval as retrieval_module
from app.rag.chunking import KnowledgeChunk
from app.rag.ranking import HybridSearchResult
from app.rag.vector_store import SemanticSearchResult


class FakeEmbeddings(Embeddings):
    def __init__(self) -> None:
        self.query_texts: list[str] = []

    async def aembed_query(self, text: str) -> list[float]:
        self.query_texts.append(text)
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("document embedding is not part of retrieval")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("synchronous embedding must not be used")


def make_chunk(chunk_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        source_id="refund-policy-v1",
        chunk_index=0,
        content="退款政策内容。",
    )


async def test_retrieve_knowledge_runs_embedding_queries_and_fusion_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    embeddings = FakeEmbeddings()
    semantic_chunk = make_chunk("a" * 64)
    keyword_chunk = make_chunk("b" * 64)
    expected = [HybridSearchResult(chunk=semantic_chunk, score=0.5)]

    async def fake_semantic_search(
        engine: AsyncEngine,
        query_embedding: list[float],
        *,
        limit: int,
    ) -> list[SemanticSearchResult]:
        events.append(f"semantic:{query_embedding}:{limit}")
        return [SemanticSearchResult(chunk=semantic_chunk, distance=0.1)]

    async def fake_keyword_search(
        engine: AsyncEngine,
        query: str,
        *,
        limit: int,
    ) -> list[KnowledgeChunk]:
        events.append(f"keyword:{query}:{limit}")
        return [keyword_chunk]

    def fake_fuse(
        semantic_results: list[SemanticSearchResult],
        keyword_results: list[KnowledgeChunk],
        *,
        limit: int,
    ) -> list[HybridSearchResult]:
        events.append(f"fuse:{len(semantic_results)}:{len(keyword_results)}:{limit}")
        return expected

    monkeypatch.setattr(
        retrieval_module,
        "search_similar_knowledge_chunks",
        fake_semantic_search,
    )
    monkeypatch.setattr(
        retrieval_module,
        "search_knowledge_chunks_by_keyword",
        fake_keyword_search,
    )
    monkeypatch.setattr(retrieval_module, "fuse_search_results", fake_fuse)

    results = await retrieval_module.retrieve_knowledge(
        engine=cast(AsyncEngine, object()),
        embeddings=embeddings,
        query="  退款政策  ",
        limit=2,
    )

    assert results == expected
    assert embeddings.query_texts == ["退款政策"]
    assert events == [
        "semantic:[0.1, 0.2, 0.3]:2",
        "keyword:退款政策:2",
        "fuse:1:1:2",
    ]


@pytest.mark.parametrize(
    ("query", "limit", "message"),
    [
        ("   ", 3, "query must not be empty or whitespace"),
        ("退款", 0, "limit must be greater than zero"),
    ],
)
async def test_retrieve_knowledge_rejects_invalid_input_before_embedding(
    query: str,
    limit: int,
    message: str,
) -> None:
    embeddings = FakeEmbeddings()

    with pytest.raises(ValueError, match=message):
        await retrieval_module.retrieve_knowledge(
            engine=cast(AsyncEngine, object()),
            embeddings=embeddings,
            query=query,
            limit=limit,
        )

    assert embeddings.query_texts == []
