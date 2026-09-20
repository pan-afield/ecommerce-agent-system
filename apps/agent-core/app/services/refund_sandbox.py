from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from urllib.parse import quote

import httpx

RefundExecutionStatus = Literal[
    "SUCCEEDED",
    "PROCESSING",
    "FAILED",
]


@dataclass(frozen=True, slots=True)
class RefundSandboxRequest:
    refund_application_id: str
    user_id: str
    order_id: str
    amount: Decimal
    currency: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RefundSandboxResult:
    status: RefundExecutionStatus
    provider_reference: str | None
    idempotency_key: str


class RefundSandboxAdapter(Protocol):
    async def execute(
        self,
        request: RefundSandboxRequest,
    ) -> RefundSandboxResult: ...
    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None: ...


class InMemoryRefundSandbox:
    def __init__(self) -> None:
        self._results: dict[str, RefundSandboxResult] = {}

    async def execute(
        self,
        request: RefundSandboxRequest,
    ) -> RefundSandboxResult:
        existing = self._results.get(request.idempotency_key)
        if existing is not None:
            return existing

        result = RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference=f"sandbox-{request.idempotency_key}",
            idempotency_key=request.idempotency_key,
        )
        self._results[request.idempotency_key] = result
        return result

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        existing = self._results.get(idempotency_key)
        if existing is None:
            return None
        return existing


class HttpRefundSandbox:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        try:
            response = await self._client.post(
                "/refunds",
                headers={"Idempotency-Key": request.idempotency_key},
                json={
                    "refund_application_id": request.refund_application_id,
                    "user_id": request.user_id,
                    "order_id": request.order_id,
                    "amount": str(request.amount),
                    "currency": request.currency,
                    "idempotency_key": request.idempotency_key,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise TimeoutError("Refund sandbox request timed out") from error
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("Invalid refund sandbox response") from error

        return parse_refund_sandbox_result(payload)

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        encoded_key = quote(idempotency_key, safe="")
        try:
            response = await self._client.get(f"/refunds/{encoded_key}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise TimeoutError("Refund sandbox request timed out") from error
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("Invalid refund sandbox response") from error

        return parse_refund_sandbox_result(payload)


def parse_refund_sandbox_result(payload: object) -> RefundSandboxResult:
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid refund sandbox response")

    raw_status = payload.get("status")
    raw_provider_reference = payload.get("provider_reference")
    raw_idempotency_key = payload.get("idempotency_key")

    if raw_status not in ("SUCCEEDED", "PROCESSING", "FAILED"):
        raise RuntimeError("Invalid refund sandbox response")
    if (
        not isinstance(raw_idempotency_key, str)
        or not raw_idempotency_key.strip()
        or len(raw_idempotency_key) > 128
    ):
        raise RuntimeError("Invalid refund sandbox response")
    if raw_provider_reference is not None and not isinstance(raw_provider_reference, str):
        raise RuntimeError("Invalid refund sandbox response")
    if isinstance(raw_provider_reference, str) and len(raw_provider_reference) > 128:
        raise RuntimeError("Invalid refund sandbox response")
    if raw_status == "SUCCEEDED" and (
        not isinstance(raw_provider_reference, str)
        or not raw_provider_reference.strip()
    ):
        raise RuntimeError("Invalid refund sandbox response")

    return RefundSandboxResult(
        status=raw_status,
        provider_reference=raw_provider_reference,
        idempotency_key=raw_idempotency_key,
    )
