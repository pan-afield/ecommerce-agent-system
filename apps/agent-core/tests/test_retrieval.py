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


def make_quality_chunk(chunk_id: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        source_id="rag-smoke-policy.md",
        chunk_index=0,
        content=content,
    )


@pytest.mark.parametrize("query", ["退款政策", "return policy"])
async def test_retrieve_knowledge_runs_embedding_queries_and_fusion_in_order(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
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
        embedding_model: str,
        limit: int,
        visible_visibilities: tuple[str, ...],
    ) -> list[SemanticSearchResult]:
        events.append(
            f"semantic:{embedding_model}:{query_embedding}:{limit}:{visible_visibilities}"
        )
        return [SemanticSearchResult(chunk=semantic_chunk, distance=0.1)]

    async def fake_keyword_search(
        engine: AsyncEngine,
        query: str,
        *,
        limit: int,
        visible_visibilities: tuple[str, ...],
    ) -> list[KnowledgeChunk]:
        events.append(f"keyword:{query}:{limit}:{visible_visibilities}")
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
        query=f"  {query}  ",
        embedding_model="test-model",
        limit=2,
    )

    assert results == expected
    assert embeddings.query_texts == [query]
    assert events == [
        "semantic:test-model:[0.1, 0.2, 0.3]:2:('PUBLIC',)",
        f"keyword:{query}:2:('PUBLIC',)",
        "fuse:1:1:2",
    ]


@pytest.mark.asyncio
async def test_retrieve_knowledge_does_not_fill_limit_for_unrelated_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = make_quality_chunk("a" * 64, "# 退款政策")
    unrelated_candidates = [
        SemanticSearchResult(chunk=title, distance=0.95),
    ]

    async def fake_semantic_search(*args: object, **kwargs: object) -> list[SemanticSearchResult]:
        return unrelated_candidates

    async def fake_keyword_search(*args: object, **kwargs: object) -> list[KnowledgeChunk]:
        return []

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

    results = await retrieval_module.retrieve_knowledge(
        cast(AsyncEngine, object()),
        FakeEmbeddings(),
        "苹果",
        embedding_model="test-model",
        limit=3,
    )

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_knowledge_keeps_policy_body_and_discards_heading_only_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = make_quality_chunk("b" * 64, "# 退款政策")
    body = make_quality_chunk(
        "c" * 64,
        "订单签收后七天内可以申请退款。退款申请必须由订单本人提交。",
    )

    async def fake_semantic_search(*args: object, **kwargs: object) -> list[SemanticSearchResult]:
        return [
            SemanticSearchResult(chunk=title, distance=0.32),
            SemanticSearchResult(chunk=body, distance=0.18),
        ]

    async def fake_keyword_search(*args: object, **kwargs: object) -> list[KnowledgeChunk]:
        return [body]

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

    results = await retrieval_module.retrieve_knowledge(
        cast(AsyncEngine, object()),
        FakeEmbeddings(),
        "退款政策",
        embedding_model="test-model",
        limit=3,
    )

    assert [result.chunk.content for result in results] == [body.content]
    assert results[0].chunk.content != title.content


@pytest.mark.parametrize("query", ["退款政策", "return policy"])
@pytest.mark.asyncio
async def test_retrieve_knowledge_keeps_stable_bilingual_policy_hit(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    body = make_quality_chunk(
        "d" * 64,
        "订单签收后七天内可以申请退款。 Customers may request a refund within seven days.",
    )

    async def fake_semantic_search(*args: object, **kwargs: object) -> list[SemanticSearchResult]:
        return [SemanticSearchResult(chunk=body, distance=0.2)]

    async def fake_keyword_search(*args: object, **kwargs: object) -> list[KnowledgeChunk]:
        return [body]

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

    results = await retrieval_module.retrieve_knowledge(
        cast(AsyncEngine, object()),
        FakeEmbeddings(),
        query,
        embedding_model="test-model",
        limit=3,
    )

    assert [result.chunk.content for result in results] == [body.content]


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
            embedding_model="test-model",
            limit=limit,
        )

    assert embeddings.query_texts == []
