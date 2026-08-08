from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.adapters.openai_chat import OpenAIChatAdapter
from app.api.exception_handlers import chat_error_handler
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.services.chat import ChatError, ChatService
from app.services.health import database_is_ready


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application instance with explicitly owned runtime resources."""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(app_settings.database_url)
        app.state.database_engine = engine
        app.state.readiness_probe = database_is_ready
        app.state.chat_service = None

        if app_settings.openai_api_key is not None:
            chat_model = OpenAIChatAdapter(
                api_key=app_settings.openai_api_key,
                model=app_settings.openai_agent_model,
                base_url=(
                    str(app_settings.openai_base_url)
                    if app_settings.openai_base_url is not None
                    else None
                ),
                reasoning_effort=app_settings.openai_reasoning_effort,
                use_responses_api=app_settings.openai_use_responses_api,
                timeout_seconds=app_settings.openai_request_timeout_seconds,
            )
            app.state.chat_service = ChatService(
                chat_model=chat_model,
                model_name=app_settings.openai_agent_model,
            )
        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if app_settings.docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.exception_handler(ChatError)(chat_error_handler)
    application.include_router(api_router)
    return application


app = create_app()
