from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.services.refund import (
    RefundApplication,
    RefundApplicationRecord,
    RefundAssessment,
    RefundOrderSnapshot,
    RefundRequest,
    RefundReviewDecision,
    assess_refund,
    build_refund_request,
    confirm_refund_application,
    fetch_refund_application_by_id,
    fetch_refund_application_by_request_id,
    matches_existing_refund_application,
    try_create_refund_application,
    try_review_refund_application,
)


class FakeInsertResult:
    def __init__(self, returned_id: str | None) -> None:
        self._returned_id = returned_id

    def scalar_one_or_none(self) -> str | None:
        return self._returned_id


class FakeRefundConnection:
    def __init__(self, returned_id: str | None) -> None:
        self._returned_id = returned_id
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeInsertResult:
        self.execution = (statement, parameters)
        return FakeInsertResult(self._returned_id)


class FakeTransactionContext:
    def __init__(self, connection: FakeRefundConnection) -> None:
        self._connection = connection
        self.exited = False

    async def __aenter__(self) -> FakeRefundConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True


class FakeRefundEngine:
    def __init__(self, returned_id: str | None) -> None:
        self.connection = FakeRefundConnection(returned_id)
        self.transaction = FakeTransactionContext(self.connection)

    def begin(self) -> FakeTransactionContext:
        return self.transaction


class FakeMappingRows:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def one_or_none(self) -> dict[str, object] | None:
        return self._row


class FakeSelectResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def mappings(self) -> FakeMappingRows:
        return FakeMappingRows(self._row)


class FakeRefundReadConnection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeSelectResult:
        self.execution = (statement, parameters)
        return FakeSelectResult(self._row)


class FakeReadConnectionContext:
    def __init__(self, connection: FakeRefundReadConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeRefundReadConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeRefundReadEngine:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.connection = FakeRefundReadConnection(row)

    def connect(self) -> FakeReadConnectionContext:
        return FakeReadConnectionContext(self.connection)


class FakeRefundUpdateEngine:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.connection = FakeRefundReadConnection(row)

    def begin(self) -> FakeReadConnectionContext:
        return FakeReadConnectionContext(self.connection)


def make_request(**overrides: object) -> RefundRequest:
    values: dict[str, object] = {
        "requester_user_id": "user-li",
        "order_user_id": "user-li",
        "order_status": "PAID",
        "order_total": Decimal("299.00"),
        "requested_amount": Decimal("100.00"),
        "order_currency": "CNY",
        "requested_currency": "CNY",
    }
    values.update(overrides)
    return RefundRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"requester_user_id": "user-wang"},
            RefundAssessment(False, "order_not_owned", False),
        ),
        (
            {"requested_amount": Decimal("0")},
            RefundAssessment(False, "invalid_amount", False),
        ),
        (
            {"requested_currency": "USD"},
            RefundAssessment(False, "currency_mismatch", False),
        ),
        (
            {"requested_amount": Decimal("299.01")},
            RefundAssessment(False, "amount_exceeds_order_total", False),
        ),
        (
            {"order_status": "CANCELLED"},
            RefundAssessment(False, "order_not_refundable", False),
        ),
    ],
)
def test_assess_refund_rejects_invalid_request(
    overrides: dict[str, object],
    expected: RefundAssessment,
) -> None:
    assert assess_refund(make_request(**overrides)) == expected


@pytest.mark.parametrize("order_status", ["PAID", "SHIPPED", "DELIVERED"])
def test_assess_refund_allows_review_for_refundable_statuses(
    order_status: str,
) -> None:
    result = assess_refund(make_request(order_status=order_status))

    assert result == RefundAssessment(True, "eligible_for_review", True)


def test_assess_refund_checks_ownership_before_amount() -> None:
    result = assess_refund(
        make_request(
            requester_user_id="user-wang",
            requested_amount=Decimal("0"),
        )
    )

    assert result == RefundAssessment(False, "order_not_owned", False)


