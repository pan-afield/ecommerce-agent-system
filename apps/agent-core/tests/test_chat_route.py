import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient, Response
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import BaseTool
from redis.exceptions import RedisError

import app.api.routes.chat as chat_route_module
from app.api.dependencies import get_chat_service
from app.core.config import Settings
from app.main import create_app
from app.rag.citations import KnowledgeCitation
from app.services.chat import (
    ChatProviderTimeoutError,
    ChatResult,
    ChatService,
)
from tests.test_rate_limit import FakeRateLimitRedis

TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"
TEST_JWT_ISSUER = "ecommerce-agent-system"


class FakeRouteChatModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.received_messages: list[list[tuple[str, str]]] = []

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        del tools
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )
        return AIMessage(content=self.response)


def make_auth_headers(sub: str = "demo-user-li") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": sub,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": TEST_JWT_ISSUER,
            "token_type": "access",
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
    # Route tests bypass the lifespan and inject a fake ChatService directly.
    # No RAG application state is needed because intent routing now owns retrieval.
    app.dependency_overrides[get_chat_service] = lambda: service
    return app


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.asyncio
async def test_chat_routes_return_429_when_user_rate_limit_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.redis_client = object()

    async def reject_request(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(chat_route_module, "consume_rate_limit", reject_request)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            path,
            json={"message": "你好"},
            headers=make_auth_headers("customer-001"),
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.json() == {
        "error": {
            "code": "chat_rate_limited",
            "message": "请求过于频繁，请稍后重试。",
        }
    }
    service.reply.assert_not_awaited()


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.asyncio
async def test_chat_route_continues_when_redis_rate_limit_check_fails(
    path: str,
) -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="你好，我可以帮你查询订单。",
        model="test-model",
        citations=[],
    )
    app = create_test_app(service)
    redis = FakeRateLimitRedis(error=RedisError("private redis connection details"))
    app.state.redis_client = redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            path,
            json={"message": "你好"},
            headers=make_auth_headers("customer-001"),
        )

    assert response.status_code == 200
    service.reply.assert_awaited_once()
    assert redis.pipeline_calls == 1
    assert "private redis connection details" not in response.text


