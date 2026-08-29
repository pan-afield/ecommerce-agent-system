from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.rag import KnowledgeCitationResponse

ChatMessage = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2_000,
    ),
]


ChatThreadId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
    ),
]

ChatRequestId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
    ),
]


class ChatRequest(BaseModel):
    message: ChatMessage
    thread_id: ChatThreadId | None = None
    request_id: ChatRequestId | None = None

    @model_validator(mode="after")
    def request_id_requires_thread_id(self) -> Self:
        if self.request_id is not None and self.thread_id is None:
            raise ValueError("thread_id is required when request_id is provided.")

        return self


class AssistantMessage(BaseModel):
    content: str


class ChatResponse(BaseModel):
    assistant: AssistantMessage
    model: str
    citations: list[KnowledgeCitationResponse] = Field(default_factory=list)
