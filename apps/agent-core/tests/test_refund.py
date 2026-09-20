import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import cast
from unittest.mock import ANY, AsyncMock, MagicMock, call

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.services.refund import (
    RefundApplication,
    RefundApplicationRecord,
    RefundAssessment,
    RefundExecutionRecord,
    RefundOrderSnapshot,
    RefundRequest,
    RefundReviewDecision,
    _apply_refund_sandbox_result,
    _apply_refund_sandbox_result_on_connection,
    _record_refund_webhook_event_on_connection,
    _try_fail_refund_execution_on_connection,
    _try_mark_refund_processing_on_connection,
    _try_succeed_refund_execution_on_connection,
    apply_refund_webhook_result,
    assess_refund,
    build_refund_request,
    confirm_refund_application,
    fetch_non_rejected_refund_application_by_order,
    fetch_refund_application_by_id,
    fetch_refund_application_by_request_id,
    fetch_refund_execution,
    fetch_refund_execution_by_idempotency_key,
    fetch_refund_reconciliation_keys,
    get_approved_refund_application,
    matches_existing_refund_application,
    reconcile_refund_batch,
    reconcile_refund_sandbox,
    try_claim_refund_execution,
    try_create_refund_application,
    try_create_refund_execution,
    try_fail_refund_execution,
    try_mark_refund_processing,
    try_record_refund_webhook_event,
    try_review_refund_application,
    try_succeed_refund_execution,
)
from app.services.refund_sandbox import (
    RefundExecutionStatus,
    RefundSandboxAdapter,
    RefundSandboxResult,
)
from app.services.refund_webhook import RefundWebhookEvent


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
        self.commit_error: Exception | None = None

    async def __aenter__(self) -> FakeRefundConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True
        if exc_type is None and self.commit_error is not None:
            raise self.commit_error


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


@pytest.mark.parametrize("limit", [1, 50, 100])
@pytest.mark.parametrize("keys", [[], ["refund:first"]])
async def test_reconciliation_keys_reads_scalar_values_and_releases_connection(
    limit: int,
    keys: list[str],
) -> None:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock(spec=AsyncConnection)
    context = engine.connect.return_value
    context.__aenter__.return_value = connection
    result = MagicMock()
    result.scalars.return_value.all.return_value = tuple(keys)
    connection.execute.return_value = result
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)

    actual = await fetch_refund_reconciliation_keys(engine, stale_before=cutoff, limit=limit)

    assert actual == keys
    assert isinstance(actual, list)
    connection.execute.assert_awaited_once_with(ANY, {"stale_before": cutoff, "limit": limit})
    context.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize("limit", [-1, 0, 101])
async def test_reconciliation_keys_rejects_invalid_limit_before_connecting(limit: int) -> None:
    engine = MagicMock(spec=AsyncEngine)
    with pytest.raises(ValueError, match="^limit must be between 1 and 100$"):
        await fetch_refund_reconciliation_keys(
            engine,
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
            limit=limit,
        )
    engine.connect.assert_not_called()


async def test_reconciliation_keys_rejects_naive_cutoff_before_connecting() -> None:
    engine = MagicMock(spec=AsyncEngine)
    with pytest.raises(ValueError, match="^stale_before must be timezone-aware$"):
        await fetch_refund_reconciliation_keys(engine, stale_before=datetime(2026, 9, 18))
    engine.connect.assert_not_called()


async def test_reconciliation_keys_propagates_database_error_and_releases_connection() -> None:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock(spec=AsyncConnection)
    context = engine.connect.return_value
    context.__aenter__.return_value = connection
    failure = SQLAlchemyError("fake reconciliation database failure")
    connection.execute.side_effect = failure

    with pytest.raises(SQLAlchemyError) as caught:
        await fetch_refund_reconciliation_keys(
            engine,
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
        )
    assert caught.value is failure
    context.__aexit__.assert_awaited_once_with(SQLAlchemyError, failure, ANY)


@pytest.mark.parametrize("record_status", [None, "PENDING", "SUCCEEDED", "FAILED"])
async def test_reconcile_skips_unknown_and_non_recoverable_refunds(
    monkeypatch: pytest.MonkeyPatch,
    record_status: str | None,
) -> None:
    engine = MagicMock(spec=AsyncEngine)
    adapter = MagicMock(spec=RefundSandboxAdapter)
    record = (
        None
        if record_status is None
        else RefundExecutionRecord(
            id="execution-001",
            refund_application_id="refund-001",
            idempotency_key="refund:001",
            status=record_status,
            amount=Decimal("88.25"),
            currency="CNY",
            provider_reference=None,
        )
    )
    fetch = AsyncMock(return_value=record)
    apply = AsyncMock()
    monkeypatch.setattr("app.services.refund.fetch_refund_execution_by_idempotency_key", fetch)
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result", apply)

    assert (
        await reconcile_refund_sandbox(engine, idempotency_key="refund:001", adapter=adapter)
        == record
    )
    fetch.assert_awaited_once_with(engine, idempotency_key="refund:001")
    adapter.get_result.assert_not_awaited()
    adapter.execute.assert_not_awaited()
    apply.assert_not_awaited()


