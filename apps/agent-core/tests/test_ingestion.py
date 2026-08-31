from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

import app.rag.ingestion as ingestion
from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk


class UnusedEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("ingestion test should use patched embedding function")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("ingestion test does not embed queries")


@pytest.mark.asyncio
async def test_ingest_knowledge_chunks_embeds_then_stores_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id="a" * 64,
            source_id="refund-policy-zh",
            chunk_index=0,
            content="签收后七天内可申请退货。",
        )
    ]
    engine = cast(AsyncEngine, object())
    embeddings = UnusedEmbeddings()
    embedded_chunk = EmbeddedKnowledgeChunk(
        chunk=chunks[0],
        embedding=[0.1],
        embedding_model="test-model",
    )
    events: list[str] = []

    async def fake_embed(
        received_chunks: list[KnowledgeChunk],
        received_embeddings: Embeddings,
        *,
        embedding_model: str,
    ) -> list[EmbeddedKnowledgeChunk]:
        assert received_chunks == chunks
        assert received_embeddings is embeddings
        assert embedding_model == "test-model"
        events.append("embed")
        return [embedded_chunk]

    async def fake_store(
        received_engine: AsyncEngine,
        received_chunks: list[EmbeddedKnowledgeChunk],
    ) -> int:
        assert received_engine is engine
        assert received_chunks == [embedded_chunk]
        events.append("store")
        return 1

    embed = AsyncMock(side_effect=fake_embed)
    store = AsyncMock(side_effect=fake_store)
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "store_embedded_knowledge_chunks", store)

    count = await ingestion.ingest_knowledge_chunks(
        engine,
        chunks,
        embeddings,
        embedding_model="test-model",
    )

    assert count == 1
    assert events == ["embed", "store"]
    embed.assert_awaited_once_with(
        chunks,
        embeddings,
        embedding_model="test-model",
    )
    store.assert_awaited_once_with(engine, [embedded_chunk])


@pytest.mark.asyncio
async def test_ingest_knowledge_chunks_skips_dependencies_for_empty_input() -> None:
    embed = AsyncMock()
    store = AsyncMock()

    # Monkeypatching through pytest keeps this test's dynamic replacement local.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "store_embedded_knowledge_chunks", store)
    try:
        count = await ingestion.ingest_knowledge_chunks(
            cast(AsyncEngine, object()),
            [],
            UnusedEmbeddings(),
            embedding_model="test-model",
        )
    finally:
        monkeypatch.undo()

    assert count == 0
    embed.assert_not_awaited()
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_knowledge_chunks_does_not_store_when_embedding_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [
        KnowledgeChunk(
            chunk_id="b" * 64,
            source_id="shipping-policy-en",
            chunk_index=0,
            content="Delivery usually takes three business days.",
        )
    ]
    embed = AsyncMock(side_effect=ValueError("embedding dimension must be 1024"))
    store = AsyncMock()
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "store_embedded_knowledge_chunks", store)

    with pytest.raises(ValueError, match="embedding dimension must be 1024"):
        await ingestion.ingest_knowledge_chunks(
            cast(AsyncEngine, object()),
            chunks,
            UnusedEmbeddings(),
            embedding_model="test-model",
        )

    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_knowledge_chunks_rejects_empty_input_before_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed = AsyncMock()
    replace = AsyncMock()
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "replace_all_embedded_knowledge_chunks", replace)

    with pytest.raises(ValueError, match="rebuild chunks must not be empty"):
        await ingestion.rebuild_knowledge_chunks(
            cast(AsyncEngine, object()),
            [],
            UnusedEmbeddings(),
            embedding_model="new-model",
        )

    embed.assert_not_awaited()
    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_knowledge_chunks_embeds_then_replaces_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = KnowledgeChunk(
        chunk_id="e" * 64,
        source_id="refund-policy",
        chunk_index=0,
        content="退款政策。",
    )
    embedded_chunk = EmbeddedKnowledgeChunk(
        chunk=chunk,
        embedding=[0.2],
        embedding_model="new-model",
    )
    engine = cast(AsyncEngine, object())
    embeddings = UnusedEmbeddings()
    events: list[str] = []

    async def fake_embed(
        chunks: list[KnowledgeChunk],
        received_embeddings: Embeddings,
        *,
        embedding_model: str,
    ) -> list[EmbeddedKnowledgeChunk]:
        assert chunks == [chunk]
        assert received_embeddings is embeddings
        assert embedding_model == "new-model"
        events.append("embed")
        return [embedded_chunk]

    async def fake_replace(
        received_engine: AsyncEngine,
        chunks: list[EmbeddedKnowledgeChunk],
    ) -> int:
        assert received_engine is engine
        assert chunks == [embedded_chunk]
        events.append("replace")
        return 1

    embed = AsyncMock(side_effect=fake_embed)
    replace = AsyncMock(side_effect=fake_replace)
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "replace_all_embedded_knowledge_chunks", replace)

    count = await ingestion.rebuild_knowledge_chunks(
        engine,
        [chunk],
        embeddings,
        embedding_model="new-model",
    )

    assert count == 1
    assert events == ["embed", "replace"]


