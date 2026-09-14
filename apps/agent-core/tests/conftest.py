from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


class FakeRoleRow:
    def __init__(self, role: str) -> None:
        self.role = role


class FakeRoleResult:
    def __init__(self, row: FakeRoleRow | None) -> None:
        self._row = row

    def fetchone(self) -> FakeRoleRow | None:
        return self._row


class FakeRoleConnection:
    def __init__(
        self,
        roles: dict[str, str],
        error: Exception | None = None,
    ) -> None:
        self._roles = roles
        self._error = error

    async def execute(
        self,
        statement: Any,
        parameters: dict[str, Any],
    ) -> FakeRoleResult:
        if self._error is not None:
            raise self._error
        role = self._roles.get(str(parameters["user_id"]))
        return FakeRoleResult(FakeRoleRow(role) if role is not None else None)


class FakeRoleContext:
    def __init__(self, connection: FakeRoleConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeRoleConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeRoleEngine:
    def __init__(
        self,
        roles: dict[str, str],
        error: Exception | None = None,
    ) -> None:
        self._context = FakeRoleContext(FakeRoleConnection(roles, error))

    def connect(self) -> FakeRoleContext:
        return self._context


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        "app.main.create_local_embeddings",
        lambda settings: object(),
    )
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        openai_api_key=None,
        jwt_secret_key="test-only-jwt-secret-at-least-32-bytes",
        _env_file=None,
    )
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client:
            yield test_client
