from hashlib import sha256
from io import BytesIO
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import app.rag.pdf as pdf_module
from app.rag.chunking import KnowledgeChunk
from app.rag.pdf import ExtractedPdfPage, chunk_pdf_document, extract_pdf_pages


def make_pdf_with_blank_first_page() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    page = writer.add_blank_page(width=300, height=300)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)  # noqa: SLF001
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 30 200 Td (Refund policy page two.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    writer.write(output)
    return output.getvalue()


def test_extract_pdf_pages_preserves_source_and_original_page_number() -> None:
    pages = extract_pdf_pages(
        "  refund-policy-v1  ",
        make_pdf_with_blank_first_page(),
    )

    assert pages == [
        ExtractedPdfPage(
            source_id="refund-policy-v1",
            page_number=2,
            content="Refund policy page two.",
        )
    ]


def test_extract_pdf_pages_returns_empty_list_for_pdf_without_text() -> None:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(output)

    assert extract_pdf_pages("blank-policy", output.getvalue()) == []


@pytest.mark.parametrize("source_id", ["", "   ", "\n\t"])
def test_extract_pdf_pages_rejects_blank_source_id(source_id: str) -> None:
    with pytest.raises(ValueError, match="source_id must not be empty or whitespace"):
        extract_pdf_pages(source_id, b"not parsed because source is invalid")


def test_extract_pdf_pages_preserves_invalid_pdf_error() -> None:
    with pytest.raises(PdfReadError):
        extract_pdf_pages("invalid-policy", b"not a PDF")


def test_chunk_pdf_document_creates_traceable_chunks_from_pdf_bytes() -> None:
    chunks = chunk_pdf_document(
        "  refund-policy-v1  ",
        make_pdf_with_blank_first_page(),
    )
    content = "Refund policy page two."
    payload = "\0".join(["refund-policy-v1", "page:2", "0", content]).encode()

    assert chunks == [
        KnowledgeChunk(
            chunk_id=sha256(payload).hexdigest(),
            source_id="refund-policy-v1",
            chunk_index=0,
            content=content,
            page_number=2,
        )
    ]


def test_chunk_pdf_document_uses_document_text_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ExtractedPdfPage(
        source_id="extracted-source",
        page_number=2,
        content="Extracted page text.",
    )
    document = Mock(
        metadata={"source_id": "document-source", "page_number": 7},
        text="Document text used for chunking.",
    )
    expected_chunks = [
        KnowledgeChunk(
            chunk_id="e" * 64,
            source_id="document-source",
            chunk_index=0,
            content="Document text used for chunking.",
            page_number=7,
        )
    ]
    extract_pages = Mock(return_value=[page])
    document_constructor = Mock(return_value=document)
    chunk_text = Mock(return_value=expected_chunks)
    monkeypatch.setattr(pdf_module, "extract_pdf_pages", extract_pages)
    monkeypatch.setattr(pdf_module, "Document", document_constructor)
    monkeypatch.setattr(pdf_module, "chunk_policy_text", chunk_text)

    chunks = pdf_module.chunk_pdf_document(
        "input-source",
        b"pdf-bytes",
        visibility="SUPPORT",
    )

    assert chunks == expected_chunks
    extract_pages.assert_called_once_with("input-source", b"pdf-bytes")
    document_constructor.assert_called_once_with(
        text="Extracted page text.",
        metadata={"source_id": "extracted-source", "page_number": 2},
    )
    chunk_text.assert_called_once_with(
        "document-source",
        "Document text used for chunking.",
        page_number=7,
        visibility="SUPPORT",
    )
