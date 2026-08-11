import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import TextClause

from app.api.routes.orders import (
    OrderDetailResponse,
    ShipmentEventResponse,
    get_demo_user_id,
    load_owned_order,
)
from app.core.config import Settings

DatabaseValue = str | Decimal | datetime | None
DatabaseRow = dict[str, DatabaseValue]


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


def test_demo_identity_comes_only_from_server_settings() -> None:
    app = FastAPI()
    app.state.settings = Settings(
        demo_user_id="demo-user-li",
        _env_file=None,
    )
    request = Request(
        {
            "type": "http",
            "app": app,
            "headers": [(b"x-demo-user-id", b"demo-user-wang")],
        }
    )

    assert get_demo_user_id(request) == "demo-user-li"


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
        response = await client.get("/v1/orders/order-demo-001")

    assert response.status_code == 503
    assert response.json() == {"detail": "订单服务暂时不可用，请稍后重试。"}
    assert "sensitive database connection detail" not in response.text
    assert "sensitive database connection detail" not in caplog.text
    assert "SQLAlchemyError" in caplog.text


@pytest.mark.asyncio
async def test_load_owned_order_queries_order_and_sorted_shipments() -> None:
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
    request = Request({"type": "http", "app": app})

    result = await load_owned_order("order-demo-001", request, "demo-user-li")

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
async def test_load_owned_order_stops_when_order_is_unavailable() -> None:
    app = FastAPI()
    connection = install_fake_order_engine(app, None)
    request = Request({"type": "http", "app": app})

    result = await load_owned_order("order-demo-002", request, "demo-user-li")

    assert result is None
    assert len(connection.executions) == 1
    _, order_parameters = connection.executions[0]
    assert order_parameters == {
        "order_id": "order-demo-002",
        "user_id": "demo-user-li",
    }
