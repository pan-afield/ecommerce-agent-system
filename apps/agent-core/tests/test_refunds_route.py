import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.refunds import RefundReviewPayload
from app.services.refund import RefundApplicationRecord, RefundExecutionRecord
from tests.conftest import FakeRoleEngine

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"
TEST_JWT_ISSUER = "ecommerce-agent-system"


def make_auth_headers(sub: str = "demo-user-li") -> dict[str, str]:
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


def make_confirmed_application() -> RefundApplicationRecord:
    return RefundApplicationRecord(
        id="refund-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="PENDING_MANUAL_APPROVAL",
    )


def make_reviewed_application() -> RefundApplicationRecord:
    return RefundApplicationRecord(
        id="refund-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="APPROVED",
        reviewed_by_user_id="staff-zhang",
        reviewed_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        review_note="已人工核对。",
    )


def configure_refund_approver(app: FastAPI) -> None:
    app.state.database_engine = FakeRoleEngine(
        {
            "staff-zhang": "ADMIN",
            "demo-user-li": "CUSTOMER",
        }
    )


def test_refund_review_payload_normalizes_review_note() -> None:
    assert (
        RefundReviewPayload(
            decision="APPROVED",
            review_note="  已人工核对。  ",
        ).review_note
        == "已人工核对。"
    )
    assert RefundReviewPayload(decision="REJECTED", review_note="   ").review_note is None


def test_refund_review_payload_rejects_overlong_review_note() -> None:
    with pytest.raises(ValidationError):
        RefundReviewPayload(decision="REJECTED", review_note="x" * 501)


@pytest.mark.asyncio
async def test_confirm_refund_returns_same_result_for_retry(
    client: AsyncClient,
) -> None:
    application = make_confirmed_application()

    with patch(
        "app.api.routes.refunds.confirm_refund_application",
        new=AsyncMock(side_effect=[application, application]),
    ) as confirm_application:
        first = await client.post(
            "/v1/refund-applications/refund-001/confirm",
            headers=make_auth_headers(),
        )
        retried = await client.post(
            "/v1/refund-applications/refund-001/confirm",
            headers=make_auth_headers(),
        )

    expected = {
        "id": "refund-001",
        "order_id": "order-demo-001",
        "request_id": "refund-request-001",
        "requested_amount": "88.00",
        "currency": "CNY",
        "status": "PENDING_MANUAL_APPROVAL",
    }
    assert first.status_code == 200
    assert retried.status_code == 200
    assert first.json() == expected
    assert retried.json() == expected
    assert confirm_application.await_count == 2


@pytest.mark.asyncio
async def test_confirm_refund_hides_unavailable_or_other_user_application(
    client: AsyncClient,
) -> None:
    with patch(
        "app.api.routes.refunds.confirm_refund_application",
        new=AsyncMock(return_value=None),
    ) as confirm_application:
        response = await client.post(
            "/v1/refund-applications/refund-other-user/confirm",
            headers=make_auth_headers(sub="demo-user-li"),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "退款申请不存在。"}
    assert confirm_application.await_args is not None
    assert confirm_application.await_args.kwargs["user_id"] == "demo-user-li"


@pytest.mark.asyncio
async def test_confirm_refund_requires_authentication(
    client: AsyncClient,
) -> None:
    with patch(
        "app.api.routes.refunds.confirm_refund_application",
        new=AsyncMock(),
    ) as confirm_application:
        response = await client.post(
            "/v1/refund-applications/refund-001/confirm"
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    confirm_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_refund_sanitizes_database_failure(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.confirm_refund_application",
            new=AsyncMock(
                side_effect=SQLAlchemyError("sensitive confirmation database detail")
            ),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/confirm",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive confirmation database detail" not in response.text
    assert "sensitive confirmation database detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text
    assert "Refund confirmation failed" in caplog.text


@pytest.mark.asyncio
async def test_review_refund_returns_approved_application_for_configured_approver(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)
    reviewed_application = make_reviewed_application()

    with (
        patch(
            "app.api.routes.refunds.try_review_refund_application",
            new=AsyncMock(return_value=True),
        ) as try_review,
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=reviewed_application),
        ) as fetch_application,
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "APPROVED", "review_note": "已人工核对。"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "refund-001",
        "order_id": "order-demo-001",
        "request_id": "refund-request-001",
        "requested_amount": "88.00",
        "currency": "CNY",
        "status": "APPROVED",
        "reviewed_by_user_id": "staff-zhang",
        "reviewed_at": "2026-08-23T10:00:00Z",
        "review_note": "已人工核对。",
    }
    assert try_review.await_args is not None
    assert try_review.await_args.kwargs["reviewer_user_id"] == "staff-zhang"
    assert fetch_application.await_count == 1


