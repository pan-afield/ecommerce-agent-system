from typing import Annotated, cast

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.services.chat import ChatNotConfiguredError, ChatService


# 从应用状态中获取聊天服务；未配置时阻止请求继续执行。
def get_chat_service(request: Request) -> ChatService:
    service = cast(
        ChatService | None,
        request.app.state.chat_service,
    )

    if service is None:
        raise ChatNotConfiguredError("Chat service is not configured.")

    return service


# 允许缺少认证头，便于依赖函数返回统一的未登录错误。
bearer_scheme = HTTPBearer(auto_error=False)


# 校验 Bearer JWT，并提取令牌中的当前用户 ID。
def get_current_user_id(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> str:
    settings = cast(Settings, request.app.state.settings)

    # 没有凭证时直接返回未登录错误，不进入令牌解析流程。
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

    # 只接受带有 sub 和 exp 声明、且使用 HS256 验证通过的令牌。
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
    # sub 必须是非空字符串，作为后续业务使用的用户身份。
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌无效或已过期。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


# 在当前登录用户基础上，进一步校验其是否为配置的退款审批人。
def get_current_refund_approver_id(
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> str:
    settings = cast(Settings, request.app.state.settings)

    # 未配置审批人时，说明审批服务尚未具备运行条件。
    if settings.refund_approver_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款审批服务尚未配置。",
        )

    # 已登录不等于有审批权限，必须与配置的审批人 ID 完全匹配。
    if current_user_id != settings.refund_approver_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权审批退款申请。",
        )

    return current_user_id
