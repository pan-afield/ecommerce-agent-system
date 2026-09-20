"""Run real execution SQL in a per-test schema, never in the development database."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import to_async_database_url
from app.services import refund as refund_service
from app.services.refund import (
    RefundExecutionRecord,
    _apply_refund_sandbox_result_on_connection,
    apply_refund_webhook_result,
    fetch_refund_execution,
    fetch_refund_reconciliation_keys,
    reconcile_refund_batch,
    reconcile_refund_sandbox,
    recover_refund_sandbox,
    run_refund_sandbox,
    try_claim_refund_execution,
    try_create_refund_execution,
    try_fail_refund_execution,
    try_mark_refund_processing,
    try_succeed_refund_execution,
)
from app.services.refund_sandbox import (
    RefundExecutionStatus,
    RefundSandboxRequest,
    RefundSandboxResult,
)
from app.services.refund_webhook import RefundWebhookEvent

pytestmark = pytest.mark.integration

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages/database/prisma/migrations"
    / "20260916090000_create_refund_executions/migration.sql"
)
WEBHOOK_MIGRATION_PATH = (
    MIGRATION_PATH.parent.parent / "20260918100000_create_refund_webhook_events/migration.sql"
)
AUDIT_MIGRATION_PATH = (
    MIGRATION_PATH.parent.parent / "20260919090000_refund_audit_and_recovery/migration.sql"
)
SANDBOX_MIGRATION_PATH = (
    MIGRATION_PATH.parent.parent / "20260919100000_http_refund_sandbox/migration.sql"
)


@pytest.fixture
async def execution_engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured; PostgreSQL behavior is unverified.")
    url = make_url(database_url)
    if url.database != "ecommerce_agents_test" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("These tests require the local ecommerce_agents_test database.")

    # Generated internally, never supplied by a caller. Excluding public from
    # search_path keeps all unqualified production SQL inside this fixture.
    schema = f"refund_execution_test_{uuid4().hex}"
    engine = create_async_engine(
        to_async_database_url(database_url),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}, "timeout": 5},
    )
    created = False
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == url.database
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            # Only the source columns consumed by this function are needed.
            # The target table uses the actual checked-in Prisma migration.
            await connection.execute(
                text(
                    "CREATE TABLE refund_applications ("
                    "id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, "
                    "order_id VARCHAR(64) NOT NULL DEFAULT 'order-001', "
                    "request_id VARCHAR(128) NOT NULL DEFAULT 'request-001', "
                    "status VARCHAR(32) NOT NULL, requested_amount NUMERIC(12, 2) NOT NULL, "
                    "currency CHAR(3) NOT NULL, reviewed_by_user_id VARCHAR(64), "
                    "reviewed_at TIMESTAMPTZ(3), review_note VARCHAR(500))"
                )
            )
            await connection.execute(text(MIGRATION_PATH.read_text(encoding="utf-8")))
            for statement in WEBHOOK_MIGRATION_PATH.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            if getattr(request, "param", None) == "existing-executions":
                await connection.execute(
                    text("""
                    INSERT INTO refund_executions
                      (id,refund_application_id,idempotency_key,status,amount,currency)
                    VALUES ('old-running','app-running','key-running','RUNNING',10,'CNY'),
                           ('old-success','app-success','key-success','SUCCEEDED',20,'CNY')
                """)
                )
            # asyncpg 原生 execute 可处理带函数体的整份 SQL，不按分号破坏 trigger。
            raw = await connection.get_raw_connection()
            assert raw.driver_connection is not None
            await raw.driver_connection.execute(AUDIT_MIGRATION_PATH.read_text(encoding="utf-8"))
            await raw.driver_connection.execute(SANDBOX_MIGRATION_PATH.read_text(encoding="utf-8"))
        created = True
        yield engine
    finally:
        try:
            if created:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f'DROP TABLE "{schema}".sandbox_refund_events, '
                            f'"{schema}".sandbox_refunds, '
                            f'"{schema}".refund_audit_events, '
                            f'"{schema}".refund_recovery_jobs, '
                            f'"{schema}".refund_webhook_events, '
                            f'"{schema}".refund_executions, '
                            f'"{schema}".refund_applications'
                        )
                    )
                    await connection.execute(
                        text(f'DROP FUNCTION "{schema}".refund_capture_transition()')
                    )
                    await connection.execute(
                        text(f'DROP FUNCTION "{schema}".refund_audit_append_only()')
                    )
                    await connection.execute(text(f'DROP SCHEMA "{schema}"'))
        finally:
            await engine.dispose()


async def seed_application(
    engine: AsyncEngine,
    *,
    application_id: str = "refund-001",
    user_id: str = "owner",
    status: str = "APPROVED",
    amount: Decimal = Decimal("88.25"),
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO refund_applications "
                "(id, user_id, status, requested_amount, currency) "
                "VALUES (:id, :user_id, :status, :amount, 'CNY')"
            ),
            {"id": application_id, "user_id": user_id, "status": status, "amount": amount},
        )


class ProcessingWithoutReference:
    def __init__(self) -> None:
        self.requests: list[RefundSandboxRequest] = []

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        self.requests.append(request)
        return RefundSandboxResult(
            status="PROCESSING",
            provider_reference=None,
            idempotency_key=request.idempotency_key,
        )

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        return None


class RecordingSuccessSandbox:
    def __init__(self, *, paused: bool = False) -> None:
        self.requests: list[RefundSandboxRequest] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not paused:
            self.release.set()

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        self.requests.append(request)
        self.entered.set()
        await self.release.wait()
        return RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="sandbox-ref-001",
            idempotency_key=request.idempotency_key,
        )

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        return None


class RecordingFailedSandbox:
    def __init__(self) -> None:
        self.requests: list[RefundSandboxRequest] = []

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        self.requests.append(request)
        return RefundSandboxResult(
            status="FAILED", provider_reference=None, idempotency_key=request.idempotency_key
        )

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        return None


class TimeoutSandbox:
    def __init__(self) -> None:
        self.requests: list[RefundSandboxRequest] = []

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        self.requests.append(request)
        raise TimeoutError("simulated response timeout")

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        return None


class MismatchedResultSandbox:
    def __init__(self, *, status: RefundExecutionStatus) -> None:
        self.status = status
        self.requests: list[RefundSandboxRequest] = []

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        self.requests.append(request)
        return RefundSandboxResult(
            status=self.status,
            provider_reference="other-payment-ref" if self.status == "SUCCEEDED" else None,
            idempotency_key="refund:other-application",
        )

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        return None


class LookupSandbox:
    def __init__(self, result: RefundSandboxResult | None) -> None:
        self.result = result
        self.queried_keys: list[str] = []

    async def execute(self, request: RefundSandboxRequest) -> RefundSandboxResult:
        raise AssertionError("recovery must query before executing")

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        self.queried_keys.append(idempotency_key)
        return self.result


class ConcurrentLookupSandbox(LookupSandbox):
    def __init__(self, result: RefundSandboxResult) -> None:
        super().__init__(result)
        self.both_queries_started = asyncio.Event()
        self.release_queries = asyncio.Event()

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        self.queried_keys.append(idempotency_key)
        if len(self.queried_keys) == 2:
            self.both_queries_started.set()
        await self.release_queries.wait()
        return self.result


class PausedLookupSandbox(LookupSandbox):
    """暂停查询响应，让测试确定性地在等待期间提交 webhook。"""

    def __init__(self, result: RefundSandboxResult | None) -> None:
        super().__init__(result)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        self.queried_keys.append(idempotency_key)
        self.entered.set()
        await self.release.wait()
        return self.result


class BatchLookupSandbox(LookupSandbox):
    """按退款键提供可控结果/异常，禁止批次重新执行退款。"""

    def __init__(self, results: dict[str, RefundSandboxResult | Exception | None]) -> None:
        super().__init__(None)
        self.results = results

    async def get_result(self, idempotency_key: str) -> RefundSandboxResult | None:
        self.queried_keys.append(idempotency_key)
        result = self.results[idempotency_key]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("failure_kind", ["timeout", "invalid_response", "database"])
async def test_reconcile_batch_limits_work_and_keeps_other_refunds_progressing_after_error(
    execution_engine: AsyncEngine,
    failure_kind: str,
) -> None:
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)
    for index in range(1, 6):
        application_id = f"refund-{index}"
        await seed_application(execution_engine, application_id=application_id)
        assert await try_create_refund_execution(
            execution_engine,
            execution_id=f"execution-{index}",
            user_id="owner",
            refund_application_id=application_id,
        )
        assert await try_claim_refund_execution(execution_engine, execution_id=f"execution-{index}")
        async with execution_engine.begin() as connection:
            await connection.execute(
                text("UPDATE refund_executions SET updated_at = :at WHERE id = :id"),
                {"at": cutoff - timedelta(minutes=10 - index), "id": f"execution-{index}"},
            )
    failure: RefundSandboxResult | Exception
    if failure_kind == "timeout":
        failure = TimeoutError("fake provider timeout")
    elif failure_kind == "invalid_response":
        failure = RuntimeError("fake invalid response")
    else:
        # 超过真实 VARCHAR(128) 上限，使第二笔更新在 PostgreSQL 中失败并回滚。
        failure = RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="x" * 129,
            idempotency_key="refund:refund-2",
        )
    sandbox = BatchLookupSandbox(
        {
            "refund:refund-1": RefundSandboxResult(
                status="SUCCEEDED",
                provider_reference="provider-1",
                idempotency_key="refund:refund-1",
            ),
            "refund:refund-2": failure,
            "refund:refund-3": RefundSandboxResult(
                status="FAILED",
                provider_reference=None,
                idempotency_key="refund:refund-3",
            ),
            "refund:refund-4": None,
            "refund:refund-5": RefundSandboxResult(
                status="SUCCEEDED",
                provider_reference="provider-5",
                idempotency_key="refund:refund-5",
            ),
        }
    )

    counts = await reconcile_refund_batch(
        execution_engine,
        stale_before=cutoff,
        adapter=sandbox,
        limit=4,
    )

    assert counts == {"checked": 3, "errors": 1}
    assert sandbox.queried_keys == [f"refund:refund-{index}" for index in range(1, 5)]
    expected = {1: "SUCCEEDED", 2: "RUNNING", 3: "FAILED", 4: "RUNNING", 5: "RUNNING"}
    for index, status in expected.items():
        record = await fetch_refund_execution(
            execution_engine,
            user_id="owner",
            refund_application_id=f"refund-{index}",
        )
        assert record is not None and record.status == status
        assert record.amount == Decimal("88.25") and record.currency == "CNY"
        assert record.provider_reference == ("provider-1" if index == 1 else None)
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 0


async def test_reconcile_overlapping_batches_keep_independent_counts_and_one_terminal_state(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET updated_at = :at WHERE id = 'target'"),
            {"at": cutoff - timedelta(hours=1)},
        )
    sandbox = ConcurrentLookupSandbox(
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        )
    )
    batches = [
        asyncio.create_task(
            reconcile_refund_batch(
                execution_engine,
                stale_before=cutoff,
                adapter=sandbox,
            )
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(sandbox.both_queries_started.wait(), timeout=5)
        sandbox.release_queries.set()
        first, second = await asyncio.wait_for(asyncio.gather(*batches), timeout=5)
    finally:
        sandbox.release_queries.set()
        for batch in batches:
            if not batch.done():
                batch.cancel()
        await asyncio.gather(*batches, return_exceptions=True)

    assert first == second == {"checked": 1, "errors": 0}
    assert first is not second
    assert sandbox.queried_keys == ["refund:refund-001", "refund:refund-001"]
    record = await fetch_refund_execution(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert record is not None and record.status == "SUCCEEDED"
    assert record.provider_reference == "provider-001"


@pytest.mark.parametrize("initial_status", ["RUNNING", "PROCESSING"])
@pytest.mark.parametrize("provider_status", [None, "SUCCEEDED", "PROCESSING", "FAILED"])
async def test_reconcile_applies_only_explicit_provider_result_and_preserves_identity(
    execution_engine: AsyncEngine,
    initial_status: str,
    provider_status: RefundExecutionStatus | None,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    if initial_status == "PROCESSING":
        assert await try_mark_refund_processing(
            execution_engine,
            execution_id="target",
            provider_reference=None,
        )
    before = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert before is not None
    provider_result = (
        None
        if provider_status is None
        else RefundSandboxResult(
            status=provider_status,
            idempotency_key=before.idempotency_key,
            provider_reference="provider-001" if provider_status != "FAILED" else None,
        )
    )
    sandbox = LookupSandbox(provider_result)

    result = await reconcile_refund_sandbox(
        execution_engine,
        idempotency_key=before.idempotency_key,
        adapter=sandbox,
    )

    assert result is not None
    assert result.status == (provider_status or initial_status)
    assert (
        result.id,
        result.refund_application_id,
        result.idempotency_key,
        result.amount,
        result.currency,
    ) == (
        before.id,
        before.refund_application_id,
        before.idempotency_key,
        before.amount,
        before.currency,
    )
    if provider_status is None or (
        initial_status == "PROCESSING" and provider_status == "PROCESSING"
    ):
        assert result == before
    elif provider_result is not None:
        assert result.provider_reference == provider_result.provider_reference
    assert result == await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert sandbox.queried_keys == [before.idempotency_key]
    # 主动查询不是 provider webhook，不伪造 event_id 或事件接收记录。
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 0


@pytest.mark.parametrize(
    "invalid_result",
    [
        RefundSandboxResult(
            status="SUCCEEDED", provider_reference="other", idempotency_key="refund:other"
        ),
        RefundSandboxResult(
            status="SUCCEEDED", provider_reference=" \t", idempotency_key="refund:refund-001"
        ),
    ],
)
async def test_reconcile_rejects_invalid_provider_result_without_state_change(
    execution_engine: AsyncEngine,
    invalid_result: RefundSandboxResult,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    before = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    with pytest.raises(RuntimeError):
        await reconcile_refund_sandbox(
            execution_engine,
            idempotency_key="refund:refund-001",
            adapter=LookupSandbox(invalid_result),
        )
    assert (
        await fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        )
        == before
    )


@pytest.mark.parametrize("late_status", [None, "SUCCEEDED", "PROCESSING", "FAILED"])
async def test_reconcile_returns_webhook_terminal_state_after_waiting_for_provider(
    execution_engine: AsyncEngine,
    late_status: RefundExecutionStatus | None,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    assert await try_mark_refund_processing(
        execution_engine, execution_id="target", provider_reference=None
    )
    late_result = (
        None
        if late_status is None
        else RefundSandboxResult(
            status=late_status,
            provider_reference="stale-reference" if late_status != "FAILED" else None,
            idempotency_key="refund:refund-001",
        )
    )
    sandbox = PausedLookupSandbox(late_result)
    reconciliation = asyncio.create_task(
        reconcile_refund_sandbox(
            execution_engine,
            idempotency_key="refund:refund-001",
            adapter=sandbox,
        )
    )
    try:
        await asyncio.wait_for(sandbox.entered.wait(), timeout=5)
        webhook_result = await asyncio.wait_for(
            apply_refund_webhook_result(
                execution_engine,
                event=RefundWebhookEvent(
                    "evt-during-reconciliation",
                    RefundSandboxResult(
                        status="SUCCEEDED",
                        provider_reference="webhook-reference",
                        idempotency_key="refund:refund-001",
                    ),
                ),
            ),
            timeout=5,
        )
        assert webhook_result is not None and webhook_result.status == "SUCCEEDED"
        sandbox.release.set()
        result = await asyncio.wait_for(reconciliation, timeout=5)
    finally:
        sandbox.release.set()
        if not reconciliation.done():
            reconciliation.cancel()
        await asyncio.gather(reconciliation, return_exceptions=True)

    assert result == webhook_result
    assert result is not None and result.provider_reference == "webhook-reference"
    assert sandbox.queried_keys == ["refund:refund-001"]


async def test_reconcile_overlapping_queries_converge_without_reexecuting_refund(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    sandbox = ConcurrentLookupSandbox(
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        )
    )
    tasks = [
        asyncio.create_task(
            reconcile_refund_sandbox(
                execution_engine,
                idempotency_key="refund:refund-001",
                adapter=sandbox,
            )
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(sandbox.both_queries_started.wait(), timeout=5)
        sandbox.release_queries.set()
        first, second = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    finally:
        sandbox.release_queries.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert first == second
    assert first is not None and first.status == "SUCCEEDED"
    assert sandbox.queried_keys == ["refund:refund-001", "refund:refund-001"]
    assert first == await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )


@pytest.mark.parametrize("limit", [1, 2, 100])
async def test_reconciliation_keys_filters_sorts_and_limits_without_writing(
    execution_engine: AsyncEngine,
    limit: int,
) -> None:
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)
    old = cutoff - timedelta(hours=2)
    rows = [
        # 同时间故意先插入 b，再插入 a，保证次级排序不依赖插入顺序。
        ("b", "RUNNING", old),
        ("a", "PROCESSING", old),
        ("c", "PROCESSING", cutoff - timedelta(minutes=1)),
        ("equal", "RUNNING", cutoff),
        ("fresh", "PROCESSING", cutoff + timedelta(seconds=1)),
        ("pending", "PENDING", old),
        ("succeeded", "SUCCEEDED", old),
        ("failed", "FAILED", old),
    ]
    async with execution_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO refund_executions "
                "(id, refund_application_id, idempotency_key, status, "
                "amount, currency, updated_at) "
                "VALUES (:id, :application_id, :key, :status, 88.25, 'CNY', :updated_at)"
            ),
            [
                {
                    "id": key,
                    "application_id": f"application-{key}",
                    "key": f"refund:{key}",
                    "status": status,
                    "updated_at": updated_at,
                }
                for key, status, updated_at in rows
            ],
        )
        before = (
            await connection.execute(text("SELECT * FROM refund_executions ORDER BY id"))
        ).all()

    expected = ["refund:a", "refund:b", "refund:c"][:limit]
    assert (
        await fetch_refund_reconciliation_keys(
            execution_engine,
            stale_before=cutoff,
            limit=limit,
        )
        == expected
    )
    # 同一时刻使用不同时区表示，TIMESTAMPTZ 的筛选结果不应改变。
    assert (
        await fetch_refund_reconciliation_keys(
            execution_engine,
            stale_before=cutoff.astimezone(timezone(timedelta(hours=8))),
            limit=limit,
        )
        == expected
    )
    async with execution_engine.connect() as connection:
        after = (
            await connection.execute(text("SELECT * FROM refund_executions ORDER BY id"))
        ).all()
    assert after == before


async def test_reconciliation_keys_empty_database_returns_empty_list(
    execution_engine: AsyncEngine,
) -> None:
    assert (
        await fetch_refund_reconciliation_keys(
            execution_engine,
            stale_before=datetime(2026, 9, 18, tzinfo=UTC),
        )
        == []
    )


async def test_reconciliation_keys_snapshot_does_not_reserve_refund(
    execution_engine: AsyncEngine,
) -> None:
    """扫描 A 结束后，webhook B 可完成退款；下一次扫描必须排除该终态。"""
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    cutoff = datetime(2026, 9, 18, 10, tzinfo=UTC)
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET updated_at = :updated_at WHERE id = 'target'"),
            {"updated_at": cutoff - timedelta(hours=1)},
        )

    keys = await fetch_refund_reconciliation_keys(execution_engine, stale_before=cutoff)
    assert keys == ["refund:refund-001"]
    completed = await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            "evt-after-scan",
            RefundSandboxResult(
                status="SUCCEEDED",
                provider_reference="provider-001",
                idempotency_key=keys[0],
            ),
        ),
    )
    assert completed is not None and completed.status == "SUCCEEDED"
    # 用更晚的截止时间保证它因终态被排除，而不是因更新时间变新被排除。
    assert (
        await fetch_refund_reconciliation_keys(
            execution_engine,
            stale_before=datetime(2100, 1, 1, tzinfo=UTC),
        )
        == []
    )
    assert keys == ["refund:refund-001"]


@pytest.mark.parametrize("amount", [Decimal("0.01"), Decimal("9999999999.99")])
async def test_create_and_retry_preserve_original_execution_and_money(
    execution_engine: AsyncEngine, amount: Decimal
) -> None:
    await seed_application(execution_engine, amount=amount)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="first",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert not await try_create_refund_execution(
        execution_engine,
        execution_id="retry",
        user_id="owner",
        refund_application_id="refund-001",
    )

    async with execution_engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT id, refund_application_id, idempotency_key, status, "
                "amount, currency, provider_reference FROM refund_executions"
            )
        )
        assert dict(result.mappings().one()) == {
            "id": "first",
            "refund_application_id": "refund-001",
            "idempotency_key": "refund:refund-001",
            "status": "PENDING",
            "amount": amount,
            "currency": "CNY",
            "provider_reference": None,
        }
        assert (
            await connection.scalar(
                text("SELECT status FROM refund_applications WHERE id = 'refund-001'")
            )
            == "APPROVED"
        )


@pytest.mark.parametrize(
    ("status", "user_id", "application_id"),
    [
        ("AWAITING_CUSTOMER_CONFIRMATION", "owner", "refund-001"),
        ("PENDING_MANUAL_APPROVAL", "owner", "refund-001"),
        ("REJECTED", "owner", "refund-001"),
        ("APPROVED", "other-user", "refund-001"),
        ("APPROVED", "owner", "missing-refund"),
    ],
)
async def test_ineligible_application_creates_no_execution(
    execution_engine: AsyncEngine, status: str, user_id: str, application_id: str
) -> None:
    await seed_application(execution_engine, status=status)
    assert not await try_create_refund_execution(
        execution_engine,
        execution_id="denied",
        user_id=user_id,
        refund_application_id=application_id,
    )
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_executions")) == 0


async def test_two_connections_creating_same_refund_produce_one_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    start = asyncio.Barrier(2)

    async def create(execution_id: str) -> bool:
        await start.wait()
        return await try_create_refund_execution(
            execution_engine,
            execution_id=execution_id,
            user_id="owner",
            refund_application_id="refund-001",
        )

    async with asyncio.timeout(10):
        results = await asyncio.gather(create("attempt-a"), create("attempt-b"))

    assert sorted(results) == [False, True]
    async with execution_engine.connect() as connection:
        result = await connection.execute(text("SELECT id, status FROM refund_executions"))
        row = result.mappings().one()
        assert row["id"] in {"attempt-a", "attempt-b"}
        assert row["status"] == "PENDING"


async def test_other_unique_violation_rolls_back_instead_of_becoming_safe_retry(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    await seed_application(execution_engine, application_id="refund-002")
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="collision",
        user_id="owner",
        refund_application_id="refund-001",
    )

    with pytest.raises(IntegrityError):
        await try_create_refund_execution(
            execution_engine,
            execution_id="collision",
            user_id="owner",
            refund_application_id="refund-002",
        )

    assert await try_create_refund_execution(
        execution_engine,
        execution_id="second",
        user_id="owner",
        refund_application_id="refund-002",
    )
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_executions")) == 2


async def test_claim_updates_only_pending_target_and_preserves_refund_details(
    execution_engine: AsyncEngine,
) -> None:
    for application_id, execution_id in [("refund-001", "target"), ("refund-002", "untouched")]:
        await seed_application(execution_engine, application_id=application_id)
        assert await try_create_refund_execution(
            execution_engine,
            execution_id=execution_id,
            user_id="owner",
            refund_application_id=application_id,
        )

    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET updated_at = '2000-01-01T00:00:00Z'")
        )
        result = await connection.execute(
            text("SELECT * FROM refund_executions WHERE id = 'target'")
        )
        before = dict(result.mappings().one())

    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    assert not await try_claim_refund_execution(execution_engine, execution_id="target")

    async with execution_engine.connect() as connection:
        result = await connection.execute(
            text("SELECT * FROM refund_executions WHERE id = 'target'")
        )
        after = dict(result.mappings().one())
        assert after["updated_at"] > before["updated_at"]
        assert after == {**before, "status": "RUNNING", "updated_at": after["updated_at"]}
        assert (
            await connection.scalar(
                text("SELECT status FROM refund_executions WHERE id = 'untouched'")
            )
            == "PENDING"
        )
        assert (
            await connection.scalar(
                text("SELECT status FROM refund_applications WHERE id = 'refund-001'")
            )
            == "APPROVED"
        )


@pytest.mark.parametrize("status", ["RUNNING", "PROCESSING", "SUCCEEDED", "FAILED", "UNKNOWN"])
async def test_claim_refuses_non_pending_without_changing_state_or_timestamp(
    execution_engine: AsyncEngine, status: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = :status WHERE id = 'target'"),
            {"status": status},
        )
        result = await connection.execute(text("SELECT * FROM refund_executions"))
        before = dict(result.mappings().one())

    assert not await try_claim_refund_execution(execution_engine, execution_id="target")

    async with execution_engine.connect() as connection:
        result = await connection.execute(text("SELECT * FROM refund_executions"))
        assert dict(result.mappings().one()) == before


async def test_claim_missing_execution_does_not_create_a_record(
    execution_engine: AsyncEngine,
) -> None:
    assert not await try_claim_refund_execution(execution_engine, execution_id="missing")
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_executions")) == 0


async def test_two_connections_claiming_same_execution_have_one_winner(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    start = asyncio.Barrier(2)

    async def claim() -> bool:
        await start.wait()
        return await try_claim_refund_execution(execution_engine, execution_id="target")

    async with asyncio.timeout(10):
        results = await asyncio.gather(claim(), claim())

    assert sorted(results) == [False, True]
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "RUNNING"


@pytest.mark.parametrize(
    ("status", "provider_reference"),
    [
        ("PENDING", None),
        ("RUNNING", None),
        ("PROCESSING", "sandbox-processing"),
        ("SUCCEEDED", "sandbox-original"),
        ("FAILED", None),
    ],
)
async def test_fetch_original_execution_at_any_status_is_read_only(
    execution_engine: AsyncEngine, status: str, provider_reference: str | None
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert not await try_create_refund_execution(
        execution_engine, execution_id="retry", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = :status, provider_reference = :reference"),
            {"status": status, "reference": provider_reference},
        )
        before_result = await connection.execute(text("SELECT * FROM refund_executions"))
        before = dict(before_result.mappings().one())

    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )

    assert record == RefundExecutionRecord(
        id="original",
        refund_application_id="refund-001",
        idempotency_key="refund:refund-001",
        status=status,
        amount=Decimal("88.25"),
        currency="CNY",
        provider_reference=provider_reference,
    )
    async with execution_engine.connect() as connection:
        after_result = await connection.execute(text("SELECT * FROM refund_executions"))
        assert dict(after_result.mappings().one()) == before


@pytest.mark.parametrize(
    ("user_id", "application_id"),
    [
        ("other-user", "refund-001"),
        ("owner", "refund-002"),
        ("owner", "no-execution"),
        ("owner", "missing-application"),
    ],
)
async def test_fetch_scopes_application_to_owner_and_hides_missing_records(
    execution_engine: AsyncEngine, user_id: str, application_id: str
) -> None:
    for owner, refund_id, execution_id in [
        ("owner", "refund-001", "execution-001"),
        ("other-user", "refund-002", "execution-002"),
    ]:
        await seed_application(execution_engine, application_id=refund_id, user_id=owner)
        assert await try_create_refund_execution(
            execution_engine,
            execution_id=execution_id,
            user_id=owner,
            refund_application_id=refund_id,
        )
    await seed_application(execution_engine, application_id="no-execution")

    assert (
        await fetch_refund_execution(
            execution_engine, user_id=user_id, refund_application_id=application_id
        )
        is None
    )
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_executions")) == 2


async def test_reading_pending_snapshot_does_not_reserve_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    first, second = await asyncio.gather(
        fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        ),
        fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        ),
    )
    assert first is not None and second is not None
    assert first.status == second.status == "PENDING"

    assert await try_claim_refund_execution(execution_engine, execution_id=first.id)
    assert second.status == "PENDING"  # Local dataclass is not live database state.
    assert not await try_claim_refund_execution(execution_engine, execution_id=second.id)
    current = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert current is not None
    assert current.status == "RUNNING"


@pytest.mark.parametrize("provider_reference", ["sandbox-ref-001", "provider-退款-001"])
async def test_success_transition_persists_reference_and_is_idempotent(
    execution_engine: AsyncEngine, provider_reference: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")

    assert await try_succeed_refund_execution(
        execution_engine, execution_id="target", provider_reference=provider_reference
    )
    assert not await try_succeed_refund_execution(
        execution_engine, execution_id="target", provider_reference="different-reference"
    )

    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "SUCCEEDED"
    assert record.provider_reference == provider_reference


async def test_success_transition_accepts_processing_state(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = 'PROCESSING' WHERE id = 'target'")
        )

    assert await try_succeed_refund_execution(
        execution_engine, execution_id="target", provider_reference="late-success"
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "SUCCEEDED"
    assert record.provider_reference == "late-success"


@pytest.mark.parametrize("status", ["PENDING", "SUCCEEDED", "FAILED"])
async def test_success_transition_refuses_pending_and_terminal_states(
    execution_engine: AsyncEngine, status: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = :status WHERE id = 'target'"),
            {"status": status},
        )

    assert not await try_succeed_refund_execution(
        execution_engine, execution_id="target", provider_reference="should-not-save"
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == status
    assert record.provider_reference is None


@pytest.mark.parametrize("provider_reference", ["", "   ", "\t\n"])
async def test_success_transition_rejects_blank_reference_without_database_write(
    execution_engine: AsyncEngine, provider_reference: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")

    with pytest.raises(ValueError, match="provider_reference must not be blank"):
        await try_succeed_refund_execution(
            execution_engine, execution_id="target", provider_reference=provider_reference
        )

    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "RUNNING"
    assert record.provider_reference is None


@pytest.mark.parametrize("initial_reference", [None, "sandbox-initial"])
@pytest.mark.parametrize("new_reference", [None, "sandbox-processing"])
async def test_processing_transition_preserves_existing_reference_when_new_one_is_empty(
    execution_engine: AsyncEngine,
    initial_reference: str | None,
    new_reference: str | None,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE refund_executions SET status = 'RUNNING', provider_reference = :reference "
                "WHERE id = 'target'"
            ),
            {"reference": initial_reference},
        )

    assert await try_mark_refund_processing(
        execution_engine, execution_id="target", provider_reference=new_reference
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "PROCESSING"
    assert record.provider_reference == (new_reference or initial_reference)


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING", "SUCCEEDED", "FAILED"])
async def test_processing_transition_refuses_non_running_states(
    execution_engine: AsyncEngine, status: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = :status WHERE id = 'target'"),
            {"status": status},
        )

    assert not await try_mark_refund_processing(
        execution_engine, execution_id="target", provider_reference="should-not-save"
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == status
    assert record.provider_reference is None


async def test_failed_transition_is_terminal_and_preserves_execution_identity(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")

    assert await try_fail_refund_execution(execution_engine, execution_id="target")
    assert not await try_fail_refund_execution(execution_engine, execution_id="target")
    assert not await try_succeed_refund_execution(
        execution_engine, execution_id="target", provider_reference="late-success"
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "FAILED"
    assert record.amount == Decimal("88.25")
    assert record.idempotency_key == "refund:refund-001"
    assert record.provider_reference is None


async def test_failed_transition_accepts_processing_state(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = 'PROCESSING' WHERE id = 'target'")
        )

    assert await try_fail_refund_execution(execution_engine, execution_id="target")
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "FAILED"
    assert record.provider_reference is None


@pytest.mark.parametrize("status", ["PENDING", "SUCCEEDED", "FAILED"])
async def test_failed_transition_refuses_pending_and_terminal_states(
    execution_engine: AsyncEngine, status: str
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE refund_executions SET status = :status, "
                "provider_reference = 'existing-reference' WHERE id = 'target'"
            ),
            {"status": status},
        )

    assert not await try_fail_refund_execution(execution_engine, execution_id="target")
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == status
    assert record.provider_reference == "existing-reference"


async def test_concurrent_success_and_failure_cannot_both_finalize(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    start = asyncio.Barrier(2)

    async def fail() -> bool:
        await start.wait()
        return await try_fail_refund_execution(execution_engine, execution_id="target")

    async def succeed() -> bool:
        await start.wait()
        return await try_succeed_refund_execution(
            execution_engine, execution_id="target", provider_reference="provider-ref"
        )

    async with asyncio.timeout(10):
        failed, succeeded = await asyncio.gather(fail(), succeed())

    assert sorted([failed, succeeded]) == [False, True]
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == ("FAILED" if failed else "SUCCEEDED")
    assert record.provider_reference == (None if failed else "provider-ref")


async def test_orchestration_accepts_processing_without_provider_reference(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    sandbox = ProcessingWithoutReference()

    first = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="first-execution",
        adapter=sandbox,
    )
    second = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="retry-execution",
        adapter=sandbox,
    )

    assert first is not None
    assert first.status == "PROCESSING"
    assert first.provider_reference is None
    assert second == first
    assert len(sandbox.requests) == 1


async def test_orchestration_persists_explicit_failure_and_does_not_repeat_it(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    sandbox = RecordingFailedSandbox()

    first = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="first-execution",
        adapter=sandbox,
    )
    second = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="retry-execution",
        adapter=sandbox,
    )

    assert first is not None
    assert first.id == "first-execution"
    assert first.status == "FAILED"
    assert first.amount == Decimal("88.25")
    assert second == first
    assert len(sandbox.requests) == 1


async def test_orchestration_timeout_remains_unknown_and_is_not_reissued(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    sandbox = TimeoutSandbox()

    with pytest.raises(TimeoutError, match="simulated response timeout"):
        await run_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            execution_id="first-execution",
            adapter=sandbox,
        )

    current = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="retry-execution",
        adapter=sandbox,
    )
    assert current is not None
    assert current.id == "first-execution"
    assert current.status == "RUNNING"
    assert len(sandbox.requests) == 1


@pytest.mark.parametrize("status", ["SUCCEEDED", "PROCESSING", "FAILED"])
async def test_orchestration_rejects_result_for_another_payment(
    execution_engine: AsyncEngine, status: RefundExecutionStatus
) -> None:
    await seed_application(execution_engine)
    sandbox = MismatchedResultSandbox(status=status)

    with pytest.raises(RuntimeError, match="idempotency") as error:
        await run_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            execution_id="first-execution",
            adapter=sandbox,
        )
    assert "refund:other-application" not in str(error.value)

    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None
    assert record.status == "RUNNING"
    assert record.provider_reference is None
    assert sandbox.requests[0].idempotency_key == "refund:refund-001"


async def test_orchestration_persists_success_and_reuses_original_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine, amount=Decimal("88.25"))
    sandbox = RecordingSuccessSandbox()

    first = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="first-execution",
        adapter=sandbox,
    )
    retry = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="different-id-on-retry",
        adapter=sandbox,
    )

    assert first is not None
    assert first.id == "first-execution"
    assert first.status == "SUCCEEDED"
    assert first.provider_reference == "sandbox-ref-001"
    assert retry == first
    assert len(sandbox.requests) == 1
    assert sandbox.requests[0] == RefundSandboxRequest(
        refund_application_id="refund-001",
        user_id="owner",
        order_id="order-001",
        amount=Decimal("88.25"),
        currency="CNY",
        idempotency_key="refund:refund-001",
    )


@pytest.mark.parametrize(
    ("status", "user_id", "application_id"),
    [
        ("APPROVED", "other-user", "refund-001"),
        ("AWAITING_CUSTOMER_CONFIRMATION", "owner", "refund-001"),
        ("PENDING_MANUAL_APPROVAL", "owner", "refund-001"),
        ("REJECTED", "owner", "refund-001"),
        ("APPROVED", "owner", "missing"),
    ],
)
async def test_orchestration_rejects_ineligible_application_before_calling_sandbox(
    execution_engine: AsyncEngine,
    status: str,
    user_id: str,
    application_id: str,
) -> None:
    await seed_application(execution_engine, status=status)
    sandbox = RecordingSuccessSandbox()

    result = await run_refund_sandbox(
        execution_engine,
        user_id=user_id,
        refund_application_id=application_id,
        execution_id="denied-execution",
        adapter=sandbox,
    )

    assert result is None
    assert sandbox.requests == []
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_executions")) == 0


async def test_orchestration_retry_during_in_flight_execution_never_calls_sandbox_twice(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    sandbox = RecordingSuccessSandbox(paused=True)
    first_task = asyncio.create_task(
        run_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            execution_id="first-execution",
            adapter=sandbox,
        )
    )

    try:
        await asyncio.wait_for(sandbox.entered.wait(), timeout=5)
        in_flight = await asyncio.wait_for(
            run_refund_sandbox(
                execution_engine,
                user_id="owner",
                refund_application_id="refund-001",
                execution_id="retry-execution",
                adapter=sandbox,
            ),
            timeout=5,
        )
        assert in_flight is not None
        assert in_flight.id == "first-execution"
        assert in_flight.status == "RUNNING"
        assert len(sandbox.requests) == 1
    finally:
        sandbox.release.set()
        completed = await asyncio.wait_for(first_task, timeout=5)

    assert completed is not None
    assert completed.status == "SUCCEEDED"
    assert len(sandbox.requests) == 1


async def test_orchestration_uses_persisted_money_on_existing_pending_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine, amount=Decimal("88.25"))
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    # Model a later correction to the application; the existing payment attempt
    # must still use the amount and idempotency identity captured in its record.
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_applications SET requested_amount = 42.00 WHERE id = 'refund-001'")
        )
    sandbox = RecordingSuccessSandbox()

    result = await run_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        execution_id="retry-execution",
        adapter=sandbox,
    )

    assert result is not None
    assert result.id == "original-execution"
    assert result.status == "SUCCEEDED"
    assert result.amount == Decimal("88.25")
    assert len(sandbox.requests) == 1
    assert sandbox.requests[0].amount == Decimal("88.25")
    assert sandbox.requests[0].idempotency_key == "refund:refund-001"


@pytest.mark.parametrize(
    ("status", "provider_reference"),
    [("SUCCEEDED", "provider-ref"), ("PROCESSING", None), ("FAILED", None)],
)
async def test_recovery_applies_provider_result_to_running_execution(
    execution_engine: AsyncEngine,
    status: RefundExecutionStatus,
    provider_reference: str | None,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    sandbox = LookupSandbox(
        RefundSandboxResult(
            status=status,
            provider_reference=provider_reference,
            idempotency_key="refund:refund-001",
        )
    )

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is not None
    assert result.id == "original-execution"
    assert result.status == status
    assert result.provider_reference == provider_reference
    assert sandbox.queried_keys == ["refund:refund-001"]


async def test_recovery_applies_provider_result_to_processing_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    async with execution_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE refund_executions SET status = 'PROCESSING' WHERE id = 'original-execution'"
            )
        )
    sandbox = LookupSandbox(
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-ref-late",
            idempotency_key="refund:refund-001",
        )
    )

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert result.provider_reference == "provider-ref-late"
    assert sandbox.queried_keys == ["refund:refund-001"]


async def test_recovery_applies_failed_provider_result_to_processing_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    async with execution_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE refund_executions SET status = 'PROCESSING' WHERE id = 'original-execution'"
            )
        )
    sandbox = LookupSandbox(
        RefundSandboxResult(
            status="FAILED",
            provider_reference=None,
            idempotency_key="refund:refund-001",
        )
    )

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is not None
    assert result.status == "FAILED"
    assert result.provider_reference is None
    assert sandbox.queried_keys == ["refund:refund-001"]


async def test_concurrent_recovery_requests_converge_on_one_terminal_state(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    sandbox = ConcurrentLookupSandbox(
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-ref-concurrent",
            idempotency_key="refund:refund-001",
        )
    )

    first = asyncio.create_task(
        recover_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            adapter=sandbox,
        )
    )
    second = asyncio.create_task(
        recover_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            adapter=sandbox,
        )
    )
    await asyncio.wait_for(sandbox.both_queries_started.wait(), timeout=2)
    sandbox.release_queries.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is not None
    assert second_result is not None
    assert first_result.status == second_result.status == "SUCCEEDED"
    assert (
        first_result.provider_reference
        == second_result.provider_reference
        == "provider-ref-concurrent"
    )
    assert sandbox.queried_keys == ["refund:refund-001", "refund:refund-001"]


@pytest.mark.parametrize("result_status", ["SUCCEEDED", "PROCESSING", "FAILED"])
async def test_result_dispatch_joins_caller_commit_and_rollback(
    execution_engine: AsyncEngine, result_status: RefundExecutionStatus
) -> None:
    """真实连接验证：调用方决定提交/回滚，其他请求只能看到已提交结果。"""
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    before = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert before is not None
    sandbox_result = RefundSandboxResult(
        status=result_status,
        provider_reference="provider-001" if result_status != "FAILED" else None,
        idempotency_key=before.idempotency_key,
    )

    with pytest.raises(RuntimeError, match="^caller failed after state update$"):
        async with execution_engine.begin() as connection:
            applied = await _apply_refund_sandbox_result_on_connection(
                connection,
                execution_id="target",
                expected_idempotency_key=before.idempotency_key,
                sandbox_result=sandbox_result,
            )
            # True 只描述本事务中的更新，随后的调用方异常仍会撤销它。
            assert applied is True
            assert connection.in_transaction()
            assert (
                await connection.scalar(
                    text("SELECT status FROM refund_executions WHERE id = 'target'")
                )
                == result_status
            )
            raise RuntimeError("caller failed after state update")

    assert (
        await fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        )
        == before
    )

    async with execution_engine.begin() as connection:
        applied = await _apply_refund_sandbox_result_on_connection(
            connection,
            execution_id="target",
            expected_idempotency_key=before.idempotency_key,
            sandbox_result=sandbox_result,
        )
        assert applied is True
        # 请求 A 尚未提交；请求 B 的独立连接必须仍读到原来的 RUNNING。
        assert (
            await fetch_refund_execution(
                execution_engine, user_id="owner", refund_application_id="refund-001"
            )
            == before
        )

    after = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert after is not None
    assert after.status == result_status
    assert after.provider_reference == sandbox_result.provider_reference

    async with execution_engine.begin() as connection:
        repeated = await _apply_refund_sandbox_result_on_connection(
            connection,
            execution_id="target",
            expected_idempotency_key=before.idempotency_key,
            sandbox_result=sandbox_result,
        )
        # 同一结果已生效后不再符合更新条件，不能将重投认作新的状态变更。
        assert repeated is False

    assert (
        await fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        )
        == after
    )


async def test_concurrent_webhook_deliveries_converge_on_one_terminal_state(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    sandbox_result = RefundSandboxResult(
        status="SUCCEEDED",
        provider_reference="provider-ref-webhook",
        idempotency_key="refund:refund-001",
    )
    event = RefundWebhookEvent(event_id="evt-001", result=sandbox_result)

    first, second = await asyncio.gather(
        apply_refund_webhook_result(execution_engine, event=event),
        apply_refund_webhook_result(execution_engine, event=event),
    )

    assert first is not None
    assert second is not None
    assert first.status == second.status == "SUCCEEDED"
    assert first.provider_reference == second.provider_reference == "provider-ref-webhook"

    final = await fetch_refund_execution(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert final is not None
    assert final.status == "SUCCEEDED"
    assert final.provider_reference == "provider-ref-webhook"
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 1


@pytest.mark.parametrize("stale_status", ["SUCCEEDED", "PROCESSING", "FAILED"])
@pytest.mark.parametrize("terminal_status", ["SUCCEEDED", "FAILED"])
async def test_stale_webhook_cannot_overwrite_terminal_execution(
    execution_engine: AsyncEngine,
    stale_status: RefundExecutionStatus,
    terminal_status: RefundExecutionStatus,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")

    success = await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            event_id="evt-success",
            result=RefundSandboxResult(
                status=terminal_status,
                provider_reference="provider-ref-original"
                if terminal_status == "SUCCEEDED"
                else None,
                idempotency_key="refund:refund-001",
            ),
        ),
    )
    assert success is not None
    assert success.status == terminal_status

    stale = await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            event_id="evt-stale",
            result=RefundSandboxResult(
                status=stale_status,
                provider_reference="provider-ref-stale" if stale_status != "FAILED" else None,
                idempotency_key="refund:refund-001",
            ),
        ),
    )

    assert stale is not None
    assert stale == success
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 2


@pytest.mark.parametrize("result_status", ["SUCCEEDED", "PROCESSING", "FAILED"])
async def test_webhook_commits_event_and_state_and_duplicate_preserves_timestamps(
    execution_engine: AsyncEngine,
    result_status: RefundExecutionStatus,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    event = RefundWebhookEvent(
        event_id="evt-001",
        result=RefundSandboxResult(
            status=result_status,
            provider_reference="provider-001" if result_status != "FAILED" else None,
            idempotency_key="refund:refund-001",
        ),
    )
    first = await apply_refund_webhook_result(execution_engine, event=event)
    assert first is not None and first.status == result_status
    async with execution_engine.connect() as connection:
        before = (
            await connection.execute(
                text(
                    "SELECT execution.updated_at, event.received_at, event.idempotency_key, "
                    "event.status, event.provider_reference FROM refund_executions execution "
                    "JOIN refund_webhook_events event "
                    "ON event.idempotency_key = execution.idempotency_key"
                )
            )
        ).one()
    # 模拟提交成功但响应丢失：提供方以相同 event_id 重投，不能重写原事件或状态。
    retry = await apply_refund_webhook_result(execution_engine, event=event)
    assert retry == first
    async with execution_engine.connect() as connection:
        after = (
            await connection.execute(
                text(
                    "SELECT execution.updated_at, event.received_at, event.idempotency_key, "
                    "event.status, event.provider_reference FROM refund_executions execution "
                    "JOIN refund_webhook_events event "
                    "ON event.idempotency_key = execution.idempotency_key"
                )
            )
        ).one()
    assert after == before
    assert after.idempotency_key == event.result.idempotency_key
    assert after.status == result_status
    assert after.provider_reference == event.result.provider_reference


async def test_webhook_distinct_events_for_one_refund_progress_to_success(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    processing = await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            "evt-processing",
            RefundSandboxResult(
                status="PROCESSING", provider_reference=None, idempotency_key="refund:refund-001"
            ),
        ),
    )
    assert processing is not None and processing.status == "PROCESSING"
    success = await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            "evt-success",
            RefundSandboxResult(
                status="SUCCEEDED",
                provider_reference="provider-001",
                idempotency_key="refund:refund-001",
            ),
        ),
    )
    assert success is not None and success.status == "SUCCEEDED"
    assert success.id == processing.id
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 2


@pytest.mark.parametrize("fail_after_update", [False, True])
async def test_webhook_database_error_rolls_back_event_and_state_and_allows_retry(
    execution_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_update: bool,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    event = RefundWebhookEvent(
        "evt-retry",
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        ),
    )
    original = refund_service._apply_refund_sandbox_result_on_connection

    async def fail_during_update(
        connection: AsyncConnection,
        *,
        execution_id: str,
        expected_idempotency_key: str,
        sandbox_result: RefundSandboxResult,
    ) -> None:
        if fail_after_update:
            await original(
                connection,
                execution_id=execution_id,
                expected_idempotency_key=expected_idempotency_key,
                sandbox_result=sandbox_result,
            )
        # 实际 SQL 错误：事务必须回滚，不只是让 Python mock 报错。
        await connection.execute(text("SELECT 1 / 0"))

    with monkeypatch.context() as patch:
        patch.setattr(
            refund_service, "_apply_refund_sandbox_result_on_connection", fail_during_update
        )
        with pytest.raises(SQLAlchemyError):
            await apply_refund_webhook_result(execution_engine, event=event)

    current = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert current is not None and current.status == "RUNNING"
    assert current.provider_reference is None
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 0
    retry = await apply_refund_webhook_result(execution_engine, event=event)
    assert retry is not None and retry.status == "SUCCEEDED"
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 1


@pytest.mark.parametrize("initially_missing", [True, False])
async def test_webhook_does_not_consume_event_before_execution_is_ready(
    execution_engine: AsyncEngine,
    initially_missing: bool,
) -> None:
    await seed_application(execution_engine)
    if not initially_missing:
        assert await try_create_refund_execution(
            execution_engine,
            execution_id="target",
            user_id="owner",
            refund_application_id="refund-001",
        )
    event = RefundWebhookEvent(
        "evt-early",
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        ),
    )
    assert await apply_refund_webhook_result(execution_engine, event=event) is None
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 0

    if initially_missing:
        assert await try_create_refund_execution(
            execution_engine,
            execution_id="target",
            user_id="owner",
            refund_application_id="refund-001",
        )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    retry = await apply_refund_webhook_result(execution_engine, event=event)
    assert retry is not None and retry.status == "SUCCEEDED"


@pytest.mark.parametrize("changed_field", ["idempotency_key", "status", "provider_reference"])
async def test_webhook_rejects_reused_event_id_with_changed_content(
    execution_engine: AsyncEngine,
    changed_field: str,
) -> None:
    for application_id, execution_id in [("refund-001", "target"), ("refund-002", "other")]:
        await seed_application(execution_engine, application_id=application_id)
        assert await try_create_refund_execution(
            execution_engine,
            execution_id=execution_id,
            user_id="owner",
            refund_application_id=application_id,
        )
        assert await try_claim_refund_execution(execution_engine, execution_id=execution_id)
    event = RefundWebhookEvent(
        "evt-immutable",
        RefundSandboxResult(
            status="PROCESSING",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        ),
    )
    original = await apply_refund_webhook_result(execution_engine, event=event)
    changed = RefundWebhookEvent(
        event.event_id,
        RefundSandboxResult(
            status="FAILED" if changed_field == "status" else "PROCESSING",
            provider_reference="other-reference"
            if changed_field == "provider_reference"
            else "provider-001",
            idempotency_key="refund:refund-002"
            if changed_field == "idempotency_key"
            else "refund:refund-001",
        ),
    )
    with pytest.raises(ValueError, match="^Refund webhook event conflict$"):
        await apply_refund_webhook_result(execution_engine, event=changed)
    assert (
        await fetch_refund_execution(
            execution_engine, user_id="owner", refund_application_id="refund-001"
        )
        == original
    )
    other = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-002"
    )
    assert other is not None and other.status == "RUNNING"
    async with execution_engine.connect() as connection:
        saved = (
            await connection.execute(
                text(
                    "SELECT event_id, idempotency_key, status, provider_reference "
                    "FROM refund_webhook_events"
                )
            )
        ).one()
    assert tuple(saved) == ("evt-immutable", "refund:refund-001", "PROCESSING", "provider-001")


@pytest.mark.parametrize("first_rolls_back", [False, True])
async def test_webhook_overlapping_duplicate_waits_for_commit_or_retries_after_rollback(
    execution_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    first_rolls_back: bool,
) -> None:
    """强制两个投递重叠，检验唯一约束等待后的提交/回滚分支，不靠 sleep 猜时序。"""
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine, execution_id="target", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="target")
    event = RefundWebhookEvent(
        "evt-overlap",
        RefundSandboxResult(
            status="SUCCEEDED",
            provider_reference="provider-001",
            idempotency_key="refund:refund-001",
        ),
    )
    first_inserted = asyncio.Event()
    second_attempted = asyncio.Event()
    release_first = asyncio.Event()
    original_insert = refund_service._record_refund_webhook_event_on_connection
    attempts = 0

    async def pause_first_insert(
        connection: AsyncConnection,
        *,
        event: RefundWebhookEvent,
    ) -> bool:
        nonlocal attempts
        attempts += 1
        position = attempts
        if position == 2:
            second_attempted.set()
        inserted = await original_insert(connection, event=event)
        if position == 1:
            assert inserted
            first_inserted.set()
            await release_first.wait()
            if first_rolls_back:
                await connection.execute(text("SELECT 1 / 0"))
        return inserted

    monkeypatch.setattr(
        refund_service, "_record_refund_webhook_event_on_connection", pause_first_insert
    )
    first = asyncio.create_task(apply_refund_webhook_result(execution_engine, event=event))
    tasks = [first]
    try:
        await asyncio.wait_for(first_inserted.wait(), timeout=5)
        second = asyncio.create_task(apply_refund_webhook_result(execution_engine, event=event))
        tasks.append(second)
        await asyncio.wait_for(second_attempted.wait(), timeout=5)
        assert not second.done()
        async with execution_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 0
        release_first.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
    finally:
        release_first.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if first_rolls_back:
        assert isinstance(results[0], SQLAlchemyError)
    else:
        assert isinstance(results[0], RefundExecutionRecord)
        assert results[0].status == "SUCCEEDED"
    assert isinstance(results[1], RefundExecutionRecord)
    assert results[1].status == "SUCCEEDED"
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_webhook_events")) == 1
    final = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert final == results[1]


async def test_recovery_keeps_running_when_provider_has_no_result(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    sandbox = LookupSandbox(None)

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is not None
    assert result.status == "RUNNING"
    assert sandbox.queried_keys == ["refund:refund-001"]


@pytest.mark.parametrize(
    ("status", "provider_reference", "result_key", "error_message"),
    [
        ("SUCCEEDED", "other-ref", "refund:other", "Refund sandbox idempotency mismatch"),
        ("PROCESSING", None, "refund:other", "Refund sandbox idempotency mismatch"),
        ("FAILED", None, "refund:other", "Refund sandbox idempotency mismatch"),
        ("SUCCEEDED", None, "refund:refund-001", "Successful refund must have provider reference"),
        ("SUCCEEDED", "", "refund:refund-001", "Successful refund must have provider reference"),
        (
            "SUCCEEDED",
            " \t\n",
            "refund:refund-001",
            "Successful refund must have provider reference",
        ),
    ],
)
async def test_recovery_rejects_invalid_provider_result_without_changing_execution(
    execution_engine: AsyncEngine,
    status: RefundExecutionStatus,
    provider_reference: str | None,
    result_key: str,
    error_message: str,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    assert await try_claim_refund_execution(execution_engine, execution_id="original-execution")
    before = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert before is not None
    sandbox = LookupSandbox(
        RefundSandboxResult(
            status=status,
            provider_reference=provider_reference,
            idempotency_key=result_key,
        )
    )

    with pytest.raises(RuntimeError) as caught:
        await recover_refund_sandbox(
            execution_engine,
            user_id="owner",
            refund_application_id="refund-001",
            adapter=sandbox,
        )

    assert str(caught.value) == error_message
    assert sandbox.queried_keys == [before.idempotency_key]
    after = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert after == before
    assert after.status == "RUNNING"
    assert after.provider_reference is None


@pytest.mark.parametrize("status", ["PENDING", "SUCCEEDED", "FAILED"])
async def test_recovery_does_not_query_non_recoverable_execution(
    execution_engine: AsyncEngine,
    status: str,
) -> None:
    await seed_application(execution_engine)
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("UPDATE refund_executions SET status = :status WHERE id = 'original-execution'"),
            {"status": status},
        )
    sandbox = LookupSandbox(None)

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="owner",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is not None
    assert result.status == status
    assert sandbox.queried_keys == []


async def test_recovery_hides_other_user_execution(
    execution_engine: AsyncEngine,
) -> None:
    await seed_application(execution_engine, user_id="owner")
    assert await try_create_refund_execution(
        execution_engine,
        execution_id="original-execution",
        user_id="owner",
        refund_application_id="refund-001",
    )
    sandbox = LookupSandbox(None)

    result = await recover_refund_sandbox(
        execution_engine,
        user_id="other-user",
        refund_application_id="refund-001",
        adapter=sandbox,
    )

    assert result is None
    assert sandbox.queried_keys == []
