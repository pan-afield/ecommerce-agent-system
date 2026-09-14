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


def test_settings_use_ecommerce_agent_issuer_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.jwt_issuer == "ecommerce-agent-system"


def test_settings_load_custom_jwt_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ISSUER", "staging-ecommerce-agent")

    settings = Settings(_env_file=None)

    assert settings.jwt_issuer == "staging-ecommerce-agent"


def test_settings_load_access_token_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ACCESS_TOKEN_TTL_SECONDS", "600")

    settings = Settings(_env_file=None)

    assert settings.jwt_access_token_ttl_seconds == 600


@pytest.mark.parametrize("value", ["0", "-1", "86401"])
def test_settings_reject_invalid_access_token_ttl(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JWT_ACCESS_TOKEN_TTL_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_use_refresh_token_ttl_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.jwt_refresh_token_ttl_seconds == 2_592_000


def test_settings_load_refresh_token_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_REFRESH_TOKEN_TTL_SECONDS", "86400")

    settings = Settings(_env_file=None)

    assert settings.jwt_refresh_token_ttl_seconds == 86_400


@pytest.mark.parametrize("value", ["0", "-1", "31536001"])
def test_settings_reject_invalid_refresh_token_ttl(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JWT_REFRESH_TOKEN_TTL_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_use_redis_cache_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.redis_url is None
    assert settings.redis_cache_ttl_seconds == 30


def test_settings_load_redis_cache_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("REDIS_CACHE_TTL_SECONDS", "60")

    settings = Settings(_env_file=None)

    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.redis_cache_ttl_seconds == 60


@pytest.mark.parametrize("value", ["0", "-1", "3601"])
def test_settings_reject_invalid_redis_cache_ttl(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("REDIS_CACHE_TTL_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_use_rate_limit_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.rate_limit_requests == 30
    assert settings.rate_limit_window_seconds == 60


def test_settings_load_rate_limit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "5")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "10")

    settings = Settings(_env_file=None)

    assert settings.rate_limit_requests == 5
    assert settings.rate_limit_window_seconds == 10


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RATE_LIMIT_REQUESTS", "0"),
        ("RATE_LIMIT_REQUESTS", "-1"),
        ("RATE_LIMIT_WINDOW_SECONDS", "0"),
        ("RATE_LIMIT_WINDOW_SECONDS", "-1"),
    ],
)
def test_settings_reject_invalid_rate_limit_configuration(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


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