@pytest.mark.asyncio
async def test_review_refund_rejects_non_approver(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)

    with patch(
        "app.api.routes.refunds.try_review_refund_application",
        new=AsyncMock(),
    ) as try_review:
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "REJECTED"},
            headers=make_auth_headers("demo-user-li"),
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "无权审批退款申请。"}
    try_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_refund_requires_authentication(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)

    with patch(
        "app.api.routes.refunds.try_review_refund_application",
        new=AsyncMock(),
    ) as try_review:
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "APPROVED"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    try_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_refund_uses_database_admin_role_without_legacy_configuration(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.database_engine = FakeRoleEngine({"staff-zhang": "ADMIN"})
    reviewed_application = make_reviewed_application()
    with patch(
        "app.api.routes.refunds.try_review_refund_application",
        new=AsyncMock(return_value=True),
    ), patch(
        "app.api.routes.refunds.fetch_refund_application_by_id",
        new=AsyncMock(return_value=reviewed_application),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "APPROVED"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_review_refund_hides_missing_application_after_failed_transition(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)

    with (
        patch(
            "app.api.routes.refunds.try_review_refund_application",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=None),
        ) as fetch_application,
    ):
        response = await client.post(
            "/v1/refund-applications/missing/review",
            json={"decision": "APPROVED"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "退款申请不存在。"}
    fetch_application.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_refund_reports_conflict_after_failed_transition(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)
    existing = make_reviewed_application()

    with (
        patch(
            "app.api.routes.refunds.try_review_refund_application",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=existing),
        ),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "REJECTED"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "退款申请已被其他审批人处理，请刷新后重试。"
    }


@pytest.mark.asyncio
async def test_review_refund_returns_same_result_for_same_decision_retry(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)
    existing = make_reviewed_application()

    with (
        patch(
            "app.api.routes.refunds.try_review_refund_application",
            new=AsyncMock(return_value=False),
        ) as try_review,
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=existing),
        ) as fetch_application,
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "APPROVED", "review_note": "重复提交"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert response.json()["review_note"] == "已人工核对。"
    try_review.assert_awaited_once()
    assert fetch_application.await_count == 1