def test_build_refund_request_keeps_user_input_and_order_facts_separate() -> None:
    application = RefundApplication(
        order_id="order-demo-001",
        requested_amount=Decimal("88.00"),
        requested_currency="CNY",
    )
    order = RefundOrderSnapshot(
        user_id="order-owner",
        status="SHIPPED",
        total_amount=Decimal("299.00"),
        currency="CNY",
    )

    result = build_refund_request(
        application,
        order,
        requester_user_id="authenticated-user",
    )

    assert result == RefundRequest(
        requester_user_id="authenticated-user",
        order_user_id="order-owner",
        order_status="SHIPPED",
        order_total=Decimal("299.00"),
        requested_amount=Decimal("88.00"),
        order_currency="CNY",
        requested_currency="CNY",
    )


@pytest.mark.parametrize(
    ("returned_id", "expected_created"),
    [("refund-001", True), (None, False)],
)
async def test_try_create_refund_application_uses_atomic_idempotent_insert(
    returned_id: str | None,
    expected_created: bool,
) -> None:
    fake_engine = FakeRefundEngine(returned_id)
    application = RefundApplication(
        order_id="order-demo-001",
        requested_amount=Decimal("88.00"),
        requested_currency="CNY",
    )

    created = await try_create_refund_application(
        cast(AsyncEngine, fake_engine),
        application_id="refund-001",
        user_id="demo-user-li",
        request_id="refund-request-001",
        application=application,
    )

    assert created is expected_created
    assert fake_engine.transaction.exited is True
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    assert "ON CONFLICT (user_id, request_id) DO NOTHING" in str(statement)
    assert "RETURNING id" in str(statement)
    assert parameters == {
        "id": "refund-001",
        "user_id": "demo-user-li",
        "order_id": "order-demo-001",
        "request_id": "refund-request-001",
        "requested_amount": Decimal("88.00"),
        "currency": "CNY",
    }


async def test_fetch_refund_application_scopes_idempotency_to_user() -> None:
    fake_engine = FakeRefundReadEngine(
        {
            "id": "refund-001",
            "user_id": "demo-user-li",
            "order_id": "order-demo-001",
            "request_id": "refund-request-001",
            "requested_amount": Decimal("88.00"),
            "currency": "CNY",
            "status": "AWAITING_CUSTOMER_CONFIRMATION",
        }
    )

    result = await fetch_refund_application_by_request_id(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        request_id="refund-request-001",
    )

    assert result is not None
    assert result.id == "refund-001"
    assert result.requested_amount == Decimal("88.00")
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    assert "user_id = :user_id" in str(statement)
    assert "request_id = :request_id" in str(statement)
    assert parameters == {
        "user_id": "demo-user-li",
        "request_id": "refund-request-001",
    }


async def test_fetch_refund_application_returns_none_when_unavailable() -> None:
    fake_engine = FakeRefundReadEngine(None)

    result = await fetch_refund_application_by_request_id(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        request_id="missing-request",
    )

    assert result is None


async def test_fetch_refund_application_by_id_includes_review_audit_fields() -> None:
    reviewed_at = datetime(2026, 8, 23, 9, 30, tzinfo=UTC)
    fake_engine = FakeRefundReadEngine(
        {
            "id": "refund-001",
            "user_id": "demo-user-li",
            "order_id": "order-demo-001",
            "request_id": "refund-request-001",
            "requested_amount": Decimal("88.00"),
            "currency": "CNY",
            "status": "APPROVED",
            "reviewed_by_user_id": "staff-zhang",
            "reviewed_at": reviewed_at,
            "review_note": "已人工核对。",
        }
    )

    result = await fetch_refund_application_by_id(
        cast(AsyncEngine, fake_engine),
        application_id="refund-001",
    )

    assert result is not None
    assert result.status == "APPROVED"
    assert result.reviewed_by_user_id == "staff-zhang"
    assert result.reviewed_at == reviewed_at
    assert result.review_note == "已人工核对。"
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "reviewed_by_user_id" in sql
    assert "reviewed_at" in sql
    assert "review_note" in sql
    assert "WHERE id = :application_id" in sql
    assert parameters == {"application_id": "refund-001"}


