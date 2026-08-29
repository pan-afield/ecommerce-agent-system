from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class KnowledgeChunk:
    """表示可追溯的政策文本知识块。"""

    chunk_id: str
    source_id: str
    chunk_index: int
    content: str
    page_number: int | None = None


DEFAULT_MAX_CHARS = 800


def chunk_policy_text(
    source_id: str,
    text: str,
    *,
    page_number: int | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[KnowledgeChunk]:
    """将政策文本切分为多个知识块。"""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if page_number is not None and page_number <= 0:
        raise ValueError("page_number must be greater than zero")

    normalized_source_id = source_id.strip()
    if not normalized_source_id:
        raise ValueError("source_id must not be empty or whitespace")
    # 先统一不同操作系统的换行符，否则相同文档会产生不同的 chunk ID。
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")

    paragraphs = []

    for paragraph in normalized_text.split("\n\n"):
        cleaned = paragraph.strip()

        if cleaned:
            paragraphs.append(cleaned)

    contents: list[str] = []

    for paragraph in paragraphs:
        for start in range(0, len(paragraph), max_chars):
            content = paragraph[start : start + max_chars].strip()

            if content:
                contents.append(content)

    chunks: list[KnowledgeChunk] = []

    for chunk_index, content in enumerate(contents):
        if page_number is None:
            payload_text = f"{normalized_source_id}\0{chunk_index}\0{content}"
        else:
            payload_text = f"{normalized_source_id}\0page:{page_number}\0{chunk_index}\0{content}"
        chunk_id = sha256(payload_text.encode()).hexdigest()

        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id,
                source_id=normalized_source_id,
                chunk_index=chunk_index,
                content=content,
                page_number=page_number,
            )
        )

    return chunks


STORE_KNOWLEDGE_CHUNK_STATEMENT = text(
    """
    INSERT INTO agent_core.knowledge_chunks (
        chunk_id,
        source_id,
        page_number,
        chunk_index,
        content
    )
    VALUES (
        :chunk_id,
        :source_id,
        :page_number,
        :chunk_index,
        :content
    )
    ON CONFLICT (chunk_id) DO NOTHING
    RETURNING chunk_id
    """
)


async def try_store_knowledge_chunk(
    engine: AsyncEngine,
    chunk: KnowledgeChunk,
) -> bool:
    """尝试插入一个知识块，重复的 ``chunk_id`` 不会重复写入。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            STORE_KNOWLEDGE_CHUNK_STATEMENT,
            {
                "chunk_id": chunk.chunk_id,
                "source_id": chunk.source_id,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
            },
        )

    return result.scalar_one_or_none() is not None


async def store_knowledge_chunks(
    engine: AsyncEngine,
    chunks: list[KnowledgeChunk],
) -> int:
    """在一个事务中批量插入知识块，并返回实际新增的数量。"""
    if not chunks:
        return 0

    stored_count = 0

    async with engine.begin() as connection:
        for chunk in chunks:
            result = await connection.execute(
                STORE_KNOWLEDGE_CHUNK_STATEMENT,
                {
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                },
            )

            if result.scalar_one_or_none() is not None:
                stored_count += 1

    return stored_count
