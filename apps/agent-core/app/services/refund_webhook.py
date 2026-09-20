import hashlib
import hmac
import time
from dataclasses import dataclass

from app.services.refund_sandbox import (
    RefundSandboxResult,
    parse_refund_sandbox_result,
)


def verify_refund_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
    *,
    timestamp: str | None = None,
) -> bool:
    """验证退款沙箱使用 HMAC-SHA256 生成的 webhook 签名。

    ``payload`` 必须是收到请求时的原始字节；先解析 JSON 再重新序列化，
    可能改变空白或字段顺序，从而得到不同的签名。
    HTTP 入口必须传入时间戳，签名内容为 timestamp + '.' + 原始正文，
    拒绝超过五分钟的旧签名；同一事件重投时生成新签名但保留 event_id。
    """
    if not secret or not signature:
        return False

    if timestamp is not None:
        if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
            return False
        if abs(time.time() - int(timestamp)) > 300:
            return False
        payload = timestamp.encode("ascii") + b"." + payload
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature.encode("ascii"), signature.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class RefundWebhookEvent:
    event_id: str
    result: RefundSandboxResult


def parse_refund_webhook_event(payload: object) -> RefundWebhookEvent:
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid refund webhook payload")

    raw_event_id = payload.get("event_id")
    if not isinstance(raw_event_id, str) or not raw_event_id.strip() or len(raw_event_id) > 128:
        raise RuntimeError("Invalid refund webhook payload")

    try:
        result = parse_refund_sandbox_result(payload)
    except RuntimeError as error:
        raise RuntimeError("Invalid refund webhook payload") from error

    return RefundWebhookEvent(
        event_id=raw_event_id.strip(),
        result=result,
    )
