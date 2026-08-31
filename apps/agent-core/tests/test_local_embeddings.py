from pathlib import Path
from unittest.mock import Mock

import pytest

import app.rag.local_embeddings as local_embeddings
from app.core.config import Settings
from app.rag.local_embeddings import LocalEmbeddingsAdapter, RagEmbeddingError


class FakeLlamaIndexEmbedding:
    def __init__(self, dimensions: int = 1) -> None:
        self.dimensions = dimensions
        self.sync_document_texts: list[str] = []
        self.sync_query_texts: list[str] = []
        self.async_document_texts: list[str] = []
        self.async_query_texts: list[str] = []

    def get_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        self.sync_document_texts = texts
        return [[1.0] * self.dimensions for _ in texts]

    def get_query_embedding(self, query: str) -> list[float]:
        self.sync_query_texts.append(query)
        return [2.0] * self.dimensions

    async def aget_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        self.async_document_texts = texts
        return [[3.0] * self.dimensions for _ in texts]

    async def aget_query_embedding(self, query: str) -> list[float]:
        self.async_query_texts.append(query)
        return [4.0] * self.dimensions


class FailingAsyncLlamaIndexEmbedding(FakeLlamaIndexEmbedding):
    async def aget_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        raise OSError("sensitive model cache path")

    async def aget_query_embedding(self, query: str) -> list[float]:
        raise OSError("sensitive model cache path")


def test_create_local_embeddings_maps_settings_to_huggingface_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_instance = object()
    embedding_constructor = Mock(return_value=embedding_instance)
    monkeypatch.setattr(
        local_embeddings,
        "HuggingFaceEmbedding",
        embedding_constructor,
    )
    settings = Settings(
        _env_file=None,
        rag_embedding_model="test-local-model",
        rag_embedding_dimensions=1024,
        rag_embedding_cache_dir=Path("/tmp/rag-models"),
        rag_embedding_device="cpu",
    )

    result = local_embeddings.create_local_embeddings(settings)

    assert isinstance(result, LocalEmbeddingsAdapter)
    assert result._embedding is embedding_instance
    embedding_constructor.assert_called_once_with(
        model_name="test-local-model",
        cache_folder="/tmp/rag-models",
        device="cpu",
    )


def test_create_local_embeddings_passes_none_for_default_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_constructor = Mock(return_value=object())
    monkeypatch.setattr(
        local_embeddings,
        "HuggingFaceEmbedding",
        embedding_constructor,
    )

    result = local_embeddings.create_local_embeddings(Settings(_env_file=None))

    embedding_constructor.assert_called_once_with(
        model_name="BAAI/bge-m3",
        cache_folder=None,
        device="cpu",
    )
    assert isinstance(result, LocalEmbeddingsAdapter)


async def test_local_embeddings_adapter_forwards_sync_and_async_calls() -> None:
    fake_embedding = FakeLlamaIndexEmbedding()
    adapter = LocalEmbeddingsAdapter(fake_embedding, dimensions=1)

    assert adapter.embed_documents(["退款政策", "shipping policy"]) == [
        [1.0],
        [1.0],
    ]
    assert adapter.embed_query("退款") == [2.0]
    assert await adapter.aembed_documents(["退货", "delivery"]) == [
        [3.0],
        [3.0],
    ]
    assert await adapter.aembed_query("delivery") == [4.0]

    assert fake_embedding.sync_document_texts == ["退款政策", "shipping policy"]
    assert fake_embedding.sync_query_texts == ["退款"]
    assert fake_embedding.async_document_texts == ["退货", "delivery"]
    assert fake_embedding.async_query_texts == ["delivery"]


@pytest.mark.parametrize(
    "method_name",
    [
        "embed_documents",
        "embed_query",
        "aembed_documents",
        "aembed_query",
    ],
)
async def test_local_embeddings_adapter_rejects_wrong_vector_dimension(
    method_name: str,
) -> None:
    adapter = LocalEmbeddingsAdapter(
        FakeLlamaIndexEmbedding(dimensions=2),
        dimensions=1,
    )

    method = getattr(adapter, method_name)
    with pytest.raises(RagEmbeddingError, match="embedding dimension must be 1"):
        if method_name.endswith("documents"):
            result = method(["refund policy"])
        else:
            result = method("refund policy")
        if method_name.startswith("a"):
            await result


@pytest.mark.parametrize(
    ("method_name", "argument", "expected_message"),
    [
        (
            "aembed_documents",
            ["refund policy"],
            "local document embedding failed",
        ),
        (
            "aembed_query",
            "refund policy",
            "local query embedding failed",
        ),
    ],
)
async def test_local_embeddings_adapter_wraps_async_model_failure(
    method_name: str,
    argument: list[str] | str,
    expected_message: str,
) -> None:
    adapter = LocalEmbeddingsAdapter(
        FailingAsyncLlamaIndexEmbedding(),
        dimensions=1,
    )
    method = getattr(adapter, method_name)

    with pytest.raises(RagEmbeddingError, match=expected_message) as error:
        await method(argument)

    assert isinstance(error.value.__cause__, OSError)
    assert "sensitive model cache path" not in str(error.value)
