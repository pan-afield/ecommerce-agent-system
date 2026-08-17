import pytest
from pydantic import ValidationError

from app.schemas.chat import AssistantMessage, ChatRequest, ChatResponse


def test_chat_request_strips_surrounding_whitespace() -> None:
    request = ChatRequest(message="  请问多久可以发货？  ")

    assert request.message == "请问多久可以发货？"
    assert request.thread_id is None
    assert request.request_id is None


def test_chat_request_strips_thread_id_whitespace() -> None:
    request = ChatRequest(
        message="请问多久可以发货？",
        thread_id="  thread-1  ",
    )

    assert request.thread_id == "thread-1"


def test_chat_request_strips_request_id_whitespace() -> None:
    request = ChatRequest(
        message="请问多久可以发货？",
        thread_id="thread-1",
        request_id="  request-1  ",
    )

    assert request.request_id == "request-1"


def test_chat_request_requires_thread_id_for_request_id() -> None:
    with pytest.raises(ValidationError, match="thread_id is required"):
        ChatRequest(
            message="请问多久可以发货？",
            request_id="request-1",
        )


@pytest.mark.parametrize("message", ["", "   ", "\n\t"])
def test_chat_request_rejects_blank_message(message: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message=message)


def test_chat_request_requires_message() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({})


def test_chat_request_accepts_maximum_length() -> None:
    request = ChatRequest(message="a" * 2_000)

    assert len(request.message) == 2_000


def test_chat_request_rejects_message_over_maximum_length() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="a" * 2_001)


@pytest.mark.parametrize("thread_id", ["", "   ", "\n\t", "a" * 129])
def test_chat_request_rejects_invalid_thread_id(thread_id: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="你好", thread_id=thread_id)


@pytest.mark.parametrize("request_id", ["", "   ", "\n\t", "a" * 129])
def test_chat_request_rejects_invalid_request_id(request_id: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="你好", request_id=request_id)


def test_chat_response_serializes_assistant_content_and_model() -> None:
    response = ChatResponse(
        assistant=AssistantMessage(content="商品通常会尽快安排发货。"),
        model="gpt-test-model",
    )

    assert response.model_dump() == {
        "assistant": {"content": "商品通常会尽快安排发货。"},
        "model": "gpt-test-model",
    }
