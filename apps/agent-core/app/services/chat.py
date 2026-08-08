from dataclasses import dataclass
from typing import Protocol


class ChatModel(Protocol):
    async def generate_reply(self, message: str) -> str: ...


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str


class ChatError(Exception):
    """Base exception for expected chat failures."""


class ChatNotConfiguredError(ChatError):
    """Raised when chat credentials are not configured."""


class ChatProviderAuthenticationError(ChatError):
    """Raised when the upstream provider rejects credentials."""


class ChatProviderRateLimitError(ChatError):
    """Raised when the upstream provider rate-limits a request."""


class ChatProviderTimeoutError(ChatError):
    """Raised when the upstream provider times out."""


class ChatProviderUnavailableError(ChatError):
    """Raised for other expected upstream provider failures."""


class EmptyChatResponseError(ChatProviderUnavailableError):
    """Raised when the model returns no usable assistant text."""


class ChatService:
    def __init__(
        self,
        chat_model: ChatModel,
        model_name: str,
    ) -> None:
        self._chat_model = chat_model
        self._model_name = model_name

    async def reply(self, message: str) -> ChatResult:
        content = (await self._chat_model.generate_reply(message)).strip()

        if not content:
            raise EmptyChatResponseError("Chat model returned an empty response.")

        return ChatResult(
            content=content,
            model=self._model_name,
        )
