from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import (
    create_access_token,
    get_current_refund_approver_id,
    get_current_user_role,
)
from app.core.config import Settings
from tests.conftest import FakeRoleEngine

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"
TEST_JWT_ISSUER = "ecommerce-agent-system"


def test_create_access_token_round_trips_with_expected_claims() -> None:
    settings = Settings.model_construct(
        jwt_secret_key=SecretStr(TEST_JWT_SECRET),
        jwt_issuer=TEST_JWT_ISSUER,
    )

    token = create_access_token("demo-user-li", settings, ttl_seconds=300)
    payload = jwt.decode(
        token,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
        issuer=TEST_JWT_ISSUER,
        options={"require": ["sub", "exp", "iss", "token_type"]},
    )

    assert payload["sub"] == "demo-user-li"
    assert payload["iss"] == TEST_JWT_ISSUER
    assert payload["token_type"] == "access"


def test_create_access_token_requires_secret() -> None:
    settings = Settings.model_construct(
        jwt_secret_key=None,
        jwt_issuer=TEST_JWT_ISSUER,
    )

    with pytest.raises(ValueError):
        create_access_token("demo-user-li", settings)


def make_auth_headers(sub: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": sub,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": TEST_JWT_ISSUER,
            "token_type": "access",
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def make_headers_with_issuer(issuer: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "demo-user-li",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": issuer,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def make_headers_with_token_type(token_type: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "demo-user-li",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": "ecommerce-agent-system",
            "token_type": token_type,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def add_refund_approver_probe(app: FastAPI) -> None:
    async def refund_approver_probe(
        approver_id: Annotated[
            str,
            Depends(get_current_refund_approver_id),
        ],
    ) -> dict[str, str]:
        return {"approver_id": approver_id}

    app.add_api_route(
        "/test-only/refund-approver",
        refund_approver_probe,
        methods=["GET"],
    )


def add_user_role_probe(app: FastAPI) -> None:
    async def user_role_probe(
        role: Annotated[str, Depends(get_current_user_role)],
    ) -> dict[str, str]:
        return {"role": role}

    app.add_api_route(
        "/test-only/user-role",
        user_role_probe,
        methods=["GET"],
    )


@pytest.mark.asyncio
async def test_current_user_role_reads_database_role(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"demo-user-li": "SUPPORT"})
    add_user_role_probe(app)

    response = await client.get(
        "/test-only/user-role",
        headers=make_auth_headers("demo-user-li"),
    )

    assert response.status_code == 200
    assert response.json() == {"role": "SUPPORT"}


@pytest.mark.asyncio
async def test_current_user_role_returns_not_found_for_missing_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({})
    add_user_role_probe(app)

    response = await client.get(
        "/test-only/user-role",
        headers=make_auth_headers("missing-user"),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "用户不存在。"}


@pytest.mark.asyncio
async def test_current_user_role_sanitizes_database_failure(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine(
        {},
        error=SQLAlchemyError("sensitive role detail"),
    )
    add_user_role_probe(app)

    response = await client.get(
        "/test-only/user-role",
        headers=make_auth_headers("demo-user-li"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}
    assert "sensitive role detail" not in response.text


@pytest.mark.asyncio
async def test_refund_approver_dependency_allows_configured_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"staff-zhang": "ADMIN"})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 200
    assert response.json() == {"approver_id": "staff-zhang"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_allows_other_admin_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"staff-li": "ADMIN"})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-li"),
    )

    assert response.status_code == 200
    assert response.json() == {"approver_id": "staff-li"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_rejects_authenticated_non_approver(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"demo-user-li": "CUSTOMER"})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("demo-user-li"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "无权审批退款申请。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_ignores_legacy_configuration(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"staff-zhang": "ADMIN"})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 200
    assert response.json() == {"approver_id": "staff-zhang"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_rejects_non_admin_role(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"staff-zhang": "SUPPORT"})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "无权审批退款申请。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_rejects_unknown_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({})
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "无权审批退款申请。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_sanitizes_database_failure(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine(
        {"staff-zhang": "ADMIN"},
        error=SQLAlchemyError("sensitive role database detail"),
    )
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试。"}
    assert "sensitive role database detail" not in response.text


@pytest.mark.asyncio
async def test_refund_approver_role_revocation_applies_to_next_request(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    add_refund_approver_probe(app)

    app.state.database_engine = FakeRoleEngine({"staff-zhang": "ADMIN"})
    first_response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    app.state.database_engine = FakeRoleEngine({"staff-zhang": "SUPPORT"})
    second_response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 403
    assert second_response.json() == {"detail": "无权审批退款申请。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_preserves_authentication_failure(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    add_refund_approver_probe(app)

    response = await client.get("/test-only/refund-approver")

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}


@pytest.mark.asyncio
async def test_authentication_rejects_token_from_another_issuer(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_headers_with_issuer("other-service"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "访问令牌无效或已过期。"}


@pytest.mark.asyncio
async def test_authentication_rejects_refresh_token_on_api_route(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_headers_with_token_type("refresh"),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "访问令牌无效或已过期。"}
