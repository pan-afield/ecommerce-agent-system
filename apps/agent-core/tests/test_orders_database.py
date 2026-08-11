import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


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
            _env_file=None,
        )
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            owned_response = await client.get("/v1/orders/order-demo-001")
            hidden_response = await client.get("/v1/orders/order-demo-002")

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
