from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, status
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.adapters.openai_chat import OpenAIChatAdapter
from app.agents.support_graph import RagContextBuilder
from app.api.exception_handlers import chat_error_handler
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.core.redis import create_redis_client
from app.rag.citations import KnowledgeCitation
from app.rag.local_embeddings import RagEmbeddingError, create_local_embeddings
from app.rag.service import build_rag_context, build_rag_prompt
from app.rag.vector_store import RagVectorDimensionError
from app.services.chat import ChatError, ChatService
from app.services.health import database_is_ready
from app.services.refund_sandbox import HttpRefundSandbox, InMemoryRefundSandbox
from app.tools.orders import build_lookup_order_tool


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application instance with explicitly owned runtime resources."""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """在应用启动时创建共享资源，并在退出时释放数据库引擎。"""
        engine = create_database_engine(app_settings.database_url)
        redis_client = create_redis_client(app_settings)
        app.state.redis_client = redis_client
        app.state.rag_embeddings = None
        app.state.database_engine = engine
        app.state.readiness_probe = database_is_ready
        app.state.chat_service = None
        refund_http_client: httpx.AsyncClient | None = None

        try:
            if app_settings.refund_sandbox_base_url is None:
                app.state.refund_sandbox_adapter = InMemoryRefundSandbox()
            else:
                refund_http_client = httpx.AsyncClient(
                    base_url=str(app_settings.refund_sandbox_base_url),
                    timeout=app_settings.refund_sandbox_request_timeout_seconds,
                    headers=(
                        {
                            "Authorization": "Bearer "
                            + app_settings.refund_sandbox_api_key.get_secret_value()
                        }
                        if app_settings.refund_sandbox_api_key is not None
                        else {}
                    ),
                )
                app.state.refund_sandbox_adapter = HttpRefundSandbox(refund_http_client)

            try:
                app.state.rag_embeddings = create_local_embeddings(app_settings)
            except Exception:
                app.state.rag_embeddings = None

            rag_context_builder: RagContextBuilder | None = None
            if app.state.rag_embeddings is not None:

                async def build_rag_context_builder(
                    message: str,
                ) -> tuple[str | None, list[KnowledgeCitation]]:
                    try:
                        context = await build_rag_context(
                            engine,
                            app.state.rag_embeddings,
                            message,
                            embedding_model=app_settings.rag_embedding_model,
                        )
                        if not context.citations:
                            return None, []
                        prompt = build_rag_prompt(
                            message,
                            context.citations,
                        )
                        return (prompt, context.citations)

                    except RagEmbeddingError as error:
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="知识库向量服务暂时不可用。",
                        ) from error
                    except RagVectorDimensionError as error:
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="知识库向量数据库配置不兼容。",
                        ) from error
                    except ValueError as error:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="无法构建 RAG 上下文。",
                        ) from error

                rag_context_builder = build_rag_context_builder

            if app_settings.openai_api_key is None:
                yield
                return

            async with AsyncPostgresSaver.from_conn_string(
                app_settings.database_url,
            ) as checkpointer:
                await checkpointer.setup()

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
                order_tools = [build_lookup_order_tool(engine)]
                app.state.chat_service = ChatService(
                    chat_model=chat_model,
                    model_name=app_settings.openai_agent_model,
                    checkpointer=checkpointer,
                    order_tools=order_tools,
                    rag_context_builder=rag_context_builder,
                    intent_router=chat_model.route_intent,
                )
                yield
        finally:
            try:
                if refund_http_client is not None:
                    await refund_http_client.aclose()
            finally:
                try:
                    if redis_client is not None:
                        await redis_client.aclose()
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
