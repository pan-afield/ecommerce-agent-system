from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import to_async_database_url


def test_settings_use_agent_core_environment_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CORE_ENVIRONMENT", "test")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"


def test_settings_read_shared_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql://app:secret@database.example/ecommerce"
    monkeypatch.setenv("DATABASE_URL", database_url)

    settings = Settings(_env_file=None)

    assert settings.database_url == database_url


def test_settings_load_jwt_secret_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-at-least-32-bytes")

    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key is not None
    assert (
        settings.jwt_secret_key.get_secret_value()
        == "test-only-jwt-secret-at-least-32-bytes"
    )
    assert str(settings.jwt_secret_key) == "**********"


def test_settings_allow_jwt_secret_to_be_unconfigured() -> None:
    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key is None


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_settings_treat_blank_jwt_secret_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", value)

    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key is None


def test_settings_reject_short_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 31)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_load_refund_approver_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFUND_APPROVER_USER_ID", "staff-zhang")

    settings = Settings(_env_file=None)

    assert settings.refund_approver_user_id == "staff-zhang"


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_settings_treat_blank_refund_approver_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("REFUND_APPROVER_USER_ID", value)

    settings = Settings(_env_file=None)

    assert settings.refund_approver_user_id is None


def test_settings_reject_overlong_refund_approver_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFUND_APPROVER_USER_ID", "x" * 65)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_load_openai_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("OPENAI_AGENT_MODEL", "gpt-test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_USE_RESPONSES_API", "true")
    monkeypatch.setenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "45")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-secret-key"
    assert str(settings.openai_api_key) == "**********"
    assert str(settings.openai_base_url) == "https://api.example.test/"
    assert settings.openai_agent_model == "gpt-test-model"
    assert settings.openai_reasoning_effort == "high"
    assert settings.openai_use_responses_api is True
    assert settings.openai_request_timeout_seconds == 45.0


def test_settings_allow_openai_to_be_unconfigured() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    assert settings.openai_base_url is None
    assert settings.openai_reasoning_effort is None
    assert settings.openai_agent_model == "gpt-4.1-mini"
    assert settings.openai_request_timeout_seconds == 30.0


def test_settings_use_local_rag_embedding_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.rag_embedding_model == "BAAI/bge-m3"
    assert settings.rag_embedding_dimensions == 1024
    assert settings.rag_embedding_cache_dir is None
    assert settings.rag_embedding_device == "cpu"


def test_settings_load_local_rag_embedding_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "local-test-model")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("RAG_EMBEDDING_CACHE_DIR", "/tmp/rag-models")
    monkeypatch.setenv("RAG_EMBEDDING_DEVICE", "cuda")

    settings = Settings(_env_file=None)

    assert settings.rag_embedding_model == "local-test-model"
    assert settings.rag_embedding_dimensions == 1024
    assert settings.rag_embedding_cache_dir == Path("/tmp/rag-models")
    assert settings.rag_embedding_device == "cuda"


def test_settings_treat_blank_rag_cache_dir_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_CACHE_DIR", "   ")

    settings = Settings(_env_file=None)

    assert settings.rag_embedding_cache_dir is None


@pytest.mark.parametrize(
    ("environment_name", "value"),
    [
        ("RAG_EMBEDDING_MODEL", ""),
        ("RAG_EMBEDDING_DIMENSIONS", "768"),
        ("RAG_EMBEDDING_DIMENSIONS", "0"),
        ("RAG_EMBEDDING_DIMENSIONS", "-1"),
        ("RAG_EMBEDDING_DEVICE", "tpu"),
    ],
)
def test_settings_reject_invalid_local_rag_embedding_configuration(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
) -> None:
    monkeypatch.setenv(environment_name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("environment_name", "field_name"),
    [
        ("OPENAI_API_KEY", "openai_api_key"),
        ("OPENAI_BASE_URL", "openai_base_url"),
        ("OPENAI_REASONING_EFFORT", "openai_reasoning_effort"),
    ],
)
def test_settings_treat_blank_optional_openai_values_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    field_name: str,
) -> None:
    monkeypatch.setenv(environment_name, "   ")

    settings = Settings(_env_file=None)

    assert getattr(settings, field_name) is None


@pytest.mark.parametrize(
    ("environment_name", "value"),
    [
        ("OPENAI_BASE_URL", "not-a-url"),
        ("OPENAI_AGENT_MODEL", ""),
        ("OPENAI_REASONING_EFFORT", "extreme"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "0"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "121"),
    ],
)
def test_settings_reject_invalid_openai_configuration(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    value: str,
) -> None:
    monkeypatch.setenv(environment_name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_prisma_postgres_url_is_converted_for_async_sqlalchemy() -> None:
    url = "postgresql://postgres:postgres@localhost:5432/ecommerce_agents"

    assert to_async_database_url(url) == (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce_agents"
    )
