from unittest.mock import AsyncMock

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


def create_test_app(service: ChatService) -> FastAPI:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        openai_api_key=None,
        _env_file=None,
    )
    app = create_app(settings)
    app.dependency_overrides[get_chat_service] = lambda: service
    return app


async def post_chat(app: FastAPI, message: str) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/chat", json={"message": message})


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
    service.reply.assert_awaited_once_with("如何查询订单？")


@pytest.mark.asyncio
async def test_chat_rejects_blank_message() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    response = await post_chat(app, "   ")

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
async def test_chat_without_api_key_returns_not_configured(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/chat",
        json={"message": "商品什么时候发货？"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "chat_not_configured",
            "message": "客服服务尚未配置。",
        }
    }
