import pytest

from app.rag.chunking import KnowledgeChunk
from app.rag.citations import (
    KnowledgeCitation,
    build_knowledge_citations,
    format_knowledge_citations,
)
from app.rag.ranking import HybridSearchResult


def test_build_knowledge_citations_preserves_result_order_and_provenance() -> None:
    first = KnowledgeChunk(
        chunk_id="a" * 64,
        source_id="refund-policy-v1",
        chunk_index=0,
        content="退款需要订单本人提交。",
        page_number=2,
    )
    second = KnowledgeChunk(
        chunk_id="b" * 64,
        source_id="shipping-policy-v1",
        chunk_index=1,
        content="配送时间以页面承诺为准。",
        page_number=None,
    )

    citations = build_knowledge_citations(
        [
            HybridSearchResult(chunk=first, score=0.25),
            HybridSearchResult(chunk=second, score=0.125),
        ]
    )

    assert [citation.source_id for citation in citations] == [
        "refund-policy-v1",
        "shipping-policy-v1",
    ]
    assert [citation.chunk_id for citation in citations] == ["a" * 64, "b" * 64]
    assert [citation.page_number for citation in citations] == [2, None]
    assert [citation.content for citation in citations] == [
        "退款需要订单本人提交。",
        "配送时间以页面承诺为准。",
    ]
    assert citations[0].score == pytest.approx(0.25)
    assert citations[1].score == pytest.approx(0.125)


def test_build_knowledge_citations_returns_empty_list_for_empty_input() -> None:
    assert build_knowledge_citations([]) == []


def test_format_knowledge_citations_returns_stable_source_block() -> None:
    citations = [
        KnowledgeCitation(
            source_id="refund-policy-v1",
            chunk_id="a" * 64,
            page_number=2,
            content="退款需要订单本人提交。",
            score=0.25,
        ),
        KnowledgeCitation(
            source_id="shipping-policy-v1",
            chunk_id="b" * 64,
            page_number=None,
            content="配送时间以页面承诺为准。",
            score=0.125,
        ),
    ]

    assert format_knowledge_citations(citations) == (
        "[1] 来源：refund-policy-v1，第 2 页\n"
        f"chunk_id: {'a' * 64}\n"
        "退款需要订单本人提交。\n\n"
        "[2] 来源：shipping-policy-v1\n"
        f"chunk_id: {'b' * 64}\n"
        "配送时间以页面承诺为准。"
    )


def test_format_knowledge_citations_returns_empty_string_for_empty_input() -> None:
    assert format_knowledge_citations([]) == ""
