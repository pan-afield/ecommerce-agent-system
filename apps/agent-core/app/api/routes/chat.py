import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import get_chat_service, get_current_user_id
from app.api.serializers import format_citations
from app.core.redis import consume_rate_limit
from app.schemas.chat import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
)
from app.schemas.error import ErrorDetail, ErrorResponse
from app.services.chat import ChatService

router = APIRouter(prefix="/v1", tags=["chat"])


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
    request: Request,
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse | JSONResponse:
    settings = request.app.state.settings
    redis_client = getattr(request.app.state, "redis_client", None)

    if redis_client is not None:
        allowed = await consume_rate_limit(
            redis_client,
            f"rate:v1:chat:user:{current_user_id}",
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

        if not allowed:
            error = ErrorResponse(
                error=ErrorDetail(
                    code="chat_rate_limited",
                    message="请求过于频繁，请稍后重试。",
                )
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=error.model_dump(),
                headers={
                    "Retry-After": str(settings.rate_limit_window_seconds),
                },
            )

    result = await service.reply(
        payload.message,
        current_user_id,
        payload.thread_id,
        request_id=payload.request_id,
    )

    return ChatResponse(
        assistant=AssistantMessage(content=result.content),
        model=result.model,
        citations=format_citations(result.citations),
    )


@router.post(
    "/chat/stream",
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
    response_model=None,
)
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse | JSONResponse:
    settings = request.app.state.settings
    redis_client = getattr(request.app.state, "redis_client", None)

    if redis_client is not None:
        allowed = await consume_rate_limit(
            redis_client,
            f"rate:v1:chat:user:{current_user_id}",
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

        if not allowed:
            error = ErrorResponse(
                error=ErrorDetail(
                    code="chat_rate_limited",
                    message="请求过于频繁，请稍后重试。",
                )
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=error.model_dump(),
                headers={
                    "Retry-After": str(settings.rate_limit_window_seconds),
                },
            )

    result = await service.reply(
        payload.message,
        current_user_id,
        payload.thread_id,
        request_id=payload.request_id,
    )

    async def event_generator() -> AsyncIterator[str]:
        payload_data = json.dumps(
            {
                "content": result.content,
                "model": result.model,
                "citations": [
                    citation.model_dump(mode="json")
                    for citation in format_citations(result.citations)
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
