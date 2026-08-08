import pytest

from app.services.chat import ChatResult, ChatService, EmptyChatResponseError


class FakeChatModel:
    def __init__(
        self,
        response: str = "",
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.received_messages: list[str] = []

    async def generate_reply(self, message: str) -> str:
        self.received_messages.append(message)

        if self.error is not None:
            raise self.error

        return self.response


async def test_chat_service_returns_trimmed_reply_and_configured_model() -> None:
    chat_model = FakeChatModel(response="  您的商品正在准备发货。  ")
    service = ChatService(chat_model=chat_model, model_name="gpt-test-model")

    result = await service.reply("请问什么时候发货？")

    assert result == ChatResult(
        content="您的商品正在准备发货。",
        model="gpt-test-model",
    )
    assert chat_model.received_messages == ["请问什么时候发货？"]


@pytest.mark.parametrize("response", ["", "   ", "\n\t"])
async def test_chat_service_rejects_empty_model_reply(response: str) -> None:
    service = ChatService(
        chat_model=FakeChatModel(response=response),
        model_name="gpt-test-model",
    )

    with pytest.raises(EmptyChatResponseError, match="empty response"):
        await service.reply("你好")


async def test_chat_service_preserves_model_errors() -> None:
    expected_error = RuntimeError("fake provider failure")
    service = ChatService(
        chat_model=FakeChatModel(error=expected_error),
        model_name="gpt-test-model",
    )

    with pytest.raises(RuntimeError) as caught_error:
        await service.reply("你好")

    assert caught_error.value is expected_error
