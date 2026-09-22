"""V1.0 的真实 SQL + 进程内 HTTP 闭环；不启动服务或调用真实支付。"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

import app.refund_sandbox_app as sandbox_module
from app.api.dependencies import get_current_refund_approver_id
from app.api.routes.refund_operations import router as operations_router
from app.api.routes.refund_webhooks import router as webhook_router
from app.api.routes.refunds import router as refunds_router
from app.core.config import Settings
from app.services.refund import (
    _apply_refund_sandbox_result_on_connection,
    apply_refund_webhook_result,
    fetch_refund_execution,
    try_claim_refund_execution,
    try_create_refund_execution,
)
from app.services.refund_audit import set_refund_audit_context
from app.services.refund_recovery import (
    acknowledge_refund_conflict,
    claim_refund_recovery,
    finish_refund_recovery,
    process_refund_recovery_batch,
    resubmit_missing_refund,
    resume_refund_recovery,
)
from app.services.refund_sandbox import HttpRefundSandbox, RefundSandboxResult
from app.services.refund_webhook import RefundWebhookEvent
from tests.test_refund_execution_integration import execution_engine as isolated_execution_engine
from tests.test_refund_execution_integration import seed_application
from tests.test_refunds_route import make_auth_headers

pytestmark = pytest.mark.integration
execution_engine = isolated_execution_engine


@pytest.mark.parametrize("execution_engine", ["existing-executions"], indirect=True)
async def test_migration_backfills_baseline_without_inventing_history(
    execution_engine: AsyncEngine,
) -> None:
    async with execution_engine.connect() as connection:
        rows = (
            await connection.execute(
                text("""
            SELECT execution_id,action,source,to_status FROM refund_audit_events
            ORDER BY execution_id
        """)
            )
        ).all()
        assert [tuple(row) for row in rows] == [
            ("old-running", "BASELINE", "migration", "RUNNING"),
            ("old-success", "BASELINE", "migration", "SUCCEEDED"),
        ]
        assert (
            await connection.execute(
                text("""
            SELECT execution_id,status FROM refund_recovery_jobs
        """)
            )
        ).one() == ("old-running", "READY")


async def prepare(engine: AsyncEngine) -> None:
    await seed_application(engine)
    assert await try_create_refund_execution(
        engine, execution_id="execution-1", user_id="owner", refund_application_id="refund-001"
    )
    assert await try_claim_refund_execution(engine, execution_id="execution-1")
    await due(engine)


async def due(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE refund_recovery_jobs SET next_attempt_at=clock_timestamp()-interval '1 second'
        """)
        )


