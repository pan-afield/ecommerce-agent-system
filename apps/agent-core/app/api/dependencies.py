from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

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
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iss", "token_type"]},
        )

        if payload.get("token_type") != "access":
            raise InvalidTokenError("token is not an access token")
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


# 在当前登录用户基础上，读取数据库角色并校验其是否为 ADMIN。
async def get_current_refund_approver_id(
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> str:
    engine = cast(
        AsyncEngine,
        request.app.state.database_engine,
    )

    try:
        async with engine.connect() as connection:
            statement = text("""SELECT role
                        FROM users
                        WHERE id = :user_id
                        """)
            result = await connection.execute(statement, {"user_id": current_user_id})
            user = result.fetchone()
            if user is None or user.role != "ADMIN":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权审批退款申请。",
                )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error

    return current_user_id


def create_access_token(
    user_id: str,
    settings: Settings,
    ttl_seconds: int = 900,
) -> str:
    """生成带有用户 ID 的 JWT 访问令牌，默认有效期为 15 分钟。"""
    if settings.jwt_secret_key is None:
        raise ValueError("JWT secret key is not configured.")

    payload = {
        "sub": user_id,
        "token_type": "access",
        "iss": settings.jwt_issuer,
        "exp": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )

    return token


async def get_current_user_role(
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> str:
    engine = cast(
        AsyncEngine,
        request.app.state.database_engine,
    )

    try:
        async with engine.connect() as connection:
            statement = text("""SELECT role
                        FROM users
                        WHERE id = :user_id
                        """)
            result = await connection.execute(statement, {"user_id": current_user_id})
            user = result.fetchone()
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="用户不存在。",
                )
            return cast(str, user.role)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error
