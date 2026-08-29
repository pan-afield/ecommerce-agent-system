from app.api.serializers import format_citations
from app.rag.citations import KnowledgeCitation


def test_format_citations_returns_empty_list_for_empty_input() -> None:
    assert format_citations([]) == []


def test_format_citations_copies_provenance_fields_without_mutating_input() -> None:
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="chunk-1",
        page_number=2,
        content="签收后七天内可申请退货。",
        score=0.95,
    )

    formatted = format_citations([citation])

    assert [item.model_dump() for item in formatted] == [
        {
            "source_id": "refund-policy-v1",
            "chunk_id": "chunk-1",
            "page_number": 2,
            "content": "签收后七天内可申请退货。",
            "score": 0.95,
        }
    ]
    assert citation.content == "签收后七天内可申请退货。"