async def job_status(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        result = await connection.scalar(text("SELECT status FROM refund_recovery_jobs"))
        assert isinstance(result, str)
        return result


async def test_audit_and_jobs_commit_or_rollback_with_state(execution_engine: AsyncEngine) -> None:
    await prepare(execution_engine)
    async with execution_engine.connect() as connection:
        rows = (
            await connection.execute(
                text("""
            SELECT action,source,actor_user_id,to_status FROM refund_audit_events ORDER BY id
        """)
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("CREATED", "execute", "owner", "PENDING"),
        ("STATE_CHANGED", "execute", None, "RUNNING"),
    ]
    with pytest.raises(RuntimeError):
        async with execution_engine.begin() as connection:
            await set_refund_audit_context(connection, source="test")
            assert await _apply_refund_sandbox_result_on_connection(
                connection,
                execution_id="execution-1",
                expected_idempotency_key="refund:refund-001",
                sandbox_result=RefundSandboxResult("SUCCEEDED", "ref-1", "refund:refund-001"),
            )
            assert await connection.scalar(text("SELECT count(*) FROM refund_audit_events")) == 3
            assert (
                await connection.scalar(text("SELECT status FROM refund_recovery_jobs"))
                == "COMPLETED"
            )
            raise RuntimeError("rollback")
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM refund_audit_events")) == 2
        assert await connection.scalar(text("SELECT status FROM refund_recovery_jobs")) == "READY"
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "RUNNING"


@pytest.mark.parametrize(
    "command",
    [
        "UPDATE refund_audit_events SET source='edited'",
        "DELETE FROM refund_audit_events",
        "TRUNCATE refund_audit_events",
    ],
)
async def test_audit_is_append_only(execution_engine: AsyncEngine, command: str) -> None:
    await prepare(execution_engine)
    with pytest.raises(SQLAlchemyError):
        async with execution_engine.begin() as connection:
            await connection.execute(text(command))


async def test_recovery_lease_excludes_concurrent_claim_and_rejects_stale_result(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    leases = await asyncio.gather(
        *[
            claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=10)
            for _ in range(2)
        ]
    )
    assert sum(lease is not None for lease in leases) == 1
    first = next(lease for lease in leases if lease is not None)
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE refund_recovery_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
        """)
        )
    second = await claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=10)
    assert second is not None and second.token != first.token
    assert not await finish_refund_recovery(
        execution_engine,
        lease=first,
        result=RefundSandboxResult("FAILED", None, first.idempotency_key),
        error_code=None,
        max_attempts=5,
        retry_seconds=60,
    )
    assert await finish_refund_recovery(
        execution_engine,
        lease=second,
        result=RefundSandboxResult("SUCCEEDED", "ref-new", second.idempotency_key),
        error_code=None,
        max_attempts=5,
        retry_seconds=60,
    )
    record = await fetch_refund_execution(
        execution_engine, user_id="owner", refund_application_id="refund-001"
    )
    assert record is not None and record.status == "SUCCEEDED"
    assert await job_status(execution_engine) == "COMPLETED"


async def test_unknown_result_backs_off_exhausts_and_can_be_manually_resumed(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    adapter = AsyncMock()
    adapter.get_result.return_value = None
    for attempt in [1, 2]:
        await due(execution_engine)
        assert await process_refund_recovery_batch(
            execution_engine,
            adapter=adapter,
            max_attempts=2,
            limit=10,
        ) == {"checked": 1, "errors": 0}
        async with execution_engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                SELECT attempts,next_attempt_at>clock_timestamp() AS delayed
                FROM refund_recovery_jobs
            """)
                )
            ).one()
            assert row.attempts == attempt and row.delayed
    assert await job_status(execution_engine) == "MANUAL_REQUIRED"
    assert await process_refund_recovery_batch(execution_engine, adapter=adapter) == {
        "checked": 0,
        "errors": 0,
    }
    adapter.execute.assert_not_awaited()
    assert await resume_refund_recovery(
        execution_engine, application_id="refund-001", actor_user_id="admin", note="已核实渠道恢复"
    )
    assert not await resume_refund_recovery(
        execution_engine, application_id="refund-001", actor_user_id="admin", note="重复点击"
    )
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT attempts FROM refund_recovery_jobs")) == 0
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "RUNNING"
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM refund_audit_events
            WHERE action='MANUAL_RESUMED' AND actor_user_id='admin'
        """)
            )
            == 1
        )


async def test_crash_on_last_attempt_enters_manual_queue(execution_engine: AsyncEngine) -> None:
    await prepare(execution_engine)
    assert await claim_refund_recovery(execution_engine, max_attempts=1, lease_seconds=10)
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE refund_recovery_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
        """)
        )
    assert await claim_refund_recovery(execution_engine, max_attempts=1, lease_seconds=10) is None
    assert await job_status(execution_engine) == "MANUAL_REQUIRED"


