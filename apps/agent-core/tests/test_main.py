from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import Settings
from app.main import create_app


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
    ):
        fake_order_tool = MagicMock()
        fake_embeddings = MagicMock()
        tool_builder.return_value = fake_order_tool
        embeddings_builder.return_value = fake_embeddings
        saver_type.from_conn_string.return_value = checkpointer_context
        application = create_app(settings)

        service_type.assert_not_called()

        async with application.router.lifespan_context(application):
            service_type.assert_called_once()
            checkpointer.setup.assert_awaited_once()

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
