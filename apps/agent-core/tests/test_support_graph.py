from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.support_graph import SupportState, build_support_graph


class FakeChatModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.received_messages: list[list[tuple[str, str]]] = []

    async def generate_reply(self, messages: Sequence[AnyMessage]) -> str:
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )
        return self.response


async def test_support_graph_passes_state_between_nodes() -> None:
    chat_model = FakeChatModel(response="  您的订单正在配送中。  ")
    support_graph = build_support_graph(chat_model.generate_reply)
    initial_state: SupportState = {
        "user_message": "  我的订单发货了吗？  ",
    }

    result = await support_graph.ainvoke(initial_state)

    assert result["user_message"] == "  我的订单发货了吗？  "
    assert result["normalized_message"] == "我的订单发货了吗？"
    assert result["response"] == "您的订单正在配送中。"
    assert [type(message) for message in result["messages"]] == [
        HumanMessage,
        AIMessage,
    ]
    assert [message.text for message in result["messages"]] == [
        "我的订单发货了吗？",
        "您的订单正在配送中。",
    ]
    assert chat_model.received_messages == [
        [("human", "我的订单发货了吗？")]
    ]


async def test_support_graph_restores_only_the_same_thread() -> None:
    chat_model = FakeChatModel(response="收到。")
    support_graph = build_support_graph(
        chat_model.generate_reply,
        checkpointer=InMemorySaver(),
    )
    thread_one_config: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-1",
        }
    }
    thread_two_config: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-2",
        }
    }

    first_state = await support_graph.ainvoke(
        SupportState(user_message="第一条消息"),
        config=thread_one_config,
    )
    second_state = await support_graph.ainvoke(
        SupportState(user_message="第二条消息"),
        config=thread_one_config,
    )
    isolated_state = await support_graph.ainvoke(
        SupportState(user_message="另一条消息"),
        config=thread_two_config,
    )

    assert [message.type for message in first_state["messages"]] == [
        "human",
        "ai",
    ]
    assert [message.type for message in second_state["messages"]] == [
        "human",
        "ai",
        "human",
        "ai",
    ]
    assert [message.text for message in second_state["messages"]] == [
        "第一条消息",
        "收到。",
        "第二条消息",
        "收到。",
    ]
    assert [message.text for message in isolated_state["messages"]] == [
        "另一条消息",
        "收到。",
    ]
    assert chat_model.received_messages == [
        [("human", "第一条消息")],
        [
            ("human", "第一条消息"),
            ("ai", "收到。"),
            ("human", "第二条消息"),
        ],
        [("human", "另一条消息")],
    ]


async def test_support_graph_accumulates_completed_requests_per_thread() -> None:
    support_graph = build_support_graph(
        FakeChatModel(response="收到。 ").generate_reply,
        checkpointer=InMemorySaver(),
    )
    thread_one_config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1"}
    }
    thread_two_config: RunnableConfig = {
        "configurable": {"thread_id": "thread-2"}
    }

    await support_graph.ainvoke(
        SupportState(
            user_message="第一条消息",
            request_id="request-1",
        ),
        config=thread_one_config,
    )
    thread_one_state = await support_graph.ainvoke(
        SupportState(
            user_message="第二条消息",
            request_id="request-2",
        ),
        config=thread_one_config,
    )
    thread_two_state = await support_graph.ainvoke(
        SupportState(
            user_message="另一条消息",
            request_id="request-1",
        ),
        config=thread_two_config,
    )

    assert thread_one_state["completed_requests"] == {
        "request-1": "收到。",
        "request-2": "收到。",
    }
    assert thread_two_state["completed_requests"] == {
        "request-1": "收到。",
    }


async def test_support_graph_does_not_complete_an_empty_response() -> None:
    support_graph = build_support_graph(
        FakeChatModel(response="   ").generate_reply,
    )

    result = await support_graph.ainvoke(
        SupportState(
            user_message="你好",
            request_id="request-1",
        )
    )

    assert result["response"] == ""
    assert "completed_requests" not in result
    assert [message.type for message in result["messages"]] == ["human"]
    assert [message.text for message in result["messages"]] == ["你好"]
