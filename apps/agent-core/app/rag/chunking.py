from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class KnowledgeChunk:
    """表示可追溯的政策文本知识块。"""

    chunk_id: str
    source_id: str
    chunk_index: int
    content: str
    page_number: int | None = None
    visibility: str = "PUBLIC"


ALLOWED_VISIBILITIES: tuple[str, ...] = (
    "PUBLIC",
    "SUPPORT",
    "ADMIN",
)

DEFAULT_MAX_CHARS = 800


def chunk_policy_text(
    source_id: str,
    text: str,
    *,
    page_number: int | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    visibility: str = "PUBLIC",
) -> list[KnowledgeChunk]:
    """将政策文本切分为多个知识块。"""

    if visibility not in ALLOWED_VISIBILITIES:
        raise ValueError("visibility must be one of PUBLIC, SUPPORT, or ADMIN")

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
    pending_heading = None

    for paragraph in normalized_text.split("\n\n"):
        cleaned = paragraph.strip()

        if not cleaned:
            continue

        is_heading = cleaned.startswith("# ")

        if is_heading:
            if pending_heading is not None:
                paragraphs.append(pending_heading)
            pending_heading = cleaned
            continue

        if pending_heading is not None:
            paragraphs.append(f"{pending_heading}\n\n{cleaned}")
            pending_heading = None
        else:
            paragraphs.append(cleaned)

    if pending_heading is not None:
        paragraphs.append(pending_heading)

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
                visibility=visibility,
            )
        )

    return chunks
