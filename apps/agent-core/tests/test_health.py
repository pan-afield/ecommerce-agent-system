from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


def test_readiness_when_database_is_available(client: TestClient, app: FastAPI) -> None:
    app.state.readiness_probe = AsyncMock(return_value=True)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readiness_when_database_is_unavailable(client: TestClient, app: FastAPI) -> None:
    app.state.readiness_probe = AsyncMock(return_value=False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable"},
    }
