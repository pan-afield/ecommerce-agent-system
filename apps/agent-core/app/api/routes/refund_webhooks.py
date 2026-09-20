import json

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.services.refund import apply_refund_webhook_result
from app.services.refund_webhook import (
    parse_refund_webhook_event,
    verify_refund_webhook_signature,
)

router = APIRouter(
    prefix="/v1/refund-webhooks",
    tags=["refund-webhooks"],
)


@router.post("/sandbox", status_code=204)
async def sandbox(request: Request) -> Response:
    """先验证签名和正文，再原子处理事件；服务事务提交成功后才返回 204。"""
    settings = request.app.state.settings
    secret = settings.refund_sandbox_webhook_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置密钥",
        )

    payload = b""
    async for part in request.stream():
        payload += part
        if len(payload) > 65_536:
            raise HTTPException(413, "退款 webhook 正文过大。")
    signature = request.headers.get("X-Refund-Signature", "")
    timestamp = request.headers.get("X-Refund-Timestamp", "")
    if not verify_refund_webhook_signature(
        payload,
        signature,
        secret.get_secret_value(),
        timestamp=timestamp,
    ):
        raise HTTPException(
            status_code=401,
            detail="退款 webhook 签名无效。",
        )

    try:
        payload_json = json.loads(payload)
        webhook_event = parse_refund_webhook_event(payload_json)
    except (ValueError, UnicodeDecodeError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="退款 webhook 数据无效。",
        ) from error

    try:
        engine = request.app.state.database_engine

        execution_record = await apply_refund_webhook_result(engine, event=webhook_event)

        if execution_record is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="退款服务暂时不可用，请稍后重试。",
            )
    except ValueError as error:
        # 事件 ID 被复用但内容改变，属于无效投递；不暴露原事件信息。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="退款 webhook 数据无效。",
        ) from error
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款服务暂时不可用，请稍后重试。",
        ) from error

    return Response(status_code=204)
