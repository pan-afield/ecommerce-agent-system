"""HTTP 边界验证：真实签名/鉴权，数据库与支付全部替换为测试替身。"""

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.dependencies import get_current_refund_approver_id
from app.api.routes import refund_operations, refund_webhooks
from app.core.config import Settings
from app.refund_sandbox_app import create_sandbox_app
from app.services.refund_webhook import verify_refund_webhook_signature
from tests.conftest import FakeRoleEngine
from tests.test_refunds_route import make_auth_headers

SECRET = "fixture-webhook-secret"
BODY = b'{"event_id":"evt-1","idempotency_key":"refund:r-1","status":"FAILED"}'


def signed_headers(body: bytes, timestamp: str | None = None) -> dict[str, str]:
    value = timestamp or str(int(time.time()))
    signature = hmac.new(SECRET.encode(), value.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Refund-Timestamp": value, "X-Refund-Signature": signature}


@pytest.fixture
def payment_app() -> FastAPI:
    app = FastAPI()
    app.include_router(refund_operations.router)
    app.include_router(refund_webhooks.router)
    app.state.settings = Settings(
        _env_file=None,
        environment="test",
        openai_api_key=None,
        jwt_secret_key="test-only-jwt-secret-at-least-32-bytes",
        refund_sandbox_webhook_secret=SECRET,
    )
    app.state.database_engine = FakeRoleEngine({"owner": "CUSTOMER", "admin": "ADMIN"})
    app.state.refund_sandbox_adapter = AsyncMock()
    return app


@pytest.mark.parametrize("offset", [-301, 301, -300, 0, 300])
def test_signature_timestamp_window(offset: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.refund_webhook.time.time", lambda: 1000.0)
    headers = signed_headers(BODY, str(1000 + offset))
    assert verify_refund_webhook_signature(
        BODY, headers["X-Refund-Signature"], SECRET, timestamp=headers["X-Refund-Timestamp"]
    ) is (abs(offset) <= 300)


@pytest.mark.parametrize("value", ["", "abc", "-1", "１２３", "9" * 13])
def test_signature_rejects_invalid_timestamp(value: str) -> None:
    headers = signed_headers(BODY, value)
    assert not verify_refund_webhook_signature(
        BODY, headers["X-Refund-Signature"], SECRET, timestamp=value
    )


def test_signature_rejects_tampering_and_non_ascii() -> None:
    headers = signed_headers(BODY)
    for body, signature in [(BODY + b" ", headers["X-Refund-Signature"]), (BODY, "无效")]:
        assert not verify_refund_webhook_signature(
            body, signature, SECRET, timestamp=headers["X-Refund-Timestamp"]
        )


@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", 204),
        ("missing", 401),
        ("stale", 401),
        ("large", 413),
        ("utf8", 400),
        ("long_id", 400),
    ],
)
async def test_webhook_boundary(
    payment_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: int,
) -> None:
    apply = AsyncMock(return_value=object())
    monkeypatch.setattr(refund_webhooks, "apply_refund_webhook_result", apply)
    body = BODY
    if case == "large":
        body = b"x" * 65_537
    elif case == "utf8":
        body = b"\xff"
    elif case == "long_id":
        body = BODY.replace(b"evt-1", b"x" * 129)
    headers = signed_headers(body, str(int(time.time()) - 301) if case == "stale" else None)
    if case == "missing":
        headers.pop("X-Refund-Timestamp")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/refund-webhooks/sandbox", content=body, headers=headers)
    assert response.status_code == expected
    if expected == 204:
        apply.assert_awaited_once()
    else:
        apply.assert_not_awaited()


@pytest.mark.parametrize(
    "path,method",
    [
        ("", "GET"),
        ("/r-1", "GET"),
        ("/r-1/resume", "POST"),
        ("/r-1/resubmit", "POST"),
        ("/r-1/acknowledge-conflict", "POST"),
    ],
)
@pytest.mark.parametrize("user,expected", [(None, 401), ("owner", 403), ("missing", 403)])
async def test_operations_require_current_database_admin(
    payment_app: FastAPI,
    path: str,
    method: str,
    user: str | None,
    expected: int,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://test"
    ) as client:
        response = await client.request(
            method,
            "/v1/refund-operations" + path,
            headers=make_auth_headers(user) if user else {},
            json={"note": "核实"},
        )
    assert response.status_code == expected


