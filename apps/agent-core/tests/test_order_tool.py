import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.support_graph import SupportState
from app.services.orders import OrderDetailResponse, ShipmentEventResponse
from app.tools.orders import build_lookup_order_tool


def build_test_tool_graph(
    lookup_order: BaseTool,
) -> CompiledStateGraph[SupportState, None, Any, Any]:
    graph_builder = StateGraph(SupportState)
    graph_builder.add_node("tools", ToolNode([lookup_order]))
    graph_builder.add_edge(START, "tools")
    graph_builder.add_edge("tools", END)
    return graph_builder.compile()


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
                status="shipped",
                description="包裹已发出",
                location="上海市",
                occurred_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
            )
        ],
    )


def make_tool_state(
    *,
    user_id: str = "demo-user-li",
    order_id: str = "order-demo-001",
    claimed_user_id: str | None = None,
) -> SupportState:
    arguments = {"order_id": order_id}
    if claimed_user_id is not None:
        arguments["user_id"] = claimed_user_id

    return SupportState(
        user_id=user_id,
        user_message=f"查询订单 {order_id}",
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup_order",
                        "args": arguments,
                        "id": "call-order-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
    )


def test_lookup_order_tool_exposes_only_order_id_to_model() -> None:
    engine = cast(AsyncEngine, MagicMock())
    lookup_order = build_lookup_order_tool(engine)

    assert lookup_order.name == "lookup_order"
    assert set(lookup_order.args) == {"order_id"}
    assert lookup_order.args["order_id"]["minLength"] == 1
    assert lookup_order.args["order_id"]["maxLength"] == 64


@pytest.mark.asyncio
async def test_lookup_order_tool_uses_identity_injected_from_state() -> None:
    engine = cast(AsyncEngine, MagicMock())
    lookup_order = build_lookup_order_tool(engine)
    tool_graph = build_test_tool_graph(lookup_order)
    fetch_order = AsyncMock(return_value=make_order_detail())

    with patch("app.tools.orders.fetch_owned_order", fetch_order):
        result = await tool_graph.ainvoke(
            make_tool_state(claimed_user_id="demo-user-wang")
        )

    fetch_order.assert_awaited_once_with(
        engine,
        "order-demo-001",
        "demo-user-li",
    )
    tool_message = result["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert json.loads(tool_message.text)["id"] == "order-demo-001"
    assert "user_id" not in json.loads(tool_message.text)


@pytest.mark.asyncio
async def test_lookup_order_tool_hides_unavailable_order() -> None:
    engine = cast(AsyncEngine, MagicMock())
    lookup_order = build_lookup_order_tool(engine)
    tool_graph = build_test_tool_graph(lookup_order)
    fetch_order = AsyncMock(return_value=None)

    with patch("app.tools.orders.fetch_owned_order", fetch_order):
        result = await tool_graph.ainvoke(make_tool_state())

    tool_message = result["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert json.loads(tool_message.text) == {"error": "order_not_found"}


@pytest.mark.asyncio
async def test_lookup_order_tool_hides_database_error_details() -> None:
    engine = cast(AsyncEngine, MagicMock())
    lookup_order = build_lookup_order_tool(engine)
    tool_graph = build_test_tool_graph(lookup_order)
    fetch_order = AsyncMock(
        side_effect=SQLAlchemyError("secret connection details")
    )

    with patch("app.tools.orders.fetch_owned_order", fetch_order):
        result = await tool_graph.ainvoke(make_tool_state())

    tool_message = result["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert json.loads(tool_message.text) == {
        "error": "order_lookup_unavailable"
    }
    assert "secret connection details" not in tool_message.text


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["   ", "x" * 65])
async def test_lookup_order_tool_rejects_invalid_order_id(order_id: str) -> None:
    engine = cast(AsyncEngine, MagicMock())
    lookup_order = build_lookup_order_tool(engine)
    tool_graph = build_test_tool_graph(lookup_order)
    fetch_order = AsyncMock()

    with patch("app.tools.orders.fetch_owned_order", fetch_order):
        result = await tool_graph.ainvoke(make_tool_state(order_id=order_id))

    fetch_order.assert_not_awaited()
    tool_message = result["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
