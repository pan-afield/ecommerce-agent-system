from dataclasses import dataclass
from io import BytesIO

from llama_index.core import Document
from pypdf import PdfReader

from app.rag.chunking import KnowledgeChunk, chunk_policy_text


@dataclass(frozen=True)
class ExtractedPdfPage:
    source_id: str
    page_number: int
    content: str


def extract_pdf_pages(
    source_id: str,
    pdf_bytes: bytes,
) -> list[ExtractedPdfPage]:
    """从 PDF 文件中提取每一页的文本内容。"""

    normalized_source_id = source_id.strip()
    if not normalized_source_id:
        raise ValueError("source_id must not be empty or whitespace")

    reader = PdfReader(BytesIO(pdf_bytes))

    extracted_pages: list[ExtractedPdfPage] = []

    for page_number, page in enumerate(reader.pages, start=1):
        content = (page.extract_text() or "").strip()
        if not content:
            continue
        extracted_pages.append(
            ExtractedPdfPage(
                source_id=normalized_source_id,
                page_number=page_number,
                content=content,
            )
        )

    return extracted_pages


def chunk_pdf_document(
    source_id: str,
    pdf_bytes: bytes,
    *,
    visibility: str = "PUBLIC",
) -> list[KnowledgeChunk]:
    """把每个非空 PDF 页面包装为 Document 后切片，并保留原始页码。"""
    pages = extract_pdf_pages(source_id, pdf_bytes)
    chunks: list[KnowledgeChunk] = []

    for page in pages:
        document = Document(
            text=page.content,
            metadata={
                "source_id": page.source_id,
                "page_number": page.page_number,
            },
        )

        chunks.extend(
            chunk_policy_text(
                str(document.metadata["source_id"]),
                document.text,
                page_number=int(document.metadata["page_number"]),
                visibility=visibility,
            )
        )

    return chunks