async def test_cancellation_preserves_lease_for_reclamation(execution_engine: AsyncEngine) -> None:
    await prepare(execution_engine)
    adapter = AsyncMock()
    adapter.get_result.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await process_refund_recovery_batch(execution_engine, adapter=adapter)
    assert await job_status(execution_engine) == "LEASED"
    assert await claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=10) is None
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE refund_recovery_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
        """)
        )
    assert (
        await claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=10) is not None
    )
    adapter.execute.assert_not_awaited()


async def test_claim_audit_foreign_key_does_not_deadlock_state_writer(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    async with execution_engine.begin() as connection:
        await connection.execute(text("SELECT id FROM refund_executions FOR NO KEY UPDATE"))
        # 与 finish 的状态锁重叠：领取任务插入审计的外键检查不等待该非主键锁。
        async with asyncio.timeout(3):
            assert await claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=10)


async def test_audit_context_is_transaction_local_on_reused_connection(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    async with execution_engine.connect() as connection:
        async with connection.begin():
            await set_refund_audit_context(connection, source="request-a", actor_user_id="admin-a")
        async with connection.begin():
            await connection.execute(text("UPDATE refund_executions SET status='FAILED'"))
            row = (
                await connection.execute(
                    text("""
                SELECT source,actor_user_id FROM refund_audit_events
                WHERE to_status='FAILED'
            """)
                )
            ).one()
            assert row == ("database", None)


@pytest.mark.parametrize("failure", [TimeoutError("secret-provider"), RuntimeError("secret-body")])
async def test_recovery_error_is_audited_without_changing_money_status(
    execution_engine: AsyncEngine,
    failure: Exception,
) -> None:
    await prepare(execution_engine)
    adapter = AsyncMock()
    adapter.get_result.side_effect = failure
    assert await process_refund_recovery_batch(
        execution_engine, adapter=adapter, max_attempts=1
    ) == {
        "checked": 0,
        "errors": 1,
    }
    async with execution_engine.connect() as connection:
        audit = (await connection.execute(text("SELECT * FROM refund_audit_events"))).all()
        assert "secret-" not in str(audit)
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "RUNNING"
    adapter.execute.assert_not_awaited()
    assert await job_status(execution_engine) == "MANUAL_REQUIRED"


async def test_conflicting_terminal_is_visible_to_admin_without_overwriting_state(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    for event_id, result in [
        ("evt-success", RefundSandboxResult("SUCCEEDED", "ref-1", "refund:refund-001")),
        ("evt-conflict", RefundSandboxResult("FAILED", None, "refund:refund-001")),
    ]:
        record = await apply_refund_webhook_result(
            execution_engine, event=RefundWebhookEvent(event_id, result)
        )
        assert record is not None and record.status == "SUCCEEDED"
    assert await job_status(execution_engine) == "MANUAL_REQUIRED"
    assert not await resume_refund_recovery(
        execution_engine, application_id="refund-001", actor_user_id="admin", note="不能重开终态"
    )
    assert await acknowledge_refund_conflict(
        execution_engine,
        application_id="refund-001",
        actor_user_id="admin",
        note="提供方确认成功，失败通知有误",
    )
    assert not await acknowledge_refund_conflict(
        execution_engine,
        application_id="refund-001",
        actor_user_id="admin",
        note="重复确认",
    )
    assert await job_status(execution_engine) == "COMPLETED"
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "SUCCEEDED"
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM refund_audit_events WHERE action='RESULT_CONFLICT'
              AND source_event_id='evt-conflict'
        """)
            )
            == 1
        )
        acknowledged = (
            await connection.execute(
                text("""
            SELECT actor_user_id,note FROM refund_audit_events
            WHERE action='CONFLICT_ACKNOWLEDGED'
        """)
            )
        ).all()
        assert [tuple(row) for row in acknowledged] == [("admin", "提供方确认成功，失败通知有误")]


async def test_audit_pages_over_fifty_events_preserve_bigint_ids(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    async with execution_engine.begin() as connection:
        await connection.execute(
            text("""
            INSERT INTO refund_audit_events (id,execution_id,action,source)
            SELECT 9007199254740992 + n,'execution-1','MANUAL_REQUIRED','test'
            FROM generate_series(1,51) AS n
        """)
        )
    backend = FastAPI()
    backend.include_router(operations_router)
    backend.state.database_engine = execution_engine
    backend.dependency_overrides[get_current_refund_approver_id] = lambda: "admin"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend), base_url="http://agent"
    ) as client:
        first = await client.get("/v1/refund-operations/refund-001?limit=50")
        assert first.status_code == 200
        first_ids = [event["id"] for event in first.json()["events"]]
        assert len(first_ids) == 50
        assert first_ids[2] == "9007199254740993"
        assert first.json()["next_after_id"] == first_ids[-1]
        second = await client.get(
            "/v1/refund-operations/refund-001",
            params={"after_id": first.json()["next_after_id"], "limit": 50},
        )
        assert second.status_code == 200
        second_ids = [event["id"] for event in second.json()["events"]]
        assert second_ids == ["9007199254741041", "9007199254741042", "9007199254741043"]
        assert set(first_ids).isdisjoint(second_ids)


