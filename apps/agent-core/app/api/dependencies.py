from typing import cast

from fastapi import Request

from app.services.chat import ChatNotConfiguredError, ChatService


def get_chat_service(request: Request) -> ChatService:
    service = cast(
        ChatService | None,
        request.app.state.chat_service,
    )

    if service is None:
        raise ChatNotConfiguredError("Chat service is not configured.")

    return service
