from dataclasses import dataclass

from langchain_core.embeddings import Embeddings

from app.rag.chunking import KnowledgeChunk


@dataclass(frozen=True)
class EmbeddedKnowledgeChunk:
    """表示知识块及其对应的嵌入向量。"""

    chunk: KnowledgeChunk
    embedding: list[float]
    embedding_model: str


EMBEDDING_DIMENSIONS = 1024


async def embed_knowledge_chunks(
    chunks: list[KnowledgeChunk], embeddings: Embeddings, *, embedding_model: str
) -> list[EmbeddedKnowledgeChunk]:
    """异步生成知识块嵌入，并按原顺序组合为向量化知识块。"""
    if not chunks:
        return []

    vectors = await embeddings.aembed_documents([chunk.content for chunk in chunks])

    if len(vectors) != len(chunks):
        raise ValueError(f"Expected {len(chunks)} embeddings, but got {len(vectors)}")

    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
        raise ValueError(f"embedding dimension must be {EMBEDDING_DIMENSIONS}")

    return [
        EmbeddedKnowledgeChunk(chunk=chunk, embedding=vector, embedding_model=embedding_model)
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
