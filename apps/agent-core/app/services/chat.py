import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.support_graph import (
    SupportState,
    build_support_graph,
)


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


class ChatModel(Protocol):
    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
    ) -> str: ...


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str


class ChatService:
    def __init__(
        self,
        chat_model: ChatModel,
        model_name: str,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._stateless_graph = build_support_graph(
            chat_model.generate_reply,
        )
        self._checkpointed_graph = build_support_graph(
            chat_model.generate_reply,
            checkpointer=checkpointer,
        )
        self._model_name = model_name
        self._checkpoint_lock = asyncio.Lock()

    async def reply(
        self,
        message: str,
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> ChatResult:

        initial_state: SupportState = {
            "user_message": message,
            "request_id": request_id,
        }
        if thread_id is None:
            state = await self._stateless_graph.ainvoke(
                initial_state,
            )
        else:
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": thread_id,
                }
            }
            async with self._checkpoint_lock:
                state = await self._checkpointed_graph.ainvoke(
                    initial_state,
                    config=config,
                )
        content = state.get("response", "").strip()

        if not content:
            raise EmptyChatResponseError("Chat model returned an empty response.")

        return ChatResult(
            content=content,
            model=self._model_name,
        )
