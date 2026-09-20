from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.main import create_app
from app.rag.citations import KnowledgeCitation
from app.rag.service import RagContext
from app.services.refund_sandbox import InMemoryRefundSandbox


async def test_lifespan_owns_postgres_checkpointer_for_chat_service() -> None:
    settings = Settings(
        environment="test",
        database_url=(
            "postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test"
        ),
        openai_api_key="test-key",
        _env_file=None,
    )

    checkpointer = MagicMock()
    checkpointer.setup = AsyncMock()
    checkpointer_context = MagicMock()
    checkpointer_context.__aenter__ = AsyncMock(return_value=checkpointer)
    checkpointer_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.main.OpenAIChatAdapter") as adapter_type,
        patch("app.main.ChatService") as service_type,
        patch("app.main.AsyncPostgresSaver") as saver_type,
        patch("app.main.build_lookup_order_tool") as tool_builder,
        patch("app.main.create_local_embeddings") as embeddings_builder,
        patch("app.main.build_rag_context", new_callable=AsyncMock) as context_builder,
        patch("app.main.build_rag_prompt") as prompt_builder,
    ):
        fake_order_tool = MagicMock()
        fake_embeddings = MagicMock()
        citation = KnowledgeCitation(
            source_id="refund-policy.md",
            chunk_id="r" * 64,
            page_number=1,
            content="签收后七天内可以申请退款。",
            score=0.02,
        )
        tool_builder.return_value = fake_order_tool
        embeddings_builder.return_value = fake_embeddings
        context_builder.return_value = RagContext(
            query="退款政策",
            citations=[citation],
        )
        prompt_builder.return_value = "RAG_PROMPT::退款政策"
        saver_type.from_conn_string.return_value = checkpointer_context
        application = create_app(settings)

        service_type.assert_not_called()

        async with application.router.lifespan_context(application):
            service_type.assert_called_once()
            checkpointer.setup.assert_awaited_once()
            rag_context_builder = service_type.call_args.kwargs["rag_context_builder"]
            assert rag_context_builder is not None
            prompt, citations = await rag_context_builder("退款政策")

            assert prompt == "RAG_PROMPT::退款政策"
            assert citations == [citation]
            context_builder.assert_awaited_once_with(
                application.state.database_engine,
                fake_embeddings,
                "退款政策",
                embedding_model=settings.rag_embedding_model,
            )
            prompt_builder.assert_called_once_with("退款政策", [citation])

            context_builder.reset_mock()
            prompt_builder.reset_mock()
            context_builder.return_value = RagContext(
                query="你好",
                citations=[],
            )

            empty_prompt, empty_citations = await rag_context_builder("你好")

            assert empty_prompt is None
            assert empty_citations == []
            prompt_builder.assert_not_called()

    assert service_type.call_args is not None
    assert service_type.call_args.kwargs["checkpointer"] is checkpointer
    saver_type.from_conn_string.assert_called_once_with(settings.database_url)
    checkpointer_context.__aenter__.assert_awaited_once()
    checkpointer_context.__aexit__.assert_awaited_once()
    adapter_type.assert_called_once()
    embeddings_builder.assert_called_once_with(settings)
    assert application.state.rag_embeddings is fake_embeddings
    tool_builder.assert_called_once_with(application.state.database_engine)
    assert service_type.call_args.kwargs["order_tools"] == [fake_order_tool]
    assert (
        service_type.call_args.kwargs["intent_router"]
        is adapter_type.return_value.route_intent
    )


async def test_lifespan_initializes_rag_without_openai_chat_configuration() -> None:
    settings = Settings(
        environment="test",
        database_url=(
            "postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test"
        ),
        openai_api_key=None,
        _env_file=None,
    )
    fake_embeddings = MagicMock()

    with patch(
        "app.main.create_local_embeddings",
        return_value=fake_embeddings,
    ) as embeddings_builder:
        application = create_app(settings)

        async with application.router.lifespan_context(application):
            assert application.state.rag_embeddings is fake_embeddings
            assert application.state.chat_service is None

    embeddings_builder.assert_called_once_with(settings)


async def test_lifespan_reuses_refund_sandbox_per_app_without_sharing_between_apps() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=None),
        patch("app.main.create_local_embeddings", return_value=None),
    ):
        first_app = create_app(settings)
        second_app = create_app(settings)

        async with first_app.router.lifespan_context(first_app):
            first_adapter = first_app.state.refund_sandbox_adapter
            assert isinstance(first_adapter, InMemoryRefundSandbox)
            assert first_app.state.refund_sandbox_adapter is first_adapter
            async with second_app.router.lifespan_context(second_app):
                assert isinstance(second_app.state.refund_sandbox_adapter, InMemoryRefundSandbox)
                assert second_app.state.refund_sandbox_adapter is not first_adapter

    assert fake_engine.dispose.await_count == 2


async def test_lifespan_creates_and_closes_http_refund_sandbox_client() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        refund_sandbox_base_url="https://sandbox.example.test/api",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    fake_adapter = MagicMock()

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=None),
        patch("app.main.create_local_embeddings", return_value=None),
        patch("app.main.httpx.AsyncClient", return_value=fake_http_client) as client_factory,
        patch("app.main.HttpRefundSandbox", return_value=fake_adapter) as adapter_factory,
    ):
        application = create_app(settings)

        async with application.router.lifespan_context(application):
            assert application.state.refund_sandbox_adapter is fake_adapter

    client_factory.assert_called_once_with(
        base_url="https://sandbox.example.test/api",
        timeout=10.0,
        headers={},
    )
    adapter_factory.assert_called_once_with(fake_http_client)
    fake_http_client.aclose.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("failure_stage", ["client", "adapter"])
