from collections.abc import AsyncIterator
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.tools import tool
from openai import (
    APITimeoutError,
    AuthenticationError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import SecretStr

from app.adapters.openai_chat import (
    ROUTER_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    IntentDecision,
    OpenAIChatAdapter,
)
from app.services.chat import (
    ChatError,
    ChatProviderAuthenticationError,
    ChatProviderRateLimitError,
    ChatProviderTimeoutError,
    ChatProviderUnavailableError,
)


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by ID for adapter binding tests."""
    return order_id


def test_openai_chat_adapter_configures_chat_openai_without_network_call() -> None:
    api_key = SecretStr("test-secret-key")

    with patch("app.adapters.openai_chat.ChatOpenAI") as chat_openai:
        OpenAIChatAdapter(
            api_key=api_key,
            model="gpt-test-model",
            base_url="https://api.example.test",
            reasoning_effort="high",
            use_responses_api=True,
            timeout_seconds=45.0,
        )

    chat_openai.assert_called_once_with(
        api_key=api_key,
        base_url="https://api.example.test",
        model="gpt-test-model",
        reasoning_effort="high",
        use_responses_api=True,
        timeout=45.0,
        max_retries=0,
        store=False,
    )
    chat_openai.return_value.with_structured_output.assert_called_once_with(
        IntentDecision,
        method="function_calling",
        strict=True,
    )


@pytest.mark.parametrize(
    ("message", "pending_intent", "expected_intent", "expected_pending_text"),
    [
        ("订单", None, "order", "当前没有待处理的订单查询。"),
        (
            "退款政策",
            "order",
            "non_action",
            "当前正在等待订单编号。",
        ),
    ],
)
async def test_openai_chat_adapter_routes_intent_with_structured_output(
    message: str,
    pending_intent: Literal["order"] | None,
    expected_intent: Literal["order", "non_action"],
    expected_pending_text: str,
) -> None:
    chat_model = MagicMock()
    intent_client = MagicMock()
    intent_client.ainvoke = AsyncMock(
        return_value=IntentDecision(intent=expected_intent),
    )
    chat_model.with_structured_output.return_value = intent_client

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        result = await adapter.route_intent(message, pending_intent)

    assert result == expected_intent
    intent_client.ainvoke.assert_awaited_once()
    messages = intent_client.ainvoke.await_args.args[0]
    assert messages == [
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=f"{expected_pending_text}\n用户输入：{message}"),
    ]


async def test_openai_chat_adapter_rejects_invalid_intent_result() -> None:
    chat_model = MagicMock()
    intent_client = MagicMock()
    intent_client.ainvoke = AsyncMock(return_value={"intent": "order"})
    chat_model.with_structured_output.return_value = intent_client

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        with pytest.raises(ChatProviderUnavailableError):
            await adapter.route_intent("订单", None)


async def test_openai_chat_adapter_generates_reply_with_system_and_user_messages() -> None:
    chat_model = MagicMock()
    chat_model.ainvoke = AsyncMock(return_value=AIMessage(content="测试助手回复"))

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        reply = await adapter.generate_reply(
            [HumanMessage(content="请问什么时候发货？")]
        )

    assert isinstance(reply, AIMessage)
    assert reply.text == "测试助手回复"
    chat_model.ainvoke.assert_awaited_once()

    messages = chat_model.ainvoke.await_args.args[0]
    assert len(messages) == 2
    assert messages[0] == SystemMessage(content=SYSTEM_PROMPT)
    assert messages[1] == HumanMessage(content="请问什么时候发货？")


async def test_openai_chat_adapter_streams_non_empty_chunks_in_order() -> None:
    chat_model = MagicMock()

    async def fake_astream(
        messages: list[SystemMessage | HumanMessage],
    ) -> AsyncIterator[AIMessageChunk]:
        assert messages[0] == SystemMessage(content=SYSTEM_PROMPT)
        yield AIMessageChunk(content="订单")
        yield AIMessageChunk(content="正在处理")
        yield AIMessageChunk(content="")

    chat_model.astream.side_effect = fake_astream

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        chunks = [
            chunk
            async for chunk in adapter.stream_reply(
                [HumanMessage(content="订单到哪里了？")]
            )
        ]

    assert chunks == ["订单", "正在处理"]
    chat_model.astream.assert_called_once()


async def test_openai_chat_adapter_binds_tools_before_model_call() -> None:
    chat_model = MagicMock()
    bound_model = MagicMock()
    bound_model.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup_order",
                    "args": {"order_id": "order-demo-001"},
                    "id": "call-order-1",
                    "type": "tool_call",
                }
            ],
        )
    )
    chat_model.bind_tools.return_value = bound_model

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        reply = await adapter.generate_reply(
            [HumanMessage(content="查询订单 order-demo-001")],
            tools=[lookup_order],
        )

    chat_model.bind_tools.assert_called_once_with([lookup_order])
    chat_model.ainvoke.assert_not_called()
    bound_model.ainvoke.assert_awaited_once()
    assert reply.tool_calls[0]["name"] == "lookup_order"


def make_status_error(
    error_type: type[AuthenticationError | PermissionDeniedError | RateLimitError],
    status_code: int,
) -> OpenAIError:
    request = httpx.Request("POST", "https://api.example.test/responses")
    response = httpx.Response(status_code=status_code, request=request)
    return error_type("internal provider detail", response=response, body=None)


@pytest.mark.parametrize(
    ("upstream_error", "expected_error"),
    [
        (
            make_status_error(AuthenticationError, 401),
            ChatProviderAuthenticationError,
        ),
        (
            make_status_error(PermissionDeniedError, 403),
            ChatProviderAuthenticationError,
        ),
        (
            make_status_error(RateLimitError, 429),
            ChatProviderRateLimitError,
        ),
        (
            APITimeoutError(
                request=httpx.Request("POST", "https://api.example.test/responses")
            ),
            ChatProviderTimeoutError,
        ),
        (
            OpenAIError("internal provider detail"),
            ChatProviderUnavailableError,
        ),
    ],
)
async def test_openai_chat_adapter_maps_provider_errors(
    upstream_error: OpenAIError,
    expected_error: type[ChatError],
) -> None:
    chat_model = MagicMock()
    chat_model.ainvoke = AsyncMock(side_effect=upstream_error)

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        with pytest.raises(expected_error) as caught_error:
            await adapter.generate_reply(
                [HumanMessage(content="测试消息")]
            )

    assert caught_error.value.__cause__ is upstream_error
    assert "internal provider detail" not in str(caught_error.value)


@pytest.mark.parametrize(
    ("upstream_error", "expected_error"),
    [
        (
            make_status_error(AuthenticationError, 401),
            ChatProviderAuthenticationError,
        ),
        (
            make_status_error(PermissionDeniedError, 403),
            ChatProviderAuthenticationError,
        ),
        (
            make_status_error(RateLimitError, 429),
            ChatProviderRateLimitError,
        ),
        (
            APITimeoutError(
                request=httpx.Request("POST", "https://api.example.test/responses")
            ),
            ChatProviderTimeoutError,
        ),
        (
            OpenAIError("internal provider detail"),
            ChatProviderUnavailableError,
        ),
        (
            ValueError("internal structured output detail"),
            ChatProviderUnavailableError,
        ),
    ],
)
async def test_openai_chat_adapter_maps_intent_router_errors(
    upstream_error: Exception,
    expected_error: type[ChatError],
) -> None:
    chat_model = MagicMock()
    intent_client = MagicMock()
    intent_client.ainvoke = AsyncMock(side_effect=upstream_error)
    chat_model.with_structured_output.return_value = intent_client

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        with pytest.raises(expected_error) as caught_error:
            await adapter.route_intent("退款政策", "order")

    assert caught_error.value.__cause__ is upstream_error
    assert "internal" not in str(caught_error.value)
