import asyncio
import json
from collections.abc import Sequence
from typing import cast

import pytest
from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import Checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from app.services.chat import ChatResult, ChatService, EmptyChatResponseError

TEST_USER_ID = "demo-user-li"


def checkpoint_config(
    thread_id: str,
    user_id: str = TEST_USER_ID,
) -> RunnableConfig:
    checkpoint_thread_id = json.dumps(
        [user_id, thread_id],
        separators=(",", ":"),
    )
    return {"configurable": {"thread_id": checkpoint_thread_id}}


class FakeChatModel:
    def __init__(
        self,
        response: str = "",
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.received_messages: list[list[tuple[str, str]]] = []

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )

        if self.error is not None:
            raise self.error

        return AIMessage(content=self.response)


class BlockingFakeChatModel:
    def __init__(self) -> None:
        self.first_call_started = asyncio.Event()
        self.release_first_call = asyncio.Event()
        self.received_messages: list[list[tuple[str, str]]] = []
        self.active_calls = 0
        self.maximum_active_calls = 0

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        self.received_messages.append(
            [(message.type, message.text) for message in messages]
        )
        self.active_calls += 1
        self.maximum_active_calls = max(
            self.maximum_active_calls,
            self.active_calls,
        )

        try:
            if len(self.received_messages) == 1:
                self.first_call_started.set()
                await self.release_first_call.wait()
            return AIMessage(content="收到。")
        finally:
            self.active_calls -= 1


def checkpoint_messages(checkpoint: Checkpoint) -> list[tuple[str, str]]:
    messages = cast(
        list[AnyMessage],
        checkpoint["channel_values"]["messages"],
    )
    return [(message.type, message.text) for message in messages]


async def test_chat_service_returns_trimmed_reply_and_configured_model() -> None:
    chat_model = FakeChatModel(response="  您的商品正在准备发货。  ")
    service = ChatService(chat_model=chat_model, model_name="gpt-test-model")

    result = await service.reply("请问什么时候发货？", user_id=TEST_USER_ID)

    assert result == ChatResult(
        content="您的商品正在准备发货。",
        model="gpt-test-model",
    )
    assert chat_model.received_messages == [
        [("human", "请问什么时候发货？")]
    ]


async def test_chat_service_passes_rag_prompt_to_graph_message() -> None:
    chat_model = FakeChatModel(response="根据政策，退款需要订单本人提交。")
    service = ChatService(chat_model=chat_model, model_name="gpt-test-model")
    rag_prompt = (
        "请根据以下企业知识库证据回答用户问题。\n\n"
        "知识库证据：\n退款需要订单本人提交。\n\n"
        "用户问题：\n退款政策"
    )

    await service.reply(
        "退款政策",
        user_id=TEST_USER_ID,
        rag_prompt=rag_prompt,
    )

    assert chat_model.received_messages == [[("human", rag_prompt)]]


async def test_chat_service_passes_rag_prompt_through_checkpointed_thread() -> None:
    chat_model = FakeChatModel(response="根据政策，退款需要订单本人提交。")
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )
    rag_prompt = "知识库证据：退款需要订单本人提交。"

    await service.reply(
        "退款政策",
        user_id=TEST_USER_ID,
        thread_id="thread-with-rag",
        rag_prompt=rag_prompt,
    )

    assert chat_model.received_messages == [[("human", rag_prompt)]]
    checkpoint = await checkpointer.aget_tuple(
        checkpoint_config("thread-with-rag")
    )
    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["rag_prompt"] == rag_prompt


async def test_chat_service_does_not_reuse_rag_prompt_on_next_thread_request() -> None:
    chat_model = FakeChatModel(response="收到。")
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=InMemorySaver(),
    )

    await service.reply(
        "退款政策",
        user_id=TEST_USER_ID,
        thread_id="thread-rag-scope",
        rag_prompt="证据 A",
    )
    await service.reply(
        "客服营业时间是几点？",
        user_id=TEST_USER_ID,
        thread_id="thread-rag-scope",
    )

    assert chat_model.received_messages == [
        [("human", "证据 A")],
        [
            ("human", "证据 A"),
            ("ai", "收到。"),
            ("human", "客服营业时间是几点？"),
        ],
    ]


async def test_chat_service_calls_use_independent_graph_state() -> None:
    chat_model = FakeChatModel(response="收到。")
    service = ChatService(chat_model=chat_model, model_name="gpt-test-model")

    await service.reply("  第一条消息  ", user_id=TEST_USER_ID)
    await service.reply("  第二条消息  ", user_id=TEST_USER_ID)

    assert chat_model.received_messages == [
        [("human", "第一条消息")],
        [("human", "第二条消息")],
    ]