async def test_reconcile_releases_read_connection_before_querying_provider() -> None:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock(spec=AsyncConnection)
    context = engine.connect.return_value
    context.__aenter__.return_value = connection
    connection.execute.return_value = FakeSelectResult(
        {
            "id": "execution-001",
            "refund_application_id": "refund-001",
            "idempotency_key": "refund:001",
            "status": "RUNNING",
            "amount": Decimal("88.25"),
            "currency": "CNY",
            "provider_reference": None,
        }
    )
    adapter = MagicMock(spec=RefundSandboxAdapter)

    async def query_result(idempotency_key: str) -> None:
        assert idempotency_key == "refund:001"
        # 外部调用开始时，第一次数据库读取的上下文已经退出。
        context.__aexit__.assert_awaited_once_with(None, None, None)

    adapter.get_result.side_effect = query_result
    record = await reconcile_refund_sandbox(engine, idempotency_key="refund:001", adapter=adapter)

    assert record is not None and record.status == "RUNNING"
    assert context.__aexit__.await_count == 2
    engine.begin.assert_not_called()
    adapter.get_result.assert_awaited_once_with("refund:001")
    adapter.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [TimeoutError("fake timeout"), RuntimeError("fake invalid result")]
)
async def test_reconcile_provider_errors_propagate_without_marking_failed(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    engine = MagicMock(spec=AsyncEngine)
    record = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:001",
        status="PROCESSING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    adapter = MagicMock(spec=RefundSandboxAdapter)
    adapter.get_result.side_effect = failure
    apply = AsyncMock()
    monkeypatch.setattr(
        "app.services.refund.fetch_refund_execution_by_idempotency_key",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result", apply)

    with pytest.raises(type(failure)) as caught:
        await reconcile_refund_sandbox(engine, idempotency_key="refund:001", adapter=adapter)
    assert caught.value is failure
    apply.assert_not_awaited()
    adapter.execute.assert_not_awaited()


@pytest.mark.parametrize("failure_phase", ["initial_read", "apply", "final_read"])
async def test_reconcile_does_not_hide_database_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    engine = MagicMock(spec=AsyncEngine)
    record = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:001",
        status="RUNNING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    failure = SQLAlchemyError("fake reconciliation database error")
    fetch = AsyncMock(
        side_effect=[
            failure if failure_phase == "initial_read" else record,
            failure if failure_phase == "final_read" else record,
        ]
    )
    apply = AsyncMock(side_effect=failure if failure_phase == "apply" else None)
    adapter = MagicMock(spec=RefundSandboxAdapter)
    adapter.get_result.return_value = RefundSandboxResult(
        status="SUCCEEDED",
        provider_reference="provider-001",
        idempotency_key="refund:001",
    )
    monkeypatch.setattr("app.services.refund.fetch_refund_execution_by_idempotency_key", fetch)
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result", apply)

    with pytest.raises(SQLAlchemyError) as caught:
        await reconcile_refund_sandbox(engine, idempotency_key="refund:001", adapter=adapter)
    assert caught.value is failure
    adapter.execute.assert_not_awaited()
    if failure_phase == "initial_read":
        adapter.get_result.assert_not_awaited()
        apply.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("fixture-secret-token in provider request"),
        RuntimeError("fixture-private-provider-response"),
        SQLAlchemyError("SELECT private_record WHERE credential='fixture-secret'"),
        ConnectionRefusedError("postgresql://fixture-user:fixture-secret@private-db"),
    ],
)
async def test_reconcile_batch_continues_after_known_failure_and_counts_calls_not_refund_status(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.refund")
    engine = MagicMock(spec=AsyncEngine)
    adapter = MagicMock(spec=RefundSandboxAdapter)
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)
    keys = ["refund:first", "refund:second", "refund:third"]
    fetch = AsyncMock(return_value=keys)
    # FAILED 是提供方的明确结果，None 可以是记录已消失；两者都不是核对调用异常。
    failed_refund = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key=keys[0],
        status="FAILED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    reconcile = AsyncMock(side_effect=[failed_refund, failure, None])
    monkeypatch.setattr("app.services.refund.fetch_refund_reconciliation_keys", fetch)
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)

    counts = await reconcile_refund_batch(engine, stale_before=cutoff, adapter=adapter, limit=3)

    assert counts == {"checked": 2, "errors": 1}
    fetch.assert_awaited_once_with(engine, stale_before=cutoff, limit=3)
    assert reconcile.await_args_list == [
        call(engine, idempotency_key=key, adapter=adapter) for key in keys
    ]
    engine.begin.assert_not_called()
    adapter.execute.assert_not_awaited()

    records = [record for record in caplog.records if record.name == "app.services.refund"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == (
        "Refund reconciliation failed: idempotency_key=refund:second "
        f"error_type={type(failure).__name__}"
    )
    assert records[0].exc_info is None
    assert records[0].exc_text is None
    assert str(failure) not in caplog.text


async def test_reconcile_batch_empty_scan_does_not_call_provider(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = MagicMock(spec=RefundSandboxAdapter)
    reconcile = AsyncMock()
    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys", AsyncMock(return_value=[])
    )
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)

    assert await reconcile_refund_batch(
        MagicMock(spec=AsyncEngine),
        stale_before=datetime(2026, 9, 18, tzinfo=UTC),
        adapter=adapter,
    ) == {"checked": 0, "errors": 0}
    reconcile.assert_not_awaited()
    adapter.get_result.assert_not_awaited()
    adapter.execute.assert_not_awaited()
    assert not [record for record in caplog.records if record.name == "app.services.refund"]


