from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolRuntime

from app.agents.support_graph import (
    SupportState,
    build_support_graph,
    normalize_message,
    route_intent,
    select_model_message,
)
from app.rag.citations import KnowledgeCitation

TEST_USER_ID = "demo-user-li"


@pytest.mark.parametrize(
    ("rag_prompt", "expected"),
    [
        (None, "原始问题"),
        ("", "原始问题"),
        ("知识库证据：退款需要订单本人提交。", "知识库证据：退款需要订单本人提交。"),
    ],
)
def test_select_model_message_prefers_non_empty_rag_prompt(
    rag_prompt: str | None,
    expected: str,
) -> None:
    assert select_model_message("原始问题", rag_prompt) == expected


@tool
async def fake_lookup_order(order_id: str) -> str:
    """Return a fake order for graph capability tests."""
    return order_id


@tool
async def fake_lookup_owned_order(
    order_id: str,
    runtime: ToolRuntime[None, SupportState],
) -> str:
    """Return the authenticated user carried by graph state."""
    return f"{runtime.state['user_id']}:{order_id}"


class FakeChatModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.received_messages: list[list[tuple[str, str]]] = []
        self.received_tools: list[list[BaseTool] | None] = []

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )
        self.received_tools.append(list(tools) if tools is not None else None)
        return AIMessage(content=self.response)


@pytest.mark.parametrize(
    ("message", "expected_intent"),
    [
        ("你们支持七天无理由吗", "general"),
        ("我的订单物流到哪了", "order_start"),
        ("这个快递什么时候到", "order_start"),
        ("包裹已经发出了吗", "order_start"),
    ],
)
def test_route_intent_classifies_normalized_message(
    message: str,
    expected_intent: str,
) -> None:
    state: SupportState = {
        "user_id": TEST_USER_ID,
        "user_message": message,
        "normalized_message": message,
    }

    assert route_intent(state) == expected_intent


def test_normalize_message_keeps_original_text_without_rag_prompt() -> None:
    result = normalize_message(
        SupportState(
            user_id=TEST_USER_ID,
            user_message="  你们支持七天无理由吗？  ",
        )
    )

    assert result["normalized_message"] == "你们支持七天无理由吗？"
    messages = result["messages"]
    assert isinstance(messages, list)
    assert messages[0].text == "你们支持七天无理由吗？"


def test_normalize_message_uses_rag_prompt_when_present() -> None:
    rag_prompt = "知识库证据：\n退款需要订单本人提交。\n\n用户问题：\n退款政策"

    result = normalize_message(
        SupportState(
            user_id=TEST_USER_ID,
            user_message="退款政策",
            rag_prompt=rag_prompt,
        )
    )

    assert result["normalized_message"] == "退款政策"
    messages = result["messages"]
    assert isinstance(messages, list)
    assert messages[0].text == rag_prompt
    assert route_intent(
        {
            "user_id": TEST_USER_ID,
            "user_message": "退款政策",
            "normalized_message": "退款政策",
            "rag_prompt": rag_prompt,
        }
    ) == "general"


def test_normalize_message_keeps_original_order_followup_over_rag_prompt() -> None:
    result = normalize_message(
        SupportState(
            user_id=TEST_USER_ID,
            user_message="order-demo-001",
            pending_intent="order",
            rag_prompt="知识库证据：不应替换订单编号",
        )
    )

    messages = result["messages"]
    assert isinstance(messages, list)
    assert messages[0].text == "order-demo-001"
    assert route_intent(
        {
            "user_id": TEST_USER_ID,
            "user_message": "order-demo-001",
            "normalized_message": "order-demo-001",
            "pending_intent": "order",
            "rag_prompt": "知识库证据：不应替换订单编号",
        }
    ) == "order_continue"


async def test_support_graph_passes_state_between_nodes() -> None:
    chat_model = FakeChatModel(response="  支持七天无理由退货。  ")
    support_graph = build_support_graph(chat_model.generate_reply)
    initial_state: SupportState = {
        "user_id": TEST_USER_ID,
        "user_message": "  你们支持七天无理由吗？  ",
    }

    result = await support_graph.ainvoke(initial_state)

    assert result["user_message"] == "  你们支持七天无理由吗？  "
    assert result["normalized_message"] == "你们支持七天无理由吗？"
    assert result["response"] == "支持七天无理由退货。"
    assert [type(message) for message in result["messages"]] == [
        HumanMessage,
        AIMessage,
    ]
    assert [message.text for message in result["messages"]] == [
        "你们支持七天无理由吗？",
        "支持七天无理由退货。",
    ]
    assert chat_model.received_messages == [
        [("human", "你们支持七天无理由吗？")]
    ]
    assert chat_model.received_tools == [None]


