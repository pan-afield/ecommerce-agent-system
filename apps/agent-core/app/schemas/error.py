from typing import Literal

from pydantic import BaseModel

ApiErrorCode = Literal[
    "chat_not_configured",
    "chat_authentication_failed",
    "chat_rate_limited",
    "chat_timeout",
    "chat_provider_unavailable",
    "rag_not_configured",
    "rag_invalid_query",
]


class ErrorDetail(BaseModel):
    code: ApiErrorCode
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
