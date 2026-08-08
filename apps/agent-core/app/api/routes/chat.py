from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_chat_service
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
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def chat(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    result = await service.reply(payload.message)

    return ChatResponse(
        assistant=AssistantMessage(content=result.content),
        model=result.model,
    )
