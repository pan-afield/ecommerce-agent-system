from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import AsyncClient


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


async def test_readiness_when_database_is_available(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.readiness_probe = AsyncMock(return_value=True)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


async def test_readiness_when_database_is_unavailable(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.readiness_probe = AsyncMock(return_value=False)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable"},
    }