async def test_reconcile_batch_normal_completion_does_not_log_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys",
        AsyncMock(return_value=["refund:normal"]),
    )
    reconcile = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)

    counts = await reconcile_refund_batch(
        MagicMock(spec=AsyncEngine),
        stale_before=datetime(2026, 9, 18, tzinfo=UTC),
        adapter=MagicMock(spec=RefundSandboxAdapter),
    )

    assert counts == {"checked": 1, "errors": 0}
    reconcile.assert_awaited_once()
    assert not [record for record in caplog.records if record.name == "app.services.refund"]


async def test_reconcile_batch_overlapping_calls_keep_log_keys_and_errors_separate(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """暂停批次 A，让 B 先报错，验证共享 logger 不串两次调用的局部信息。"""
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    engine = MagicMock(spec=AsyncEngine)
    adapter = MagicMock(spec=RefundSandboxAdapter)
    cutoff = datetime(2026, 9, 18, tzinfo=UTC)
    caplog.set_level(logging.WARNING, logger="app.services.refund")

    async def reconcile(
        engine: AsyncEngine,
        *,
        idempotency_key: str,
        adapter: RefundSandboxAdapter,
    ) -> None:
        if idempotency_key == "refund:alpha":
            first_entered.set()
            await release_first.wait()
            raise TimeoutError("fixture-private-alpha")
        assert idempotency_key == "refund:beta"
        raise SQLAlchemyError("fixture-private-beta")

    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys",
        AsyncMock(side_effect=[["refund:alpha"], ["refund:beta"]]),
    )
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)
    first = asyncio.create_task(
        reconcile_refund_batch(engine, stale_before=cutoff, adapter=adapter)
    )
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        second_counts = await reconcile_refund_batch(engine, stale_before=cutoff, adapter=adapter)
        release_first.set()
        first_counts = await asyncio.wait_for(first, timeout=2)
    finally:
        release_first.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)

    assert first_counts == second_counts == {"checked": 0, "errors": 1}
    records = [record for record in caplog.records if record.name == "app.services.refund"]
    assert [record.getMessage() for record in records] == [
        "Refund reconciliation failed: idempotency_key=refund:beta error_type=SQLAlchemyError",
        "Refund reconciliation failed: idempotency_key=refund:alpha error_type=TimeoutError",
    ]
    assert "fixture-private" not in caplog.text
    adapter.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError("fake scan failure"), ConnectionRefusedError("fake scan connection refused")],
)
async def test_reconcile_batch_scan_failure_propagates_instead_of_returning_empty_summary(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    reconcile = AsyncMock()
    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys", AsyncMock(side_effect=failure)
    )
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)

    with pytest.raises(type(failure)) as caught:
        await reconcile_refund_batch(
            MagicMock(spec=AsyncEngine),
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
            adapter=MagicMock(spec=RefundSandboxAdapter),
        )
    assert caught.value is failure
    reconcile.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), ValueError("unexpected coding error")]
)
async def test_reconcile_batch_does_not_swallow_cancellation_or_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    reconcile = AsyncMock(side_effect=[None, failure, None])
    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys",
        AsyncMock(
            return_value=["refund:first", "refund:second", "refund:third"],
        ),
    )
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)

    with pytest.raises(type(failure)) as caught:
        await reconcile_refund_batch(
            MagicMock(spec=AsyncEngine),
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
            adapter=MagicMock(spec=RefundSandboxAdapter),
        )
    assert caught.value is failure
    assert reconcile.await_count == 2


async def test_reconcile_batch_waits_for_each_item_before_starting_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    peak_active = 0
    started: list[str] = []

    async def reconcile(
        engine: AsyncEngine,
        *,
        idempotency_key: str,
        adapter: RefundSandboxAdapter,
    ) -> None:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        started.append(idempotency_key)
        try:
            if idempotency_key == "refund:first":
                first_entered.set()
                await release_first.wait()
        finally:
            active -= 1

    monkeypatch.setattr(
        "app.services.refund.fetch_refund_reconciliation_keys",
        AsyncMock(
            return_value=["refund:first", "refund:second"],
        ),
    )
    monkeypatch.setattr("app.services.refund.reconcile_refund_sandbox", reconcile)
    batch = asyncio.create_task(
        reconcile_refund_batch(
            MagicMock(spec=AsyncEngine),
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
            adapter=MagicMock(spec=RefundSandboxAdapter),
        )
    )
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        assert started == ["refund:first"]
        release_first.set()
        result = await asyncio.wait_for(batch, timeout=2)
    finally:
        release_first.set()
        if not batch.done():
            batch.cancel()
        await asyncio.gather(batch, return_exceptions=True)

    assert result == {"checked": 2, "errors": 0}
    assert started == ["refund:first", "refund:second"]
    assert peak_active == 1


