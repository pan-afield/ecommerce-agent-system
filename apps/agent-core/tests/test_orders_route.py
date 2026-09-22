import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Self, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.api.routes.orders import (
    RefundApplicationPayload,
    RefundApplicationResponse,
    RefundAssessmentPayload,
    load_owned_order,
)
from app.services.orders import (
    OrderDetailResponse,
    ShipmentEventResponse,
    fetch_owned_order,
)
from app.services.refund import RefundApplicationRecord

DatabaseValue = str | Decimal | datetime | None
DatabaseRow = dict[str, DatabaseValue]
TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"
TEST_JWT_ISSUER = "ecommerce-agent-system"


def test_refund_assessment_payload_parses_decimal_and_normalizes_currency() -> None:
    payload = RefundAssessmentPayload(
        requested_amount="88.00",
        requested_currency=" cny ",
    )

    assert payload.requested_amount == Decimal("88.00")
    assert payload.requested_currency == "CNY"


def test_refund_assessment_payload_leaves_amount_rule_to_service() -> None:
    payload = RefundAssessmentPayload(
        requested_amount="0",
        requested_currency="CNY",
    )

    assert payload.requested_amount == Decimal("0")


@pytest.mark.parametrize("currency", ["", "CN", "CNYX"])
def test_refund_assessment_payload_rejects_invalid_currency_length(
    currency: str,
) -> None:
    with pytest.raises(ValidationError):
        RefundAssessmentPayload(
            requested_amount="88.00",
            requested_currency=currency,
        )


def test_refund_application_payload_normalizes_id_and_currency() -> None:
    payload = RefundApplicationPayload(
        request_id=" refund-request-001 ",
        requested_amount="88.00",
        requested_currency=" cny ",
    )

    assert payload.request_id == "refund-request-001"
    assert payload.requested_amount == Decimal("88.00")
    assert payload.requested_currency == "CNY"


@pytest.mark.parametrize("request_id", ["", "   ", "x" * 129])
def test_refund_application_payload_rejects_invalid_request_id(
    request_id: str,
) -> None:
    with pytest.raises(ValidationError):
        RefundApplicationPayload(
            request_id=request_id,
            requested_amount="88.00",
            requested_currency="CNY",
        )


def test_refund_application_response_serializes_decimal_as_json_string() -> None:
    response = RefundApplicationResponse(
        id="refund-001",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="AWAITING_CUSTOMER_CONFIRMATION",
        created=True,
    )

    assert response.model_dump(mode="json")["requested_amount"] == "88.00"


def make_auth_headers(
    sub: str = "demo-user-li",
    *,
    secret: str = TEST_JWT_SECRET,
    expires_at: datetime | None = None,
    include_sub: bool = True,
) -> dict[str, str]:
    payload: dict[str, str | datetime] = {
        "exp": expires_at or datetime.now(UTC) + timedelta(minutes=5),
        "iss": TEST_JWT_ISSUER,
        "token_type": "access",
    }
    if include_sub:
        payload["sub"] = sub

    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def make_order_detail() -> OrderDetailResponse:
    return OrderDetailResponse(
        id="order-demo-001",
        order_number="EC-20260810-001",
        status="shipped",
        total_amount=Decimal("299.00"),
        currency="CNY",
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=UTC),
        shipment_events=[
            ShipmentEventResponse(
                id="shipment-event-001",
                status="confirmed",
                description="商家已确认订单",
                location="杭州市",
                occurred_at=datetime(2026, 8, 8, 1, 15, tzinfo=UTC),
            ),
            ShipmentEventResponse(
                id="shipment-event-002",
                status="packed",
                description="商品已完成打包",
                location="杭州市",
                occurred_at=datetime(2026, 8, 8, 8, 45, tzinfo=UTC),
            ),
            ShipmentEventResponse(
                id="shipment-event-003",
                status="shipped",
                description="包裹已从上海分拨中心发出",
                location="上海市",
                occurred_at=datetime(2026, 8, 9, 3, 30, tzinfo=UTC),
            ),
        ],
    )


