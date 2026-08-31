from pathlib import Path

from langchain_core.embeddings import Embeddings
from llama_index.core import Document
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.chunking import KnowledgeChunk, chunk_policy_text
from app.rag.embedding import embed_knowledge_chunks
from app.rag.pdf import chunk_pdf_document
from app.rag.vector_store import (
    replace_all_embedded_knowledge_chunks,
    store_embedded_knowledge_chunks,
)


async def ingest_knowledge_chunks(
    engine: AsyncEngine,
    chunks: list[KnowledgeChunk],
    embeddings: Embeddings,
    *,
    embedding_model: str,
) -> int:
    """将知识块嵌入并存储到数据库中，返回实际新增的知识块数量。"""
    if not chunks:
        return 0

    # 生成知识块嵌入
    embedded_chunks = await embed_knowledge_chunks(
        chunks, embeddings, embedding_model=embedding_model
    )

    # 批量存储向量化知识块
    count = await store_embedded_knowledge_chunks(engine, embedded_chunks)
    return count


async def rebuild_knowledge_chunks(
    engine: AsyncEngine,
    chunks: list[KnowledgeChunk],
    embeddings: Embeddings,
    *,
    embedding_model: str,
) -> int:
    """用当前 embedding 模型生成全部向量，并原子替换旧知识块。"""
    if not chunks:
        raise ValueError("rebuild chunks must not be empty")

    embedded_chunks = await embed_knowledge_chunks(
        chunks,
        embeddings,
        embedding_model=embedding_model,
    )

    return await replace_all_embedded_knowledge_chunks(
        engine,
        embedded_chunks,
    )


async def ingest_policy_text(
    engine: AsyncEngine,
    embeddings: Embeddings,
    source_id: str,
    text: str,
    *,
    embedding_model: str,
) -> int:
    """将一份纯文本政策转换为 Document、切片、向量化并写入知识库。"""
    document = Document(
        text=text,
        metadata={"source_id": source_id},
    )
    chunks = chunk_policy_text(
        str(document.metadata["source_id"]),
        document.text,
    )

    return await ingest_knowledge_chunks(
        engine,
        chunks,
        embeddings,
        embedding_model=embedding_model,
    )


async def ingest_policy_pdf(
    engine: AsyncEngine,
    embeddings: Embeddings,
    source_id: str,
    pdf_bytes: bytes,
    *,
    embedding_model: str,
) -> int:
    """解析 PDF 的页码和文本，再复用通用知识块入库流程。"""
    chunks = chunk_pdf_document(source_id, pdf_bytes)

    return await ingest_knowledge_chunks(
        engine,
        chunks,
        embeddings,
        embedding_model=embedding_model,
    )


def load_policy_file_chunks(file_path: Path) -> list[KnowledgeChunk]:
    """按扩展名读取单个政策文件；不负责 embedding 或数据库写入。"""
    source_id = file_path.name
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return chunk_pdf_document(
            source_id,
            file_path.read_bytes(),
        )

    if suffix in {".txt", ".md"}:
        document = Document(
            text=file_path.read_text(encoding="utf-8"),
            metadata={"source_id": source_id},
        )
        return chunk_policy_text(
            str(document.metadata["source_id"]),
            document.text,
        )

    raise ValueError("policy file must use .txt, .md, or .pdf")


def load_policy_files_chunks(
    file_paths: list[Path],
) -> list[KnowledgeChunk]:
    """读取多个政策文件，并在进入向量化前拒绝重复文件名。"""
    source_ids = [file_path.name for file_path in file_paths]

    if len(source_ids) != len(set(source_ids)):
        raise ValueError("policy file names must be unique")

    chunks: list[KnowledgeChunk] = []

    for file_path in file_paths:
        chunks.extend(load_policy_file_chunks(file_path))

    return chunks
