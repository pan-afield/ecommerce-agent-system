from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.services.refund_sandbox import RefundSandboxResult
from app.services.refund_webhook import RefundWebhookEvent

PAYLOAD = (
    b'{"event_id":"evt-001","status":"SUCCEEDED","provider_reference":"provider-1",'
    b'"idempotency_key":"idem-1"}'
)
SECRET = "sandbox-webhook-secret"
SIGNATURE = "3f6e5bbce685d54567c23999b3c59da1e4f52a720465f61bb543cf9584d9c34a"


@pytest.mark.asyncio
async def test_refund_webhook_returns_503_when_secret_is_not_configured(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    response = await client.post(
        "/v1/refund-webhooks/sandbox",
        content=PAYLOAD,
        headers={"X-Refund-Signature": SIGNATURE},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "未配置密钥"}


@pytest.mark.asyncio
async def test_refund_webhook_returns_401_for_invalid_signature(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr(SECRET)

    response = await client.post(
        "/v1/refund-webhooks/sandbox",
        content=PAYLOAD,
        headers={"X-Refund-Signature": "bad-signature"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "退款 webhook 签名无效。"}


@pytest.mark.asyncio
async def test_refund_webhook_returns_204_for_valid_signature(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")

    with (
        patch(
            "app.api.routes.refund_webhooks.verify_refund_webhook_signature",
            return_value=True,
        ) as verify_signature,
        patch(
            "app.api.routes.refund_webhooks.apply_refund_webhook_result",
            return_value=object(),
        ) as apply_result,
    ):
        response = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )

    assert response.status_code == 204
    assert response.content == b""
    verify_signature.assert_called_once_with(
        PAYLOAD,
        SIGNATURE,
        "sandbox-secret",
        timestamp="",
    )
    apply_result.assert_awaited_once_with(
        app.state.database_engine,
        event=RefundWebhookEvent(
            event_id="evt-001",
            result=RefundSandboxResult(
                status="SUCCEEDED", provider_reference="provider-1", idempotency_key="idem-1"
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"status":"UNKNOWN","idempotency_key":"idem-1"}',
        b'{"status":"SUCCEEDED","idempotency_key":""}',
    ],
)
async def test_refund_webhook_returns_400_for_invalid_payload(
    client: AsyncClient,
    app: FastAPI,
    payload: bytes,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")

    with patch(
        "app.api.routes.refund_webhooks.verify_refund_webhook_signature",
        return_value=True,
    ):
        response = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=payload,
            headers={"X-Refund-Signature": SIGNATURE},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "退款 webhook 数据无效。"}


@pytest.mark.asyncio
async def test_refund_webhook_checks_signature_before_parsing_payload(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")

    response = await client.post(
        "/v1/refund-webhooks/sandbox",
        content=b"not-json",
        headers={"X-Refund-Signature": "bad-signature"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "退款 webhook 签名无效。"}


@pytest.mark.asyncio
async def test_refund_webhook_returns_503_when_execution_record_is_unknown(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")

    with (
        patch(
            "app.api.routes.refund_webhooks.verify_refund_webhook_signature",
            return_value=True,
        ),
        patch(
            "app.api.routes.refund_webhooks.apply_refund_webhook_result",
            return_value=None,
        ),
    ):
        response = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_refund_webhook_sanitizes_database_failure(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")
    failure = SQLAlchemyError("sensitive webhook database detail")

    with (
        patch(
            "app.api.routes.refund_webhooks.verify_refund_webhook_signature",
            return_value=True,
        ),
        patch(
            "app.api.routes.refund_webhooks.apply_refund_webhook_result",
            side_effect=failure,
        ),
    ):
        response = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "退款服务暂时不可用，请稍后重试。"}
    assert "sensitive webhook database detail" not in response.text


@pytest.mark.asyncio
async def test_refund_webhook_duplicate_delivery_is_acknowledged_twice(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")

    with (
        patch(
            "app.api.routes.refund_webhooks.verify_refund_webhook_signature",
            return_value=True,
        ),
        patch(
            "app.api.routes.refund_webhooks.apply_refund_webhook_result",
            side_effect=[object(), object()],
        ) as apply_result,
    ):
        first = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )
        retry = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )

    assert first.status_code == 204
    assert retry.status_code == 204
    assert apply_result.await_count == 2


async def test_refund_webhook_event_conflict_returns_sanitized_400(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    app.state.settings.refund_sandbox_webhook_secret = SecretStr("sandbox-secret")
    with (
        patch("app.api.routes.refund_webhooks.verify_refund_webhook_signature", return_value=True),
        patch(
            "app.api.routes.refund_webhooks.apply_refund_webhook_result",
            side_effect=ValueError("sensitive event mismatch detail"),
        ),
    ):
        response = await client.post(
            "/v1/refund-webhooks/sandbox",
            content=PAYLOAD,
            headers={"X-Refund-Signature": SIGNATURE},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "退款 webhook 数据无效。"}
    assert "sensitive" not in response.text