async def test_lifespan_releases_acquired_resources_when_refund_startup_fails(
    failure_stage: str,
) -> None:
    settings = Settings(
        environment="test",
        refund_sandbox_base_url="https://sandbox.example.test",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    failure = RuntimeError("simulated refund initialization failure")

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=fake_redis),
        patch("app.main.create_local_embeddings") as embeddings_factory,
        patch("app.main.httpx.AsyncClient", return_value=fake_http_client) as client_factory,
        patch("app.main.HttpRefundSandbox") as adapter_factory,
    ):
        if failure_stage == "client":
            client_factory.side_effect = failure
        else:
            adapter_factory.side_effect = failure
        application = create_app(settings)

        with pytest.raises(RuntimeError) as caught:
            async with application.router.lifespan_context(application):
                pytest.fail("Application must not accept requests after startup failure")

    assert caught.value is failure
    client_factory.assert_called_once()
    embeddings_factory.assert_not_called()
    if failure_stage == "client":
        adapter_factory.assert_not_called()
        fake_http_client.aclose.assert_not_awaited()
    else:
        adapter_factory.assert_called_once_with(fake_http_client)
        fake_http_client.aclose.assert_awaited_once()
    fake_redis.aclose.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("failure_stage", ["http", "redis"])
async def test_lifespan_attempts_remaining_cleanup_after_close_failure(
    failure_stage: str,
) -> None:
    settings = Settings(
        environment="test",
        refund_sandbox_base_url="https://sandbox.example.test",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock()
    failure = RuntimeError("simulated resource cleanup failure")
    if failure_stage == "http":
        fake_http_client.aclose.side_effect = failure
    else:
        fake_redis.aclose.side_effect = failure

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=fake_redis),
        patch("app.main.create_local_embeddings", return_value=None),
        patch("app.main.httpx.AsyncClient", return_value=fake_http_client),
        patch("app.main.HttpRefundSandbox"),
    ):
        application = create_app(settings)
        with pytest.raises(RuntimeError) as caught:
            async with application.router.lifespan_context(application):
                fake_http_client.aclose.assert_not_awaited()
                fake_redis.aclose.assert_not_awaited()
                fake_engine.dispose.assert_not_awaited()

    assert caught.value is failure
    fake_http_client.aclose.assert_awaited_once()
    fake_redis.aclose.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


async def test_lifespan_exposes_last_cleanup_failure_after_attempting_all_resources() -> None:
    settings = Settings(
        environment="test",
        refund_sandbox_base_url="https://sandbox.example.test",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock(side_effect=RuntimeError("engine cleanup failed"))
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock(side_effect=RuntimeError("redis cleanup failed"))
    fake_http_client = MagicMock()
    fake_http_client.aclose = AsyncMock(side_effect=RuntimeError("http cleanup failed"))

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=fake_redis),
        patch("app.main.create_local_embeddings", return_value=None),
        patch("app.main.httpx.AsyncClient", return_value=fake_http_client),
        patch("app.main.HttpRefundSandbox"),
    ):
        application = create_app(settings)
        with pytest.raises(RuntimeError, match="engine cleanup failed"):
            async with application.router.lifespan_context(application):
                pytest.fail("Application should exit through lifespan cleanup")

    fake_http_client.aclose.assert_awaited_once()
    fake_redis.aclose.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


async def test_lifespan_keeps_app_available_when_local_rag_initialization_fails() -> None:
    settings = Settings(
        environment="test",
        database_url=(
            "postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test"
        ),
        openai_api_key=None,
        _env_file=None,
    )

    with patch(
        "app.main.create_local_embeddings",
        side_effect=RuntimeError("model cache is unavailable"),
    ) as embeddings_builder:
        application = create_app(settings)

        async with application.router.lifespan_context(application):
            assert application.state.rag_embeddings is None
            assert application.state.chat_service is None

    embeddings_builder.assert_called_once_with(settings)


async def test_lifespan_disposes_database_when_redis_is_unconfigured() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=None) as redis_factory,
        patch("app.main.create_local_embeddings", return_value=None),
    ):
        application = create_app(settings)

        async with application.router.lifespan_context(application):
            assert application.state.redis_client is None

    redis_factory.assert_called_once_with(settings)
    fake_engine.dispose.assert_awaited_once()


async def test_lifespan_closes_redis_and_database_on_shutdown() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test",
        redis_url="redis://localhost:6379/0",
        openai_api_key=None,
        _env_file=None,
    )
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()

    with (
        patch("app.main.create_database_engine", return_value=fake_engine),
        patch("app.main.create_redis_client", return_value=fake_redis) as redis_factory,
        patch("app.main.create_local_embeddings", return_value=None),
    ):
        application = create_app(settings)

        async with application.router.lifespan_context(application):
            assert application.state.redis_client is fake_redis

    redis_factory.assert_called_once_with(settings)
    fake_redis.aclose.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()
