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
        "jwt_secret_key",
        "rag_embedding_cache_dir",
        "refund_sandbox_webhook_secret",
        "refund_sandbox_api_key",
        "refund_sandbox_base_url",
        "refund_sandbox_callback_url",
        mode="before",
    )
    @classmethod
    def empty_optional_configuration_value_is_none(
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

    jwt_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias="JWT_SECRET_KEY",
        min_length=32,
    )

    rag_embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias="RAG_EMBEDDING_MODEL",
        min_length=1,
        max_length=100,
    )

    rag_embedding_dimensions: int = Field(
        default=1024,
        validation_alias="RAG_EMBEDDING_DIMENSIONS",
        ge=1024,
        le=1024,
    )

    rag_embedding_cache_dir: Path | None = Field(
        default=None,
        validation_alias="RAG_EMBEDDING_CACHE_DIR",
    )

    rag_embedding_device: Literal["cpu", "cuda"] = Field(
        default="cpu",
        validation_alias="RAG_EMBEDDING_DEVICE",
    )

    redis_url: str | None = Field(
        default=None,
        validation_alias="REDIS_URL",
    )

    redis_cache_ttl_seconds: int = Field(
        default=30,
        validation_alias="REDIS_CACHE_TTL_SECONDS",
        gt=0,
        le=3600,
    )

    jwt_issuer: str = Field(
        default="ecommerce-agent-system",
        validation_alias="JWT_ISSUER",
        min_length=1,
        max_length=100,
    )

    jwt_access_token_ttl_seconds: int = Field(
        default=900,
        validation_alias="JWT_ACCESS_TOKEN_TTL_SECONDS",
        gt=0,
        le=86_400,
    )

    jwt_refresh_token_ttl_seconds: int = Field(
        default=2_592_000,
        validation_alias="JWT_REFRESH_TOKEN_TTL_SECONDS",
        gt=0,
        le=31_536_000,
    )

    rate_limit_requests: int = Field(
        default=30,
        validation_alias="RATE_LIMIT_REQUESTS",
        ge=1,
    )

    rate_limit_window_seconds: int = Field(
        default=60,
        validation_alias="RATE_LIMIT_WINDOW_SECONDS",
        ge=1,
    )

    refund_sandbox_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="REFUND_SANDBOX_BASE_URL",
    )

    refund_sandbox_request_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="REFUND_SANDBOX_REQUEST_TIMEOUT_SECONDS",
        gt=0,
        le=120,
    )

    refund_sandbox_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias="REFUND_SANDBOX_WEBHOOK_SECRET",
    )

    refund_sandbox_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="REFUND_SANDBOX_API_KEY",
        min_length=16,
    )
    refund_sandbox_callback_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="REFUND_SANDBOX_CALLBACK_URL",
    )
    refund_recovery_max_attempts: int = Field(
        default=5,
        validation_alias="REFUND_RECOVERY_MAX_ATTEMPTS",
        ge=1,
        le=20,
    )
    refund_recovery_lease_seconds: int = Field(
        default=120,
        validation_alias="REFUND_RECOVERY_LEASE_SECONDS",
        ge=10,
        le=3600,
    )
    refund_recovery_retry_seconds: int = Field(
        default=60,
        validation_alias="REFUND_RECOVERY_RETRY_SECONDS",
        ge=1,
        le=3600,
    )


@lru_cache
def get_settings() -> Settings:
    """在进程内缓存配置对象，避免每个请求重复读取环境变量和配置文件。"""
    return Settings()
