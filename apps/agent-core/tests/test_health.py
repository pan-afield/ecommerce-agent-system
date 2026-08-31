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
    assert response.json() == {
        "status": "ok",
        "checks": {
            "database": "ok",
            "rag_embeddings": "ok",
            "openai_chat": "unavailable",
        },
    }


async def test_readiness_reports_chat_and_rag_independently(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.readiness_probe = AsyncMock(return_value=True)
    app.state.rag_embeddings = None
    app.state.chat_service = object()

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {
            "database": "ok",
            "rag_embeddings": "unavailable",
            "openai_chat": "ok",
        },
    }


async def test_readiness_when_database_is_unavailable(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.readiness_probe = AsyncMock(return_value=False)
    app.state.rag_embeddings = None
    app.state.chat_service = None

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": "unavailable",
            "rag_embeddings": "unavailable",
            "openai_chat": "unavailable",
        },
    }