async def test_compensation_does_not_apply_after_webhook_completes(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    lease = await claim_refund_recovery(execution_engine, max_attempts=5, lease_seconds=120)
    assert lease is not None
    await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            "evt-winner", RefundSandboxResult("SUCCEEDED", "ref-webhook", lease.idempotency_key)
        ),
    )
    assert not await finish_refund_recovery(
        execution_engine,
        lease=lease,
        result=RefundSandboxResult("FAILED", None, lease.idempotency_key),
        error_code=None,
        max_attempts=5,
        retry_seconds=60,
    )
    assert await job_status(execution_engine) == "COMPLETED"


@pytest.mark.parametrize("delivery", ["webhook", "compensation"])
async def test_http_sandbox_full_refund_loop_and_idempotency(
    execution_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    delivery: str,
) -> None:
    """应用与支付提供方通过真实 HTTPX 序列化互通，传输在进程内，无 TCP 服务。"""
    await seed_application(execution_engine)
    config = Settings(
        _env_file=None,
        environment="test",
        openai_api_key=None,
        refund_sandbox_api_key="fixture-sandbox-token",
        refund_sandbox_webhook_secret="fixture-secret",
        refund_sandbox_callback_url="http://agent/v1/refund-webhooks/sandbox",
        jwt_secret_key="test-only-jwt-secret-at-least-32-bytes",
    )
    provider = sandbox_module.create_sandbox_app(config)
    provider.state.database_engine = execution_engine
    backend = FastAPI()
    backend.include_router(refunds_router)
    backend.include_router(webhook_router)
    backend.include_router(operations_router)
    backend.state.settings = config
    backend.state.database_engine = execution_engine
    backend.dependency_overrides[get_current_refund_approver_id] = lambda: "admin"
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=provider),
            base_url="http://sandbox",
            headers={"Authorization": "Bearer fixture-sandbox-token"},
        ) as payment,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=backend), base_url="http://agent"
        ) as client,
    ):
        adapter = HttpRefundSandbox(payment)
        backend.state.refund_sandbox_adapter = adapter
        responses = await asyncio.gather(
            *[
                client.post(
                    "/v1/refund-applications/refund-001/execute", headers=make_auth_headers("owner")
                )
                for _ in range(2)
            ]
        )
        assert all(response.status_code == 200 for response in responses), [
            (response.status_code, response.text) for response in responses
        ]
        assert (
            await client.get(
                "/v1/refund-applications/refund-001/execution", headers=make_auth_headers("owner")
            )
        ).json()["status"] == "PROCESSING"
        assert (
            await client.get(
                "/v1/refund-applications/refund-001/execution", headers=make_auth_headers("other")
            )
        ).status_code == 404
        async with execution_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM sandbox_refunds")) == 1
        # 同键变更金额必须拒绝，不能让同一提供方幂等键代表另一笔退款。
        conflict = await payment.post(
            "/refunds",
            headers={"Idempotency-Key": "refund:refund-001"},
            json={
                "idempotency_key": "refund:refund-001",
                "refund_application_id": "refund-001",
                "user_id": "owner",
                "order_id": "order-001",
                "amount": "99.00",
                "currency": "CNY",
            },
        )
        assert conflict.status_code == 409
        settlement = await payment.post(
            "/refunds/refund:refund-001/settle", json={"status": "SUCCEEDED"}
        )
        assert settlement.status_code == 200
        event_id = settlement.json()["event_id"]
        repeated = await payment.post(
            "/refunds/refund:refund-001/settle", json={"status": "SUCCEEDED"}
        )
        assert repeated.json() == settlement.json()
        assert (
            await payment.post("/refunds/refund:refund-001/settle", json={"status": "FAILED"})
        ).status_code == 409
        if delivery == "webhook":
            real_client = httpx.AsyncClient

            def callback_client(*, timeout: float) -> httpx.AsyncClient:
                return real_client(transport=httpx.ASGITransport(app=backend), timeout=timeout)

            monkeypatch.setattr(
                sandbox_module,
                "httpx",
                SimpleNamespace(AsyncClient=callback_client, HTTPError=httpx.HTTPError),
            )
            for _ in range(2):
                delivered = await payment.post(f"/events/{event_id}/deliver")
                assert delivered.status_code == 200
        else:
            await due(execution_engine)
            assert await process_refund_recovery_batch(execution_engine, adapter=adapter) == {
                "checked": 1,
                "errors": 0,
            }
        status = await client.get(
            "/v1/refund-applications/refund-001/execution", headers=make_auth_headers("owner")
        )
        assert status.json()["status"] == "SUCCEEDED"
        assert Decimal(status.json()["amount"]) == Decimal("88.25")
        audit = await client.get("/v1/refund-operations/refund-001?limit=2")
        assert audit.status_code == 200
        assert audit.json()["recovery"]["status"] == "COMPLETED"
        first_page = audit.json()["events"]
        second = await client.get(
            f"/v1/refund-operations/refund-001?after_id={audit.json()['next_after_id']}"
        )
        assert set(e["id"] for e in first_page).isdisjoint(e["id"] for e in second.json()["events"])
        async with execution_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("""
                SELECT count(*) FROM refund_audit_events
                WHERE action='STATE_CHANGED' AND to_status='SUCCEEDED'
            """)
                )
                == 1
            )
        # 新应用实例仍能查询同一条提供方记录，模拟支付沙箱进程重启。
        restarted = sandbox_module.create_sandbox_app(config)
        restarted.state.database_engine = execution_engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://sandbox",
            headers={"Authorization": "Bearer fixture-sandbox-token"},
        ) as restarted_client:
            assert (await restarted_client.get("/refunds/refund:refund-001")).json()[
                "status"
            ] == "SUCCEEDED"


