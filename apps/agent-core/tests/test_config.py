import pytest

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


def test_prisma_postgres_url_is_converted_for_async_sqlalchemy() -> None:
    url = "postgresql://postgres:postgres@localhost:5432/ecommerce_agents"

    assert to_async_database_url(url) == (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/ecommerce_agents"
    )
