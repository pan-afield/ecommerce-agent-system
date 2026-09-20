"""本地可持久化的 HTTP 支付沙箱；需用户单独启动，不调用真实支付。"""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.core.database import create_database_engine


class SandboxRequest(BaseModel):
    refund_application_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    idempotency_key: str = Field(min_length=1, max_length=128)


class Settlement(BaseModel):
    status: Literal["SUCCEEDED", "FAILED"]


def create_sandbox_app(settings: Settings | None = None) -> FastAPI:
    """创建独立的沙箱应用，资源在该进程自己的 lifespan 内创建和关闭。"""
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.environment == "production":
            raise RuntimeError("Local payment sandbox is disabled in production")
        engine = create_database_engine(config.database_url)
        app.state.database_engine = engine
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Local Refund HTTP Sandbox", lifespan=lifespan)

    async def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        """每个沙箱操作都需服务凭证，缺配置时关闭入口而非匿名放行。"""
        if config.refund_sandbox_api_key is None:
            raise HTTPException(503, "沙箱访问密钥尚未配置。")
        expected = "Bearer " + config.refund_sandbox_api_key.get_secret_value()
        if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
            raise HTTPException(401, "沙箱凭证无效。")

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def database_error(request: Request, error: Exception) -> JSONResponse:
        """不将连接串、SQL 或模拟支付内容暴露到 HTTP 响应。"""
        return JSONResponse(status_code=503, content={"detail": "沙箱数据库暂时不可用。"})

    @app.post("/refunds", dependencies=[Depends(authorize)])
    async def execute(
        payload: SandboxRequest,
        request: Request,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """唯一键保证跨进程幂等，同键但金额/身份等内容不同返回 409。"""
        if idempotency_key != payload.idempotency_key:
            raise HTTPException(400, "幂等请求头与正文不一致。")
        normalized = payload.model_dump(mode="json")
        normalized["amount"] = format(payload.amount, ".2f")
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        async with request.app.state.database_engine.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO sandbox_refunds (idempotency_key,payload_hash)
                VALUES (:key,:hash) ON CONFLICT (idempotency_key) DO NOTHING
            """),
                {"key": payload.idempotency_key, "hash": digest},
            )
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT * FROM sandbox_refunds WHERE idempotency_key=:key
            """),
                        {"key": payload.idempotency_key},
                    )
                )
                .mappings()
                .one()
            )
            if row["payload_hash"] != digest:
                raise HTTPException(409, "同一幂等键的退款内容不能变更。")
            return {key: row[key] for key in ("idempotency_key", "status", "provider_reference")}

    @app.get("/refunds/{key:path}", dependencies=[Depends(authorize)])
    async def get_result(key: str, request: Request) -> dict[str, Any]:
        """读取沙箱已持久化结果，重启应用不会丢失此前退款。"""
        async with request.app.state.database_engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT idempotency_key,status,provider_reference FROM sandbox_refunds
                WHERE idempotency_key=:key
            """),
                        {"key": key},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "沙箱退款不存在。")
        return dict(row)

    @app.post("/refunds/{key:path}/settle", dependencies=[Depends(authorize)])
    async def settle(key: str, payload: Settlement, request: Request) -> dict[str, Any]:
        """模拟提供方异步结算，状态与稳定 event_id 原子保存，不自动发 webhook。"""
        async with request.app.state.database_engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT status FROM sandbox_refunds WHERE idempotency_key=:key FOR UPDATE
            """),
                        {"key": key},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(404, "沙箱退款不存在。")
            if row["status"] != "PROCESSING" and row["status"] != payload.status:
                raise HTTPException(409, "沙箱终态不能被覆盖。")
            if row["status"] == "PROCESSING":
                reference = f"sandbox-{uuid4()}" if payload.status == "SUCCEEDED" else None
                await connection.execute(
                    text("""
                    UPDATE sandbox_refunds SET status=:status,provider_reference=:reference
                    WHERE idempotency_key=:key
                """),
                    {"key": key, "status": payload.status, "reference": reference},
                )
                await connection.execute(
                    text("""
                    INSERT INTO sandbox_refund_events
                      (event_id,idempotency_key,status,provider_reference)
                    VALUES (:id,:key,:status,:reference)
                """),
                    {
                        "id": str(uuid4()),
                        "key": key,
                        "status": payload.status,
                        "reference": reference,
                    },
                )
            event = (
                (
                    await connection.execute(
                        text("""
                SELECT event_id,idempotency_key,status,provider_reference FROM sandbox_refund_events
                WHERE idempotency_key=:key
            """),
                        {"key": key},
                    )
                )
                .mappings()
                .one()
            )
            return dict(event)

    @app.post("/events/{event_id}/deliver", dependencies=[Depends(authorize)])
    async def deliver(event_id: str, request: Request) -> dict[str, str]:
        """手动投递或重投同一事件，每次用新时间戳签名；目标仅来自服务端配置。"""
        if (
            config.refund_sandbox_callback_url is None
            or config.refund_sandbox_webhook_secret is None
        ):
            raise HTTPException(503, "沙箱回调尚未配置。")
        async with request.app.state.database_engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT event_id,idempotency_key,status,provider_reference FROM sandbox_refund_events
                WHERE event_id=:id
            """),
                        {"id": event_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "沙箱事件不存在。")
        body = json.dumps(dict(row), separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            config.refund_sandbox_webhook_secret.get_secret_value().encode(),
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        try:
            async with httpx.AsyncClient(
                timeout=config.refund_sandbox_request_timeout_seconds
            ) as client:
                response = await client.post(
                    str(config.refund_sandbox_callback_url),
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Refund-Timestamp": timestamp,
                        "X-Refund-Signature": signature,
                    },
                )
                if response.status_code != 204:
                    raise HTTPException(502, "回调未确认，可重投同一事件。")
        except httpx.HTTPError as error:
            raise HTTPException(502, "回调暂时失败，可重投同一事件。") from error
        return {"event_id": event_id, "delivery": "ACKNOWLEDGED"}

    return app


app = create_sandbox_app()
