from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import (
    APITimeoutError,
    AuthenticationError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import SecretStr

from app.adapters.openai_chat import SYSTEM_PROMPT, OpenAIChatAdapter
from app.services.chat import (
    ChatError,
    ChatProviderAuthenticationError,
    ChatProviderRateLimitError,
    ChatProviderTimeoutError,
    ChatProviderUnavailableError,
)


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


async def test_openai_chat_adapter_generates_reply_with_system_and_user_messages() -> None:
    chat_model = AsyncMock()
    chat_model.ainvoke.return_value = AIMessage(content="测试助手回复")

    with patch("app.adapters.openai_chat.ChatOpenAI", return_value=chat_model):
        adapter = OpenAIChatAdapter(
            api_key=SecretStr("test-secret-key"),
            model="gpt-test-model",
            base_url=None,
            reasoning_effort=None,
            use_responses_api=True,
            timeout_seconds=30.0,
        )

        reply = await adapter.generate_reply("请问什么时候发货？")

    assert reply == "测试助手回复"
    chat_model.ainvoke.assert_awaited_once()

    messages = chat_model.ainvoke.await_args.args[0]
    assert len(messages) == 2
    assert messages[0] == SystemMessage(content=SYSTEM_PROMPT)
    assert messages[1] == HumanMessage(content="请问什么时候发货？")


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
    chat_model = AsyncMock()
    chat_model.ainvoke.side_effect = upstream_error

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
            await adapter.generate_reply("测试消息")

    assert caught_error.value.__cause__ is upstream_error
    assert "internal provider detail" not in str(caught_error.value)
