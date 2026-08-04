from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AGENT_CORE_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    app_name: str = "Ecommerce Agent Core"
    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/ecommerce_agents",
        validation_alias="DATABASE_URL",
        description="Shared PostgreSQL URL; converted to the asyncpg dialect at engine creation.",
    )
    docs_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=(REPOSITORY_ROOT / ".env", AGENT_CORE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="AGENT_CORE_",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