async def test_support_graph_preserves_model_tool_call_metadata() -> None:
    async def generate_with_tool_call(
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        return AIMessage(
            content="正在查询。",
            tool_calls=[
                {
                    "name": "lookup_order",
                    "args": {"order_id": "order-demo-001"},
                    "id": "call-order-1",
                    "type": "tool_call",
                }
            ],
        )

    support_graph = build_support_graph(generate_with_tool_call)

    result = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="你好")
    )

    response_message = result["messages"][-1]
    assert isinstance(response_message, AIMessage)
    assert response_message.tool_calls == [
        {
            "name": "lookup_order",
            "args": {"order_id": "order-demo-001"},
            "id": "call-order-1",
            "type": "tool_call",
        }
    ]


async def test_order_intent_requests_identifier_without_calling_model() -> None:
    chat_model = FakeChatModel(response="暂时还不能查询真实订单。")
    support_graph = build_support_graph(chat_model.generate_reply)

    result = await support_graph.ainvoke(
        SupportState(
            user_id=TEST_USER_ID,
            user_message="我的订单物流到哪了",
        )
    )

    assert result["response"] == "我可以帮你查询订单，请提供订单编号。"
    assert [message.text for message in result["messages"]] == [
        "我的订单物流到哪了",
        "我可以帮你查询订单，请提供订单编号。",
    ]
    assert chat_model.received_messages == []


@pytest.mark.asyncio
async def test_general_branch_builds_rag_context_before_model() -> None:
    chat_model = FakeChatModel(response="根据政策回答。")
    citation = KnowledgeCitation(
        source_id="refund-policy.md",
        chunk_id="r" * 64,
        page_number=None,
        content="订单签收后七天内可以申请退款。",
        score=0.02,
    )
    rag_calls: list[str] = []

    async def fake_rag_context(message: str) -> tuple[str, list[KnowledgeCitation]]:
        rag_calls.append(message)
        return "RAG_PROMPT::退款政策", [citation]

    support_graph = build_support_graph(
        chat_model.generate_reply,
        rag_context_builder=fake_rag_context,
    )

    result = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="退款政策")
    )

    assert rag_calls == ["退款政策"]
    assert result["rag_prompt"] == "RAG_PROMPT::退款政策"
    assert result["rag_citations"] == [
        {
            "source_id": "refund-policy.md",
            "chunk_id": "r" * 64,
            "page_number": None,
            "content": "订单签收后七天内可以申请退款。",
            "score": 0.02,
        }
    ]
    assert chat_model.received_messages == [[("human", "RAG_PROMPT::退款政策")]]


@pytest.mark.asyncio
async def test_order_branches_do_not_build_rag_context() -> None:
    chat_model = FakeChatModel(response="不应调用模型。")
    rag_called = False

    async def unexpected_rag_context(message: str) -> tuple[str, list[KnowledgeCitation]]:
        nonlocal rag_called
        rag_called = True
        raise AssertionError("order branches must not call RAG")

    support_graph = build_support_graph(
        chat_model.generate_reply,
        checkpointer=InMemorySaver(),
        rag_context_builder=unexpected_rag_context,
        order_tools=[fake_lookup_order],
    )
    config: RunnableConfig = {"configurable": {"thread_id": "thread-order-rag"}}

    first = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="查询订单"),
        config=config,
    )
    followup = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="order-demo-001"),
        config=config,
    )

    assert rag_called is False
    assert first["response"] == "我可以帮你查询订单，请提供订单编号。"
    assert followup["rag_citations"] == []
    assert chat_model.received_messages[-1][-1] == ("human", "order-demo-001")
    assert all(
        message_text != "知识库证据：不应替换订单编号"
        for messages in chat_model.received_messages
        for _, message_text in messages
    )


async def test_order_intent_retry_reuses_completed_response() -> None:
    chat_model = FakeChatModel(response="不应调用模型。")
    support_graph = build_support_graph(
        chat_model.generate_reply,
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-order"}
    }
    initial_state = SupportState(
        user_id=TEST_USER_ID,
        user_message="查询订单",
        request_id="request-order-1",
    )

    first_result = await support_graph.ainvoke(initial_state, config=config)
    retried_result = await support_graph.ainvoke(initial_state, config=config)

    assert retried_result["response"] == first_result["response"]
    assert [message.text for message in retried_result["messages"]] == [
        "查询订单",
        "我可以帮你查询订单，请提供订单编号。",
    ]
    assert chat_model.received_messages == []


