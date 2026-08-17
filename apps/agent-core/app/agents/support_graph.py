from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph


class SupportState(TypedDict):
    user_message: str
    normalized_message: NotRequired[str]
    response: NotRequired[str]
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    request_id: NotRequired[str | None]
    completed_requests: NotRequired[dict[str, str]]


GenerateReply = Callable[
    [Sequence[AnyMessage]],
    Awaitable[str],
]


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


def build_support_graph(
    generate_reply: GenerateReply,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[SupportState, None, Any, Any]:
    async def generate_response(
        state: SupportState,
    ) -> dict[
        str,
        str | list[AnyMessage] | dict[str, str],
    ]:
        response = await generate_reply(state["messages"])
        response = response.strip()
        update: dict[
            str,
            str | list[AnyMessage] | dict[str, str],
        ] = {
            "response": response,
        }

        if not response:
            return update

        update["messages"] = [
            AIMessage(content=response),
        ]
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

    graph_builder.add_edge(
        "normalize_message",
        "generate_response",
    )
    graph_builder.add_edge("generate_response", END)
    graph_builder.add_edge("reuse_completed_response", END)

    return graph_builder.compile(
        checkpointer=checkpointer,
    )