async def test_fetch_refund_execution_preserves_persisted_result_and_money() -> None:
    engine = FakeRefundReadEngine(
        {
            "id": "original-execution",
            "refund_application_id": "refund-001",
            "idempotency_key": "refund:refund-001",
            "status": "SUCCEEDED",
            "amount": Decimal("88.25"),
            "currency": "CNY",
            "provider_reference": "sandbox-original",
        }
    )

    record = await fetch_refund_execution(
        cast(AsyncEngine, engine), user_id="owner", refund_application_id="refund-001"
    )

    assert record == RefundExecutionRecord(
        id="original-execution",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="SUCCEEDED",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference="sandbox-original",
    )
    assert engine.connection.execution is not None
    _, parameters = engine.connection.execution
    assert parameters == {"user_id": "owner", "refund_application_id": "refund-001"}


async def test_fetch_refund_execution_returns_none_when_no_visible_row() -> None:
    engine = FakeRefundReadEngine(None)

    assert (
        await fetch_refund_execution(
            cast(AsyncEngine, engine), user_id="owner", refund_application_id="missing"
        )
        is None
    )


async def test_fetch_refund_execution_does_not_hide_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundReadEngine(None)
    failure = SQLAlchemyError("fake read failure")
    execute = AsyncMock(side_effect=failure)
    monkeypatch.setattr(engine.connection, "execute", execute)

    with pytest.raises(SQLAlchemyError) as error:
        await fetch_refund_execution(
            cast(AsyncEngine, engine), user_id="owner", refund_application_id="refund-001"
        )

    assert error.value is failure
    execute.assert_awaited_once()


async def test_fetch_refund_execution_by_idempotency_key_returns_persisted_record() -> None:
    engine = FakeRefundReadEngine(
        {
            "id": "original-execution",
            "refund_application_id": "refund-001",
            "idempotency_key": "refund:refund-001",
            "status": "PROCESSING",
            "amount": Decimal("88.25"),
            "currency": "CNY",
            "provider_reference": "provider-001",
        }
    )

    record = await fetch_refund_execution_by_idempotency_key(
        cast(AsyncEngine, engine),
        idempotency_key="refund:refund-001",
    )

    assert record == RefundExecutionRecord(
        id="original-execution",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="PROCESSING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference="provider-001",
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "WHERE execution.idempotency_key = :idempotency_key" in sql
    assert parameters == {"idempotency_key": "refund:refund-001"}
    assert "user_id" not in parameters


async def test_fetch_refund_execution_by_idempotency_key_returns_none_for_unknown_key() -> None:
    engine = FakeRefundReadEngine(None)

    record = await fetch_refund_execution_by_idempotency_key(
        cast(AsyncEngine, engine),
        idempotency_key="missing-key",
    )

    assert record is None


@pytest.mark.parametrize("inserted", [True, False])
async def test_apply_refund_webhook_result_reuses_guarded_state_transition(
    monkeypatch: pytest.MonkeyPatch,
    inserted: bool,
) -> None:
    engine = FakeRefundEngine("evt-001")
    before = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status="RUNNING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    after = RefundExecutionRecord(
        id=before.id,
        refund_application_id=before.refund_application_id,
        idempotency_key=before.idempotency_key,
        status="SUCCEEDED",
        amount=before.amount,
        currency=before.currency,
        provider_reference="provider-001",
    )
    sandbox_result = RefundSandboxResult(
        status="SUCCEEDED",
        provider_reference="provider-001",
        idempotency_key="refund:refund-001",
    )
    fetch = AsyncMock(side_effect=[before, after])
    apply = AsyncMock()
    record = AsyncMock(return_value=inserted)
    monkeypatch.setattr(
        "app.services.refund._fetch_refund_execution_by_idempotency_key_on_connection", fetch
    )
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result_on_connection", apply)
    monkeypatch.setattr("app.services.refund._record_refund_webhook_event_on_connection", record)
    monkeypatch.setattr(
        engine.connection,
        "execute",
        AsyncMock(
            return_value=FakeSelectResult(
                {
                    "idempotency_key": sandbox_result.idempotency_key,
                    "status": sandbox_result.status,
                    "provider_reference": sandbox_result.provider_reference,
                }
            )
        ),
    )
    event = RefundWebhookEvent(event_id="evt-001", result=sandbox_result)

    result = await apply_refund_webhook_result(
        cast(AsyncEngine, engine),
        event=event,
    )

    assert result == after
    assert fetch.await_count == 2
    record.assert_awaited_once_with(engine.connection, event=event)
    if inserted:
        apply.assert_awaited_once_with(
            engine.connection,
            execution_id="execution-001",
            expected_idempotency_key="refund:refund-001",
            sandbox_result=sandbox_result,
        )
    else:
        apply.assert_not_awaited()
    assert engine.transaction.exited