async def test_fetch_refund_application_by_id_returns_none_when_missing() -> None:
    fake_engine = FakeRefundReadEngine(None)

    result = await fetch_refund_application_by_id(
        cast(AsyncEngine, fake_engine),
        application_id="refund-missing",
    )

    assert result is None


@pytest.mark.parametrize(
    ("application", "expected"),
    [
        (
            RefundApplication("order-demo-001", Decimal("88.00"), "CNY"),
            True,
        ),
        (
            RefundApplication("order-demo-002", Decimal("88.00"), "CNY"),
            False,
        ),
        (
            RefundApplication("order-demo-001", Decimal("99.00"), "CNY"),
            False,
        ),
        (
            RefundApplication("order-demo-001", Decimal("88.00"), "USD"),
            False,
        ),
    ],
)
def test_matches_existing_refund_application_compares_logical_payload(
    application: RefundApplication,
    expected: bool,
) -> None:
    existing = RefundApplicationRecord(
        id="refund-001",
        user_id="demo-user-li",
        order_id="order-demo-001",
        request_id="refund-request-001",
        requested_amount=Decimal("88.00"),
        currency="CNY",
        status="AWAITING_CUSTOMER_CONFIRMATION",
    )

    assert matches_existing_refund_application(existing, application) is expected


async def test_confirm_refund_application_atomically_advances_owned_record() -> None:
    fake_engine = FakeRefundUpdateEngine(
        {
            "id": "refund-001",
            "user_id": "demo-user-li",
            "order_id": "order-demo-001",
            "request_id": "refund-request-001",
            "requested_amount": Decimal("88.00"),
            "currency": "CNY",
            "status": "PENDING_MANUAL_APPROVAL",
        }
    )

    result = await confirm_refund_application(
        cast(AsyncEngine, fake_engine),
        application_id="refund-001",
        user_id="demo-user-li",
    )

    assert result is not None
    assert result.status == "PENDING_MANUAL_APPROVAL"
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP)" in sql
    assert "'AWAITING_CUSTOMER_CONFIRMATION'" in sql
    assert "'PENDING_MANUAL_APPROVAL'" in sql
    assert "user_id = :user_id" in sql
    assert parameters == {
        "application_id": "refund-001",
        "user_id": "demo-user-li",
    }


async def test_confirm_refund_application_hides_unavailable_record() -> None:
    fake_engine = FakeRefundUpdateEngine(None)

    result = await confirm_refund_application(
        cast(AsyncEngine, fake_engine),
        application_id="refund-other-user",
        user_id="demo-user-li",
    )

    assert result is None


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
async def test_try_review_refund_application_atomically_claims_pending_record(
    decision: RefundReviewDecision,
) -> None:
    fake_engine = FakeRefundUpdateEngine({"id": "refund-001"})

    reviewed = await try_review_refund_application(
        cast(AsyncEngine, fake_engine),
        application_id="refund-001",
        reviewer_user_id="staff-zhang",
        decision=decision,
        review_note="已人工核对。",
    )

    assert reviewed is True
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert 'status = CAST(:decision AS "RefundStatus")' in sql
    assert "status = 'PENDING_MANUAL_APPROVAL'" in sql
    assert "reviewed_by_user_id = :reviewer_user_id" in sql
    assert "reviewed_at = CURRENT_TIMESTAMP" in sql
    assert "RETURNING id" in sql
    assert parameters == {
        "application_id": "refund-001",
        "reviewer_user_id": "staff-zhang",
        "decision": decision,
        "review_note": "已人工核对。",
    }


async def test_try_review_refund_application_reports_lost_transition() -> None:
    fake_engine = FakeRefundUpdateEngine(None)

    reviewed = await try_review_refund_application(
        cast(AsyncEngine, fake_engine),
        application_id="refund-001",
        reviewer_user_id="staff-zhang",
        decision="APPROVED",
        review_note=None,
    )

    assert reviewed is False