def test_chat_routes_document_rag_validation_error() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    schema = app.openapi()

    for path in ("/v1/chat", "/v1/chat/stream"):
        response_schema = schema["paths"][path]["post"]["responses"]["422"]
        assert response_schema["content"]["application/json"]["schema"]["$ref"] == (
            "#/components/schemas/ErrorResponse"
        )


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


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.asyncio
async def test_order_http_paths_skip_unavailable_rag(path: str) -> None:
    async def unavailable_rag(
        message: str,
    ) -> tuple[str, list[KnowledgeCitation]]:
        raise AssertionError(f"order path must not call RAG for {message}")

    chat_model = FakeRouteChatModel(response="不应调用模型。")
    service = ChatService(
        chat_model=chat_model,
        model_name="test-model",
        rag_context_builder=unavailable_rag,
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            path,
            json={"message": "订单"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    if path.endswith("/stream"):
        assistant_event = response.text.split("\n", maxsplit=2)[1]
        response_data = json.loads(assistant_event.removeprefix("data: "))
    else:
        response_data = response.json()
    assert response_data["citations"] == []
    assert chat_model.received_messages == []


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.asyncio
async def test_knowledge_http_paths_use_graph_rag_result(path: str) -> None:
    citation = KnowledgeCitation(
        source_id="refund-policy.md",
        chunk_id="r" * 64,
        page_number=1,
        content="签收后七天内可以申请退款。",
        score=0.02,
    )
    rag_calls: list[str] = []

    async def fake_rag_context(
        message: str,
    ) -> tuple[str, list[KnowledgeCitation]]:
        rag_calls.append(message)
        return "RAG_PROMPT::退款政策", [citation]

    chat_model = FakeRouteChatModel(response="根据政策，签收后七天内可申请退款。")
    service = ChatService(
        chat_model=chat_model,
        model_name="test-model",
        rag_context_builder=fake_rag_context,
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            path,
            json={"message": "退款政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    if path.endswith("/stream"):
        assistant_event = response.text.split("\n", maxsplit=2)[1]
        response_data = json.loads(assistant_event.removeprefix("data: "))
    else:
        response_data = response.json()
    assert response_data["citations"][0]["source_id"] == "refund-policy.md"
    assert rag_calls == ["退款政策"]
    assert chat_model.received_messages == [[("human", "RAG_PROMPT::退款政策")]]


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
        "citations": [],
    }
    service.reply.assert_awaited_once_with(
        "如何查询订单？",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_route_does_not_require_rag_application_state() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="收到。",
        model="test-model",
    )
    app = create_test_app(service)
    response = await post_chat(app, "你好")

    assert response.status_code == 200
    service.reply.assert_awaited_once_with(
        "你好",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_serializes_citations_returned_by_service() -> None:
    citation = KnowledgeCitation(
        source_id="policy.md",
        chunk_id="chunk-1",
        page_number=1,
        content="签收后七天内可申请退货。",
        score=0.95,
    )
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="根据政策，退货期限为七天。",
        model="test-model",
        citations=[citation],
    )
    app = create_test_app(service)

    response = await post_chat(app, "  退货政策  ")

    assert response.status_code == 200
    assert response.json()["citations"] == [
        {
            "source_id": "policy.md",
            "chunk_id": "chunk-1",
            "page_number": 1,
            "content": "签收后七天内可申请退货。",
            "score": 0.95,
        }
    ]
    service.reply.assert_awaited_once_with(
        "退货政策",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_order_result_returns_no_citations() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="我可以帮你查询订单，请提供订单编号。",
        model="test-model",
    )
    app = create_test_app(service)

    response = await post_chat(app, "订单")

    assert response.status_code == 200
    assert response.json()["citations"] == []
    service.reply.assert_awaited_once_with(
        "订单",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_concurrent_requests_keep_service_results_isolated() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    async def fake_reply(
        message: str,
        user_id: str,
        thread_id: str | None = None,
        *,
        request_id: str | None = None,
        rag_prompt: str | None = None,
    ) -> ChatResult:
        del user_id, thread_id, request_id, rag_prompt
        await asyncio.sleep(0)
        return ChatResult(
            content=f"回答-{message}",
            model="test-model",
            citations=[
                KnowledgeCitation(
                    source_id=f"policy-{message}",
                    chunk_id=f"chunk-{message}",
                    page_number=None,
                    content=f"证据-{message}",
                    score=0.9,
                )
            ],
        )

    service.reply.side_effect = fake_reply

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        responses = await asyncio.gather(
            client.post(
                "/v1/chat",
                json={"message": "退款"},
                headers=make_auth_headers(),
            ),
            client.post(
                "/v1/chat",
                json={"message": "发货"},
                headers=make_auth_headers(),
            ),
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["citations"][0]["source_id"] == "policy-退款"
    assert responses[1].json()["citations"][0]["source_id"] == "policy-发货"


@pytest.mark.asyncio
async def test_chat_returns_stable_rag_validation_error_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="无法构建 RAG 上下文。",
    )
    app = create_test_app(service)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 422
    assert response.json() == {
        "detail": "无法构建 RAG 上下文。",
    }
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_returns_stable_embedding_failure_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="知识库向量服务暂时不可用。",
    )
    app = create_test_app(service)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量服务暂时不可用。"}
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_returns_stable_vector_dimension_error_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="知识库向量数据库配置不兼容。",
    )
    app = create_test_app(service)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量数据库配置不兼容。"}
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_stream_returns_assistant_and_done_events() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="订单正在配送中。",
        model="test-model",
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "订单到哪里了？"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.text == (
        'event: assistant\ndata: {"content": "订单正在配送中。", '
        '"model": "test-model", "citations": []}\n\n'
        "event: done\ndata: {}\n\n"
    )
    service.reply.assert_awaited_once_with(
        "订单到哪里了？",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_stream_serializes_citations_returned_by_service() -> None:
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="chunk-1",
        page_number=2,
        content="签收后七天内可申请退货。",
        score=0.95,
    )
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="根据政策，退货期限为七天。",
        model="test-model",
        citations=[citation],
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assistant_event = response.text.split("\n", maxsplit=2)[1]
    assert json.loads(assistant_event.removeprefix("data: ")) == {
        "content": "根据政策，退货期限为七天。",
        "model": "test-model",
        "citations": [
            {
                "source_id": "refund-policy-v1",
                "chunk_id": "chunk-1",
                "page_number": 2,
                "content": "签收后七天内可申请退货。",
                "score": 0.95,
            }
        ],
    }
    service.reply.assert_awaited_once_with(
        "退货政策",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_stream_order_result_returns_no_citations() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="我可以帮你查询订单，请提供订单编号。",
        model="test-model",
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "订单"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 200
    assistant_event = response.text.split("\n", maxsplit=2)[1]
    assert json.loads(assistant_event.removeprefix("data: "))["citations"] == []
    service.reply.assert_awaited_once_with(
        "订单",
        "demo-user-li",
        None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_chat_stream_returns_stable_rag_validation_error_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="无法构建 RAG 上下文。",
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "无法构建 RAG 上下文。"}
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_stream_returns_stable_embedding_failure_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="知识库向量服务暂时不可用。",
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量服务暂时不可用。"}
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_stream_returns_stable_vector_dimension_error_from_service() -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.side_effect = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="知识库向量数据库配置不兼容。",
    )
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量数据库配置不兼容。"}
    service.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_stream_requires_bearer_token_before_service_lookup() -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "订单到哪里了？"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    service.reply.assert_not_awaited()


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
