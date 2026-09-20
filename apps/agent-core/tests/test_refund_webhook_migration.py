"""Static and isolated-database checks for the webhook event migration."""

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import to_async_database_url

PRISMA_DIRECTORY = Path(__file__).resolve().parents[3] / "packages/database/prisma"
MIGRATION_PATH = (
    PRISMA_DIRECTORY
    / "migrations/20260918100000_create_refund_webhook_events/migration.sql"
)


def test_webhook_event_schema_and_migration_match() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    schema = (PRISMA_DIRECTORY / "schema.prisma").read_text(encoding="utf-8")
    model = re.search(r"model\s+RefundWebhookEvent\s*\{([^}]+)\}", schema)

    assert model is not None
    assert '@@map("refund_webhook_events")' in model.group(1)
    assert '"event_id" VARCHAR(128) PRIMARY KEY' in sql
    assert '"idempotency_key" VARCHAR(128) NOT NULL' in sql
    assert '"status" VARCHAR(32) NOT NULL' in sql
    assert '"provider_reference" VARCHAR(128)' in sql
    assert '"received_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP' in sql
    assert 'ON "refund_webhook_events" ("idempotency_key", "received_at")' in sql

    for field, column, length in [
        ("eventId", "event_id", 128),
        ("idempotencyKey", "idempotency_key", 128),
        ("status", "status", 32),
        ("providerReference", "provider_reference", 128),
    ]:
        declaration = re.search(rf"^\s*{field}\s+([^\n]+)", model.group(1), re.M)
        assert declaration is not None
        assert f'@map("{column}")' in declaration.group(1) or field == "status"
        assert f"@db.VarChar({length})" in declaration.group(1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_event_migration_enforces_event_id_uniqueness() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    url = make_url(database_url)
    if url.database != "ecommerce_agents_test" or url.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        pytest.fail("This test requires the local ecommerce_agents_test database.")

    schema = f"refund_webhook_test_{uuid4().hex}"
    engine: AsyncEngine = create_async_engine(
        to_async_database_url(database_url),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}, "timeout": 5},
    )
    created = False
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == url.database
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for statement_sql in MIGRATION_PATH.read_text(encoding="utf-8").split(";"):
                if statement_sql.strip():
                    await connection.execute(text(statement_sql))
        created = True

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO refund_webhook_events "
                    "(event_id, idempotency_key, status) "
                    "VALUES (:event_id, :idempotency_key, :status)"
                ),
                {
                    "event_id": "evt-001",
                    "idempotency_key": "refund:001",
                    "status": "SUCCEEDED",
                },
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO refund_webhook_events "
                        "(event_id, idempotency_key, status) "
                        "VALUES (:event_id, :idempotency_key, :status)"
                    ),
                    {
                        "event_id": "evt-001",
                        "idempotency_key": "refund:001",
                        "status": "SUCCEEDED",
                    },
                )
    finally:
        try:
            if created:
                async with engine.begin() as connection:
                    await connection.execute(text(f'DROP TABLE "{schema}".refund_webhook_events'))
                    await connection.execute(text(f'DROP SCHEMA "{schema}"'))
        finally:
            await engine.dispose()