@pytest.mark.parametrize(
    "operation,function",
    [
        ("resume", "resume_refund_recovery"),
        ("acknowledge-conflict", "acknowledge_refund_conflict"),
        ("resubmit", "resubmit_missing_refund"),
    ],
)
@pytest.mark.parametrize(
    "outcome,expected", [(True, 204), (False, 409), (OSError("secret-db-url"), 503)]
)
async def test_admin_action_uses_actor_and_sanitizes_errors(
    payment_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    function: str,
    outcome: bool | OSError,
    expected: int,
) -> None:
    action = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, OSError)
        else AsyncMock(return_value=outcome)
    )
    monkeypatch.setattr(refund_operations, function, action)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/refund-operations/r-1/{operation}",
            headers=make_auth_headers("admin"),
            json={"note": "  已核实渠道  "},
        )
    assert response.status_code == expected
    assert "secret" not in response.text
    assert action.await_args is not None
    assert action.await_args.kwargs["actor_user_id"] == "admin"
    assert action.await_args.kwargs["note"] == "已核实渠道"


@pytest.mark.parametrize("note", [" ", "x" * 501])
async def test_admin_note_is_required(payment_app: FastAPI, note: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/refund-operations/r-1/resume",
            headers=make_auth_headers("admin"),
            json={"note": note},
        )
    assert response.status_code == 422


async def test_admin_read_errors_are_sanitized(payment_app: FastAPI) -> None:
    payment_app.dependency_overrides[get_current_refund_approver_id] = lambda: "admin"
    payment_app.state.database_engine = Mock(connect=Mock(side_effect=OSError("secret-db-url")))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://test"
    ) as client:
        for path in ["", "/r-1"]:
            response = await client.get("/v1/refund-operations" + path)
            assert response.status_code == 503
            assert "secret" not in response.text


@pytest.mark.parametrize(
    "token,configured,status", [(None, True, 401), ("wrong", True, 401), (None, False, 503)]
)
async def test_sandbox_auth_is_closed_by_default(
    token: str | None, configured: bool, status: int
) -> None:
    app = create_sandbox_app(
        Settings(
            _env_file=None, refund_sandbox_api_key="fixture-sandbox-token" if configured else None
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/refunds/any", headers={"Authorization": "Bearer " + token} if token else {}
        )
    assert response.status_code == status


def test_sandbox_openapi_uses_bearer_security_scheme() -> None:
    app = create_sandbox_app(
        Settings(_env_file=None, refund_sandbox_api_key="fixture-sandbox-token")
    )

    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["RefundSandboxBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    operation = schema["paths"]["/refunds/{key}"]["get"]
    assert operation["security"] == [{"RefundSandboxBearer": []}]
    assert "authorization" not in {
        parameter["name"].lower() for parameter in operation.get("parameters", [])
    }


async def test_sandbox_refuses_production_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_engine = Mock(side_effect=AssertionError("must not create resources"))
    monkeypatch.setattr("app.refund_sandbox_app.create_database_engine", create_engine)
    app = create_sandbox_app(Settings(_env_file=None, environment="production"))
    with pytest.raises(RuntimeError, match="disabled"):
        async with app.router.lifespan_context(app):
            pytest.fail("Production sandbox started")
    create_engine.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        {"refund_sandbox_api_key": "short"},
        {"refund_sandbox_callback_url": "file:///tmp/callback"},
        {"refund_recovery_max_attempts": 0},
        {"refund_recovery_max_attempts": 21},
        {"refund_recovery_lease_seconds": 9},
        {"refund_recovery_lease_seconds": 3601},
        {"refund_recovery_retry_seconds": 0},
        {"refund_recovery_retry_seconds": 3601},
    ],
)
def test_invalid_recovery_configuration(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]