@pytest.mark.parametrize("existing_status", [None, "PENDING"])
async def test_apply_refund_webhook_result_ignores_unknown_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
    existing_status: str | None,
) -> None:
    engine = FakeRefundEngine("evt-001")
    existing = (
        None
        if existing_status is None
        else RefundExecutionRecord(
            id="execution-001",
            refund_application_id="refund-001",
            idempotency_key="refund:001",
            status=existing_status,
            amount=Decimal("88.25"),
            currency="CNY",
            provider_reference=None,
        )
    )
    fetch = AsyncMock(return_value=existing)
    apply = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr(
        "app.services.refund._fetch_refund_execution_by_idempotency_key_on_connection", fetch
    )
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result_on_connection", apply)
    monkeypatch.setattr("app.services.refund._record_refund_webhook_event_on_connection", record)

    result = await apply_refund_webhook_result(
        cast(AsyncEngine, engine),
        event=RefundWebhookEvent(
            event_id="evt-001",
            result=RefundSandboxResult(
                status="FAILED",
                provider_reference=None,
                idempotency_key="refund:001",
            ),
        ),
    )

    assert result is None
    apply.assert_not_awaited()
    record.assert_not_awaited()


@pytest.mark.parametrize("failure_phase", ["insert", "update", "read_after_update", "commit"])
async def test_webhook_transaction_does_not_hide_database_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    engine = FakeRefundEngine("evt-001")
    failure = SQLAlchemyError("fake webhook database failure")
    execution = RefundExecutionRecord(
        id="execution-001",
        refund_application_id="refund-001",
        idempotency_key="refund:001",
        status="RUNNING",
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=None,
    )
    fetch = AsyncMock(
        side_effect=[
            execution,
            failure if failure_phase == "read_after_update" else execution,
        ]
    )
    record = AsyncMock(
        return_value=True, side_effect=failure if failure_phase == "insert" else None
    )
    apply = AsyncMock(side_effect=failure if failure_phase == "update" else None)
    monkeypatch.setattr(
        "app.services.refund._fetch_refund_execution_by_idempotency_key_on_connection", fetch
    )
    monkeypatch.setattr("app.services.refund._record_refund_webhook_event_on_connection", record)
    monkeypatch.setattr("app.services.refund._apply_refund_sandbox_result_on_connection", apply)
    if failure_phase == "commit":
        engine.transaction.commit_error = failure

    with pytest.raises(SQLAlchemyError) as caught:
        await apply_refund_webhook_result(
            cast(AsyncEngine, engine),
            event=RefundWebhookEvent(
                "evt-001",
                RefundSandboxResult(
                    status="FAILED",
                    provider_reference=None,
                    idempotency_key="refund:001",
                ),
            ),
        )
    assert caught.value is failure
    assert engine.transaction.exited
    if failure_phase == "insert":
        apply.assert_not_awaited()


@pytest.mark.parametrize("returned_id", ["evt-001", None])
async def test_record_refund_webhook_event_returns_insert_outcome_and_uses_event_conflict(
    returned_id: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)
    event = RefundWebhookEvent(
        event_id="evt-001",
        result=RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:001",
        ),
    )

    recorded = await try_record_refund_webhook_event(
        cast(AsyncEngine, engine),
        event=event,
    )

    assert recorded is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "INSERT INTO refund_webhook_events" in sql
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert parameters == {
        "event_id": "evt-001",
        "idempotency_key": "refund:001",
        "status": "SUCCEEDED",
        "provider_reference": "provider-001",
    }


async def test_record_refund_webhook_event_does_not_hide_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundEngine("evt-001")
    failure = SQLAlchemyError("fake webhook event database failure")
    execute = AsyncMock(side_effect=failure)
    monkeypatch.setattr(engine.connection, "execute", execute)

    with pytest.raises(SQLAlchemyError) as error:
        await try_record_refund_webhook_event(
            cast(AsyncEngine, engine),
            event=RefundWebhookEvent(
                event_id="evt-001",
                result=RefundSandboxResult(
                    status="FAILED",
                    provider_reference=None,
                    idempotency_key="refund:001",
                ),
            ),
        )

    assert error.value is failure
    assert engine.transaction.exited
    execute.assert_awaited_once()


async def test_record_refund_webhook_event_can_join_an_existing_connection() -> None:
    engine = FakeRefundEngine("evt-001")
    event = RefundWebhookEvent(
        event_id="evt-001",
        result=RefundSandboxResult(
            status="PROCESSING",
            provider_reference=None,
            idempotency_key="refund:001",
        ),
    )

    recorded = await _record_refund_webhook_event_on_connection(
        cast(AsyncConnection, engine.connection),
        event=event,
    )

    assert recorded is True
    assert engine.connection.execution is not None
    assert not engine.transaction.exited


