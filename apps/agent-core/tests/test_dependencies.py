from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from app.api.dependencies import get_current_refund_approver_id
from app.core.config import Settings

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"


def make_auth_headers(sub: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": sub,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
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


@pytest.mark.asyncio
async def test_refund_approver_dependency_allows_configured_user(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    settings = cast(Settings, app.state.settings)
    settings.refund_approver_user_id = "staff-zhang"
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 200
    assert response.json() == {"approver_id": "staff-zhang"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_rejects_authenticated_non_approver(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    settings = cast(Settings, app.state.settings)
    settings.refund_approver_user_id = "staff-zhang"
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("demo-user-li"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "无权审批退款申请。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_reports_unconfigured_service(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    add_refund_approver_probe(app)

    response = await client.get(
        "/test-only/refund-approver",
        headers=make_auth_headers("staff-zhang"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款审批服务尚未配置。"}


@pytest.mark.asyncio
async def test_refund_approver_dependency_preserves_authentication_failure(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    settings = cast(Settings, app.state.settings)
    settings.refund_approver_user_id = "staff-zhang"
    add_refund_approver_probe(app)

    response = await client.get("/test-only/refund-approver")

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
