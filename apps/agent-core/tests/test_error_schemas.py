import pytest
from pydantic import ValidationError

from app.schemas.error import ErrorDetail, ErrorResponse


def test_error_response_serializes_stable_shape() -> None:
    response = ErrorResponse(
        error=ErrorDetail(
            code="chat_timeout",
            message="客服服务响应超时，请稍后重试。",
        )
    )

    assert response.model_dump() == {
        "error": {
            "code": "chat_timeout",
            "message": "客服服务响应超时，请稍后重试。",
        }
    }


def test_error_detail_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError):
        ErrorDetail.model_validate(
            {
                "code": "unknown_error",
                "message": "Unexpected error.",
            }
        )