@pytest.mark.parametrize("returned_id", ["execution-001", None])
async def test_create_refund_execution_returns_insert_outcome_and_uses_guarded_sql(
    returned_id: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)

    created = await try_create_refund_execution(
        cast(AsyncEngine, engine),
        execution_id="execution-001",
        user_id="demo-user-li",
        refund_application_id="refund-001",
    )

    assert created is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "INSERT INTO refund_executions" in sql
    assert "FROM refund_applications" in sql
    assert "WHERE id = :refund_application_id" in sql
    assert "AND user_id = :user_id" in sql
    assert "AND status = 'APPROVED'" in sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert parameters == {
        "execution_id": "execution-001",
        "user_id": "demo-user-li",
        "refund_application_id": "refund-001",
    }
    # This checks SQL intent, not real PostgreSQL filtering or conflict handling.
    assert set(statement.compile().params) == set(parameters)


async def test_create_refund_execution_propagates_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundEngine(None)
    failure = SQLAlchemyError("fake database unavailable")
    execute = AsyncMock(side_effect=failure)
    monkeypatch.setattr(engine.connection, "execute", execute)

    with pytest.raises(SQLAlchemyError) as error:
        await try_create_refund_execution(
            cast(AsyncEngine, engine),
            execution_id="execution-001",
            user_id="demo-user-li",
            refund_application_id="refund-001",
        )

    assert error.value is failure
    assert engine.transaction.exited
    execute.assert_awaited_once()


@pytest.mark.parametrize("returned_id", ["execution-001", None])
async def test_claim_refund_execution_returns_conditional_update_outcome(
    returned_id: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)

    claimed = await try_claim_refund_execution(
        cast(AsyncEngine, engine), execution_id="execution-001"
    )

    assert claimed is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "UPDATE refund_executions" in sql
    assert "status = 'RUNNING'" in sql
    assert "updated_at = CURRENT_TIMESTAMP" in sql
    assert "WHERE id = :execution_id AND status = 'PENDING'" in sql
    assert "RETURNING id" in sql
    assert parameters == {"execution_id": "execution-001"}


@pytest.mark.parametrize("failure_phase", ["execute", "commit"])
async def test_claim_does_not_report_success_or_safe_retry_on_database_failure(
    monkeypatch: pytest.MonkeyPatch, failure_phase: str
) -> None:
    engine = FakeRefundEngine("execution-001")
    failure = SQLAlchemyError("fake database failure")
    if failure_phase == "execute":
        monkeypatch.setattr(engine.connection, "execute", AsyncMock(side_effect=failure))
    else:
        engine.transaction.commit_error = failure

    with pytest.raises(SQLAlchemyError) as error:
        await try_claim_refund_execution(cast(AsyncEngine, engine), execution_id="execution-001")

    assert error.value is failure
    assert engine.transaction.exited


@pytest.mark.parametrize("returned_id", ["execution-001", None])
async def test_succeed_refund_execution_uses_recoverable_state_guard_and_reference(
    returned_id: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)

    succeeded = await try_succeed_refund_execution(
        cast(AsyncEngine, engine),
        execution_id="execution-001",
        provider_reference="sandbox-ref-001",
    )

    assert succeeded is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "UPDATE refund_executions" in sql
    assert "status = 'SUCCEEDED'" in sql
    assert "provider_reference = :provider_reference" in sql
    assert "updated_at = CURRENT_TIMESTAMP" in sql
    assert "WHERE id = :execution_id AND status IN ('RUNNING', 'PROCESSING')" in sql
    assert "RETURNING id" in sql
    assert parameters == {
        "execution_id": "execution-001",
        "provider_reference": "sandbox-ref-001",
    }


@pytest.mark.parametrize("provider_reference", ["", "   ", "\t\n"])
async def test_succeed_refund_execution_rejects_blank_provider_reference(
    provider_reference: str,
) -> None:
    engine = FakeRefundEngine(None)

    with pytest.raises(ValueError, match="provider_reference must not be blank"):
        await try_succeed_refund_execution(
            cast(AsyncEngine, engine),
            execution_id="execution-001",
            provider_reference=provider_reference,
        )

    assert engine.connection.execution is None
    assert not engine.transaction.exited


async def test_succeed_refund_execution_propagates_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundEngine(None)
    failure = SQLAlchemyError("fake database failure")
    monkeypatch.setattr(engine.connection, "execute", AsyncMock(side_effect=failure))

    with pytest.raises(SQLAlchemyError) as error:
        await try_succeed_refund_execution(
            cast(AsyncEngine, engine),
            execution_id="execution-001",
            provider_reference="sandbox-ref-001",
        )

    assert error.value is failure
    assert engine.transaction.exited