async def test_chat_service_keeps_no_thread_calls_stateless_with_checkpointer() -> None:
    chat_model = FakeChatModel(response="收到。")
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=InMemorySaver(),
    )

    await service.reply("第一条消息", user_id=TEST_USER_ID)
    await service.reply("第二条消息", user_id=TEST_USER_ID)

    assert chat_model.received_messages == [
        [("human", "第一条消息")],
        [("human", "第二条消息")],
    ]


async def test_chat_service_restores_state_for_the_same_thread() -> None:
    chat_model = FakeChatModel(response="收到。")
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    await service.reply(
        "第一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="request-1",
    )
    await service.reply(
        "第二条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
    )
    await service.reply(
        "另一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-2",
    )

    thread_one_config = checkpoint_config("thread-1")
    thread_two_config = checkpoint_config("thread-2")
    thread_one_checkpoint = await checkpointer.aget_tuple(thread_one_config)
    thread_two_checkpoint = await checkpointer.aget_tuple(thread_two_config)

    assert thread_one_checkpoint is not None
    assert checkpoint_messages(thread_one_checkpoint.checkpoint) == [
        ("human", "第一条消息"),
        ("ai", "收到。"),
        ("human", "第二条消息"),
        ("ai", "收到。"),
    ]
    assert thread_two_checkpoint is not None
    assert checkpoint_messages(thread_two_checkpoint.checkpoint) == [
        ("human", "另一条消息"),
        ("ai", "收到。"),
    ]


async def test_chat_service_isolates_same_thread_id_between_users() -> None:
    chat_model = FakeChatModel(response="收到。")
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    await service.reply(
        "用户 A 的第一条消息",
        user_id="user-a",
        thread_id="shared-thread",
    )
    await service.reply(
        "用户 B 的第一条消息",
        user_id="user-b",
        thread_id="shared-thread",
    )
    await service.reply(
        "用户 A 的第二条消息",
        user_id="user-a",
        thread_id="shared-thread",
    )

    assert chat_model.received_messages == [
        [("human", "用户 A 的第一条消息")],
        [("human", "用户 B 的第一条消息")],
        [
            ("human", "用户 A 的第一条消息"),
            ("ai", "收到。"),
            ("human", "用户 A 的第二条消息"),
        ],
    ]
    user_a_checkpoint = await checkpointer.aget_tuple(
        checkpoint_config("shared-thread", user_id="user-a")
    )
    user_b_checkpoint = await checkpointer.aget_tuple(
        checkpoint_config("shared-thread", user_id="user-b")
    )
    assert user_a_checkpoint is not None
    assert user_b_checkpoint is not None
    assert user_a_checkpoint.checkpoint["channel_values"]["user_id"] == "user-a"
    assert user_b_checkpoint.checkpoint["channel_values"]["user_id"] == "user-b"


async def test_chat_service_clears_request_id_when_next_call_omits_it() -> None:
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=FakeChatModel(response="收到。"),
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    await service.reply(
        "第一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="request-1",
    )
    await service.reply(
        "第二条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
    )

    config = checkpoint_config("thread-1")
    checkpoint = await checkpointer.aget_tuple(config)

    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["request_id"] is None


async def test_chat_service_reuses_completed_request_in_same_thread() -> None:
    chat_model = FakeChatModel(response="首次回复。")
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    first_result = await service.reply(
        "同一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="request-1",
    )
    retried_result = await service.reply(
        "同一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="request-1",
    )

    assert retried_result == first_result
    assert chat_model.received_messages == [
        [("human", "同一条消息")]
    ]

    config = checkpoint_config("thread-1")
    checkpoint = await checkpointer.aget_tuple(config)
    assert checkpoint is not None
    assert checkpoint_messages(checkpoint.checkpoint) == [
        ("human", "同一条消息"),
        ("ai", "首次回复。"),
    ]


async def test_chat_service_scopes_request_id_to_thread() -> None:
    chat_model = FakeChatModel(response="收到。")
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=InMemorySaver(),
    )

    await service.reply(
        "会话一",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="shared-request",
    )
    await service.reply(
        "会话二",
        user_id=TEST_USER_ID,
        thread_id="thread-2",
        request_id="shared-request",
    )

    assert chat_model.received_messages == [
        [("human", "会话一")],
        [("human", "会话二")],
    ]