@pytest.mark.asyncio
async def test_review_refund_validates_decision_and_note_length(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    configure_refund_approver(app)

    with patch(
        "app.api.routes.refunds.try_review_refund_application",
        new=AsyncMock(),
    ) as try_review:
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "WAITING", "review_note": "x" * 501},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 422
    try_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_refund_sanitizes_database_failure(
    app: FastAPI,
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_refund_approver(app)

    with (
        patch(
            "app.api.routes.refunds.try_review_refund_application",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive review detail")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/review",
            json={"decision": "APPROVED"},
            headers=make_auth_headers("staff-zhang"),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive review detail" not in response.text
    assert "sensitive review detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_refund_execution_returns_owner_snapshot_without_idempotency_key(
    app: FastAPI, client: AsyncClient
) -> None:
    record = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="PROCESSING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    with patch(
        "app.api.routes.refunds.fetch_refund_execution", new=AsyncMock(return_value=record)
    ) as fetch:
        response = await client.get(
            "/v1/refund-applications/refund-001/execution",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "execution-001",
        "status": "PROCESSING",
        "amount": "88.25",
        "currency": "CNY",
        "provider_reference": None,
    }
    fetch.assert_awaited_once_with(
        engine=app.state.database_engine,
        user_id="demo-user-li",
        refund_application_id="refund-001",
    )


@pytest.mark.asyncio
async def test_refund_execution_requires_login(client: AsyncClient) -> None:
    with patch(
        "app.api.routes.refunds.fetch_refund_execution", new=AsyncMock()
    ) as fetch:
        response = await client.get("/v1/refund-applications/refund-001/execution")

    assert response.status_code == 401
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_execution_returns_null_for_owned_application_without_execution(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    application = RefundApplicationRecord(
        id="refund-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.25"),
        currency="CNY",
        status="APPROVED",
    )
    with (
        patch(
            "app.api.routes.refunds.fetch_refund_execution",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=application),
        ) as fetch_application,
    ):
        response = await client.get(
            "/v1/refund-applications/refund-001/execution",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() is None
    fetch_application.assert_awaited_once_with(
        app.state.database_engine,
        application_id="refund-001",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "application",
    [
        None,
        RefundApplicationRecord(
            id="refund-other-user",
            user_id="other-user",
            order_id="order-other-user",
            request_id="refund-request-other-user",
            requested_amount=Decimal("88.25"),
            currency="CNY",
            status="APPROVED",
        ),
    ],
)
async def test_refund_execution_hides_other_or_missing_application(
    client: AsyncClient,
    application: RefundApplicationRecord | None,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.fetch_refund_execution",
            new=AsyncMock(return_value=None),
        ) as fetch,
        patch(
            "app.api.routes.refunds.fetch_refund_application_by_id",
            new=AsyncMock(return_value=application),
        ),
    ):
        response = await client.get(
            "/v1/refund-applications/refund-other-user/execution",
            headers=make_auth_headers(),
        )

    assert response.status_code == 404
    assert fetch.await_args is not None
    assert fetch.await_args.kwargs["user_id"] == "demo-user-li"


@pytest.mark.asyncio
async def test_refund_execution_sanitizes_database_failure(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.fetch_refund_execution",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive refund database detail")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.get(
            "/v1/refund-applications/refund-001/execution",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive refund database detail" not in response.text
    assert "sensitive refund database detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text
    assert "Refund status failed" in caplog.text


@pytest.mark.asyncio
async def test_refund_execution_returns_failed_snapshot_without_idempotency_key(
    client: AsyncClient,
) -> None:
    record = RefundExecutionRecord(
        id="execution-failed",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="FAILED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    with patch(
        "app.api.routes.refunds.fetch_refund_execution", new=AsyncMock(return_value=record)
    ):
        response = await client.get(
            "/v1/refund-applications/refund-001/execution",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "execution-failed",
        "status": "FAILED",
        "amount": "88.25",
        "currency": "CNY",
        "provider_reference": None,
    }
    assert "idempotency_key" not in response.text


@pytest.mark.asyncio
async def test_start_refund_execute_returns_persisted_execution(
    app: FastAPI, client: AsyncClient
) -> None:
    record = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="SUCCEEDED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference="sandbox-ref-001",
    )
    generated_id = UUID("11111111-1111-1111-1111-111111111111")
    with (
        patch("app.api.routes.refunds.uuid4", return_value=generated_id),
        patch(
            "app.api.routes.refunds.run_refund_sandbox", new=AsyncMock(return_value=record)
        ) as run_sandbox,
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/execute",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "execution-001",
        "status": "SUCCEEDED",
        "amount": "88.25",
        "currency": "CNY",
        "provider_reference": "sandbox-ref-001",
    }
    assert run_sandbox.await_args is not None
    assert run_sandbox.await_args.kwargs["engine"] is app.state.database_engine
    assert run_sandbox.await_args.kwargs["user_id"] == "demo-user-li"
    assert run_sandbox.await_args.kwargs["refund_application_id"] == "refund-001"
    assert run_sandbox.await_args.kwargs["execution_id"] == str(generated_id)
    assert run_sandbox.await_args.kwargs["adapter"] is app.state.refund_sandbox_adapter