async def test_succeed_refund_execution_can_join_an_existing_connection() -> None:
    engine = FakeRefundEngine("execution-001")

    succeeded = await _try_succeed_refund_execution_on_connection(
        cast(AsyncConnection, engine.connection),
        execution_id="execution-001",
        provider_reference="sandbox-ref-001",
    )

    assert succeeded is True
    assert engine.connection.execution is not None
    assert not engine.transaction.exited


@pytest.mark.parametrize("returned_id", ["execution-001", None])
@pytest.mark.parametrize("provider_reference", [None, "sandbox-processing-001"])
async def test_mark_refund_processing_uses_running_guard_and_optional_reference(
    returned_id: str | None,
    provider_reference: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)

    processing = await try_mark_refund_processing(
        cast(AsyncEngine, engine),
        execution_id="execution-001",
        provider_reference=provider_reference,
    )

    assert processing is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "UPDATE refund_executions" in sql
    assert "status = 'PROCESSING'" in sql
    assert "COALESCE" in sql
    assert "NULLIF(:provider_reference, '')" in sql
    assert "WHERE id = :execution_id AND status = 'RUNNING'" in sql
    assert "RETURNING id" in sql
    assert parameters == {
        "execution_id": "execution-001",
        "provider_reference": provider_reference,
    }


async def test_mark_refund_processing_propagates_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundEngine(None)
    failure = SQLAlchemyError("fake database failure")
    monkeypatch.setattr(engine.connection, "execute", AsyncMock(side_effect=failure))

    with pytest.raises(SQLAlchemyError) as error:
        await try_mark_refund_processing(
            cast(AsyncEngine, engine),
            execution_id="execution-001",
            provider_reference=None,
        )

    assert error.value is failure
    assert engine.transaction.exited


async def test_mark_refund_processing_can_join_an_existing_connection() -> None:
    engine = FakeRefundEngine("execution-001")

    processing = await _try_mark_refund_processing_on_connection(
        cast(AsyncConnection, engine.connection),
        execution_id="execution-001",
        provider_reference="sandbox-processing-001",
    )

    assert processing is True
    assert engine.connection.execution is not None
    assert not engine.transaction.exited


@pytest.mark.parametrize("returned_id", ["execution-001", None])
async def test_fail_refund_execution_only_updates_recoverable_record(
    returned_id: str | None,
) -> None:
    engine = FakeRefundEngine(returned_id)

    failed = await try_fail_refund_execution(
        cast(AsyncEngine, engine), execution_id="execution-001"
    )

    assert failed is (returned_id is not None)
    assert engine.transaction.exited
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = " ".join(str(statement).split())
    assert "UPDATE refund_executions" in sql
    assert "status = 'FAILED'" in sql
    assert "updated_at = CURRENT_TIMESTAMP" in sql
    assert "WHERE id = :execution_id AND status IN ('RUNNING', 'PROCESSING')" in sql
    assert "RETURNING id" in sql
    assert parameters == {"execution_id": "execution-001"}


@pytest.mark.parametrize("failure_phase", ["execute", "commit"])
async def test_fail_refund_execution_propagates_database_failure(
    monkeypatch: pytest.MonkeyPatch, failure_phase: str
) -> None:
    engine = FakeRefundEngine("execution-001")
    failure = SQLAlchemyError("fake database failure")
    if failure_phase == "execute":
        monkeypatch.setattr(engine.connection, "execute", AsyncMock(side_effect=failure))
    else:
        engine.transaction.commit_error = failure

    with pytest.raises(SQLAlchemyError) as error:
        await try_fail_refund_execution(cast(AsyncEngine, engine), execution_id="execution-001")

    assert error.value is failure
    assert engine.transaction.exited


async def test_fail_refund_execution_can_join_an_existing_connection() -> None:
    engine = FakeRefundEngine("execution-001")

    failed = await _try_fail_refund_execution_on_connection(
        cast(AsyncConnection, engine.connection),
        execution_id="execution-001",
    )

    assert failed is True
    assert engine.connection.execution is not None
    assert not engine.transaction.exited


