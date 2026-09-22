from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.services.refund_sandbox import (
    HttpRefundSandbox,
    InMemoryRefundSandbox,
    RefundSandboxRequest,
    RefundSandboxResult,
    parse_refund_sandbox_result,
)


def make_request(idempotency_key: str) -> RefundSandboxRequest:
    return RefundSandboxRequest(
        refund_application_id=str(uuid4()),
        user_id=str(uuid4()),
        order_id=str(uuid4()),
        amount=Decimal("29.90"),
        currency="CNY",
        idempotency_key=idempotency_key,
    )


def make_http_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://sandbox.example.test",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("status", "provider_reference"),
    [("SUCCEEDED", "provider-001"), ("PROCESSING", None), ("FAILED", None)],
)
def test_parse_refund_sandbox_result_accepts_valid_payload(
    status: str,
    provider_reference: str | None,
) -> None:
    result = parse_refund_sandbox_result(
        {
            "status": status,
            "provider_reference": provider_reference,
            "idempotency_key": "refund-001",
        }
    )

    assert result.status == status
    assert result.provider_reference == provider_reference
    assert result.idempotency_key == "refund-001"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"status": "UNKNOWN", "idempotency_key": "refund-001"},
        {"status": "SUCCEEDED", "idempotency_key": ""},
        {"status": "SUCCEEDED", "idempotency_key": "   "},
        {"status": "SUCCEEDED"},
        {
            "status": "SUCCEEDED",
            "idempotency_key": "refund-001",
            "provider_reference": None,
        },
        {
            "status": "SUCCEEDED",
            "idempotency_key": "refund-001",
            "provider_reference": "   ",
        },
        {
            "status": "SUCCEEDED",
            "idempotency_key": "refund-001",
            "provider_reference": 123,
        },
    ],
)
def test_parse_refund_sandbox_result_rejects_invalid_payload(payload: object) -> None:
    with pytest.raises(RuntimeError, match="^Invalid refund sandbox response$") as caught:
        parse_refund_sandbox_result(payload)

    assert "UNKNOWN" not in str(caught.value)
    assert "refund-001" not in str(caught.value)


@pytest.mark.asyncio
async def test_http_sandbox_execute_sends_decimal_as_string_and_parses_result() -> None:
    request = make_request("refund:execute-001")

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.method == "POST"
        assert http_request.url.path == "/refunds"
        assert http_request.read().decode() == (
            '{"refund_application_id":"'
            + request.refund_application_id
            + '","user_id":"'
            + request.user_id
            + '","order_id":"'
            + request.order_id
            + '","amount":"29.90","currency":"CNY",'
            '"idempotency_key":"refund:execute-001"}'
        )
        return httpx.Response(
            200,
            json={
                "status": "SUCCEEDED",
                "provider_reference": "provider-001",
                "idempotency_key": "refund:execute-001",
            },
            request=http_request,
        )

    async with make_http_client(handler) as client:
        result = await HttpRefundSandbox(client).execute(request)

    assert result == RefundSandboxResult(
        status="SUCCEEDED",
        provider_reference="provider-001",
        idempotency_key="refund:execute-001",
    )


@pytest.mark.asyncio
async def test_http_sandbox_get_result_encodes_key_and_returns_result() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.method == "GET"
        assert http_request.url.raw_path == b"/refunds/refund%3Alookup-001"
        return httpx.Response(
            200,
            json={
                "status": "PROCESSING",
                "provider_reference": None,
                "idempotency_key": "refund:lookup-001",
            },
            request=http_request,
        )

    async with make_http_client(handler) as client:
        result = await HttpRefundSandbox(client).get_result("refund:lookup-001")

    assert result == RefundSandboxResult(
        status="PROCESSING",
        provider_reference=None,
        idempotency_key="refund:lookup-001",
    )


@pytest.mark.asyncio
async def test_http_sandbox_get_result_returns_none_for_provider_404() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=http_request)

    async with make_http_client(handler) as client:
        result = await HttpRefundSandbox(client).get_result("refund:missing")

    assert result is None


@pytest.mark.asyncio
async def test_http_sandbox_maps_timeout_without_provider_detail() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive timeout detail", request=http_request)

    async with make_http_client(handler) as client:
        with pytest.raises(TimeoutError, match="^Refund sandbox request timed out$") as caught:
            await HttpRefundSandbox(client).get_result("refund:timeout")

    assert "sensitive timeout detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_http_sandbox_execute_treats_connection_loss_as_unknown_without_retry() -> None:
    attempts = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("sensitive connection detail", request=http_request)

    async with make_http_client(handler) as client:
        with pytest.raises(TimeoutError) as caught:
            await HttpRefundSandbox(client).execute(make_request("refund:connection-loss"))

    assert attempts == 1
    assert "sensitive connection detail" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 500])
async def test_http_sandbox_maps_http_failure_without_response_body(
    status_code: int,
) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            text="sensitive provider response",
            request=http_request,
        )

    async with make_http_client(handler) as client:
        with pytest.raises(RuntimeError, match="^Invalid refund sandbox response$") as caught:
            await HttpRefundSandbox(client).execute(make_request("refund:http-error"))

    assert "sensitive provider response" not in str(caught.value)


@pytest.mark.asyncio
async def test_first_execution_returns_successful_sandbox_result() -> None:
    sandbox = InMemoryRefundSandbox()

    result = await sandbox.execute(make_request("refund-001"))

    assert result.status == "SUCCEEDED"
    assert result.provider_reference == "sandbox-refund-001"
    assert result.idempotency_key == "refund-001"


@pytest.mark.asyncio
async def test_repeated_idempotency_key_returns_same_result() -> None:
    sandbox = InMemoryRefundSandbox()
    first_request = make_request("refund-002")

    first_result = await sandbox.execute(first_request)
    retry_result = await sandbox.execute(make_request("refund-002"))

    assert retry_result == first_result


@pytest.mark.asyncio
async def test_different_idempotency_keys_create_independent_results() -> None:
    sandbox = InMemoryRefundSandbox()

    first_result = await sandbox.execute(make_request("refund-003"))
    second_result = await sandbox.execute(make_request("refund-004"))

    assert first_result != second_result
    assert first_result.provider_reference != second_result.provider_reference


@pytest.mark.asyncio
async def test_get_result_returns_existing_result_without_reexecution() -> None:
    sandbox = InMemoryRefundSandbox()
    original = await sandbox.execute(make_request("refund-existing"))

    recovered = await sandbox.get_result("refund-existing")

    assert recovered is original


@pytest.mark.asyncio
async def test_get_result_returns_none_for_unknown_key() -> None:
    sandbox = InMemoryRefundSandbox()

    assert await sandbox.get_result("refund-missing") is None


@pytest.mark.asyncio
async def test_get_result_miss_does_not_create_a_result() -> None:
    sandbox = InMemoryRefundSandbox()
    request = make_request("refund-after-query")

    assert await sandbox.get_result(request.idempotency_key) is None
    executed = await sandbox.execute(request)

    assert executed.status == "SUCCEEDED"
    assert await sandbox.get_result(request.idempotency_key) is executed
