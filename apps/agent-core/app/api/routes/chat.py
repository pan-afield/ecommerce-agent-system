import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_chat_service, get_current_user_id
from app.api.serializers import format_citations
from app.rag.citations import KnowledgeCitation
from app.rag.local_embeddings import RagEmbeddingError
from app.rag.service import build_rag_context, build_rag_prompt
from app.rag.vector_store import RagVectorDimensionError
from app.schemas.chat import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
)
from app.schemas.error import ErrorResponse
from app.services.chat import ChatService

router = APIRouter(prefix="/v1", tags=["chat"])


async def _build_chat_rag_prompt(
    request: Request,
    message: str,
) -> tuple[str | None, list[KnowledgeCitation]]:
    rag_prompt: str | None = None
    rag_embeddings = request.app.state.rag_embeddings
    database_engine = request.app.state.database_engine
    embedding_model = request.app.state.settings.rag_embedding_model
    if rag_embeddings is not None:
        try:
            rag_context = await build_rag_context(
                database_engine, rag_embeddings, message, embedding_model=embedding_model
            )
            rag_prompt = build_rag_prompt(
                message,
                rag_context.citations,
            )
            return rag_prompt, rag_context.citations
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

    return None, []


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def chat(
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
    request: Request,
) -> ChatResponse:
    rag_prompt, citations = await _build_chat_rag_prompt(request, payload.message)
    result = await service.reply(
        payload.message,
        current_user_id,
        payload.thread_id,
        request_id=payload.request_id,
        rag_prompt=rag_prompt,
    )

    return ChatResponse(
        assistant=AssistantMessage(content=result.content),
        model=result.model,
        citations=format_citations(citations),
    )


@router.post(
    "/chat/stream",
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def chat_stream(
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
    request: Request,
) -> StreamingResponse:
    rag_prompt, citations = await _build_chat_rag_prompt(request, payload.message)
    result = await service.reply(
        payload.message,
        current_user_id,
        payload.thread_id,
        request_id=payload.request_id,
        rag_prompt=rag_prompt,
    )

    async def event_generator() -> AsyncIterator[str]:
        payload_data = json.dumps(
            {
                "content": result.content,
                "model": result.model,
                "citations": [
                    citation.model_dump(mode="json") for citation in format_citations(citations)
                ],
            },
            ensure_ascii=False,
        )
        yield f"event: assistant\ndata: {payload_data}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
