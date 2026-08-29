import pytest
from langchain_core.embeddings import Embeddings

from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk, embed_knowledge_chunks


class FakeEmbeddings(Embeddings):
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.received_texts: list[str] | None = None
        self.async_call_count = 0

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.async_call_count += 1
        self.received_texts = texts
        return self.vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("the synchronous embedding method must not be used")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("query embedding is not part of this step")


def make_chunks() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            chunk_id="a" * 64,
            source_id="policy-v1",
            chunk_index=0,
            content="退款需要订单本人提交。",
        ),
        KnowledgeChunk(
            chunk_id="b" * 64,
            source_id="policy-v1",
            chunk_index=1,
            content="审批通过后才会进入处理。",
        ),
    ]


async def test_embed_knowledge_chunks_returns_vectors_in_chunk_order() -> None:
    first_vector = [0.1] * 1536
    second_vector = [0.3] * 1536
    fake = FakeEmbeddings([first_vector, second_vector])
    chunks = make_chunks()

    embedded = await embed_knowledge_chunks(chunks, fake)

    assert embedded == [
        EmbeddedKnowledgeChunk(chunk=chunks[0], embedding=first_vector),
        EmbeddedKnowledgeChunk(chunk=chunks[1], embedding=second_vector),
    ]
    assert fake.received_texts == [chunk.content for chunk in chunks]
    assert fake.async_call_count == 1


async def test_embed_knowledge_chunks_does_not_call_model_for_empty_input() -> None:
    fake = FakeEmbeddings([])

    embedded = await embed_knowledge_chunks([], fake)

    assert embedded == []
    assert fake.received_texts is None
    assert fake.async_call_count == 0


async def test_embed_knowledge_chunks_rejects_mismatched_vector_count() -> None:
    fake = FakeEmbeddings([[0.1, 0.2]])

    with pytest.raises(ValueError, match="embeddings"):
        await embed_knowledge_chunks(make_chunks(), fake)


async def test_embed_knowledge_chunks_rejects_wrong_vector_dimension() -> None:
    fake = FakeEmbeddings([[0.1] * 1536, [0.2, 0.3]])

    with pytest.raises(ValueError, match="embedding dimension must be 1536"):
        await embed_knowledge_chunks(make_chunks(), fake)
