from typing import cast

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

import app.rag.service as service_module
from app.rag.chunking import KnowledgeChunk
from app.rag.citations import KnowledgeCitation
from app.rag.ranking import HybridSearchResult


class UnusedEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("the service test should replace retrieval")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the service test should replace retrieval")


async def test_build_rag_context_cleans_query_and_transfers_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    embeddings = UnusedEmbeddings()
    chunk = KnowledgeChunk(
        chunk_id="a" * 64,
        source_id="refund-policy-v1",
        chunk_index=0,
        content="退款需要订单本人提交。",
        page_number=2,
    )
    retrieval_result = [HybridSearchResult(chunk=chunk, score=0.2)]
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="a" * 64,
        page_number=2,
        content="退款需要订单本人提交。",
        score=0.2,
    )
    calls: list[tuple[object, object, str, int]] = []

    async def fake_retrieve(
        received_engine: AsyncEngine,
        received_embeddings: Embeddings,
        received_query: str,
        *,
        limit: int,
    ) -> list[HybridSearchResult]:
        calls.append((received_engine, received_embeddings, received_query, limit))
        return retrieval_result

    def fake_build_citations(
        results: list[HybridSearchResult],
    ) -> list[KnowledgeCitation]:
        assert results is retrieval_result
        return [citation]

    monkeypatch.setattr(service_module, "retrieve_knowledge", fake_retrieve)
    monkeypatch.setattr(service_module, "build_knowledge_citations", fake_build_citations)

    context = await service_module.build_rag_context(
        engine,
        embeddings,
        "  退款政策  ",
        limit=5,
    )

    assert context.query == "退款政策"
    assert context.citations == [citation]
    assert calls == [(engine, embeddings, "  退款政策  ", 5)]


async def test_build_rag_context_propagates_retrieval_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_retrieve(*args: object, **kwargs: object) -> list[HybridSearchResult]:
        raise ValueError("query must not be empty or whitespace")

    monkeypatch.setattr(service_module, "retrieve_knowledge", failing_retrieve)

    with pytest.raises(ValueError, match="query must not be empty or whitespace"):
        await service_module.build_rag_context(
            cast(AsyncEngine, object()),
            UnusedEmbeddings(),
            "   ",
        )


def test_build_rag_prompt_includes_evidence_and_normalized_query() -> None:
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="a" * 64,
        page_number=2,
        content="退款需要订单本人提交。",
        score=0.2,
    )

    prompt = service_module.build_rag_prompt("  退款政策  ", [citation])

    assert "知识库证据：" in prompt
    assert "来源：refund-policy-v1，第 2 页" in prompt
    assert "退款需要订单本人提交。" in prompt
    assert "用户问题：\n退款政策" in prompt
    assert "  退款政策  " not in prompt


def test_build_rag_prompt_explains_missing_evidence() -> None:
    prompt = service_module.build_rag_prompt("  物流时效  ", [])

    assert prompt == (
        "当前没有检索到可用的企业知识库证据。\n\n"
        "用户问题：\n物流时效"
    )


def test_build_rag_prompt_rejects_blank_query() -> None:
    with pytest.raises(ValueError, match="query must not be empty or whitespace"):
        service_module.build_rag_prompt(" \n\t", [])
