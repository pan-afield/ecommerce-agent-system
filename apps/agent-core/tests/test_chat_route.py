import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.dependencies import get_chat_service
from app.core.config import Settings
from app.main import create_app
from app.rag.citations import KnowledgeCitation
from app.rag.local_embeddings import RagEmbeddingError
from app.rag.service import RagContext
from app.rag.vector_store import RagVectorDimensionError
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
    # These are normally initialized by the application lifespan.  Route tests
    # bypass the lifespan so they can inject a fake ChatService directly.
    app.state.rag_embeddings = None
    app.state.database_engine = object()
    app.dependency_overrides[get_chat_service] = lambda: service
    return app


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
        rag_prompt=None,
    )


@pytest.mark.asyncio
async def test_chat_without_embeddings_skips_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="收到。",
        model="test-model",
    )
    app = create_test_app(service)
    build_context = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.chat.build_rag_context",
        build_context,
    )

    response = await post_chat(app, "你好")

    assert response.status_code == 200
    build_context.assert_not_awaited()
    service.reply.assert_awaited_once_with(
        "你好",
        "demo-user-li",
        None,
        request_id=None,
        rag_prompt=None,
    )


@pytest.mark.asyncio
async def test_chat_with_embeddings_builds_and_forwards_rag_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="根据政策，退货期限为七天。",
        model="test-model",
    )
    app = create_test_app(service)
    app.state.rag_embeddings = object()

    citation = KnowledgeCitation(
        source_id="policy.md",
        chunk_id="chunk-1",
        page_number=1,
        content="签收后七天内可申请退货。",
        score=0.95,
    )
    build_context = AsyncMock(
        return_value=RagContext(query="退货政策", citations=[citation]),
    )
    def build_prompt(query: str, citations: list[KnowledgeCitation]) -> str:
        return f"RAG::{query}::{len(citations)}"

    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)
    monkeypatch.setattr("app.api.routes.chat.build_rag_prompt", build_prompt)

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
    build_context.assert_awaited_once_with(
        app.state.database_engine,
        app.state.rag_embeddings,
        "退货政策",
        embedding_model=app.state.settings.rag_embedding_model,
    )
    service.reply.assert_awaited_once_with(
        "退货政策",
        "demo-user-li",
        None,
        request_id=None,
        rag_prompt="RAG::退货政策::1",
    )


@pytest.mark.asyncio
async def test_chat_concurrent_requests_keep_rag_data_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()

    async def build_context(
        engine: object,
        embeddings: object,
        query: str,
        *,
        embedding_model: str,
    ) -> RagContext:
        assert embedding_model == app.state.settings.rag_embedding_model
        await asyncio.sleep(0)
        citation = KnowledgeCitation(
            source_id=f"policy-{query}",
            chunk_id=f"chunk-{query}",
            page_number=None,
            content=f"证据-{query}",
            score=0.9,
        )
        return RagContext(query=query, citations=[citation])

    def build_prompt(query: str, citations: list[KnowledgeCitation]) -> str:
        return f"PROMPT::{query}::{citations[0].source_id}"

    async def fake_reply(
        message: str,
        user_id: str,
        thread_id: str | None = None,
        *,
        request_id: str | None = None,
        rag_prompt: str | None = None,
    ) -> ChatResult:
        await asyncio.sleep(0)
        return ChatResult(content=f"回答-{message}", model="test-model")

    service.reply.side_effect = fake_reply
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)
    monkeypatch.setattr("app.api.routes.chat.build_rag_prompt", build_prompt)

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
    assert {call.kwargs["rag_prompt"] for call in service.reply.await_args_list} == {
        "PROMPT::退款::policy-退款",
        "PROMPT::发货::policy-发货",
    }


@pytest.mark.asyncio
async def test_chat_maps_rag_value_error_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    build_context = AsyncMock(
        side_effect=ValueError("internal embedding detail"),
    )
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 422
    assert response.json() == {
        "detail": "无法构建 RAG 上下文。",
    }
    assert "internal embedding detail" not in response.text
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_maps_embedding_failure_to_503_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    sensitive_detail = "embedding dimension must be 1024"
    build_context = AsyncMock(side_effect=RagEmbeddingError(sensitive_detail))
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量服务暂时不可用。"}
    assert sensitive_detail not in response.text
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_maps_vector_dimension_mismatch_to_503_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    sensitive_detail = (
        "knowledge embedding column dimension mismatch: expected 1024, got 1536"
    )
    build_context = AsyncMock(side_effect=RagVectorDimensionError(sensitive_detail))
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    response = await post_chat(app, "退货政策")

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量数据库配置不兼容。"}
    assert "1536" not in response.text
    service.reply.assert_not_awaited()


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
        rag_prompt=None,
    )


@pytest.mark.asyncio
async def test_chat_stream_with_embeddings_forwards_rag_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(
        content="根据政策，退货期限为七天。",
        model="test-model",
    )
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="chunk-1",
        page_number=2,
        content="签收后七天内可申请退货。",
        score=0.95,
    )
    build_context = AsyncMock(
        return_value=RagContext(query="退货政策", citations=[citation]),
    )

    def build_prompt(query: str, citations: list[KnowledgeCitation]) -> str:
        return f"STREAM-RAG::{query}::{len(citations)}"

    build_prompt_mock = Mock(side_effect=build_prompt)
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)
    monkeypatch.setattr("app.api.routes.chat.build_rag_prompt", build_prompt_mock)

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
    build_context.assert_awaited_once_with(
        app.state.database_engine,
        app.state.rag_embeddings,
        "退货政策",
        embedding_model=app.state.settings.rag_embedding_model,
    )
    build_prompt_mock.assert_called_once_with("退货政策", [citation])
    service.reply.assert_awaited_once_with(
        "退货政策",
        "demo-user-li",
        None,
        request_id=None,
        rag_prompt="STREAM-RAG::退货政策::1",
    )


@pytest.mark.asyncio
async def test_chat_stream_maps_rag_value_error_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    build_context = AsyncMock(side_effect=ValueError("internal stream detail"))
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "无法构建 RAG 上下文。"}
    assert "internal stream detail" not in response.text
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_stream_maps_embedding_failure_to_503_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    sensitive_detail = "embedding model cache unavailable"
    build_context = AsyncMock(side_effect=RagEmbeddingError(sensitive_detail))
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量服务暂时不可用。"}
    assert sensitive_detail not in response.text
    service.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_stream_maps_vector_dimension_mismatch_to_503_without_calling_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AsyncMock(spec=ChatService)
    app = create_test_app(service)
    app.state.rag_embeddings = object()
    sensitive_detail = (
        "knowledge embedding column dimension mismatch: expected 1024, got 1536"
    )
    build_context = AsyncMock(side_effect=RagVectorDimensionError(sensitive_detail))
    monkeypatch.setattr("app.api.routes.chat.build_rag_context", build_context)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/stream",
            json={"message": "退货政策"},
            headers=make_auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "知识库向量数据库配置不兼容。"}
    assert "1536" not in response.text
    service.reply.assert_not_awaited()


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
        rag_prompt=None,
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
