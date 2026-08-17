from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import Settings
from app.main import create_app


async def test_lifespan_creates_one_in_memory_checkpointer_for_chat_service() -> None:
    settings = Settings(
        environment="test",
        database_url=(
            "postgresql://postgres:postgres@localhost:5432/ecommerce_agents_test"
        ),
        openai_api_key="test-key",
        _env_file=None,
    )

    with (
        patch("app.main.OpenAIChatAdapter") as adapter_type,
        patch("app.main.ChatService") as service_type,
    ):
        application = create_app(settings)

        service_type.assert_not_called()

        async with application.router.lifespan_context(application):
            service_type.assert_called_once()

    assert service_type.call_args is not None
    checkpointer = service_type.call_args.kwargs["checkpointer"]
    assert isinstance(checkpointer, InMemorySaver)
    adapter_type.assert_called_once()
