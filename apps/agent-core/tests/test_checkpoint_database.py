import os
from collections.abc import Sequence
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.services.chat import ChatService


class FakeChatModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.received_messages: list[list[tuple[str, str]]] = []

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )
        return AIMessage(content=self.response)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_new_service_lifecycle() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    thread_id = f"checkpoint-test-{uuid4()}"

    first_model = FakeChatModel(response="第一条回复。")
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        await checkpointer.setup()
        first_service = ChatService(
            chat_model=first_model,
            model_name="test-model",
            checkpointer=checkpointer,
        )
        await first_service.reply(
            "第一条消息",
            user_id="checkpoint-test-user",
            thread_id=thread_id,
            request_id="request-1",
        )

    second_model = FakeChatModel(response="第二条回复。")
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        await checkpointer.setup()
        second_service = ChatService(
            chat_model=second_model,
            model_name="test-model",
            checkpointer=checkpointer,
        )

        retried_result = await second_service.reply(
            "第一条消息",
            user_id="checkpoint-test-user",
            thread_id=thread_id,
            request_id="request-1",
        )
        await second_service.reply(
            "第二条消息",
            user_id="checkpoint-test-user",
            thread_id=thread_id,
            request_id="request-2",
        )

    assert retried_result.content == "第一条回复。"
    assert second_model.received_messages == [
        [
            ("human", "第一条消息"),
            ("ai", "第一条回复。"),
            ("human", "第二条消息"),
        ]
    ]
