import json
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import Field, StringConstraints
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.support_graph import SupportState
from app.services.orders import fetch_owned_order

OrderId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
    ),
    Field(description="订单 ID，例如 order-demo-001"),
]


def build_lookup_order_tool(engine: AsyncEngine) -> BaseTool:
    @tool
    async def lookup_order(
        order_id: OrderId,
        runtime: ToolRuntime[None, SupportState],
    ) -> str:
        """查询当前已认证用户拥有的订单及物流详情。"""
        try:
            order = await fetch_owned_order(
                engine,
                order_id,
                runtime.state["user_id"],
            )

            if order is None:
                return json.dumps(
                    {"error": "order_not_found"},
                    separators=(",", ":"),
                )
        except SQLAlchemyError:
            return json.dumps(
                {"error": "order_lookup_unavailable"},
                separators=(",", ":"),
            )

        return order.model_dump_json()

    return lookup_order