@pytest.mark.asyncio
async def test_start_refund_execute_hides_missing_or_other_application(
    client: AsyncClient,
) -> None:
    with patch(
        "app.api.routes.refunds.run_refund_sandbox", new=AsyncMock(return_value=None)
    ) as run_sandbox:
        response = await client.post(
            "/v1/refund-applications/refund-other-user/execute",
            headers=make_auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "退款申请不存在。"}
    run_sandbox.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_refund_execute_requires_login(client: AsyncClient) -> None:
    with patch(
        "app.api.routes.refunds.run_refund_sandbox", new=AsyncMock()
    ) as run_sandbox:
        response = await client.post("/v1/refund-applications/refund-001/execute")

    assert response.status_code == 401
    run_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_refund_execute_sanitizes_database_failure(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.run_refund_sandbox",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive execution database detail")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/execute",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive execution database detail" not in response.text
    assert "sensitive execution database detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_start_refund_execute_maps_timeout_to_unknown_result(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.run_refund_sandbox",
            new=AsyncMock(side_effect=TimeoutError("sensitive provider timeout")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/execute",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款结果暂时未知，请稍后查询。"}
    assert "sensitive provider timeout" not in response.text
    assert "sensitive provider timeout" not in caplog.text
    assert "TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_start_refund_execute_maps_sandbox_contract_error(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.run_refund_sandbox",
            new=AsyncMock(side_effect=RuntimeError("sensitive provider payload")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/execute",
            headers=make_auth_headers(),
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "退款服务响应无效，请稍后重试。"}
    assert "sensitive provider payload" not in response.text
    assert "sensitive provider payload" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_recover_refund_returns_existing_execution_snapshot(
    app: FastAPI, client: AsyncClient
) -> None:
    record = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="SUCCEEDED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference="provider-ref-001",
    )
    with patch(
        "app.api.routes.refunds.recover_refund_sandbox", new=AsyncMock(return_value=record)
    ) as recover:
        response = await client.post(
            "/v1/refund-applications/refund-001/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "execution-001",
        "status": "SUCCEEDED",
        "amount": "88.25",
        "currency": "CNY",
        "provider_reference": "provider-ref-001",
    }
    recover.assert_awaited_once_with(
        engine=app.state.database_engine,
        user_id="demo-user-li",
        refund_application_id="refund-001",
        adapter=app.state.refund_sandbox_adapter,
    )


@pytest.mark.asyncio
async def test_recover_refund_returns_failed_snapshot_without_idempotency_key(
    client: AsyncClient,
) -> None:
    record = RefundExecutionRecord(
        id="execution-failed",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="FAILED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    with patch(
        "app.api.routes.refunds.recover_refund_sandbox", new=AsyncMock(return_value=record)
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "execution-failed",
        "status": "FAILED",
        "amount": "88.25",
        "currency": "CNY",
        "provider_reference": None,
    }
    assert "idempotency_key" not in response.text


@pytest.mark.asyncio
async def test_recover_refund_hides_missing_or_other_application(
    client: AsyncClient,
) -> None:
    with patch(
        "app.api.routes.refunds.recover_refund_sandbox", new=AsyncMock(return_value=None)
    ) as recover:
        response = await client.post(
            "/v1/refund-applications/refund-other-user/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "退款申请不存在。"}
    recover.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_refund_requires_login(client: AsyncClient) -> None:
    with patch(
        "app.api.routes.refunds.recover_refund_sandbox", new=AsyncMock()
    ) as recover:
        response = await client.post("/v1/refund-applications/refund-001/recover")

    assert response.status_code == 401
    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_refund_maps_database_failure(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.recover_refund_sandbox",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive recovery database detail")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive recovery database detail" not in response.text
    assert "sensitive recovery database detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_recover_refund_maps_timeout_to_unknown_result(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.recover_refund_sandbox",
            new=AsyncMock(side_effect=TimeoutError("sensitive recovery timeout")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款结果暂时未知，请稍后查询。"}
    assert "sensitive recovery timeout" not in response.text
    assert "sensitive recovery timeout" not in caplog.text
    assert "TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_recover_refund_maps_sandbox_contract_error(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch(
            "app.api.routes.refunds.recover_refund_sandbox",
            new=AsyncMock(side_effect=RuntimeError("sensitive recovery payload")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/refund-applications/refund-001/recover",
            headers=make_auth_headers(),
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "退款服务响应无效，请稍后重试。"}
    assert "sensitive recovery payload" not in response.text
    assert "sensitive recovery payload" not in caplog.text
    assert "RuntimeError" in caplog.text
