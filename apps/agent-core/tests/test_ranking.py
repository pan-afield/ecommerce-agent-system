import pytest

from app.rag.chunking import KnowledgeChunk
from app.rag.ranking import (
    RRF_RANK_CONSTANT,
    fuse_search_results,
)
from app.rag.vector_store import SemanticSearchResult


def make_chunk(character: str, index: int) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=character * 64,
        source_id="refund-policy-v1",
        page_number=index + 1,
        chunk_index=index,
        content=f"政策段落 {index}。",
    )


def test_fuse_search_results_rewards_chunks_found_by_both_paths() -> None:
    chunk_a = make_chunk("a", 0)
    chunk_b = make_chunk("b", 1)
    chunk_c = make_chunk("c", 2)

    results = fuse_search_results(
        semantic_results=[
            SemanticSearchResult(chunk=chunk_a, distance=0.1),
            SemanticSearchResult(chunk=chunk_b, distance=0.2),
        ],
        keyword_results=[chunk_b, chunk_c],
    )

    assert [result.chunk for result in results] == [chunk_b, chunk_a, chunk_c]
    assert results[0].score == pytest.approx(
        1 / (RRF_RANK_CONSTANT + 2) + 1 / (RRF_RANK_CONSTANT + 1)
    )
    assert results[1].score == pytest.approx(1 / (RRF_RANK_CONSTANT + 1))
    assert results[2].score == pytest.approx(1 / (RRF_RANK_CONSTANT + 2))


def test_fuse_search_results_uses_input_rank_instead_of_raw_distance() -> None:
    first = make_chunk("a", 0)
    second = make_chunk("b", 1)

    results = fuse_search_results(
        semantic_results=[
            SemanticSearchResult(chunk=first, distance=999.0),
            SemanticSearchResult(chunk=second, distance=0.001),
        ],
        keyword_results=[],
    )

    assert [result.chunk for result in results] == [first, second]


def test_fuse_search_results_breaks_equal_scores_by_chunk_id_and_limits() -> None:
    chunk_a = make_chunk("a", 0)
    chunk_b = make_chunk("b", 1)
    chunk_c = make_chunk("c", 2)

    results = fuse_search_results(
        semantic_results=[SemanticSearchResult(chunk=chunk_c, distance=0.1)],
        keyword_results=[chunk_b, chunk_a],
        limit=2,
    )

    assert [result.chunk.chunk_id for result in results] == [
        chunk_b.chunk_id,
        chunk_c.chunk_id,
    ]


def test_fuse_search_results_returns_empty_list_for_empty_inputs() -> None:
    assert fuse_search_results([], []) == []


@pytest.mark.parametrize("limit", [0, -1])
def test_fuse_search_results_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be greater than zero"):
        fuse_search_results([], [], limit=limit)
