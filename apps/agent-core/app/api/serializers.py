from app.rag.citations import KnowledgeCitation
from app.schemas.rag import KnowledgeCitationResponse


def format_citations(
    citations: list[KnowledgeCitation],
) -> list[KnowledgeCitationResponse]:
    return [
        KnowledgeCitationResponse(
            source_id=citation.source_id,
            chunk_id=citation.chunk_id,
            page_number=citation.page_number,
            content=citation.content,
            score=citation.score,
        )
        for citation in citations
    ]
