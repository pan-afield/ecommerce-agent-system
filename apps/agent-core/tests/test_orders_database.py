import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"


@pytest.fixture(autouse=True)
def avoid_loading_local_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.create_local_embeddings", lambda _settings: object())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_order_detail_uses_isolated_postgres() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    app = create_app(
        Settings(
            environment="test",
            database_url=database_url,
            openai_api_key=None,
            jwt_secret_key=TEST_JWT_SECRET,
            _env_file=None,
        )
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            token = jwt.encode(
                {
                    "sub": "demo-user-li",
                    "exp": datetime.now(UTC) + timedelta(minutes=5),
                },
                TEST_JWT_SECRET,
                algorithm="HS256",
            )
            headers = {"Authorization": f"Bearer {token}"}
            owned_response = await client.get(
                "/v1/orders/order-demo-001",
                headers=headers,
            )
            hidden_response = await client.get(
                "/v1/orders/order-demo-002",
                headers=headers,
            )

    assert owned_response.status_code == 200
    owned_order = owned_response.json()
    assert owned_order["total_amount"] == "299.00"
    assert [event["status"] for event in owned_order["shipment_events"]] == [
        "confirmed",
        "packed",
        "shipped",
    ]
    assert hidden_response.status_code == 404
    assert hidden_response.json() == {"detail": "订单不存在。"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refund_application_allows_only_one_non_rejected_record_per_order() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    app = create_app(
        Settings(
            environment="test",
            database_url=database_url,
            openai_api_key=None,
            jwt_secret_key=TEST_JWT_SECRET,
            _env_file=None,
        )
    )

    async with app.router.lifespan_context(app):
        engine = cast(AsyncEngine, app.state.database_engine)
        cleanup = text(
            """
            DELETE FROM refund_applications
            WHERE user_id = 'demo-user-li'
              AND order_id = 'order-demo-001'
            """
        )
        async with engine.begin() as connection:
            await connection.execute(cleanup)

        token = jwt.encode(
            {
                "sub": "demo-user-li",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                first = await client.post(
                    "/v1/orders/order-demo-001/refund-applications",
                    json={
                        "request_id": f"first-{uuid4()}",
                        "requested_amount": "88.00",
                        "requested_currency": "CNY",
                    },
                    headers=headers,
                )
                duplicate = await client.post(
                    "/v1/orders/order-demo-001/refund-applications",
                    json={
                        "request_id": f"second-{uuid4()}",
                        "requested_amount": "66.00",
                        "requested_currency": "CNY",
                    },
                    headers=headers,
                )
                current = await client.get(
                    "/v1/orders/order-demo-001/refund-application",
                    headers=headers,
                )
        finally:
            async with engine.begin() as connection:
                await connection.execute(cleanup)

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["id"] == first.json()["id"]
    assert duplicate.json()["requested_amount"] == "88.00"
    assert current.status_code == 200
    assert current.json()["id"] == first.json()["id"]
