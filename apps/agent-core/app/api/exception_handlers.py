import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.schemas.error import ChatErrorCode, ErrorDetail, ErrorResponse
from app.services.chat import (
    ChatError,
    ChatNotConfiguredError,
    ChatProviderAuthenticationError,
    ChatProviderRateLimitError,
    ChatProviderTimeoutError,
)

logger = logging.getLogger(__name__)


def _map_chat_error(
    error: ChatError,
) -> tuple[int, ChatErrorCode, str]:
    if isinstance(error, ChatNotConfiguredError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_not_configured",
            "客服服务尚未配置。",
        )

    if isinstance(error, ChatProviderAuthenticationError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "chat_authentication_failed",
            "客服服务暂时不可用，请联系管理员。",
        )

    if isinstance(error, ChatProviderRateLimitError):
        return (
            status.HTTP_429_TOO_MANY_REQUESTS,
            "chat_rate_limited",
            "请求过于频繁，请稍后重试。",
        )

    if isinstance(error, ChatProviderTimeoutError):
        return (
            status.HTTP_504_GATEWAY_TIMEOUT,
            "chat_timeout",
            "客服服务响应超时，请稍后重试。",
        )

    return (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "chat_provider_unavailable",
        "客服服务暂时不可用，请稍后重试。",
    )


async def chat_error_handler(
    request: Request,
    error: ChatError,
) -> JSONResponse:
    status_code, code, message = _map_chat_error(error)

    logger.warning(
        "Chat request failed: error_type=%s path=%s",
        type(error).__name__,
        request.url.path,
    )

    response = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(),
    )
