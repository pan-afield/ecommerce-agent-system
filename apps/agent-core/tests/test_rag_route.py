from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncEngine

import app.api.routes.rag as rag_route_module
from app.api.router import api_router
from app.core.config import Settings
from app.rag.citations import KnowledgeCitation
from app.rag.local_embeddings import RagEmbeddingError
from app.rag.service import RagContext
from app.rag.vector_store import RagVectorDimensionError


class UnusedEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("route test replaces the RAG service")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("route test replaces the RAG service")


TEST_JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"


def auth_headers() -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": "test-user",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def create_rag_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(rag_route_module.router)
    app.state.settings = Settings(
        environment="test",
        jwt_secret_key=TEST_JWT_SECRET,
        _env_file=None,
    )
    app.state.database_engine = cast(AsyncEngine, object())
    app.state.rag_embeddings = UnusedEmbeddings()
    return app


def test_main_api_router_contains_rag_search_route() -> None:
    paths = {route.path for route in api_router.routes if isinstance(route, APIRoute)}

    assert "/v1/rag/search" in paths


@pytest.mark.asyncio
async def test_rag_search_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    service_called = False

    async def unexpected_service_call(*args: object, **kwargs: object) -> RagContext:
        nonlocal service_called
        service_called = True
        raise AssertionError("the service must not run for unauthenticated requests")

    monkeypatch.setattr(rag_route_module, "build_rag_context", unexpected_service_call)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录。"}
    assert service_called is False


@pytest.mark.asyncio
async def test_rag_search_returns_citations_from_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    citation = KnowledgeCitation(
        source_id="refund-policy-v1",
        chunk_id="a" * 64,
        page_number=2,
        content="退款需要订单本人提交。",
        score=0.25,
    )
    calls: list[tuple[object, object, str, str, int]] = []

    async def fake_build_context(
        engine: AsyncEngine,
        embeddings: Embeddings,
        query: str,
        *,
        limit: int,
        embedding_model: str,
    ) -> RagContext:
        calls.append((engine, embeddings, query, embedding_model, limit))
        return RagContext(query="退款政策", citations=[citation])

    monkeypatch.setattr(rag_route_module, "build_rag_context", fake_build_context)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "  退款政策  ", "limit": 5},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "query": "退款政策",
        "citations": [
            {
                "source_id": "refund-policy-v1",
                "chunk_id": "a" * 64,
                "page_number": 2,
                "content": "退款需要订单本人提交。",
                "score": 0.25,
            }
        ],
    }
    assert calls == [
        (
            app.state.database_engine,
            app.state.rag_embeddings,
            "  退款政策  ",
            app.state.settings.rag_embedding_model,
            5,
        )
    ]


@pytest.mark.asyncio
async def test_rag_search_returns_validation_error_for_missing_query() -> None:
    app = create_rag_test_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            headers=auth_headers(),
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rag_search_returns_503_when_embeddings_are_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    app.state.rag_embeddings = None
    service_called = False

    async def unexpected_service_call(*args: object, **kwargs: object) -> RagContext:
        nonlocal service_called
        service_called = True
        raise AssertionError("the service must not run without embeddings")

    monkeypatch.setattr(rag_route_module, "build_rag_context", unexpected_service_call)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策"},
            headers=auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "rag_not_configured",
            "message": "知识库检索服务尚未配置。",
        }
    }
    assert service_called is False


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ("   ", 3),
    ],
)
@pytest.mark.asyncio
async def test_rag_search_maps_service_value_error_to_422(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    limit: int,
) -> None:
    app = create_rag_test_app()
    sensitive_detail = "database connection secret"

    async def failing_service(*args: object, **kwargs: object) -> RagContext:
        raise ValueError(sensitive_detail)

    monkeypatch.setattr(rag_route_module, "build_rag_context", failing_service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": query, "limit": limit},
            headers=auth_headers(),
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "rag_invalid_query",
            "message": "知识库查询参数无效。",
        }
    }
    assert sensitive_detail not in response.text


@pytest.mark.asyncio
async def test_rag_search_maps_embedding_failure_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    sensitive_detail = "embedding dimension must be 1024"

    async def failing_service(*args: object, **kwargs: object) -> RagContext:
        raise RagEmbeddingError(sensitive_detail)

    monkeypatch.setattr(rag_route_module, "build_rag_context", failing_service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策"},
            headers=auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "rag_embedding_unavailable",
            "message": "知识库向量服务暂时不可用。",
        }
    }
    assert sensitive_detail not in response.text


@pytest.mark.asyncio
async def test_rag_search_maps_vector_dimension_mismatch_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    sensitive_detail = (
        "knowledge embedding column dimension mismatch: expected 1024, got 1536"
    )

    async def failing_service(*args: object, **kwargs: object) -> RagContext:
        raise RagVectorDimensionError(sensitive_detail)

    monkeypatch.setattr(rag_route_module, "build_rag_context", failing_service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策"},
            headers=auth_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "rag_database_incompatible",
            "message": "知识库向量数据库配置不兼容。",
        }
    }
    assert "1536" not in response.text


@pytest.mark.asyncio
async def test_rag_search_does_not_hide_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    sensitive_detail = "pgvector connection secret"

    async def failing_service(*args: object, **kwargs: object) -> RagContext:
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(rag_route_module, "build_rag_context", failing_service)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策"},
            headers=auth_headers(),
        )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert sensitive_detail not in response.text


@pytest.mark.parametrize("limit", [0, -1, 11])
@pytest.mark.asyncio
async def test_rag_search_rejects_limit_outside_api_bounds_before_service(
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    app = create_rag_test_app()
    service_called = False

    async def unexpected_service_call(*args: object, **kwargs: object) -> RagContext:
        nonlocal service_called
        service_called = True
        raise AssertionError("the service must not run for an invalid limit")

    monkeypatch.setattr(rag_route_module, "build_rag_context", unexpected_service_call)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退款政策", "limit": limit},
            headers=auth_headers(),
        )

    assert response.status_code == 422
    assert service_called is False


@pytest.mark.asyncio
async def test_rag_search_rejects_overlong_query_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_rag_test_app()
    service_called = False

    async def unexpected_service_call(*args: object, **kwargs: object) -> RagContext:
        nonlocal service_called
        service_called = True
        raise AssertionError("the service must not run for an overlong query")

    monkeypatch.setattr(rag_route_module, "build_rag_context", unexpected_service_call)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/v1/rag/search",
            params={"query": "退" * 501},
            headers=auth_headers(),
        )

    assert response.status_code == 422
    assert service_called is False
