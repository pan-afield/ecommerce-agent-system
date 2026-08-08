from typing import Literal

from pydantic import BaseModel

ChatErrorCode = Literal[
    "chat_not_configured",
    "chat_authentication_failed",
    "chat_rate_limited",
    "chat_timeout",
    "chat_provider_unavailable",
]


class ErrorDetail(BaseModel):
    code: ChatErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
