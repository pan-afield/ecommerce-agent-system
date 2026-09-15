"""真实路由和计数函数配合 Fake Redis；不运行服务或真实模型。"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from langgraph.checkpoint.memory import InMemorySaver

import app.api.routes.rag as rag_route
from app.rag.service import RagContext
from app.services.chat import ChatResult, ChatService
from tests.conftest import FakeRoleEngine
from tests.test_chat_route import FakeRouteChatModel, create_test_app, make_auth_headers
from tests.test_rag_route import UnusedEmbeddings
from tests.test_rate_limit import FakeRateLimitRedis

PATHS = ("/v1/chat", "/v1/chat/stream", "/v1/rag/search")


def make_limited_app(redis: FakeRateLimitRedis, service: ChatService) -> FastAPI:
    app = create_test_app(service)
    app.state.settings.rate_limit_requests = 2
    app.state.settings.rate_limit_window_seconds = 10
    app.state.redis_client = redis
    app.state.database_engine = FakeRoleEngine(
        {"user-a": "CUSTOMER", "user-b": "CUSTOMER", "unknown": "UNKNOWN"}
    )
    app.state.rag_embeddings = UnusedEmbeddings()
    return app


@pytest.fixture
def setup_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock]:
    redis = FakeRateLimitRedis()
    service = AsyncMock(spec=ChatService)
    service.reply.return_value = ChatResult(content="测试回复。", model="fake-model")
    context = AsyncMock(return_value=RagContext(query="退款政策", citations=[]))
    monkeypatch.setattr(rag_route, "build_rag_context", context)
    return make_limited_app(redis, service), redis, service, context


async def request_endpoint(
    client: AsyncClient, path: str, user_id: str = "user-a"
) -> Response:
    headers = make_auth_headers(user_id)
    if path == "/v1/rag/search":
        # 证据数量 limit=1 不应误作每个用户的请求次数上限。
        return await client.get(path, params={"query": "退款政策", "limit": 1}, headers=headers)
    return await client.post(path, json={"message": "你好"}, headers=headers)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.asyncio
async def test_route_threshold_and_expiry_precede_business_work(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock], path: str
) -> None:
    app, redis, service, context = setup_limit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await request_endpoint(client, path)).status_code == 200
        assert (await request_endpoint(client, path)).status_code == 200
        work_before = (service.reply.await_count, context.await_count)
        cache_before = list(redis.cache_operations)
        rejected = await request_endpoint(client, path)

        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "10"
        assert rejected.headers["content-type"].startswith("application/json")
        code = "rag_rate_limited" if path == "/v1/rag/search" else "chat_rate_limited"
        assert rejected.json() == {
            "error": {"code": code, "message": "请求过于频繁，请稍后重试。"}
        }
        assert (service.reply.await_count, context.await_count) == work_before
        assert redis.cache_operations == cache_before
        if path == "/v1/rag/search":
            # 第二次命中缓存仍消耗额度；第三次在读取缓存前已被拦截。
            context.assert_awaited_once()
            assert [operation for operation, _ in cache_before] == ["get", "set", "get"]
        else:
            assert service.reply.await_count == 2

        redis.advance(10)
        assert (await request_endpoint(client, path)).status_code == 200


@pytest.mark.asyncio
async def test_chat_and_stream_share_quota_but_rag_and_other_users_do_not(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock],
) -> None:
    app, redis, _, _ = setup_limit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await request_endpoint(client, "/v1/chat")).status_code == 200
        assert (await request_endpoint(client, "/v1/chat/stream")).status_code == 200
        assert (await request_endpoint(client, "/v1/chat")).status_code == 429
        assert (await request_endpoint(client, "/v1/rag/search")).status_code == 200
        assert (await request_endpoint(client, "/v1/chat", "user-b")).status_code == 200

    assert redis.values == {
        "rate:v1:chat:user:user-a": 3,
        "rate:v1:rag:user:user-a": 1,
        "rate:v1:chat:user:user-b": 1,
    }


@pytest.mark.parametrize("paths", [("/v1/chat", "/v1/chat/stream"), ("/v1/rag/search",)])
@pytest.mark.asyncio
async def test_two_app_instances_share_limits_under_concurrent_requests(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock],
    paths: tuple[str, ...],
) -> None:
    first_app, redis, service, _ = setup_limit
    second_app = make_limited_app(redis, service)
    async with (
        AsyncClient(transport=ASGITransport(app=first_app), base_url="http://first") as first,
        AsyncClient(transport=ASGITransport(app=second_app), base_url="http://second") as second,
    ):
        requests = [(user, index) for user in ("user-a", "user-b") for index in range(5)]
        responses = await asyncio.gather(
            *(
                request_endpoint(
                    first if index % 2 == 0 else second, paths[index % len(paths)], user
                )
                for user, index in requests
            )
        )

    for user in ("user-a", "user-b"):
        statuses = [
            response.status_code
            for (request_user, _), response in zip(requests, responses, strict=True)
            if request_user == user
        ]
        assert statuses.count(200) == 2
        assert statuses.count(429) == 3
    bucket = "rag" if paths == ("/v1/rag/search",) else "chat"
    assert redis.values == {f"rate:v1:{bucket}:user:{user}": 5 for user in ("user-a", "user-b")}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.asyncio
async def test_authentication_and_input_validation_do_not_consume_quota(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock], path: str
) -> None:
    app, redis, service, context = setup_limit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for headers in ({}, {"Authorization": "Bearer invalid-token"}):
            if path == "/v1/rag/search":
                response = await client.get(path, params={"query": "退款政策"}, headers=headers)
            else:
                response = await client.post(path, json={"message": "你好"}, headers=headers)
            assert response.status_code == 401

        if path == "/v1/rag/search":
            invalid = await client.get(
                path, params={"query": ""}, headers=make_auth_headers("user-a")
            )
        else:
            invalid = await client.post(
                path, json={"message": ""}, headers=make_auth_headers("user-a")
            )
        assert invalid.status_code == 422

    assert redis.pipeline_calls == 0
    service.reply.assert_not_awaited()
    context.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_forbidden_role_cannot_consume_quota_or_read_cache(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock],
) -> None:
    app, redis, _, context = setup_limit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await request_endpoint(client, "/v1/rag/search", "unknown")
    assert response.status_code == 403
    assert redis.pipeline_calls == 0
    assert redis.cache_operations == []
    context.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_preserves_checkpointed_request_replay() -> None:
    redis = FakeRateLimitRedis()
    model = FakeRouteChatModel(response="首次回答。")
    service = ChatService(chat_model=model, model_name="fake-model", checkpointer=InMemorySaver())
    app = make_limited_app(redis, service)
    payload = {"message": "你好", "thread_id": "thread-1", "request_id": "request-1"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/v1/chat", json=payload, headers=make_auth_headers("user-a"))
        replay = await client.post("/v1/chat", json=payload, headers=make_auth_headers("user-a"))
        denied = await client.post("/v1/chat", json=payload, headers=make_auth_headers("user-a"))
        assert first.status_code == replay.status_code == 200
        assert replay.json() == first.json()
        assert denied.status_code == 429
        redis.advance(10)
        later = await client.post("/v1/chat", json=payload, headers=make_auth_headers("user-a"))
        assert later.status_code == 200
        assert later.json() == first.json()
    assert model.received_messages == [[("human", "你好")]]


def test_all_limited_routes_document_json_429(
    setup_limit: tuple[FastAPI, FakeRateLimitRedis, AsyncMock, AsyncMock],
) -> None:
    app, _, _, _ = setup_limit
    schema = app.openapi()
    for path in PATHS:
        method = "get" if path == "/v1/rag/search" else "post"
        response = schema["paths"][path][method]["responses"]["429"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
