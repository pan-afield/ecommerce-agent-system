from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.dependencies import get_chat_service
from app.core.config import Settings
from app.main import create_app
from app.services.chat import (
    ChatProviderTimeoutError,
    ChatResult,
    ChatService,
)

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"


def make_auth_headers(sub: str = "demo-user-li") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": sub,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def create_test_app(service: ChatService) -> FastAPI:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        openai_api_key=None,
        jwt_secret_key=TEST_JWT_SECRET,
        _env_file=None,
    )
    app = create_app(settings)
    app.dependency_overrides[get_chat_service] = lambda: service
    return app


async def post_chat(
    app: FastAPI,
    message: str,
    thread_id: str | None = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    payload = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if request_id is not None:
        payload["request_id"] = request_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/chat",
            json=payload,
            headers=headers if headers is not None else make_auth_headers(),
        )


@pytest.mark.asyncio
async def test_chat_returns_assistant_response() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="您好，订单查询功能将在后续版本提供。",
        model="test-model",
    )
    app = create_test_app(service)

    response = await post_chat(app, "  如何查询订单？  ")

    assert response.status_code == 200
    assert response.json() == {
        "assistant": {
            "content": "您好，订单查询功能将在后续版本提供。",
        },
        "model": "test-model",
    }
    service.reply.assert_awaited_once_with(
        "如何查询订单？",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_forwards_normalized_thread_id() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="收到。",
        model="test-model",
    )
    app = create_test_app(service)

    response = await post_chat(
        app,
        "继续查询",
        thread_id="  thread-1  ",
        request_id="  request-1  ",
    )

    assert response.status_code == 200
    service.reply.assert_awaited_once_with(
        "继续查询",
        "demo-user-li",
        "thread-1",
        request_id="request-1",
    )


@pytest.mark.asyncio
async def test_chat_rejects_blank_message() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    response = await post_chat(app, "   ")

    assert response.status_code == 422
    service.reply.assert_not_awaited()


@pytest.mark.parametrize("thread_id", ["", "   ", "a" * 129])
@pytest.mark.asyncio
async def test_chat_rejects_invalid_thread_id(thread_id: str) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    response = await post_chat(app, "你好", thread_id=thread_id)

    assert response.status_code == 422
    service.reply.assert_not_awaited()


@pytest.mark.parametrize("request_id", ["", "   ", "a" * 129])
@pytest.mark.asyncio
async def test_chat_rejects_invalid_request_id(request_id: str) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    response = await post_chat(app, "你好", request_id=request_id)

    assert response.status_code == 422
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_rejects_request_id_without_thread_id() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    response = await post_chat(
        app,
        "你好",
        request_id="request-1",
    )

    assert response.status_code == 422
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_maps_service_timeout_to_stable_error() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = ChatProviderTimeoutError("sensitive upstream detail")
    app = create_test_app(service)

    response = await post_chat(app, "商品什么时候发货？")

    assert response.status_code == 504
    assert response.json() == {
        "error": {
            "code": "chat_timeout",
            "message": "客服服务响应超时，请稍后重试。",
        }
    }
    assert "sensitive upstream detail" not in response.text


@pytest.mark.asyncio
async def test_chat_requires_bearer_token_before_service_lookup(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/chat",
        json={"message": "商品什么时候发货？"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_chat_without_api_key_returns_not_configured(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/chat",
        json={"message": "商品什么时候发货？"},
        headers=make_auth_headers(),
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "chat_not_configured",
            "message": "客服服务尚未配置。",
        }
    }
