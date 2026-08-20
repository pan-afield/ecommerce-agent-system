from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode


class SupportState(TypedDict):
    user_id: str
    user_message: str
    normalized_message: NotRequired[str]
    response: NotRequired[str]
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    request_id: NotRequired[str | None]
    completed_requests: NotRequired[dict[str, str]]
    pending_intent: NotRequired[Literal["order"]]


GenerateReply = Callable[
    [
        Sequence[AnyMessage],
        Sequence[BaseTool] | None,
    ],
    Awaitable[AIMessage],
]


def route_intent(
    state: SupportState,
) -> Literal["general", "order_start", "order_continue"]:
    if state.get("pending_intent") == "order":
        return "order_continue"

    message = state["normalized_message"]
    if any(keyword in message for keyword in ("订单", "物流", "快递", "包裹")):
        return "order_start"

    return "general"


def normalize_message(
    state: SupportState,
) -> dict[str, str | list[AnyMessage]]:
    normalized_message = state["user_message"].strip()
    request_id = state.get("request_id")
    message_id = f"request:{request_id}:human" if request_id is not None else None
    return {
        "messages": [
            HumanMessage(
                content=normalized_message,
                id=message_id,
            )
        ],
        "normalized_message": normalized_message,
    }


def route_request(
    state: SupportState,
) -> Literal["new", "completed"]:
    request_id = state.get("request_id")
    completed_requests = state.get("completed_requests", {})

    if request_id is not None and request_id in completed_requests:
        return "completed"

    return "new"


def reuse_completed_response(
    state: SupportState,
) -> dict[str, str]:
    request_id = state.get("request_id")
    assert request_id is not None

    completed_requests = state.get("completed_requests", {})
    return {
        "response": completed_requests[request_id],
    }


def request_order_identifier(
    state: SupportState,
) -> dict[str, str | list[AnyMessage] | dict[str, str]]:
    response = "我可以帮你查询订单，请提供订单编号。"
    update: dict[str, str | list[AnyMessage] | dict[str, str]] = {
        "response": response,
        "messages": [AIMessage(content=response)],
    }

    request_id = state.get("request_id")
    if request_id is not None:
        completed_requests = dict(state.get("completed_requests", {}))
        completed_requests[request_id] = response
        update["completed_requests"] = completed_requests

    update["pending_intent"] = "order"
    return update


def route_order_action(
    state: SupportState,
) -> Literal["tools", "done"]:
    last_message = state["messages"][-1]

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"

    return "done"


def build_support_graph(
    generate_reply: GenerateReply,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    *,
    order_tools: Sequence[BaseTool] = (),
) -> CompiledStateGraph[SupportState, None, Any, Any]:
    async def generate_response(
        state: SupportState,
    ) -> dict[
        str,
        str | list[AnyMessage] | dict[str, str],
    ]:
        response_message = await generate_reply(
            state["messages"],
            None,
        )
        response = response_message.text.strip()
        update: dict[
            str,
            str | list[AnyMessage] | dict[str, str],
        ] = {
            "response": response,
        }

        if not response:
            return update

        response_message.content = response
        update["messages"] = [response_message]

        request_id = state.get("request_id")
        if request_id is not None:
            completed_requests = dict(state.get("completed_requests", {}))
            completed_requests[request_id] = response
            update["completed_requests"] = completed_requests

        return update

    async def generate_order_response(
        state: SupportState,
    ) -> dict[str, str | list[AnyMessage] | dict[str, str]]:
        response_message = await generate_reply(
            state["messages"],
            order_tools,
        )
        response = response_message.text.strip()

        response_message.content = response
        update: dict[str, str | list[AnyMessage] | dict[str, str]] = {
            "response": response,
            "messages": [response_message],
        }

        if response_message.tool_calls:
            return update

        request_id = state.get("request_id")
        if request_id is not None:
            completed_requests = dict(state.get("completed_requests", {}))
            completed_requests[request_id] = response
            update["completed_requests"] = completed_requests

        return update

    graph_builder = StateGraph(SupportState)

    graph_builder.add_conditional_edges(
        START,
        route_request,
        {
            "new": "normalize_message",
            "completed": "reuse_completed_response",
        },
    )
    graph_builder.add_node(
        "normalize_message",
        normalize_message,
    )
    graph_builder.add_node(
        "reuse_completed_response",
        reuse_completed_response,
    )
    graph_builder.add_node(
        "generate_response",
        generate_response,
    )

    graph_builder.add_node(
        "generate_order_response",
        generate_order_response,
    )

    graph_builder.add_node(
        "request_order_identifier",
        request_order_identifier,
    )
    graph_builder.add_node(
        "order_tools",
        ToolNode(order_tools),
    )

    graph_builder.add_conditional_edges(
        "normalize_message",
        route_intent,
        {
            "general": "generate_response",
            "order_start": "request_order_identifier",
            "order_continue": "generate_order_response",
        },
    )

    graph_builder.add_edge("generate_response", END)
    graph_builder.add_edge("reuse_completed_response", END)
    graph_builder.add_edge("request_order_identifier", END)
    graph_builder.add_conditional_edges(
        "generate_order_response",
        route_order_action,
        {
            "tools": "order_tools",
            "done": END,
        },
    )
    graph_builder.add_edge("order_tools", "generate_order_response")

    return graph_builder.compile(
        checkpointer=checkpointer,
    )
