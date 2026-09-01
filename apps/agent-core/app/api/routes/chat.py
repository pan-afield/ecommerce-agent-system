import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_chat_service, get_current_user_id
from app.api.serializers import format_citations
from app.schemas.chat import (
    AssistantMessage,
    ChatRequest,
    ChatResponse,
)
from app.schemas.error import ErrorResponse
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
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
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
)
async def chat_stream(
    payload: ChatRequest,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse:
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
