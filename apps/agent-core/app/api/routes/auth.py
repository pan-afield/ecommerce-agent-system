from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import create_access_token, get_current_user_id
from app.core.password import verify_password
from app.core.refresh_token import generate_refresh_token, hash_refresh_token
from app.schemas.auth import AuthRequest, AuthResponse, CurrentUserResponse, RefreshRequest

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/login", response_model=AuthResponse)
async def login(payload: AuthRequest, request: Request) -> AuthResponse:
    """
    用户登录接口。

    该接口接受一个包含用户名和密码的 JSON 请求体，并返回一个包含 JWT 令牌的响应。
    """
    engine: AsyncEngine = request.app.state.database_engine

    statement = text("""
                        SELECT id, password_hash FROM users WHERE email = :email
                    """)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(statement, {"email": payload.email})
            user = result.fetchone()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="用户名或密码错误。",
        )

    if user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="用户名或密码错误。",
        )

    try:
        password_valid = verify_password(payload.password, user.password_hash)
    except VerificationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error

    if not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
            detail="用户名或密码错误。",
        )
    else:
        # 生成 JWT 令牌
        settings = request.app.state.settings

        try:
            token = create_access_token(
                user_id=user.id,
                settings=settings,
                ttl_seconds=settings.jwt_access_token_ttl_seconds,
            )
            (raw_token, token_hash) = generate_refresh_token()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.jwt_refresh_token_ttl_seconds
            )
            refresh_token_id = uuid4().hex

            # 将 refresh token 存储到数据库中
            insert_statement = text(
                """
                INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
                VALUES (:id, :user_id, :token_hash, :expires_at)
                """
            )
            async with engine.begin() as connection:
                await connection.execute(
                    insert_statement,
                    {
                        "id": refresh_token_id,
                        "user_id": user.id,
                        "token_hash": token_hash,
                        "expires_at": expires_at,
                    },
                )
            refresh_token = raw_token
        except (SQLAlchemyError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="认证服务暂时不可用，请稍后重试。",
            ) from error

        return AuthResponse(access_token=token, refresh_token=refresh_token, token_type="bearer")


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(payload: RefreshRequest, request: Request) -> AuthResponse:
    """
    刷新 JWT 令牌接口。

    该接口接受一个包含 refresh token 的 JSON 请求体，并返回一个新的 JWT 令牌。
    """
    engine: AsyncEngine = request.app.state.database_engine
    settings = request.app.state.settings

    refresh_token_hash = hash_refresh_token(payload.refresh_token)

    try:
        async with engine.begin() as connection:
            statement = text("""
                SELECT id, user_id, expires_at, revoked_at
                FROM refresh_tokens
                WHERE token_hash = :token_hash
                FOR UPDATE
            """)
            result = await connection.execute(statement, {"token_hash": refresh_token_hash})
            row = result.fetchone()

            if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效的 refresh token。",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # 将旧的 refresh token 标记为已撤销，并将新的 refresh token 存储到数据库中
            await connection.execute(
                text("""
                    UPDATE refresh_tokens
                    SET revoked_at = :revoked_at
                    WHERE id = :id AND revoked_at IS NULL
                """),
                {"id": row.id, "revoked_at": datetime.now(UTC)},
            )

            # 生成新的 access token 和 refresh token
            new_access_token = create_access_token(
                user_id=row.user_id,
                settings=settings,
                ttl_seconds=settings.jwt_access_token_ttl_seconds,
            )
            (raw_token, token_hash) = generate_refresh_token()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.jwt_refresh_token_ttl_seconds
            )
            refresh_token_id = uuid4().hex
            await connection.execute(
                text("""
                    INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
                    VALUES (:id, :user_id, :token_hash, :expires_at)
                """),
                {
                    "id": refresh_token_id,
                    "user_id": row.user_id,
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                },
            )

            new_refresh_token = raw_token
    except (SQLAlchemyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error

    return AuthResponse(
        access_token=new_access_token, refresh_token=new_refresh_token, token_type="bearer"
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, request: Request) -> None:
    """
    用户登出接口。

    该接口接受一个包含 refresh token 的 JSON 请求体，并将其标记为已撤销。
    """
    engine: AsyncEngine = request.app.state.database_engine
    refresh_token_hash = hash_refresh_token(payload.refresh_token)

    try:
        async with engine.begin() as connection:
            statement = text("""
                UPDATE refresh_tokens
                SET revoked_at = :revoked_at
                WHERE token_hash = :token_hash
                  AND revoked_at IS NULL
            """)
            await connection.execute(
                statement,
                {"token_hash": refresh_token_hash, "revoked_at": datetime.now(UTC)},
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error


@router.get("/me", response_model=CurrentUserResponse)
async def get_current_user(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
) -> CurrentUserResponse:
    """
    获取当前登录用户信息接口。

    该接口返回当前登录用户的基本信息。
    """
    engine: AsyncEngine = request.app.state.database_engine
    try:
        async with engine.connect() as connection:
            statement = text("""
                SELECT id, email, role FROM users WHERE id = :user_id
            """)
            result = await connection.execute(statement, {"user_id": current_user_id})
            user = result.fetchone()
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="用户不存在。",
                )
    except VerificationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试。",
        ) from error
    return CurrentUserResponse(id=current_user_id, email=user.email, role=user.role)
