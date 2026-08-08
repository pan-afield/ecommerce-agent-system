from typing import Annotated

from pydantic import BaseModel, StringConstraints

ChatMessage = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2_000,
    ),
]


class ChatRequest(BaseModel):
    message: ChatMessage


class AssistantMessage(BaseModel):
    content: str


class ChatResponse(BaseModel):
    assistant: AssistantMessage
    model: str
