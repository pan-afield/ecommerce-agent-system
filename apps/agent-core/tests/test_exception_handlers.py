import json
import logging

import pytest
from fastapi import FastAPI, Request, status

from app.api.exception_handlers import chat_error_handler
from app.services.chat import (
    ChatError,
    ChatNotConfiguredError,
    ChatProviderAuthenticationError,
    ChatProviderRateLimitError,
    ChatProviderTimeoutError,
    ChatProviderUnavailableError,
    EmptyChatResponseError,
)


def test_chat_error_handler_is_registered(app: FastAPI) -> None:
    assert app.exception_handlers[ChatError] is chat_error_handler


def make_chat_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/v1/chat",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code", "expected_message"),
    [
        (
            ChatNotConfiguredError("sensitive upstream detail"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_not_configured",
            "客服服务尚未配置。",
        ),
        (
            ChatProviderAuthenticationError("sensitive upstream detail"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_authentication_failed",
            "客服服务暂时不可用，请联系管理员。",
        ),
        (
            ChatProviderRateLimitError("sensitive upstream detail"),
            status.HTTP_429_TOO_MANY_REQUESTS,
            "chat_rate_limited",
            "请求过于频繁，请稍后重试。",
        ),
        (
            ChatProviderTimeoutError("sensitive upstream detail"),
            status.HTTP_504_GATEWAY_TIMEOUT,
            "chat_timeout",
            "客服服务响应超时，请稍后重试。",
        ),
        (
            ChatProviderUnavailableError("sensitive upstream detail"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_provider_unavailable",
            "客服服务暂时不可用，请稍后重试。",
        ),
        (
            EmptyChatResponseError("sensitive upstream detail"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_provider_unavailable",
            "客服服务暂时不可用，请稍后重试。",
        ),
    ],
)
@pytest.mark.asyncio
async def test_chat_error_handler_returns_stable_response(
    error: ChatError,
    expected_status: int,
    expected_code: str,
    expected_message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        response = await chat_error_handler(make_chat_request(), error)

    response_body = bytes(response.body)
    assert response.status_code == expected_status
    assert json.loads(response_body) == {
        "error": {
            "code": expected_code,
            "message": expected_message,
        }
    }
    assert "sensitive upstream detail" not in response_body.decode()
    assert "sensitive upstream detail" not in caplog.text
    assert "/v1/chat" in caplog.text