@pytest.mark.parametrize("result_status", ["SUCCEEDED", "PROCESSING", "FAILED"])
@pytest.mark.parametrize("returned_id", ["execution-001", None])
async def test_apply_result_uses_supplied_connection_without_ending_transaction(
    result_status: RefundExecutionStatus,
    returned_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeRefundEngine(returned_id)
    audit_ignored = AsyncMock()
    monkeypatch.setattr("app.services.refund.record_ignored_refund_result", audit_ignored)

    applied = await _apply_refund_sandbox_result_on_connection(
        cast(AsyncConnection, engine.connection),
        execution_id="execution-001",
        expected_idempotency_key="refund:001",
        sandbox_result=RefundSandboxResult(
            status=result_status,
            provider_reference="provider-001",
            idempotency_key="refund:001",
        ),
    )

    assert applied is (returned_id is not None)
    assert audit_ignored.await_count == (1 if returned_id is None else 0)
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert f"status = '{result_status}'" in str(statement)
    assert parameters["execution_id"] == "execution-001"
    assert not engine.transaction.exited


@pytest.mark.parametrize(
    ("result_status", "reference", "result_key", "message"),
    [
        ("SUCCEEDED", "provider-001", "other-refund", "Refund sandbox idempotency mismatch"),
        ("PROCESSING", None, "other-refund", "Refund sandbox idempotency mismatch"),
        ("FAILED", None, "other-refund", "Refund sandbox idempotency mismatch"),
        ("SUCCEEDED", None, "refund:001", "Successful refund must have provider reference"),
        ("SUCCEEDED", " \t", "refund:001", "Successful refund must have provider reference"),
        ("UNKNOWN", None, "refund:001", "Unexpected sandbox result status: UNKNOWN"),
    ],
)
async def test_apply_result_on_connection_rejects_invalid_result_before_writing(
    result_status: str,
    reference: str | None,
    result_key: str,
    message: str,
) -> None:
    engine = FakeRefundEngine("execution-001")
    # dataclass 不做运行时类型校验；模拟适配器违反约定的返回值。
    sandbox_result = RefundSandboxResult(
        status=cast(RefundExecutionStatus, result_status),
        provider_reference=reference,
        idempotency_key=result_key,
    )

    with pytest.raises(RuntimeError, match=f"^{message}$"):
        await _apply_refund_sandbox_result_on_connection(
            cast(AsyncConnection, engine.connection),
            execution_id="execution-001",
            expected_idempotency_key="refund:001",
            sandbox_result=sandbox_result,
        )

    assert engine.connection.execution is None
    assert not engine.transaction.exited


@pytest.mark.parametrize("failure_phase", ["execute", "commit"])
async def test_apply_result_transaction_wrapper_propagates_database_failure(
    monkeypatch: pytest.MonkeyPatch, failure_phase: str
) -> None:
    engine = FakeRefundEngine("execution-001")
    failure = SQLAlchemyError("fake database failure")
    if failure_phase == "execute":
        monkeypatch.setattr(engine.connection, "execute", AsyncMock(side_effect=failure))
    else:
        engine.transaction.commit_error = failure

    with pytest.raises(SQLAlchemyError) as caught:
        await _apply_refund_sandbox_result(
            cast(AsyncEngine, engine),
            execution_id="execution-001",
            expected_idempotency_key="refund:001",
            sandbox_result=RefundSandboxResult(
                status="FAILED", provider_reference=None, idempotency_key="refund:001"
            ),
        )

    assert caught.value is failure
    assert engine.transaction.exited


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
    assert "ON CONFLICT DO NOTHING" in str(statement)
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
    "status",
    ["AWAITING_CUSTOMER_CONFIRMATION", "PENDING_MANUAL_APPROVAL", "REJECTED"],
)
async def test_get_approved_refund_application_rejects_non_approved_status(
    status: str,
) -> None:
    fake_engine = FakeRefundReadEngine(
        {
            "id": "refund-001",
            "user_id": "demo-user-li",
            "order_id": "order-demo-001",
            "request_id": "refund-request-001",
            "requested_amount": Decimal("88.00"),
            "currency": "CNY",
            "status": status,
            "reviewed_by_user_id": None,
            "reviewed_at": None,
            "review_note": None,
        }
    )

    result = await get_approved_refund_application(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        refund_application_id="refund-001",
    )

    assert result is None


async def test_get_approved_refund_application_rejects_other_users_application() -> None:
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
            "reviewed_at": None,
            "review_note": None,
        }
    )

    result = await get_approved_refund_application(
        cast(AsyncEngine, fake_engine),
        user_id="another-user",
        refund_application_id="refund-001",
    )

    assert result is None


async def test_get_approved_refund_application_returns_owned_approved_application() -> None:
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
            "reviewed_at": None,
            "review_note": "已人工核对。",
        }
    )

    result = await get_approved_refund_application(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        refund_application_id="refund-001",
    )

    assert result is not None
    assert result.status == "APPROVED"
    assert result.requested_amount == Decimal("88.00")
    assert result.currency == "CNY"


async def test_fetch_non_rejected_refund_application_scopes_order_to_user() -> None:
    reviewed_at = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
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

    result = await fetch_non_rejected_refund_application_by_order(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        order_id="order-demo-001",
    )

    assert result is not None
    assert result.id == "refund-001"
    assert result.status == "APPROVED"
    assert result.reviewed_at == reviewed_at
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "user_id = :user_id" in sql
    assert "order_id = :order_id" in sql
    assert "status != 'REJECTED'" in sql
    assert parameters == {
        "user_id": "demo-user-li",
        "order_id": "order-demo-001",
    }


async def test_fetch_non_rejected_refund_application_returns_none_when_missing() -> None:
    fake_engine = FakeRefundReadEngine(None)

    result = await fetch_non_rejected_refund_application_by_order(
        cast(AsyncEngine, fake_engine),
        user_id="demo-user-li",
        order_id="order-demo-001",
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