class FakeOrderResult:
    def __init__(self, rows: list[DatabaseRow]) -> None:
        self._rows = rows

    def mappings(self) -> Self:
        return self

    def one_or_none(self) -> DatabaseRow | None:
        if len(self._rows) > 1:
            raise AssertionError("Expected at most one fake database row.")
        return self._rows[0] if self._rows else None

    def all(self) -> list[DatabaseRow]:
        return self._rows


class FakeOrderConnection:
    def __init__(self, results: list[FakeOrderResult]) -> None:
        self._results = results
        self.executions: list[tuple[TextClause, dict[str, str]]] = []

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, str],
    ) -> FakeOrderResult:
        self.executions.append((statement, parameters))
        if not self._results:
            raise AssertionError("Unexpected extra fake database query.")
        return self._results.pop(0)


class FakeConnectionContext:
    def __init__(self, connection: FakeOrderConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeOrderConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeOrderEngine:
    def __init__(self, connection: FakeOrderConnection) -> None:
        self._connection = connection

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(self._connection)


class FailingConnectionContext:
    async def __aenter__(self) -> FakeOrderConnection:
        raise SQLAlchemyError("sensitive database connection detail")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FailingOrderEngine:
    def connect(self) -> FailingConnectionContext:
        return FailingConnectionContext()


def install_fake_order_engine(
    app: FastAPI,
    order_row: DatabaseRow | None,
    shipment_rows: list[DatabaseRow] | None = None,
) -> FakeOrderConnection:
    results = [FakeOrderResult([order_row] if order_row is not None else [])]
    if order_row is not None:
        results.append(FakeOrderResult(shipment_rows or []))
    connection = FakeOrderConnection(results)
    app.state.database_engine = FakeOrderEngine(connection)
    return connection


@pytest.mark.asyncio
async def test_order_route_returns_loaded_order(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    response = await client.get("/v1/orders/order-demo-001")

    assert response.status_code == 200
    assert response.json() == {
        "id": "order-demo-001",
        "order_number": "EC-20260810-001",
        "status": "shipped",
        "total_amount": "299.00",
        "currency": "CNY",
        "created_at": "2026-08-10T08:30:00Z",
        "shipment_events": [
            {
                "id": "shipment-event-001",
                "status": "confirmed",
                "description": "商家已确认订单",
                "location": "杭州市",
                "occurred_at": "2026-08-08T01:15:00Z",
            },
            {
                "id": "shipment-event-002",
                "status": "packed",
                "description": "商品已完成打包",
                "location": "杭州市",
                "occurred_at": "2026-08-08T08:45:00Z",
            },
            {
                "id": "shipment-event-003",
                "status": "shipped",
                "description": "包裹已从上海分拨中心发出",
                "location": "上海市",
                "occurred_at": "2026-08-09T03:30:00Z",
            },
        ],
    }
    assert "owner_id" not in response.json()


@pytest.mark.asyncio
async def test_refund_assessment_allows_owned_refundable_order(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    response = await client.post(
        "/v1/orders/order-demo-001/refund-assessment",
        json={
            "requested_amount": "88.00",
            "requested_currency": "cny",
        },
        headers=make_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "eligible_for_review": True,
        "reason": "eligible_for_review",
        "requires_customer_confirmation": True,
    }


@pytest.mark.asyncio
async def test_refund_assessment_returns_stable_rejection_reason(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    response = await client.post(
        "/v1/orders/order-demo-001/refund-assessment",
        json={
            "requested_amount": "299.01",
            "requested_currency": "CNY",
        },
        headers=make_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "eligible_for_review": False,
        "reason": "amount_exceeds_order_total",
        "requires_customer_confirmation": False,
    }


@pytest.mark.asyncio
async def test_refund_assessment_hides_unavailable_order(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = lambda: None

    response = await client.post(
        "/v1/orders/order-demo-002/refund-assessment",
        json={
            "requested_amount": "88.00",
            "requested_currency": "CNY",
        },
        headers=make_auth_headers(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "订单不存在。"}


@pytest.mark.asyncio
async def test_refund_assessment_requires_bearer_token(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    response = await client.post(
        "/v1/orders/order-demo-001/refund-assessment",
        json={
            "requested_amount": "88.00",
            "requested_currency": "CNY",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}


@pytest.mark.asyncio
async def test_submit_refund_application_creates_waiting_confirmation_record(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail
    fixed_id = UUID("00000000-0000-0000-0000-000000000001")

    with (
        patch(
            "app.api.routes.orders.try_create_refund_application",
            new=AsyncMock(return_value=True),
        ) as create_application,
        patch("app.api.routes.orders.uuid4", return_value=fixed_id),
    ):
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": " refund-request-001 ",
                "requested_amount": "88.00",
                "requested_currency": "cny",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(fixed_id),
        "order_id": "order-demo-001",
        "request_id": "refund-request-001",
        "requested_amount": "88.00",
        "currency": "CNY",
        "status": "AWAITING_CUSTOMER_CONFIRMATION",
        "created": True,
    }
    create_application.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_refund_application_reuses_matching_request(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail
    existing = RefundApplicationRecord(
        id="refund-existing-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="PENDING_MANUAL_APPROVAL",
    )

    with (
        patch(
            "app.api.routes.orders.try_create_refund_application",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.routes.orders.fetch_refund_application_by_request_id",
            new=AsyncMock(return_value=existing),
        ),
    ):
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": "refund-request-001",
                "requested_amount": "88.00",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["id"] == "refund-existing-001"
    assert response.json()["status"] == "PENDING_MANUAL_APPROVAL"
    assert response.json()["created"] is False


@pytest.mark.asyncio
async def test_submit_refund_application_returns_existing_order_application(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail
    existing = RefundApplicationRecord(
        id="refund-existing-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="original-refund-request",
        requested_amount=Decimal("66.00"),
        currency="CNY",
        status="APPROVED",
    )

    with (
        patch(
            "app.api.routes.orders.try_create_refund_application",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.routes.orders.fetch_refund_application_by_request_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
            new=AsyncMock(return_value=existing),
        ) as fetch_by_order,
    ):
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": "new-refund-request",
                "requested_amount": "88.00",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "refund-existing-001",
        "order_id": "order-demo-001",
        "request_id": "original-refund-request",
        "requested_amount": "66.00",
        "currency": "CNY",
        "status": "APPROVED",
        "created": False,
    }
    assert fetch_by_order.await_args is not None
    assert fetch_by_order.await_args.kwargs["user_id"] == "demo-user-li"
    assert fetch_by_order.await_args.kwargs["order_id"] == "order-demo-001"


@pytest.mark.asyncio
async def test_submit_refund_application_rejects_reused_key_with_new_payload(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail
    existing = RefundApplicationRecord(
        id="refund-existing-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("99.00"),
        currency="CNY",
        status="AWAITING_CUSTOMER_CONFIRMATION",
    )

    with (
        patch(
            "app.api.routes.orders.try_create_refund_application",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.routes.orders.fetch_refund_application_by_request_id",
            new=AsyncMock(return_value=existing),
        ),
    ):
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": "refund-request-001",
                "requested_amount": "88.00",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "退款请求幂等键已用于其他申请。"}


@pytest.mark.asyncio
async def test_submit_refund_application_does_not_write_rejected_request(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    with patch(
        "app.api.routes.orders.try_create_refund_application",
        new=AsyncMock(),
    ) as create_application:
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": "refund-request-001",
                "requested_amount": "299.01",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "amount_exceeds_order_total"}
    create_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_refund_application_hides_unavailable_order(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = lambda: None

    with patch(
        "app.api.routes.orders.try_create_refund_application",
        new=AsyncMock(),
    ) as create_application:
        response = await client.post(
            "/v1/orders/order-demo-002/refund-applications",
            json={
                "request_id": "refund-request-001",
                "requested_amount": "88.00",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "订单不存在。"}
    create_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_refund_application_sanitizes_database_failure(
    client: AsyncClient,
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    with (
        patch(
            "app.api.routes.orders.try_create_refund_application",
            new=AsyncMock(
                side_effect=SQLAlchemyError("sensitive refund database detail")
            ),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.post(
            "/v1/orders/order-demo-001/refund-applications",
            json={
                "request_id": "refund-request-001",
                "requested_amount": "88.00",
                "requested_currency": "CNY",
            },
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive refund database detail" not in response.text
    assert "sensitive refund database detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_current_refund_application_returns_owned_non_rejected_record(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail
    existing = RefundApplicationRecord(
        id="refund-existing-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="PENDING_MANUAL_APPROVAL",
    )

    with patch(
        "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
        new=AsyncMock(return_value=existing),
    ) as fetch_application:
        response = await client.get(
            "/v1/orders/order-demo-001/refund-application",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": "refund-existing-001",
        "order_id": "order-demo-001",
        "request_id": "refund-request-001",
        "requested_amount": "88.00",
        "currency": "CNY",
        "status": "PENDING_MANUAL_APPROVAL",
    }
    assert fetch_application.await_args is not None
    assert fetch_application.await_args.kwargs["user_id"] == "demo-user-li"
    assert fetch_application.await_args.kwargs["order_id"] == "order-demo-001"


@pytest.mark.asyncio
async def test_current_refund_application_returns_null_when_none_exists(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    with patch(
        "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
        new=AsyncMock(return_value=None),
    ):
        response = await client.get(
            "/v1/orders/order-demo-001/refund-application",
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
async def test_current_refund_application_hides_unavailable_order(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = lambda: None

    with patch(
        "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
        new=AsyncMock(),
    ) as fetch_application:
        response = await client.get(
            "/v1/orders/order-demo-002/refund-application",
            headers=make_auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "订单不存在。"}
    fetch_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_refund_application_requires_authentication(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    with patch(
        "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
        new=AsyncMock(),
    ) as fetch_application:
        response = await client.get(
            "/v1/orders/order-demo-001/refund-application"
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    fetch_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_refund_application_sanitizes_database_failure(
    client: AsyncClient,
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[load_owned_order] = make_order_detail

    with (
        patch(
            "app.api.routes.orders.fetch_non_rejected_refund_application_by_order",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive current refund detail")),
        ),
        caplog.at_level(logging.WARNING),
    ):
        response = await client.get(
            "/v1/orders/order-demo-001/refund-application",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive current refund detail" not in response.text
    assert "sensitive current refund detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_id",
    ["order-demo-002", "order-does-not-exist"],
)
async def test_order_route_hides_unavailable_order(
    client: AsyncClient,
    app: FastAPI,
    order_id: str,
) -> None:
    app.dependency_overrides[load_owned_order] = lambda: None

    response = await client.get(f"/v1/orders/{order_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "订单不存在。"}


@pytest.mark.asyncio
async def test_order_route_sanitizes_database_failure(
    client: AsyncClient,
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.state.database_engine = FailingOrderEngine()

    with caplog.at_level(logging.WARNING):
        response = await client.get(
            "/v1/orders/order-demo-001",
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "订单服务暂时不可用，请稍后重试。"}
    assert "sensitive database connection detail" not in response.text
    assert "sensitive database connection detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_order_route_requires_bearer_token(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/orders/order-demo-001")

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        make_auth_headers(secret="wrong-test-secret-at-least-32-bytes"),
        make_auth_headers(expires_at=datetime.now(UTC) - timedelta(minutes=1)),
        make_auth_headers(include_sub=False),
    ],
    ids=["wrong-signature", "expired", "missing-sub"],
)
async def test_order_route_rejects_invalid_token(
    client: AsyncClient,
    headers: dict[str, str],
) -> None:
    response = await client.get("/v1/orders/order-demo-001", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "访问令牌无效或已过期。"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_order_route_reports_unconfigured_auth_service(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.jwt_secret_key = None

    response = await client.get(
        "/v1/orders/order-demo-001",
        headers=make_auth_headers(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务尚未配置。"}


@pytest.mark.asyncio
async def test_order_route_uses_verified_subject_as_owner(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    connection = install_fake_order_engine(
        app,
        {
            "id": "order-demo-001",
            "order_number": "EC-20260810-001",
            "status": "shipped",
            "total_amount": Decimal("299.00"),
            "currency": "CNY",
            "created_at": datetime(2026, 8, 10, 8, 30, tzinfo=UTC),
        },
    )

    response = await client.get(
        "/v1/orders/order-demo-001",
        headers=make_auth_headers(sub="demo-user-li"),
    )

    assert response.status_code == 200
    assert connection.executions[0][1] == {
        "order_id": "order-demo-001",
        "user_id": "demo-user-li",
    }


@pytest.mark.asyncio
async def test_order_route_hides_order_from_verified_other_user(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    connection = install_fake_order_engine(app, None)

    response = await client.get(
        "/v1/orders/order-demo-001",
        headers=make_auth_headers(sub="demo-user-wang"),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "订单不存在。"}
    assert connection.executions[0][1] == {
        "order_id": "order-demo-001",
        "user_id": "demo-user-wang",
    }


@pytest.mark.asyncio
async def test_fetch_owned_order_queries_order_and_sorted_shipments() -> None:
    app = FastAPI()
    connection = install_fake_order_engine(
        app,
        {
            "id": "order-demo-001",
            "order_number": "EC-20260810-001",
            "status": "shipped",
            "total_amount": Decimal("299.00"),
            "currency": "CNY",
            "created_at": datetime(2026, 8, 10, 8, 30, tzinfo=UTC),
        },
        [
            {
                "id": "shipment-event-001",
                "status": "confirmed",
                "description": "商家已确认订单",
                "location": "杭州市",
                "occurred_at": datetime(2026, 8, 8, 1, 15, tzinfo=UTC),
            },
            {
                "id": "shipment-event-002",
                "status": "packed",
                "description": "商品已完成打包",
                "location": "杭州市",
                "occurred_at": datetime(2026, 8, 8, 8, 45, tzinfo=UTC),
            },
            {
                "id": "shipment-event-003",
                "status": "shipped",
                "description": "包裹已从上海分拨中心发出",
                "location": "上海市",
                "occurred_at": datetime(2026, 8, 9, 3, 30, tzinfo=UTC),
            },
        ],
    )
    engine = cast(AsyncEngine, app.state.database_engine)

    result = await fetch_owned_order(engine, "order-demo-001", "demo-user-li")

    assert result == make_order_detail()
    assert len(connection.executions) == 2
    order_statement, order_parameters = connection.executions[0]
    shipment_statement, shipment_parameters = connection.executions[1]
    assert order_parameters == {
        "order_id": "order-demo-001",
        "user_id": "demo-user-li",
    }
    assert "user_id = :user_id" in str(order_statement)
    assert shipment_parameters == {"order_id": "order-demo-001"}
    assert "ORDER BY occurred_at ASC, id ASC" in str(shipment_statement)


@pytest.mark.asyncio
async def test_fetch_owned_order_stops_when_order_is_unavailable() -> None:
    app = FastAPI()
    connection = install_fake_order_engine(app, None)
    engine = cast(AsyncEngine, app.state.database_engine)

    result = await fetch_owned_order(engine, "order-demo-002", "demo-user-li")

    assert result is None
    assert len(connection.executions) == 1
    _, order_parameters = connection.executions[0]
    assert order_parameters == {
        "order_id": "order-demo-002",
        "user_id": "demo-user-li",
    }