async def exhaust_missing(engine: AsyncEngine) -> None:
    await prepare(engine)
    adapter = AsyncMock()
    adapter.get_result.return_value = None
    await process_refund_recovery_batch(engine, adapter=adapter, max_attempts=1)
    assert await job_status(engine) == "MANUAL_REQUIRED"


async def test_late_success_closes_exhausted_recovery(execution_engine: AsyncEngine) -> None:
    await exhaust_missing(execution_engine)
    await apply_refund_webhook_result(
        execution_engine,
        event=RefundWebhookEvent(
            "late", RefundSandboxResult("SUCCEEDED", "ref-late", "refund:refund-001")
        ),
    )
    assert await job_status(execution_engine) == "COMPLETED"


@pytest.mark.parametrize("already_submitted", [False, True])
async def test_admin_resubmission_queries_first_and_preserves_original_snapshot(
    execution_engine: AsyncEngine,
    already_submitted: bool,
) -> None:
    await exhaust_missing(execution_engine)
    adapter = AsyncMock()
    result = RefundSandboxResult("SUCCEEDED", "ref-1", "refund:refund-001")
    adapter.get_result.return_value = result if already_submitted else None
    adapter.execute.return_value = result
    assert await resubmit_missing_refund(
        execution_engine,
        application_id="refund-001",
        actor_user_id="admin",
        note="人工查证未提交",
        adapter=adapter,
    )
    adapter.get_result.assert_awaited_once_with("refund:refund-001")
    if already_submitted:
        adapter.execute.assert_not_awaited()
    else:
        adapter.execute.assert_awaited_once()
        sent = adapter.execute.await_args.args[0]
        assert sent.idempotency_key == "refund:refund-001"
        assert sent.amount == Decimal("88.25")
        assert sent.user_id == "owner" and sent.order_id == "order-001"
    assert not await resubmit_missing_refund(
        execution_engine,
        application_id="refund-001",
        actor_user_id="admin",
        note="重复点击",
        adapter=adapter,
    )
    assert await job_status(execution_engine) == "COMPLETED"
    async with execution_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM refund_audit_events WHERE action='RESUBMISSION_AUTHORIZED'
              AND actor_user_id='admin' AND note='人工查证未提交'
        """)
            )
            == 1
        )


async def test_overlapping_admin_resubmission_submits_only_once(
    execution_engine: AsyncEngine,
) -> None:
    await exhaust_missing(execution_engine)
    querying = asyncio.Event()
    release = asyncio.Event()
    adapter = AsyncMock()

    async def blocked_query(key: str) -> None:
        querying.set()
        await release.wait()

    adapter.get_result.side_effect = blocked_query
    adapter.execute.return_value = RefundSandboxResult("PROCESSING", None, "refund:refund-001")
    async with asyncio.timeout(10):
        first = asyncio.create_task(
            resubmit_missing_refund(
                execution_engine,
                application_id="refund-001",
                actor_user_id="admin-a",
                note="核实",
                adapter=adapter,
            )
        )
        try:
            await querying.wait()
            assert not await resubmit_missing_refund(
                execution_engine,
                application_id="refund-001",
                actor_user_id="admin-b",
                note="同时点击",
                adapter=adapter,
            )
        finally:
            release.set()
        assert await first
    adapter.execute.assert_awaited_once()
    assert await job_status(execution_engine) == "READY"


async def test_admin_submission_timeout_keeps_unknown_state_and_query_job(
    execution_engine: AsyncEngine,
) -> None:
    await exhaust_missing(execution_engine)
    adapter = AsyncMock()
    adapter.get_result.return_value = None
    adapter.execute.side_effect = TimeoutError("secret-body")
    with pytest.raises(RuntimeError, match="unknown"):
        await resubmit_missing_refund(
            execution_engine,
            application_id="refund-001",
            actor_user_id="admin",
            note="核实",
            adapter=adapter,
        )
    assert await job_status(execution_engine) == "READY"
    async with execution_engine.connect() as connection:
        assert await connection.scalar(text("SELECT status FROM refund_executions")) == "RUNNING"
        assert (
            await connection.scalar(text("SELECT last_error_code FROM refund_recovery_jobs"))
            == "PROVIDER_TIMEOUT"
        )
        assert "secret" not in str(
            (await connection.execute(text("SELECT * FROM refund_audit_events"))).all()
        )


async def test_final_conflict_evidence_contains_observed_provider_reference(
    execution_engine: AsyncEngine,
) -> None:
    await prepare(execution_engine)
    for event_id, reference in [("first", "original"), ("second", "conflicting")]:
        await apply_refund_webhook_result(
            execution_engine,
            event=RefundWebhookEvent(
                event_id,
                RefundSandboxResult("SUCCEEDED", reference, "refund:refund-001"),
            ),
        )
    async with execution_engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT provider_reference FROM refund_executions"))
            == "original"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT provider_reference FROM refund_audit_events "
                    "WHERE action='RESULT_CONFLICT'"
                )
            )
            == "conflicting"
        )
    assert await job_status(execution_engine) == "MANUAL_REQUIRED"


async def test_sandbox_failed_settlement_and_delivery_failure_can_be_retried(
    execution_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Settings(
        _env_file=None,
        refund_sandbox_api_key="fixture-sandbox-token",
        refund_sandbox_callback_url="http://agent/callback",
        refund_sandbox_webhook_secret="fixture-secret",
    )
    provider = sandbox_module.create_sandbox_app(config)
    provider.state.database_engine = execution_engine
    responses = [502, 204]
    delivered: list[bytes] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        delivered.append(request.content)
        return httpx.Response(responses.pop(0))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        sandbox_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: real_client(transport=httpx.MockTransport(receiver)),
            HTTPError=httpx.HTTPError,
        ),
    )
    async with real_client(
        transport=httpx.ASGITransport(app=provider),
        base_url="http://sandbox",
        headers={"Authorization": "Bearer fixture-sandbox-token"},
    ) as client:
        assert (await client.get("/refunds/missing")).status_code == 404
        assert (await client.post("/events/missing/deliver")).status_code == 404
        payload = {
            "refund_application_id": "refund-1",
            "user_id": "owner",
            "order_id": "order-1",
            "amount": "5.00",
            "currency": "CNY",
            "idempotency_key": "key-1",
        }
        assert (await client.post("/refunds", json=payload)).status_code == 400
        assert (
            await client.post(
                "/refunds", json=dict(payload, amount="0"), headers={"Idempotency-Key": "key-1"}
            )
        ).status_code == 422
        assert (
            await client.post("/refunds", json=payload, headers={"Idempotency-Key": "key-1"})
        ).status_code == 200
        event = (await client.post("/refunds/key-1/settle", json={"status": "FAILED"})).json()
        assert event["status"] == "FAILED" and event["provider_reference"] is None
        assert (await client.post(f"/events/{event['event_id']}/deliver")).status_code == 502
        assert (await client.post(f"/events/{event['event_id']}/deliver")).status_code == 200
        assert delivered[0] == delivered[1]
