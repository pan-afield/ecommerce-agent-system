import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.support_graph import (
    IntentRouter,
    RagContextBuilder,
    SupportState,
    build_support_graph,
)
from app.rag.citations import KnowledgeCitation


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
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage: ...


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    citations: list[KnowledgeCitation] = field(default_factory=list)


class ChatService:
    def __init__(
        self,
        chat_model: ChatModel,
        model_name: str,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        *,
        order_tools: Sequence[BaseTool] = (),
        rag_context_builder: RagContextBuilder | None = None,
        intent_router: IntentRouter | None = None,
    ) -> None:
        self._stateless_graph = build_support_graph(
            chat_model.generate_reply,
            order_tools=order_tools,
            rag_context_builder=rag_context_builder,
            intent_router=intent_router,
        )
        self._checkpointed_graph = build_support_graph(
            chat_model.generate_reply,
            checkpointer=checkpointer,
            order_tools=order_tools,
            rag_context_builder=rag_context_builder,
            intent_router=intent_router,
        )
        self._model_name = model_name
        # 上协程锁
        self._checkpoint_lock = asyncio.Lock()

    async def reply(
        self,
        message: str,
        user_id: str,
        thread_id: str | None = None,
        request_id: str | None = None,
        rag_prompt: str | None = None,
    ) -> ChatResult:
        initial_state: SupportState = {
            "user_id": user_id,
            "user_message": message,
            "request_id": request_id,
            "rag_prompt": rag_prompt,
        }
        if thread_id is None:
            state = await self._stateless_graph.ainvoke(
                initial_state,
            )
        else:
            checkpoint_thread_id = json.dumps(
                [user_id, thread_id],
                separators=(",", ":"),
            )
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": checkpoint_thread_id,
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
            citations=[
                KnowledgeCitation(
                    source_id=citation["source_id"],
                    chunk_id=citation["chunk_id"],
                    page_number=citation["page_number"],
                    content=citation["content"],
                    score=citation["score"],
                )
                for citation in state.get("rag_citations", [])
            ],
        )
