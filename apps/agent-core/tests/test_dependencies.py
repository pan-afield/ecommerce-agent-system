from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request

from app.api.dependencies import get_chat_service
from app.services.chat import ChatNotConfiguredError, ChatService


def make_request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def test_get_chat_service_returns_application_service() -> None:
    app = FastAPI()
    service = MagicMock(spec=ChatService)
    app.state.chat_service = service

    result = get_chat_service(make_request(app))

    assert result is service


def test_get_chat_service_rejects_missing_configuration() -> None:
    app = FastAPI()
    app.state.chat_service = None

    with pytest.raises(ChatNotConfiguredError):
        get_chat_service(make_request(app))