@pytest.mark.asyncio
async def test_rebuild_knowledge_chunks_keeps_store_when_embedding_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = KnowledgeChunk(
        chunk_id="f" * 64,
        source_id="refund-policy",
        chunk_index=0,
        content="退款政策。",
    )
    embed = AsyncMock(side_effect=RuntimeError("model unavailable"))
    replace = AsyncMock()
    monkeypatch.setattr(ingestion, "embed_knowledge_chunks", embed)
    monkeypatch.setattr(ingestion, "replace_all_embedded_knowledge_chunks", replace)

    with pytest.raises(RuntimeError, match="model unavailable"):
        await ingestion.rebuild_knowledge_chunks(
            cast(AsyncEngine, object()),
            [chunk],
            UnusedEmbeddings(),
            embedding_model="new-model",
        )

    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_policy_text_chunks_then_ingests_with_model_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    embeddings = UnusedEmbeddings()
    chunks = [
        KnowledgeChunk(
            chunk_id="c" * 64,
            source_id="returns-policy",
            chunk_index=0,
            content="Seven-day returns are supported.",
        )
    ]
    document = Mock(
        metadata={"source_id": "document-source"},
        text="Document text used for chunking.",
    )
    document_constructor = Mock(return_value=document)
    chunk_text = Mock(return_value=chunks)
    ingest_chunks = AsyncMock(return_value=1)
    monkeypatch.setattr(ingestion, "Document", document_constructor)
    monkeypatch.setattr(ingestion, "chunk_policy_text", chunk_text)
    monkeypatch.setattr(ingestion, "ingest_knowledge_chunks", ingest_chunks)

    count = await ingestion.ingest_policy_text(
        engine,
        embeddings,
        " returns-policy ",
        " Seven-day returns are supported. ",
        embedding_model="test-local-model",
    )

    assert count == 1
    document_constructor.assert_called_once_with(
        text=" Seven-day returns are supported. ",
        metadata={"source_id": " returns-policy "},
    )
    chunk_text.assert_called_once_with(
        "document-source",
        "Document text used for chunking.",
    )
    ingest_chunks.assert_awaited_once_with(
        engine,
        chunks,
        embeddings,
        embedding_model="test-local-model",
    )


@pytest.mark.asyncio
async def test_ingest_policy_pdf_preserves_page_chunks_and_model_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    embeddings = UnusedEmbeddings()
    pdf_bytes = b"fake-pdf-content"
    chunks = [
        KnowledgeChunk(
            chunk_id="d" * 64,
            source_id="refund-policy.pdf",
            chunk_index=0,
            content="退款申请需由订单本人提交。",
            page_number=2,
        )
    ]
    chunk_pdf = Mock(return_value=chunks)
    ingest_chunks = AsyncMock(return_value=1)
    monkeypatch.setattr(ingestion, "chunk_pdf_document", chunk_pdf)
    monkeypatch.setattr(ingestion, "ingest_knowledge_chunks", ingest_chunks)

    count = await ingestion.ingest_policy_pdf(
        engine,
        embeddings,
        " refund-policy.pdf ",
        pdf_bytes,
        embedding_model="test-local-model",
    )

    assert count == 1
    assert chunks[0].page_number == 2
    chunk_pdf.assert_called_once_with(" refund-policy.pdf ", pdf_bytes)
    ingest_chunks.assert_awaited_once_with(
        engine,
        chunks,
        embeddings,
        embedding_model="test-local-model",
    )


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_load_policy_text_file_chunks_uses_utf8_and_filename_as_source(
    tmp_path: Path,
    suffix: str,
) -> None:
    file_path = tmp_path / f"退款政策{suffix}"
    file_path.write_text("签收后七天内可申请退货。", encoding="utf-8")

    chunks = ingestion.load_policy_file_chunks(file_path)

    assert len(chunks) == 1
    assert chunks[0].source_id == file_path.name
    assert chunks[0].content == "签收后七天内可申请退货。"
    assert str(tmp_path) not in chunks[0].source_id


def test_load_policy_pdf_file_chunks_accepts_case_insensitive_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = tmp_path / "policy.PDF"
    file_path.write_bytes(b"fake-pdf-bytes")
    expected_chunks = [
        KnowledgeChunk(
            chunk_id="1" * 64,
            source_id="policy.PDF",
            chunk_index=0,
            content="Policy content.",
            page_number=1,
        )
    ]
    chunk_pdf = Mock(return_value=expected_chunks)
    monkeypatch.setattr(ingestion, "chunk_pdf_document", chunk_pdf)

    chunks = ingestion.load_policy_file_chunks(file_path)

    assert chunks == expected_chunks
    chunk_pdf.assert_called_once_with("policy.PDF", b"fake-pdf-bytes")


def test_load_policy_file_chunks_rejects_unsupported_suffix(tmp_path: Path) -> None:
    file_path = tmp_path / "policy.docx"

    with pytest.raises(ValueError, match=r"\.txt, \.md, or \.pdf"):
        ingestion.load_policy_file_chunks(file_path)


def test_load_policy_files_chunks_preserves_file_and_chunk_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = Path("policies/refund.md")
    second_path = Path("policies/shipping.txt")
    first_chunks = [
        KnowledgeChunk(
            chunk_id="2" * 64,
            source_id="refund.md",
            chunk_index=0,
            content="Refund policy.",
        )
    ]
    second_chunks = [
        KnowledgeChunk(
            chunk_id="3" * 64,
            source_id="shipping.txt",
            chunk_index=0,
            content="Shipping policy.",
        )
    ]
    load_file = Mock(side_effect=[first_chunks, second_chunks])
    monkeypatch.setattr(ingestion, "load_policy_file_chunks", load_file)

    chunks = ingestion.load_policy_files_chunks([first_path, second_path])

    assert chunks == [*first_chunks, *second_chunks]
    assert load_file.call_args_list == [
        ((first_path,),),
        ((second_path,),),
    ]


def test_load_policy_files_chunks_rejects_duplicate_source_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_file = Mock()
    monkeypatch.setattr(ingestion, "load_policy_file_chunks", load_file)

    with pytest.raises(ValueError, match="policy file names must be unique"):
        ingestion.load_policy_files_chunks(
            [
                Path("policies/zh/refund.md"),
                Path("policies/en/refund.md"),
            ]
        )

    load_file.assert_not_called()
