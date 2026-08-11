from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OpenAIReasoningEffort = Literal[
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]

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

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    openai_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="OPENAI_BASE_URL",
    )
    openai_agent_model: str = Field(
        default="gpt-4.1-mini",
        validation_alias="OPENAI_AGENT_MODEL",
        min_length=1,
        max_length=100,
    )
    openai_reasoning_effort: OpenAIReasoningEffort | None = Field(
        default=None,
        validation_alias="OPENAI_REASONING_EFFORT",
    )
    openai_use_responses_api: bool = Field(
        default=True,
        validation_alias="OPENAI_USE_RESPONSES_API",
    )
    openai_request_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="OPENAI_REQUEST_TIMEOUT_SECONDS",
        gt=0,
        le=120,
    )

    @field_validator(
        "openai_api_key",
        "openai_base_url",
        "openai_reasoning_effort",
        mode="before",
    )
    @classmethod
    def empty_optional_openai_value_is_none(
        cls,
        value: object,
    ) -> object:
        if isinstance(value, str) and not value.strip():
            return None

        return value

    model_config = SettingsConfigDict(
        env_file=(REPOSITORY_ROOT / ".env", AGENT_CORE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="AGENT_CORE_",
        extra="ignore",
        populate_by_name=True,
    )

    demo_user_id: str = Field(
        default="demo-user-li",
        validation_alias="DEMO_USER_ID",
        min_length=1,
        max_length=64,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
