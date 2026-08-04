from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.services.health import database_is_ready


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application instance with explicitly owned runtime resources."""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(app_settings.database_url)
        app.state.database_engine = engine
        app.state.readiness_probe = database_is_ready
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
    application.include_router(api_router)
    return application


app = create_app()