async def test_chat_service_reuses_overlapping_duplicate_request() -> None:
    chat_model = BlockingFakeChatModel()
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    first_reply = asyncio.create_task(
        service.reply(
            "同一条消息",
            user_id=TEST_USER_ID,
            thread_id="thread-1",
            request_id="request-1",
        )
    )
    await asyncio.wait_for(chat_model.first_call_started.wait(), timeout=1)
    duplicate_reply = asyncio.create_task(
        service.reply(
            "同一条消息",
            user_id=TEST_USER_ID,
            thread_id="thread-1",
            request_id="request-1",
        )
    )
    await asyncio.sleep(0)

    assert len(chat_model.received_messages) == 1
    assert not duplicate_reply.done()

    chat_model.release_first_call.set()
    first_result, duplicate_result = await asyncio.wait_for(
        asyncio.gather(first_reply, duplicate_reply),
        timeout=1,
    )

    assert duplicate_result == first_result
    assert chat_model.received_messages == [
        [("human", "同一条消息")]
    ]
    assert chat_model.maximum_active_calls == 1

    config = checkpoint_config("thread-1")
    checkpoint = await checkpointer.aget_tuple(config)
    assert checkpoint is not None
    assert checkpoint_messages(checkpoint.checkpoint) == [
        ("human", "同一条消息"),
        ("ai", "收到。"),
    ]


async def test_idempotent_retry_after_empty_response_keeps_clean_history() -> None:
    chat_model = FakeChatModel(response="   ")
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    with pytest.raises(EmptyChatResponseError):
        await service.reply(
            "同一条消息",
            user_id=TEST_USER_ID,
            thread_id="thread-1",
            request_id="request-1",
        )

    chat_model.response = "恢复后的回复。"
    result = await service.reply(
        "同一条消息",
        user_id=TEST_USER_ID,
        thread_id="thread-1",
        request_id="request-1",
    )

    assert result.content == "恢复后的回复。"
    assert chat_model.received_messages == [
        [("human", "同一条消息")],
        [("human", "同一条消息")],
    ]

    config = checkpoint_config("thread-1")
    checkpoint = await checkpointer.aget_tuple(config)
    assert checkpoint is not None
    assert checkpoint_messages(checkpoint.checkpoint) == [
        ("human", "同一条消息"),
        ("ai", "恢复后的回复。"),
    ]


async def test_chat_service_serializes_overlapping_calls_for_same_thread() -> None:
    chat_model = BlockingFakeChatModel()
    checkpointer = InMemorySaver()
    service = ChatService(
        chat_model=chat_model,
        model_name="gpt-test-model",
        checkpointer=checkpointer,
    )

    first_reply = asyncio.create_task(
        service.reply(
            "第一条消息",
            user_id=TEST_USER_ID,
            thread_id="thread-1",
        )
    )
    await asyncio.wait_for(chat_model.first_call_started.wait(), timeout=1)

    second_reply = asyncio.create_task(
        service.reply(
            "第二条消息",
            user_id=TEST_USER_ID,
            thread_id="thread-1",
        )
    )
    await asyncio.sleep(0)

    assert chat_model.received_messages == [
        [("human", "第一条消息")]
    ]
    assert not second_reply.done()

    chat_model.release_first_call.set()
    await asyncio.wait_for(
        asyncio.gather(first_reply, second_reply),
        timeout=1,
    )

    config = checkpoint_config("thread-1")
    checkpoint = await checkpointer.aget_tuple(config)

    assert chat_model.received_messages == [
        [("human", "第一条消息")],
        [
            ("human", "第一条消息"),
            ("ai", "收到。"),
            ("human", "第二条消息"),
        ],
    ]
    assert chat_model.maximum_active_calls == 1
    assert checkpoint is not None
    assert checkpoint_messages(checkpoint.checkpoint) == [
        ("human", "第一条消息"),
        ("ai", "收到。"),
        ("human", "第二条消息"),
        ("ai", "收到。"),
    ]


@pytest.mark.parametrize("response", ["", "   ", "\n\t"])
async def test_chat_service_rejects_empty_model_reply(response: str) -> None:
    service = ChatService(
        chat_model=FakeChatModel(response=response),
        model_name="gpt-test-model",
    )

    with pytest.raises(EmptyChatResponseError, match="empty response"):
        await service.reply("你好", user_id=TEST_USER_ID)


async def test_chat_service_preserves_model_errors() -> None:
    expected_error = RuntimeError("fake provider failure")
    service = ChatService(
        chat_model=FakeChatModel(error=expected_error),
        model_name="gpt-test-model",
    )

    with pytest.raises(RuntimeError) as caught_error:
        await service.reply("你好", user_id=TEST_USER_ID)

    assert caught_error.value is expected_error