async def test_pending_order_intent_routes_followup_in_same_thread() -> None:
    chat_model = FakeChatModel(response="订单模型已收到编号。")
    support_graph = build_support_graph(
        chat_model.generate_reply,
        checkpointer=InMemorySaver(),
        order_tools=[fake_lookup_order],
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-order"}
    }

    await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="帮我查询订单"),
        config=config,
    )
    followup = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="EC-20260810-001"),
        config=config,
    )

    assert followup["pending_intent"] == "order"
    assert followup["response"] == "订单模型已收到编号。"
    assert [message.text for message in followup["messages"]] == [
        "帮我查询订单",
        "我可以帮你查询订单，请提供订单编号。",
        "EC-20260810-001",
        "订单模型已收到编号。",
    ]
    assert chat_model.received_messages == [
        [
            ("human", "帮我查询订单"),
            ("ai", "我可以帮你查询订单，请提供订单编号。"),
            ("human", "EC-20260810-001"),
        ]
    ]
    assert chat_model.received_tools == [[fake_lookup_order]]


async def test_order_graph_executes_tool_and_returns_to_model() -> None:
    model_calls: list[list[AnyMessage]] = []

    async def generate_reply(
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        model_calls.append(list(messages))
        if not any(isinstance(message, ToolMessage) for message in messages):
            assert tools == [fake_lookup_owned_order]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fake_lookup_owned_order",
                        "args": {"order_id": "order-demo-001"},
                        "id": "call-order-1",
                        "type": "tool_call",
                    }
                ],
            )

        tool_message = next(
            message for message in messages if isinstance(message, ToolMessage)
        )
        return AIMessage(content=f"查询结果：{tool_message.text}")

    support_graph = build_support_graph(
        generate_reply,
        checkpointer=InMemorySaver(),
        order_tools=[fake_lookup_owned_order],
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-order-retry"}
    }
    initial_state = SupportState(
        user_id=TEST_USER_ID,
        user_message="订单编号是 order-demo-001",
        pending_intent="order",
        request_id="order-request-1",
    )
    result = await support_graph.ainvoke(initial_state, config=config)
    retried = await support_graph.ainvoke(initial_state, config=config)

    assert result["response"] == "查询结果：demo-user-li:order-demo-001"
    assert result["pending_intent"] is None
    assert retried["response"] == result["response"]
    assert len(model_calls) == 2
    assert [message.type for message in model_calls[1]] == [
        "human",
        "ai",
        "tool",
    ]


async def test_pending_order_intent_does_not_leak_to_another_thread() -> None:
    chat_model = FakeChatModel(response="一般问题回复。")
    support_graph = build_support_graph(
        chat_model.generate_reply,
        checkpointer=InMemorySaver(),
    )
    first_thread: RunnableConfig = {
        "configurable": {"thread_id": "thread-order"}
    }
    other_thread: RunnableConfig = {
        "configurable": {"thread_id": "thread-other"}
    }

    await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="帮我查询订单"),
        config=first_thread,
    )
    other_result = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="EC-20260810-001"),
        config=other_thread,
    )

    assert other_result.get("pending_intent") is None
    assert other_result["response"] == "一般问题回复。"
    assert chat_model.received_messages == [
        [("human", "EC-20260810-001")]
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
        SupportState(user_id=TEST_USER_ID, user_message="第一条消息"),
        config=thread_one_config,
    )
    second_state = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="第二条消息"),
        config=thread_one_config,
    )
    isolated_state = await support_graph.ainvoke(
        SupportState(user_id=TEST_USER_ID, user_message="另一条消息"),
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
            user_id=TEST_USER_ID,
            user_message="第一条消息",
            request_id="request-1",
        ),
        config=thread_one_config,
    )
    thread_one_state = await support_graph.ainvoke(
        SupportState(
            user_id=TEST_USER_ID,
            user_message="第二条消息",
            request_id="request-2",
        ),
        config=thread_one_config,
    )
    thread_two_state = await support_graph.ainvoke(
        SupportState(
            user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
            user_message="你好",
            request_id="request-1",
        )
    )

    assert result["response"] == ""
    assert "completed_requests" not in result
    assert [message.type for message in result["messages"]] == ["human"]
    assert [message.text for message in result["messages"]] == ["你好"]
