from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any

import jwt
import pytest
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import get_current_user_id
from app.schemas.auth import AuthRequest, CurrentUserResponse


class FakeUserRow:
    def __init__(self, user_id: str, password_hash: str | None) -> None:
        self.id = user_id
        self.password_hash = password_hash


class FakeAuthResult:
    def __init__(self, row: FakeUserRow | None) -> None:
        self.row = row

    def fetchone(self) -> FakeUserRow | None:
        return self.row


class FakeAuthConnection:
    def __init__(
        self,
        result: FakeAuthResult | None = None,
        error: Exception | None = None,
        insert_error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.insert_error = insert_error
        self.inserted_parameters: dict[str, Any] | None = None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeAuthResult:
        if self.error is not None:
            raise self.error
        if "email" in parameters:
            assert parameters["email"] == "li.ming@example.com"
            assert "password" not in parameters
            assert self.result is not None
            return self.result

        if self.insert_error is not None:
            raise self.insert_error
        self.inserted_parameters = parameters
        return FakeAuthResult(None)

    async def commit(self) -> None:
        return None


class FakeAuthContext:
    def __init__(self, connection: FakeAuthConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeAuthConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeAuthEngine:
    def __init__(self, connection: FakeAuthConnection) -> None:
        self.connection = connection
        self.connect_calls = 0
        self.begin_calls = 0

    def connect(self) -> FakeAuthContext:
        self.connect_calls += 1
        if self.connect_calls > 1:
            raise AssertionError("Refresh token persistence must use engine.begin().")
        return FakeAuthContext(self.connection)

    def begin(self) -> FakeAuthContext:
        self.begin_calls += 1
        return FakeAuthContext(self.connection)


class FakeCurrentUserRow:
    def __init__(self, user_id: str, email: str, role: str) -> None:
        self.id = user_id
        self.email = email
        self.role = role


class FakeCurrentUserResult:
    def __init__(self, row: FakeCurrentUserRow | None) -> None:
        self.row = row

    def fetchone(self) -> FakeCurrentUserRow | None:
        return self.row


class FakeCurrentUserConnection:
    def __init__(self, result: FakeCurrentUserResult, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeCurrentUserResult:
        if self.error is not None:
            raise self.error
        assert parameters["user_id"] == "demo-user-li"
        return self.result


class FakeCurrentUserContext:
    def __init__(self, connection: FakeCurrentUserConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeCurrentUserConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeCurrentUserEngine:
    def __init__(self, connection: FakeCurrentUserConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeCurrentUserContext:
        return FakeCurrentUserContext(self.connection)


def test_current_user_response_restricts_role_values() -> None:
    response = CurrentUserResponse(
        id="demo-user-li",
        email="li.ming@example.com",
        role="ADMIN",
    )
    assert response.role == "ADMIN"

    with pytest.raises(ValidationError):
        CurrentUserResponse(
            id="demo-user-li",
            email="li.ming@example.com",
            role="UNKNOWN",
        )


def make_access_headers(user_id: str = "demo-user-li") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": "ecommerce-agent-system",
            "token_type": "access",
        },
        "test-only-jwt-secret-at-least-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class FakeRefreshRow:
    def __init__(
        self,
        token_id: str,
        user_id: str,
        expires_at: datetime,
        revoked_at: datetime | None = None,
    ) -> None:
        self.id = token_id
        self.user_id = user_id
        self.expires_at = expires_at
        self.revoked_at = revoked_at


class FakeRefreshResult:
    def __init__(self, row: FakeRefreshRow | None) -> None:
        self.row = row

    def fetchone(self) -> FakeRefreshRow | None:
        return self.row


class FakeRefreshConnection:
    def __init__(self, row: FakeRefreshRow | None, error: Exception | None = None) -> None:
        self.row = row
        self.error = error
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> FakeRefreshResult:
        if self.error is not None:
            raise self.error
        statement_text = str(statement)
        self.statements.append((statement_text, parameters))
        if "SELECT" in statement_text:
            return FakeRefreshResult(self.row)
        if self.row is not None and "revoked_at" in parameters:
            self.row.revoked_at = parameters["revoked_at"]
        return FakeRefreshResult(None)


class FakeRefreshContext:
    def __init__(self, connection: FakeRefreshConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeRefreshConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeRefreshEngine:
    def __init__(self, connection: FakeRefreshConnection) -> None:
        self.connection = connection
        self.begin_calls = 0

    def begin(self) -> FakeRefreshContext:
        self.begin_calls += 1
        return FakeRefreshContext(self.connection)


@pytest.mark.asyncio
async def test_login_returns_access_token_for_valid_credentials(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(FakeAuthResult(FakeUserRow("demo-user-li", "hash")))
    )
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: True)

    response = await client.post(
        "/v1/auth/login",
        json={"email": " LI.MING@EXAMPLE.COM ", "password": "dev-password"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    assert app.state.database_engine.connection.inserted_parameters is not None
    stored = app.state.database_engine.connection.inserted_parameters
    assert stored["token_hash"] != response.json()["refresh_token"]
    assert len(stored["token_hash"]) == 64
    assert app.state.database_engine.begin_calls == 1


def test_auth_request_normalizes_email_but_preserves_password_spaces() -> None:
    payload = AuthRequest(email=" LI.MING@EXAMPLE.COM ", password=" pass word ")

    assert payload.email == "li.ming@example.com"
    assert payload.password == " pass word "


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "", "password": "secret"},
        {"email": "   ", "password": "secret"},
        {"email": "li.ming@example.com", "password": ""},
        {"email": "li.ming@example.com", "password": "x" * 257},
    ],
)
def test_auth_request_rejects_empty_or_oversized_values(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        AuthRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_login_token_is_accepted_by_protected_dependency(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(FakeAuthResult(FakeUserRow("demo-user-li", "hash")))
    )
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: True)

    async def protected_probe(
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, str]:
        return {"user_id": user_id}

    app.add_api_route("/test-only/auth-probe", protected_probe, methods=["GET"])

    login_response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )
    access_token = login_response.json()["access_token"]

    response = await client.get(
        "/test-only/auth-probe",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "demo-user-li"}


@pytest.mark.asyncio
async def test_login_uses_configured_access_token_ttl(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(FakeAuthResult(FakeUserRow("demo-user-li", "hash")))
    )
    app.state.settings.jwt_access_token_ttl_seconds = 60
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: True)

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )
    payload = jwt.decode(
        response.json()["access_token"],
        "test-only-jwt-secret-at-least-32-bytes",
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    remaining = payload["exp"] - int(datetime.now(UTC).timestamp())
    assert 55 <= remaining <= 60


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [FakeUserRow("demo-user-li", "hash"), None, FakeUserRow("demo-user-li", None)],
)
async def test_login_rejects_invalid_or_missing_credentials(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    row: FakeUserRow | None,
) -> None:
    app.state.database_engine = FakeAuthEngine(FakeAuthConnection(FakeAuthResult(row)))
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: False)

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码错误。"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_login_sanitizes_database_failure(app: FastAPI, client: AsyncClient) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(error=SQLAlchemyError("sensitive database detail"))
    )

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_login_sanitizes_corrupted_password_hash(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(FakeAuthResult(FakeUserRow("demo-user-li", "corrupted-hash")))
    )

    def raise_verification_error(password: str, password_hash: str) -> bool:
        raise VerificationError("corrupted hash details")

    monkeypatch.setattr("app.api.routes.auth.verify_password", raise_verification_error)

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_login_sanitizes_missing_jwt_secret(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(FakeAuthResult(FakeUserRow("demo-user-li", "hash")))
    )
    app.state.settings.jwt_secret_key = None
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: True)

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_login_does_not_return_tokens_when_refresh_persistence_fails(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.database_engine = FakeAuthEngine(
        FakeAuthConnection(
            FakeAuthResult(FakeUserRow("demo-user-li", "hash")),
            insert_error=SQLAlchemyError("sensitive insert detail"),
        )
    )
    monkeypatch.setattr("app.api.routes.auth.verify_password", lambda password, hashed: True)

    response = await client.post(
        "/v1/auth/login",
        json={"email": "li.ming@example.com", "password": "dev-password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_refresh_rotates_token_in_one_transaction(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    old_token_id = "refresh-row-001"
    app.state.database_engine = FakeRefreshEngine(
        FakeRefreshConnection(
            FakeRefreshRow(
                old_token_id,
                "demo-user-li",
                datetime.now(UTC) + timedelta(days=1),
            )
        )
    )

    response = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "raw-refresh-token"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    engine = app.state.database_engine
    assert engine.begin_calls == 1
    assert len(engine.connection.statements) == 3
    update_params = engine.connection.statements[1][1]
    insert_params = engine.connection.statements[2][1]
    assert update_params["id"] == old_token_id
    assert insert_params["token_hash"] != response.json()["refresh_token"]


@pytest.mark.asyncio
async def test_refresh_requires_request_model_field(app: FastAPI, client: AsyncClient) -> None:
    app.state.database_engine = FakeRefreshEngine(FakeRefreshConnection(None))

    response = await client.post("/v1/auth/refresh", json={})

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_token", ["", "   ", "x" * 513])
async def test_refresh_rejects_empty_or_oversized_token(
    app: FastAPI,
    client: AsyncClient,
    refresh_token: str,
) -> None:
    app.state.database_engine = FakeRefreshEngine(FakeRefreshConnection(None))

    response = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_refresh_sanitizes_database_failure(app: FastAPI, client: AsyncClient) -> None:
    app.state.database_engine = FakeRefreshEngine(
        FakeRefreshConnection(None, error=SQLAlchemyError("sensitive refresh detail"))
    )

    response = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "raw-refresh-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_logout_is_idempotent_and_returns_no_content(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    engine = FakeRefreshEngine(FakeRefreshConnection(None))
    app.state.database_engine = engine

    first_response = await client.post(
        "/v1/auth/logout",
        json={"refresh_token": "raw-refresh-token"},
    )
    second_response = await client.post(
        "/v1/auth/logout",
        json={"refresh_token": "raw-refresh-token"},
    )

    assert first_response.status_code == 204
    assert first_response.content == b""
    assert second_response.status_code == 204
    assert engine.begin_calls == 2
    assert len(engine.connection.statements) == 2
    assert engine.connection.statements[0][1]["token_hash"] != "raw-refresh-token"


@pytest.mark.asyncio
async def test_logout_sanitizes_database_failure(app: FastAPI, client: AsyncClient) -> None:
    app.state.database_engine = FakeRefreshEngine(
        FakeRefreshConnection(None, error=SQLAlchemyError("sensitive logout detail"))
    )

    response = await client.post(
        "/v1/auth/logout",
        json={"refresh_token": "raw-refresh-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_revoked_refresh_token_cannot_be_used_again(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    row = FakeRefreshRow(
        "refresh-row-001",
        "demo-user-li",
        datetime.now(UTC) + timedelta(days=1),
    )
    engine = FakeRefreshEngine(FakeRefreshConnection(row))
    app.state.database_engine = engine

    logout_response = await client.post(
        "/v1/auth/logout",
        json={"refresh_token": "raw-refresh-token"},
    )
    refresh_response = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "raw-refresh-token"},
    )

    assert logout_response.status_code == 204
    assert refresh_response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_database_role_for_authenticated_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeCurrentUserEngine(
        FakeCurrentUserConnection(
            FakeCurrentUserResult(
                FakeCurrentUserRow("demo-user-li", "li.ming@example.com", "SUPPORT")
            )
        )
    )

    response = await client.get("/v1/auth/me", headers=make_access_headers())

    assert response.status_code == 200
    assert response.json() == {
        "id": "demo-user-li",
        "email": "li.ming@example.com",
        "role": "SUPPORT",
    }


@pytest.mark.asyncio
async def test_me_rejects_jwt_for_missing_database_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeCurrentUserEngine(
        FakeCurrentUserConnection(FakeCurrentUserResult(None))
    )

    response = await client.get("/v1/auth/me", headers=make_access_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "用户不存在。"}


@pytest.mark.asyncio
async def test_me_sanitizes_database_failure(app: FastAPI, client: AsyncClient) -> None:
    app.state.database_engine = FakeCurrentUserEngine(
        FakeCurrentUserConnection(
            FakeCurrentUserResult(None),
            error=SQLAlchemyError("sensitive user detail"),
        )
    )

    response = await client.get("/v1/auth/me", headers=make_access_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}
