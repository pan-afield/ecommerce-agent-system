from typing import Annotated, cast

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.services.chat import ChatNotConfiguredError, ChatService


def get_chat_service(request: Request) -> ChatService:
    service = cast(
        ChatService | None,
        request.app.state.chat_service,
    )

    if service is None:
        raise ChatNotConfiguredError("Chat service is not configured.")

    return service


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_id(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    settings = cast(Settings, request.app.state.settings)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.jwt_secret_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务尚未配置。",
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
    except InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌无效或已过期。",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌无效或已过期。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


def get_current_refund_approver_id(
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> str:
    settings = cast(Settings, request.app.state.settings)

    if settings.refund_approver_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款审批服务尚未配置。",
        )

    if current_user_id != settings.refund_approver_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权审批退款申请。",
        )

    return current_user_id
