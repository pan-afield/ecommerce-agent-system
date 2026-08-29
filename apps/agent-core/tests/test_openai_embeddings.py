import pytest

import app.rag.openai_embeddings as openai_embeddings_module
from app.core.config import Settings


def test_create_openai_embeddings_maps_settings_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_client = object()

    def fake_constructor(**kwargs: object) -> object:
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        openai_embeddings_module,
        "OpenAIEmbeddings",
        fake_constructor,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-embedding-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "embedding-test-model")
    monkeypatch.setenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "17.5")
    settings = Settings(_env_file=None)

    client = openai_embeddings_module.create_openai_embeddings(settings)

    assert client is fake_client
    assert captured["openai_api_key"] == settings.openai_api_key
    assert captured["model"] == "embedding-test-model"
    assert captured["openai_api_base"] == "https://api.example.test/"
    assert captured["request_timeout"] == 17.5
    assert captured["max_retries"] == 0


def test_create_openai_embeddings_rejects_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_constructor(**kwargs: object) -> object:
        raise AssertionError("the client must not be constructed without an API key")

    monkeypatch.setattr(
        openai_embeddings_module,
        "OpenAIEmbeddings",
        unexpected_constructor,
    )

    with pytest.raises(RuntimeError, match="not configured"):
        openai_embeddings_module.create_openai_embeddings(Settings(_env_file=None))
