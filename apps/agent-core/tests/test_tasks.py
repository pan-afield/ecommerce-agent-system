import asyncio
import json
import os
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

import app.tasks as tasks_module
from app.core.database import create_database_engine
from app.tasks import (
    IngestionTask,
    IngestionTaskOutboxEvent,
    claim_ingestion_task,
    claim_next_ingestion_event,
    claim_next_ingestion_task,
    create_ingestion_task,
    fail_ingestion_task,
    get_ingestion_task,
    get_next_unpublished_ingestion_event,
    mark_ingestion_event_published,
    publish_next_ingestion_event_once,
    reclaim_stale_ingestion_events,
    reclaim_stale_ingestion_task,
    retry_ingestion_task,
    run_ingestion_task_once,
    run_next_ingestion_task_once,
    succeed_ingestion_task,
)


class FakeTaskMappings:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def one(self) -> dict[str, object]:
        assert self._row is not None
        return self._row

    def one_or_none(self) -> dict[str, object] | None:
        return self._row


class FakeTaskResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row
        self.rowcount = 1 if row is not None else 0

    def mappings(self) -> FakeTaskMappings:
        return FakeTaskMappings(self._row)


class FakeTaskConnection:
    def __init__(
        self,
        row: dict[str, object] | None,
        error: RuntimeError | None = None,
        error_on_call: int | None = None,
    ) -> None:
        self._row = row
        self._error = error
        self._error_on_call = error_on_call
        self._execute_calls = 0
        self.execution: tuple[TextClause, dict[str, object] | None] | None = None
        self.executions: list[tuple[TextClause, dict[str, object] | None]] = []

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object] | None = None,
    ) -> FakeTaskResult:
        self._execute_calls += 1
        self.execution = (statement, parameters)
        self.executions.append((statement, parameters))
        if self._error is not None and (
            self._error_on_call is None or self._execute_calls == self._error_on_call
        ):
            raise self._error
        return FakeTaskResult(self._row)


class FakeTaskTransaction:
    def __init__(self, connection: FakeTaskConnection) -> None:
        self._connection = connection
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> FakeTaskConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_exception_type = exc_type


class FakeTaskEngine:
    def __init__(
        self,
        error: RuntimeError | None = None,
        *,
        row: dict[str, object] | None = None,
        has_row: bool = True,
        error_on_call: int | None = None,
    ) -> None:
        self.created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
        returned_row = (
            row
            if row is not None
            else {
                "id": "task-demo-001",
                "status": "PENDING",
                "created_at": self.created_at,
                "started_at": None,
            }
        )
        returned_row.setdefault("retry_count", 0)
        returned_row.setdefault("attempt_id", None)
        self.connection = FakeTaskConnection(
            returned_row if has_row else None,
            error,
            error_on_call,
        )
        self.transaction = FakeTaskTransaction(self.connection)
        self.begin_calls = 0

    def begin(self) -> FakeTaskTransaction:
        self.begin_calls += 1
        return self.transaction


@pytest.mark.asyncio
async def test_create_ingestion_task_inserts_only_task_id_in_transaction() -> None:
    engine = FakeTaskEngine()

    task = await create_ingestion_task(cast(AsyncEngine, engine), "task-demo-001")

    assert task == IngestionTask(
        id="task-demo-001",
        status="PENDING",
        created_at=engine.created_at,
        started_at=None,
        retry_count=0,
    )
    assert engine.begin_calls == 1
    assert engine.transaction.exit_exception_type is None
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "INSERT INTO agent_core.ingestion_tasks (id)" in str(statement)
    assert "VALUES (:task_id)" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count" in str(statement)
    assert parameters == {"task_id": "task-demo-001"}


@pytest.mark.asyncio
async def test_create_ingestion_task_propagates_database_error() -> None:
    database_error = RuntimeError("duplicate task")
    engine = FakeTaskEngine(database_error)

    with pytest.raises(RuntimeError, match="duplicate task"):
        await create_ingestion_task(cast(AsyncEngine, engine), "task-demo-001")

    assert engine.transaction.exit_exception_type is RuntimeError


@pytest.mark.asyncio
async def test_get_ingestion_task_reads_full_current_snapshot() -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    started_at = datetime(2026, 9, 2, 9, 31, tzinfo=UTC)
    attempt_id = UUID("12121212-1212-1212-1212-121212121212")
    engine = FakeTaskEngine(
        row={
            "id": "task-running-001",
            "status": "RUNNING",
            "created_at": created_at,
            "started_at": started_at,
            "retry_count": 2,
            "attempt_id": attempt_id,
        }
    )

    task = await get_ingestion_task(
        cast(AsyncEngine, engine),
        "task-running-001",
    )

    assert task == IngestionTask(
        id="task-running-001",
        status="RUNNING",
        created_at=created_at,
        started_at=started_at,
        retry_count=2,
        attempt_id=attempt_id,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "SELECT id, status, created_at, started_at, retry_count,attempt_id" in str(
        statement
    )
    assert "FROM agent_core.ingestion_tasks" in str(statement)
    assert "WHERE id = :task_id" in str(statement)
    assert parameters == {"task_id": "task-running-001"}


@pytest.mark.asyncio
async def test_get_ingestion_task_returns_none_when_task_does_not_exist() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await get_ingestion_task(cast(AsyncEngine, engine), "task-missing")

    assert task is None
    assert engine.connection.execution is not None
    _, parameters = engine.connection.execution
    assert parameters == {"task_id": "task-missing"}


@pytest.mark.asyncio
async def test_get_next_unpublished_ingestion_event_reads_oldest_event() -> None:
    event_id = UUID("56565656-5656-5656-5656-565656565656")
    attempt_id = UUID("67676767-6767-6767-6767-676767676767")
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    engine = FakeTaskEngine(
        row={
            "id": event_id,
            "task_id": "task-outbox-001",
            "attempt_id": attempt_id,
            "event_type": "ingestion_task.succeeded",
            "payload": {"status": "SUCCEEDED"},
            "created_at": created_at,
            "published_at": None,
        }
    )

    event = await get_next_unpublished_ingestion_event(cast(AsyncEngine, engine))

    assert event == IngestionTaskOutboxEvent(
        id=event_id,
        task_id="task-outbox-001",
        attempt_id=attempt_id,
        event_type="ingestion_task.succeeded",
        payload={"status": "SUCCEEDED"},
        created_at=created_at,
        published_at=None,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = str(statement)
    assert "FROM agent_core.ingestion_task_outbox" in sql
    assert "WHERE published_at IS NULL" in sql
    assert "ORDER BY created_at ASC, id ASC" in sql
    assert "LIMIT 1" in sql
    assert parameters is None


@pytest.mark.asyncio
async def test_get_next_unpublished_ingestion_event_returns_none_when_empty() -> None:
    engine = FakeTaskEngine(has_row=False)

    event = await get_next_unpublished_ingestion_event(cast(AsyncEngine, engine))

    assert event is None


@pytest.mark.asyncio
async def test_claim_next_ingestion_event_sets_publish_lease() -> None:
    event_id = UUID("94949494-9494-9494-9494-949494949494")
    task_attempt_id = UUID("95959595-9595-9595-9595-959595959595")
    publish_attempt_id = UUID("96969696-9696-9696-9696-969696969696")
    publishing_at = datetime(2026, 9, 2, 9, 32, tzinfo=UTC)
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    engine = FakeTaskEngine(
        row={
            "id": event_id,
            "task_id": "task-outbox-claim-001",
            "attempt_id": task_attempt_id,
            "event_type": "ingestion_task.succeeded",
            "payload": {"status": "SUCCEEDED"},
            "created_at": created_at,
            "published_at": None,
            "publish_attempt_id": publish_attempt_id,
            "publishing_at": publishing_at,
        }
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tasks_module, "uuid4", lambda: publish_attempt_id)
    try:
        event = await claim_next_ingestion_event(cast(AsyncEngine, engine))
    finally:
        monkeypatch.undo()

    assert event == IngestionTaskOutboxEvent(
        id=event_id,
        task_id="task-outbox-claim-001",
        attempt_id=task_attempt_id,
        event_type="ingestion_task.succeeded",
        payload={"status": "SUCCEEDED"},
        created_at=created_at,
        published_at=None,
        publish_attempt_id=publish_attempt_id,
        publishing_at=publishing_at,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = str(statement)
    assert "published_at IS NULL" in sql
    assert "publish_attempt_id IS NULL" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "publish_attempt_id = :publish_attempt_id" in sql
    assert "publishing_at = CURRENT_TIMESTAMP" in sql
    assert "event.publish_attempt_id" in sql
    assert "event.publishing_at" in sql
    assert parameters == {"publish_attempt_id": publish_attempt_id}


@pytest.mark.asyncio
async def test_claim_next_ingestion_event_returns_none_when_no_eligible_event() -> None:
    engine = FakeTaskEngine(has_row=False)

    event = await claim_next_ingestion_event(cast(AsyncEngine, engine))

    assert event is None


@pytest.mark.asyncio
async def test_mark_ingestion_event_published_updates_only_unpublished_event() -> None:
    event_id = UUID("78787878-7878-7878-7878-787878787878")
    publish_attempt_id = UUID("79797979-7979-7979-7979-797979797979")
    engine = FakeTaskEngine(row={"id": event_id})

    published = await mark_ingestion_event_published(
        cast(AsyncEngine, engine),
        event_id,
        publish_attempt_id=publish_attempt_id,
    )

    assert published is True
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "UPDATE agent_core.ingestion_task_outbox" in str(statement)
    assert "published_at = CURRENT_TIMESTAMP" in str(statement)
    assert "publish_attempt_id = NULL" in str(statement)
    assert "publishing_at = NULL" in str(statement)
    assert "WHERE id = :event_id" in str(statement)
    assert "AND published_at IS NULL" in str(statement)
    assert "AND publish_attempt_id = :publish_attempt_id" in str(statement)
    assert "RETURNING id" in str(statement)
    assert parameters == {
        "event_id": event_id,
        "publish_attempt_id": publish_attempt_id,
    }


@pytest.mark.asyncio
async def test_mark_ingestion_event_published_returns_false_when_no_row_matches() -> None:
    event_id = UUID("89898989-8989-8989-8989-898989898989")
    publish_attempt_id = UUID("89898989-8989-8989-8989-898989898998")
    engine = FakeTaskEngine(has_row=False)

    published = await mark_ingestion_event_published(
        cast(AsyncEngine, engine),
        event_id,
        publish_attempt_id=publish_attempt_id,
    )

    assert published is False


@pytest.mark.asyncio
async def test_publish_next_ingestion_event_once_publishes_then_marks() -> None:
    stale_before = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    publish_attempt_id = UUID("94949494-9494-9494-9494-949494949494")
    event = IngestionTaskOutboxEvent(
        id=UUID("90909090-9090-9090-9090-909090909090"),
        task_id="task-outbox-001",
        attempt_id=UUID("91919191-9191-9191-9191-919191919191"),
        event_type="ingestion_task.succeeded",
        payload={"status": "SUCCEEDED"},
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        published_at=None,
        publish_attempt_id=publish_attempt_id,
    )
    engine = object()
    claim_next = AsyncMock(return_value=event)
    reclaim = AsyncMock()
    mark = AsyncMock(return_value=True)
    publish = AsyncMock()
    original_claim_next = tasks_module.claim_next_ingestion_event
    original_reclaim = tasks_module.reclaim_stale_ingestion_events
    original_mark = tasks_module.mark_ingestion_event_published
    tasks_module.claim_next_ingestion_event = claim_next
    tasks_module.reclaim_stale_ingestion_events = reclaim
    tasks_module.mark_ingestion_event_published = mark
    try:
        result = await publish_next_ingestion_event_once(
            cast(AsyncEngine, engine),
            publish,
            stale_before=stale_before,
        )
    finally:
        tasks_module.claim_next_ingestion_event = original_claim_next
        tasks_module.reclaim_stale_ingestion_events = original_reclaim
        tasks_module.mark_ingestion_event_published = original_mark

    assert result is True
    reclaim.assert_awaited_once_with(engine, stale_before=stale_before)
    claim_next.assert_awaited_once_with(engine)
    publish.assert_awaited_once_with(event)
    mark.assert_awaited_once_with(
        engine,
        event.id,
        publish_attempt_id=publish_attempt_id,
    )


@pytest.mark.asyncio
async def test_publish_next_ingestion_event_once_returns_false_for_empty_queue() -> None:
    stale_before = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    engine = object()
    claim_next = AsyncMock(return_value=None)
    reclaim = AsyncMock()
    mark = AsyncMock()
    publish = AsyncMock()
    original_claim_next = tasks_module.claim_next_ingestion_event
    original_reclaim = tasks_module.reclaim_stale_ingestion_events
    original_mark = tasks_module.mark_ingestion_event_published
    tasks_module.claim_next_ingestion_event = claim_next
    tasks_module.reclaim_stale_ingestion_events = reclaim
    tasks_module.mark_ingestion_event_published = mark
    try:
        result = await publish_next_ingestion_event_once(
            cast(AsyncEngine, engine),
            publish,
            stale_before=stale_before,
        )
    finally:
        tasks_module.claim_next_ingestion_event = original_claim_next
        tasks_module.reclaim_stale_ingestion_events = original_reclaim
        tasks_module.mark_ingestion_event_published = original_mark

    assert result is False
    reclaim.assert_awaited_once_with(engine, stale_before=stale_before)
    publish.assert_not_awaited()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_next_ingestion_event_once_leaves_event_unmarked_on_failure() -> None:
    stale_before = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    publish_attempt_id = UUID("94949494-9494-9494-9494-949494949495")
    event = IngestionTaskOutboxEvent(
        id=UUID("92929292-9292-9292-9292-929292929292"),
        task_id="task-outbox-002",
        attempt_id=UUID("93939393-9393-9393-9393-939393939393"),
        event_type="ingestion_task.failed",
        payload={"status": "FAILED"},
        created_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        published_at=None,
        publish_attempt_id=publish_attempt_id,
    )
    engine = object()
    claim_next = AsyncMock(return_value=event)
    reclaim = AsyncMock()
    mark = AsyncMock()
    publish = AsyncMock(side_effect=RuntimeError("broker unavailable"))
    original_claim_next = tasks_module.claim_next_ingestion_event
    original_reclaim = tasks_module.reclaim_stale_ingestion_events
    original_mark = tasks_module.mark_ingestion_event_published
    tasks_module.claim_next_ingestion_event = claim_next
    tasks_module.reclaim_stale_ingestion_events = reclaim
    tasks_module.mark_ingestion_event_published = mark
    try:
        with pytest.raises(RuntimeError, match="broker unavailable"):
            await publish_next_ingestion_event_once(
                cast(AsyncEngine, engine),
                publish,
                stale_before=stale_before,
            )
    finally:
        tasks_module.claim_next_ingestion_event = original_claim_next
        tasks_module.reclaim_stale_ingestion_events = original_reclaim
        tasks_module.mark_ingestion_event_published = original_mark

    reclaim.assert_awaited_once_with(engine, stale_before=stale_before)
    publish.assert_awaited_once_with(event)
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_stale_ingestion_events_releases_expired_lease() -> None:
    stale_before = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    engine = FakeTaskEngine(row={"id": UUID("95959595-9595-9595-9595-959595959595")})

    reclaimed = await reclaim_stale_ingestion_events(
        cast(AsyncEngine, engine),
        stale_before=stale_before,
    )

    assert reclaimed == 1
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = str(statement)
    assert "published_at IS NULL" in sql
    assert "publish_attempt_id IS NOT NULL" in sql
    assert "publishing_at < :stale_before" in sql
    assert "publish_attempt_id = NULL" in sql
    assert "publishing_at = NULL" in sql
    assert parameters == {"stale_before": stale_before}


@pytest.mark.asyncio
async def test_reclaim_stale_ingestion_events_returns_zero_when_no_lease_matches() -> None:
    engine = FakeTaskEngine(has_row=False)

    reclaimed = await reclaim_stale_ingestion_events(
        cast(AsyncEngine, engine),
        stale_before=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
    )

    assert reclaimed == 0


@pytest.mark.asyncio
async def test_reclaim_stale_ingestion_task_requeues_expired_running_task() -> None:
    stale_before = datetime(2026, 9, 2, 9, 45, tzinfo=UTC)
    created_at = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    engine = FakeTaskEngine(
        row={
            "id": "task-stale-001",
            "status": "PENDING",
            "created_at": created_at,
            "started_at": None,
            "retry_count": 2,
            "attempt_id": None,
        }
    )

    task = await reclaim_stale_ingestion_task(
        cast(AsyncEngine, engine),
        "task-stale-001",
        stale_before=stale_before,
    )

    assert task == IngestionTask(
        id="task-stale-001",
        status="PENDING",
        created_at=created_at,
        started_at=None,
        retry_count=2,
        attempt_id=None,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "status = 'PENDING'" in str(statement)
    assert "started_at = NULL" in str(statement)
    assert "attempt_id = NULL" in str(statement)
    assert "AND status = 'RUNNING'" in str(statement)
    assert "AND started_at < :stale_before" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count, attempt_id" in str(
        statement
    )
    assert parameters == {
        "task_id": "task-stale-001",
        "stale_before": stale_before,
    }


@pytest.mark.asyncio
async def test_reclaim_stale_ingestion_task_returns_none_when_update_does_not_match() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await reclaim_stale_ingestion_task(
        cast(AsyncEngine, engine),
        "task-fresh-001",
        stale_before=datetime(2026, 9, 2, 9, 45, tzinfo=UTC),
    )

    assert task is None


@pytest.mark.asyncio
async def test_claim_ingestion_task_atomically_changes_pending_to_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    started_at = datetime(2026, 9, 2, 9, 31, tzinfo=UTC)
    attempt_id = UUID("11111111-1111-1111-1111-111111111111")
    engine = FakeTaskEngine(
        row={
            "id": "task-demo-001",
            "status": "RUNNING",
            "created_at": created_at,
            "started_at": started_at,
            "attempt_id": attempt_id,
        }
    )
    monkeypatch.setattr(tasks_module, "uuid4", lambda: attempt_id)

    task = await claim_ingestion_task(cast(AsyncEngine, engine), "task-demo-001")

    assert task == IngestionTask(
        id="task-demo-001",
        status="RUNNING",
        created_at=created_at,
        started_at=started_at,
        retry_count=0,
        attempt_id=attempt_id,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "status = 'RUNNING'" in str(statement)
    assert "started_at = CURRENT_TIMESTAMP" in str(statement)
    assert "attempt_id = :attempt_id" in str(statement)
    assert "AND status = 'PENDING'" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count, attempt_id" in str(
        statement
    )
    assert parameters == {
        "task_id": "task-demo-001",
        "attempt_id": attempt_id,
    }


@pytest.mark.asyncio
async def test_claim_ingestion_task_returns_none_without_execution_right() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await claim_ingestion_task(cast(AsyncEngine, engine), "task-unavailable")

    assert task is None


@pytest.mark.asyncio
async def test_succeed_ingestion_task_atomically_changes_running_to_succeeded() -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    started_at = datetime(2026, 9, 2, 9, 31, tzinfo=UTC)
    attempt_id = UUID("22222222-2222-2222-2222-222222222222")
    engine = FakeTaskEngine(
        row={
            "id": "task-demo-001",
            "status": "SUCCEEDED",
            "created_at": created_at,
            "started_at": started_at,
            "attempt_id": attempt_id,
        }
    )

    task = await succeed_ingestion_task(
        cast(AsyncEngine, engine),
        "task-demo-001",
        attempt_id=attempt_id,
    )

    assert task == IngestionTask(
        id="task-demo-001",
        status="SUCCEEDED",
        created_at=created_at,
        started_at=started_at,
        retry_count=0,
        attempt_id=attempt_id,
    )
    assert len(engine.connection.executions) == 2
    statement, parameters = engine.connection.executions[0]
    assert "SET status = 'SUCCEEDED'" in str(statement)
    assert "AND status = 'RUNNING'" in str(statement)
    assert "AND attempt_id = :attempt_id" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count, attempt_id" in str(
        statement
    )
    assert parameters == {
        "task_id": "task-demo-001",
        "attempt_id": attempt_id,
    }
    outbox_statement, outbox_parameters = engine.connection.executions[1]
    assert "INSERT INTO agent_core.ingestion_task_outbox" in str(outbox_statement)
    assert "CAST(:payload AS jsonb)" in str(outbox_statement)
    assert outbox_parameters is not None
    assert outbox_parameters["task_id"] == "task-demo-001"
    assert outbox_parameters["attempt_id"] == attempt_id
    assert outbox_parameters["event_id"] is not None
    assert outbox_parameters["payload"] == json.dumps(
        {
            "task_id": "task-demo-001",
            "attempt_id": str(attempt_id),
            "status": "SUCCEEDED",
        }
    )
    assert engine.begin_calls == 1


@pytest.mark.asyncio
async def test_succeed_ingestion_task_rolls_back_when_outbox_insert_fails() -> None:
    attempt_id = UUID("23232323-2323-2323-2323-232323232323")
    engine = FakeTaskEngine(
        error=RuntimeError("outbox unavailable"),
        error_on_call=2,
        row={
            "id": "task-demo-001",
            "status": "SUCCEEDED",
            "created_at": datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
            "started_at": datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
            "attempt_id": attempt_id,
        },
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await succeed_ingestion_task(
            cast(AsyncEngine, engine),
            "task-demo-001",
            attempt_id=attempt_id,
        )

    assert engine.transaction.exit_exception_type is RuntimeError
    assert len(engine.connection.executions) == 2


@pytest.mark.asyncio
async def test_succeed_ingestion_task_returns_none_for_non_running_task() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await succeed_ingestion_task(
        cast(AsyncEngine, engine),
        "task-already-done",
        attempt_id=UUID("33333333-3333-3333-3333-333333333333"),
    )

    assert task is None


@pytest.mark.asyncio
async def test_fail_ingestion_task_atomically_changes_running_to_failed() -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    started_at = datetime(2026, 9, 2, 9, 31, tzinfo=UTC)
    attempt_id = UUID("44444444-4444-4444-4444-444444444444")
    engine = FakeTaskEngine(
        row={
            "id": "task-demo-001",
            "status": "FAILED",
            "created_at": created_at,
            "started_at": started_at,
            "attempt_id": attempt_id,
        }
    )

    task = await fail_ingestion_task(
        cast(AsyncEngine, engine),
        "task-demo-001",
        attempt_id=attempt_id,
    )

    assert task == IngestionTask(
        id="task-demo-001",
        status="FAILED",
        created_at=created_at,
        started_at=started_at,
        retry_count=0,
        attempt_id=attempt_id,
    )
    assert len(engine.connection.executions) == 2
    statement, parameters = engine.connection.executions[0]
    assert "SET status = 'FAILED'" in str(statement)
    assert "AND status = 'RUNNING'" in str(statement)
    assert "AND attempt_id = :attempt_id" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count, attempt_id" in str(
        statement
    )
    assert parameters == {
        "task_id": "task-demo-001",
        "attempt_id": attempt_id,
    }
    outbox_statement, outbox_parameters = engine.connection.executions[1]
    assert "INSERT INTO agent_core.ingestion_task_outbox" in str(outbox_statement)
    assert "'ingestion_task.failed'" in str(outbox_statement)
    assert outbox_parameters is not None
    assert outbox_parameters["task_id"] == "task-demo-001"
    assert outbox_parameters["attempt_id"] == attempt_id
    assert outbox_parameters["event_id"] is not None
    assert outbox_parameters["payload"] == json.dumps(
        {
            "task_id": "task-demo-001",
            "attempt_id": str(attempt_id),
            "status": "FAILED",
        }
    )
    assert engine.begin_calls == 1


@pytest.mark.asyncio
async def test_fail_ingestion_task_rolls_back_when_outbox_insert_fails() -> None:
    attempt_id = UUID("34343434-3434-3434-3434-343434343434")
    engine = FakeTaskEngine(
        error=RuntimeError("outbox unavailable"),
        error_on_call=2,
        row={
            "id": "task-demo-001",
            "status": "FAILED",
            "created_at": datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
            "started_at": datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
            "attempt_id": attempt_id,
        },
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await fail_ingestion_task(
            cast(AsyncEngine, engine),
            "task-demo-001",
            attempt_id=attempt_id,
        )

    assert engine.transaction.exit_exception_type is RuntimeError
    assert len(engine.connection.executions) == 2


@pytest.mark.asyncio
async def test_fail_ingestion_task_returns_none_for_non_running_task() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await fail_ingestion_task(
        cast(AsyncEngine, engine),
        "task-already-done",
        attempt_id=UUID("55555555-5555-5555-5555-555555555555"),
    )

    assert task is None


@pytest.mark.asyncio
async def test_retry_ingestion_task_requeues_failed_task_and_increments_count() -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    attempt_id = UUID("66666666-6666-6666-6666-666666666666")
    engine = FakeTaskEngine(
        row={
            "id": "task-demo-001",
            "status": "PENDING",
            "created_at": created_at,
            "started_at": None,
            "retry_count": 1,
            "attempt_id": None,
        }
    )

    task = await retry_ingestion_task(
        cast(AsyncEngine, engine),
        "task-demo-001",
        max_retries=3,
        attempt_id=attempt_id,
    )

    assert task == IngestionTask(
        id="task-demo-001",
        status="PENDING",
        created_at=created_at,
        started_at=None,
        retry_count=1,
        attempt_id=None,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    assert "status = 'PENDING'" in str(statement)
    assert "started_at = NULL" in str(statement)
    assert "attempt_id = NULL" in str(statement)
    assert "retry_count = retry_count + 1" in str(statement)
    assert "AND status = 'FAILED'" in str(statement)
    assert "AND attempt_id = :attempt_id" in str(statement)
    assert "RETURNING id, status, created_at, started_at, retry_count, attempt_id" in str(
        statement
    )
    assert parameters == {
        "task_id": "task-demo-001",
        "max_retries": 3,
        "attempt_id": attempt_id,
    }


@pytest.mark.asyncio
async def test_retry_ingestion_task_returns_none_for_non_failed_task() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await retry_ingestion_task(
        cast(AsyncEngine, engine),
        "task-not-failed",
        max_retries=3,
        attempt_id=UUID("77777777-7777-7777-7777-777777777777"),
    )

    assert task is None


@pytest.mark.asyncio
async def test_retry_ingestion_task_returns_none_at_retry_limit() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await retry_ingestion_task(
        cast(AsyncEngine, engine),
        "task-at-limit",
        max_retries=2,
        attempt_id=UUID("88888888-8888-8888-8888-888888888888"),
    )

    assert task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("max_retries", [0, -1])
async def test_retry_ingestion_task_rejects_non_positive_limit(max_retries: int) -> None:
    engine = FakeTaskEngine()

    with pytest.raises(ValueError, match="max_retries must be greater than zero"):
        await retry_ingestion_task(
            cast(AsyncEngine, engine),
            "task-demo-001",
            max_retries=max_retries,
            attempt_id=UUID("99999999-9999-9999-9999-999999999999"),
        )

    assert engine.begin_calls == 0


@pytest.mark.asyncio
async def test_claim_next_ingestion_task_claims_one_pending_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    started_at = datetime(2026, 9, 2, 9, 31, tzinfo=UTC)
    attempt_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    engine = FakeTaskEngine(
        row={
            "id": "task-next-demo-001",
            "status": "RUNNING",
            "created_at": created_at,
            "started_at": started_at,
            "retry_count": 0,
            "attempt_id": attempt_id,
        }
    )
    monkeypatch.setattr(tasks_module, "uuid4", lambda: attempt_id)

    task = await claim_next_ingestion_task(cast(AsyncEngine, engine))

    assert task == IngestionTask(
        id="task-next-demo-001",
        status="RUNNING",
        created_at=created_at,
        started_at=started_at,
        retry_count=0,
        attempt_id=attempt_id,
    )
    assert engine.connection.execution is not None
    statement, parameters = engine.connection.execution
    sql = str(statement)
    assert "WHERE status = 'PENDING'" in sql
    assert "ORDER BY created_at ASC, id ASC" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 1" in sql
    assert "started_at = CURRENT_TIMESTAMP" in sql
    assert "attempt_id = :attempt_id" in sql
    assert "task.attempt_id" in sql
    assert parameters == {"attempt_id": attempt_id}


@pytest.mark.asyncio
async def test_claim_next_ingestion_task_returns_none_for_empty_queue() -> None:
    engine = FakeTaskEngine(has_row=False)

    task = await claim_next_ingestion_task(cast(AsyncEngine, engine))

    assert task is None


@pytest.mark.asyncio
async def test_run_ingestion_task_once_runs_work_and_marks_success() -> None:
    engine = object()
    attempt_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    claimed = IngestionTask(
        id="task-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
        attempt_id=attempt_id,
    )
    succeeded = IngestionTask(
        id=claimed.id,
        status="SUCCEEDED",
        created_at=claimed.created_at,
        started_at=claimed.started_at,
        retry_count=claimed.retry_count,
        attempt_id=attempt_id,
    )
    claim = AsyncMock(return_value=claimed)
    succeed = AsyncMock(return_value=succeeded)
    fail = AsyncMock()
    work = AsyncMock()

    original_claim = tasks_module.claim_ingestion_task
    original_succeed = tasks_module.succeed_ingestion_task
    original_fail = tasks_module.fail_ingestion_task
    tasks_module.claim_ingestion_task = claim
    tasks_module.succeed_ingestion_task = succeed
    tasks_module.fail_ingestion_task = fail
    try:
        result = await run_ingestion_task_once(cast(AsyncEngine, engine), claimed.id, work)
    finally:
        tasks_module.claim_ingestion_task = original_claim
        tasks_module.succeed_ingestion_task = original_succeed
        tasks_module.fail_ingestion_task = original_fail

    assert result == succeeded
    work.assert_awaited_once_with()
    claim.assert_awaited_once_with(engine, claimed.id)
    succeed.assert_awaited_once_with(engine, claimed.id, attempt_id=attempt_id)
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_ingestion_task_once_marks_failure_when_work_raises() -> None:
    engine = object()
    attempt_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    claimed = IngestionTask(
        id="task-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
        attempt_id=attempt_id,
    )
    failed = IngestionTask(
        id=claimed.id,
        status="FAILED",
        created_at=claimed.created_at,
        started_at=claimed.started_at,
        retry_count=claimed.retry_count,
        attempt_id=attempt_id,
    )
    claim = AsyncMock(return_value=claimed)
    succeed = AsyncMock()
    fail = AsyncMock(return_value=failed)

    async def work() -> None:
        raise RuntimeError("embedding failed")

    original_claim = tasks_module.claim_ingestion_task
    original_succeed = tasks_module.succeed_ingestion_task
    original_fail = tasks_module.fail_ingestion_task
    tasks_module.claim_ingestion_task = claim
    tasks_module.succeed_ingestion_task = succeed
    tasks_module.fail_ingestion_task = fail
    try:
        result = await run_ingestion_task_once(cast(AsyncEngine, engine), claimed.id, work)
    finally:
        tasks_module.claim_ingestion_task = original_claim
        tasks_module.succeed_ingestion_task = original_succeed
        tasks_module.fail_ingestion_task = original_fail

    assert result == failed
    claim.assert_awaited_once_with(engine, claimed.id)
    fail.assert_awaited_once_with(engine, claimed.id, attempt_id=attempt_id)
    succeed.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_ingestion_task_once_skips_work_without_claim() -> None:
    claim = AsyncMock(return_value=None)
    work = AsyncMock()
    original_claim = tasks_module.claim_ingestion_task
    tasks_module.claim_ingestion_task = claim
    try:
        result = await run_ingestion_task_once(cast(AsyncEngine, object()), "missing-task", work)
    finally:
        tasks_module.claim_ingestion_task = original_claim

    assert result is None
    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_ingestion_task_once_rejects_claim_without_attempt_id() -> None:
    claimed = IngestionTask(
        id="task-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
    )
    claim = AsyncMock(return_value=claimed)
    work = AsyncMock()
    original_claim = tasks_module.claim_ingestion_task
    tasks_module.claim_ingestion_task = claim
    try:
        with pytest.raises(RuntimeError, match="missing attempt_id"):
            await run_ingestion_task_once(cast(AsyncEngine, object()), claimed.id, work)
    finally:
        tasks_module.claim_ingestion_task = original_claim

    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_ingestion_task_once_does_not_swallow_cancellation() -> None:
    attempt_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    claimed = IngestionTask(
        id="task-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
        attempt_id=attempt_id,
    )
    claim = AsyncMock(return_value=claimed)
    fail = AsyncMock()

    async def work() -> None:
        raise asyncio.CancelledError

    original_claim = tasks_module.claim_ingestion_task
    original_fail = tasks_module.fail_ingestion_task
    tasks_module.claim_ingestion_task = claim
    tasks_module.fail_ingestion_task = fail
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_ingestion_task_once(cast(AsyncEngine, object()), claimed.id, work)
    finally:
        tasks_module.claim_ingestion_task = original_claim
        tasks_module.fail_ingestion_task = original_fail

    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_next_ingestion_task_once_uses_claimed_snapshot_and_succeeds() -> None:
    engine = object()
    attempt_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    claimed = IngestionTask(
        id="task-next-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
        attempt_id=attempt_id,
    )
    succeeded = IngestionTask(
        id=claimed.id,
        status="SUCCEEDED",
        created_at=claimed.created_at,
        started_at=claimed.started_at,
        retry_count=claimed.retry_count,
        attempt_id=attempt_id,
    )
    claim_next = AsyncMock(return_value=claimed)
    succeed = AsyncMock(return_value=succeeded)
    fail = AsyncMock()
    work = AsyncMock()
    original_claim_next = tasks_module.claim_next_ingestion_task
    original_succeed = tasks_module.succeed_ingestion_task
    original_fail = tasks_module.fail_ingestion_task
    tasks_module.claim_next_ingestion_task = claim_next
    tasks_module.succeed_ingestion_task = succeed
    tasks_module.fail_ingestion_task = fail
    try:
        result = await run_next_ingestion_task_once(cast(AsyncEngine, engine), work)
    finally:
        tasks_module.claim_next_ingestion_task = original_claim_next
        tasks_module.succeed_ingestion_task = original_succeed
        tasks_module.fail_ingestion_task = original_fail

    assert result == succeeded
    claim_next.assert_awaited_once()
    work.assert_awaited_once_with(claimed)
    succeed.assert_awaited_once_with(engine, claimed.id, attempt_id=attempt_id)
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_next_ingestion_task_once_marks_claimed_task_failed() -> None:
    engine = object()
    attempt_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    claimed = IngestionTask(
        id="task-next-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
        attempt_id=attempt_id,
    )
    failed = IngestionTask(
        id=claimed.id,
        status="FAILED",
        created_at=claimed.created_at,
        started_at=claimed.started_at,
        retry_count=claimed.retry_count,
        attempt_id=attempt_id,
    )
    claim_next = AsyncMock(return_value=claimed)
    fail = AsyncMock(return_value=failed)

    async def work(task: IngestionTask) -> None:
        assert task == claimed
        raise RuntimeError("document processing failed")

    original_claim_next = tasks_module.claim_next_ingestion_task
    original_fail = tasks_module.fail_ingestion_task
    tasks_module.claim_next_ingestion_task = claim_next
    tasks_module.fail_ingestion_task = fail
    try:
        result = await run_next_ingestion_task_once(cast(AsyncEngine, engine), work)
    finally:
        tasks_module.claim_next_ingestion_task = original_claim_next
        tasks_module.fail_ingestion_task = original_fail

    assert result == failed
    fail.assert_awaited_once_with(engine, claimed.id, attempt_id=attempt_id)


@pytest.mark.asyncio
async def test_run_next_ingestion_task_once_skips_work_when_queue_is_empty() -> None:
    engine = object()
    claim_next = AsyncMock(return_value=None)
    work = AsyncMock()
    original_claim_next = tasks_module.claim_next_ingestion_task
    tasks_module.claim_next_ingestion_task = claim_next
    try:
        result = await run_next_ingestion_task_once(cast(AsyncEngine, engine), work)
    finally:
        tasks_module.claim_next_ingestion_task = original_claim_next

    assert result is None
    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_next_ingestion_task_once_rejects_claim_without_attempt_id() -> None:
    claimed = IngestionTask(
        id="task-next-demo-001",
        status="RUNNING",
        created_at=datetime(2026, 9, 2, 9, 30, tzinfo=UTC),
        started_at=datetime(2026, 9, 2, 9, 31, tzinfo=UTC),
        retry_count=0,
    )
    claim_next = AsyncMock(return_value=claimed)
    work = AsyncMock()
    original_claim_next = tasks_module.claim_next_ingestion_task
    tasks_module.claim_next_ingestion_task = claim_next
    try:
        with pytest.raises(RuntimeError, match="missing attempt_id"):
            await run_next_ingestion_task_once(cast(AsyncEngine, object()), work)
    finally:
        tasks_module.claim_next_ingestion_task = original_claim_next

    work.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_ingestion_task_uses_database_default_and_rejects_duplicate() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"ingestion-task-test-{uuid4()}"

    try:
        task = await create_ingestion_task(engine, task_id)

        assert task.id == task_id
        assert task.status == "PENDING"
        assert task.created_at is not None
        assert task.started_at is None
        assert task.retry_count == 0

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, status, created_at, started_at, retry_count
                    FROM agent_core.ingestion_tasks
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
            row = result.mappings().one()

        assert row["id"] == task_id
        assert row["status"] == "PENDING"
        assert row["created_at"] is not None
        assert row["started_at"] is None
        assert row["retry_count"] == 0

        with pytest.raises(IntegrityError):
            await create_ingestion_task(engine, task_id)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_only_one_concurrent_claimant_gets_execution_right() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"ingestion-claim-test-{uuid4()}"
    failed_task_id = f"ingestion-fail-test-{uuid4()}"
    worker_task_id = f"ingestion-worker-test-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)

        claims = await asyncio.gather(
            claim_ingestion_task(engine, task_id),
            claim_ingestion_task(engine, task_id),
        )

        claimed = [task for task in claims if task is not None]
        assert len(claimed) == 1
        assert claimed[0].id == task_id
        assert claimed[0].status == "RUNNING"
        assert claimed[0].started_at is not None
        assert claimed[0].retry_count == 0
        assert claimed[0].attempt_id is not None
        assert await claim_ingestion_task(engine, "missing-task") is None

        completed = await succeed_ingestion_task(
            engine,
            task_id,
            attempt_id=claimed[0].attempt_id,
        )
        assert completed is not None
        assert completed.status == "SUCCEEDED"
        assert completed.started_at == claimed[0].started_at
        assert completed.retry_count == 0
        assert (
            await succeed_ingestion_task(
                engine,
                task_id,
                attempt_id=claimed[0].attempt_id,
            )
            is None
        )

        await create_ingestion_task(engine, failed_task_id)
        running_failed = await claim_ingestion_task(engine, failed_task_id)
        assert running_failed is not None
        assert running_failed.attempt_id is not None
        failed = await fail_ingestion_task(
            engine,
            failed_task_id,
            attempt_id=running_failed.attempt_id,
        )
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.started_at == running_failed.started_at
        assert failed.retry_count == 0
        assert (
            await fail_ingestion_task(
                engine,
                failed_task_id,
                attempt_id=running_failed.attempt_id,
            )
            is None
        )

        retry_results = await asyncio.gather(
            retry_ingestion_task(
                engine,
                failed_task_id,
                max_retries=3,
                attempt_id=running_failed.attempt_id,
            ),
            retry_ingestion_task(
                engine,
                failed_task_id,
                max_retries=3,
                attempt_id=running_failed.attempt_id,
            ),
        )
        retried = [task for task in retry_results if task is not None]
        assert len(retried) == 1
        assert retried[0].status == "PENDING"
        assert retried[0].started_at is None
        assert retried[0].retry_count == 1

        retry_claim = await claim_ingestion_task(engine, failed_task_id)
        assert retry_claim is not None
        assert retry_claim.status == "RUNNING"
        assert retry_claim.started_at is not None
        assert retry_claim.retry_count == 1

        await create_ingestion_task(engine, worker_task_id)
        work_calls: list[str] = []

        async def work() -> None:
            work_calls.append(worker_task_id)
            await asyncio.sleep(0.01)

        worker_results = await asyncio.gather(
            run_ingestion_task_once(engine, worker_task_id, work),
            run_ingestion_task_once(engine, worker_task_id, work),
        )
        completed_workers = [task for task in worker_results if task is not None]
        assert len(completed_workers) == 1
        assert completed_workers[0].status == "SUCCEEDED"
        assert work_calls == [worker_task_id]
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id IN (:task_id, :failed_task_id, :worker_task_id)"
                ),
                {
                    "task_id": task_id,
                    "failed_task_id": failed_task_id,
                    "worker_task_id": worker_task_id,
                },
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks "
                    "WHERE id IN (:task_id, :failed_task_id, :worker_task_id)"
                ),
                {
                    "task_id": task_id,
                    "failed_task_id": failed_task_id,
                    "worker_task_id": worker_task_id,
                },
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_reclaimers_requeue_stale_task_once() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"ingestion-reclaim-test-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)
        claimed = await claim_ingestion_task(engine, task_id)
        assert claimed is not None
        assert claimed.started_at is not None

        reclaim_results = await asyncio.gather(
            reclaim_stale_ingestion_task(
                engine,
                task_id,
                stale_before=claimed.started_at,
            ),
            reclaim_stale_ingestion_task(
                engine,
                task_id,
                stale_before=claimed.started_at,
            ),
        )
        assert all(task is None for task in reclaim_results)

        stale_before = datetime.now(UTC)
        reclaim_results = await asyncio.gather(
            reclaim_stale_ingestion_task(
                engine,
                task_id,
                stale_before=stale_before,
            ),
            reclaim_stale_ingestion_task(
                engine,
                task_id,
                stale_before=stale_before,
            ),
        )
        reclaimed = [task for task in reclaim_results if task is not None]
        assert len(reclaimed) == 1
        assert reclaimed[0].status == "PENDING"
        assert reclaimed[0].started_at is None
        assert reclaimed[0].retry_count == claimed.retry_count
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reclaim_and_complete_race_allows_only_one_state_transition() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"reclaim-race-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)
        claimed = await claim_ingestion_task(engine, task_id)
        assert claimed is not None
        assert claimed.started_at is not None
        assert claimed.attempt_id is not None

        transition_results = await asyncio.gather(
            reclaim_stale_ingestion_task(
                engine,
                task_id,
                stale_before=datetime.now(UTC),
            ),
            succeed_ingestion_task(
                engine,
                task_id,
                attempt_id=claimed.attempt_id,
            ),
        )
        transitioned = [task for task in transition_results if task is not None]
        assert len(transitioned) == 1
        assert transitioned[0].status in {"PENDING", "SUCCEEDED"}

        current = await get_ingestion_task(engine, task_id)
        assert current is not None
        assert current.status == transitioned[0].status
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_old_attempt_cannot_complete_after_task_is_reclaimed() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"attempt-fence-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)
        first_claim = await claim_ingestion_task(engine, task_id)
        assert first_claim is not None
        assert first_claim.attempt_id is not None

        reclaimed = await reclaim_stale_ingestion_task(
            engine,
            task_id,
            stale_before=datetime.now(UTC),
        )
        assert reclaimed is not None
        assert reclaimed.status == "PENDING"
        assert reclaimed.attempt_id is None

        second_claim = await claim_ingestion_task(engine, task_id)
        assert second_claim is not None
        assert second_claim.attempt_id is not None
        assert second_claim.attempt_id != first_claim.attempt_id

        late_completion = await succeed_ingestion_task(
            engine,
            task_id,
            attempt_id=first_claim.attempt_id,
        )
        assert late_completion is None

        current = await get_ingestion_task(engine, task_id)
        assert current is not None
        assert current.status == "RUNNING"
        assert current.attempt_id == second_claim.attempt_id

        completed = await succeed_ingestion_task(
            engine,
            task_id,
            attempt_id=second_claim.attempt_id,
        )
        assert completed is not None
        assert completed.status == "SUCCEEDED"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_succeed_ingestion_task_writes_one_outbox_event_idempotently() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"outbox-success-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)
        claimed = await claim_ingestion_task(engine, task_id)
        assert claimed is not None
        assert claimed.attempt_id is not None

        completed = await succeed_ingestion_task(
            engine,
            task_id,
            attempt_id=claimed.attempt_id,
        )
        assert completed is not None
        assert completed.status == "SUCCEEDED"

        duplicate = await succeed_ingestion_task(
            engine,
            task_id,
            attempt_id=claimed.attempt_id,
        )
        assert duplicate is None

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT task_id, attempt_id, event_type, payload, published_at "
                    "FROM agent_core.ingestion_task_outbox WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            rows = result.mappings().all()

        assert len(rows) == 1
        assert rows[0]["task_id"] == task_id
        assert rows[0]["attempt_id"] == claimed.attempt_id
        assert rows[0]["event_type"] == "ingestion_task.succeeded"
        assert rows[0]["payload"]["status"] == "SUCCEEDED"
        assert rows[0]["published_at"] is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fail_ingestion_task_writes_one_outbox_event_idempotently() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_id = f"outbox-failed-{uuid4()}"

    try:
        await create_ingestion_task(engine, task_id)
        claimed = await claim_ingestion_task(engine, task_id)
        assert claimed is not None
        assert claimed.attempt_id is not None

        failed = await fail_ingestion_task(
            engine,
            task_id,
            attempt_id=claimed.attempt_id,
        )
        assert failed is not None
        assert failed.status == "FAILED"

        duplicate = await fail_ingestion_task(
            engine,
            task_id,
            attempt_id=claimed.attempt_id,
        )
        assert duplicate is None

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT task_id, attempt_id, event_type, payload, published_at "
                    "FROM agent_core.ingestion_task_outbox WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            rows = result.mappings().all()

        assert len(rows) == 1
        assert rows[0]["task_id"] == task_id
        assert rows[0]["attempt_id"] == claimed.attempt_id
        assert rows[0]["event_type"] == "ingestion_task.failed"
        assert rows[0]["payload"]["status"] == "FAILED"
        assert rows[0]["published_at"] is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_next_unpublished_event_ignores_published_rows() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    published_id = uuid4()
    pending_id = uuid4()
    published_attempt_id = uuid4()
    pending_attempt_id = uuid4()

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO agent_core.ingestion_task_outbox "
                    "(id, task_id, attempt_id, event_type, payload, published_at) "
                    "VALUES (:id, :task_id, :attempt_id, :event_type, "
                    "CAST(:payload AS jsonb), CURRENT_TIMESTAMP)"
                ),
                {
                    "id": published_id,
                    "task_id": f"published-{published_id}",
                    "attempt_id": published_attempt_id,
                    "event_type": "ingestion_task.succeeded",
                    "payload": '{"status": "SUCCEEDED"}',
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_core.ingestion_task_outbox "
                    "(id, task_id, attempt_id, event_type, payload) "
                    "VALUES (:id, :task_id, :attempt_id, :event_type, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "id": pending_id,
                    "task_id": f"pending-{pending_id}",
                    "attempt_id": pending_attempt_id,
                    "event_type": "ingestion_task.failed",
                    "payload": '{"status": "FAILED"}',
                },
            )

        event = await get_next_unpublished_ingestion_event(engine)

        assert event is not None
        assert event.id == pending_id
        assert event.published_at is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE id IN (:published_id, :pending_id)"
                ),
                {"published_id": published_id, "pending_id": pending_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_publish_markers_update_event_once() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    event_id = uuid4()
    attempt_id = uuid4()

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO agent_core.ingestion_task_outbox "
                    "(id, task_id, attempt_id, event_type, payload) "
                    "VALUES (:id, :task_id, :attempt_id, :event_type, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "id": event_id,
                    "task_id": f"publish-mark-{event_id}",
                    "attempt_id": attempt_id,
                    "event_type": "ingestion_task.succeeded",
                    "payload": '{"status": "SUCCEEDED"}',
                },
            )

        claimed = await claim_next_ingestion_event(engine)
        assert claimed is not None
        assert claimed.publish_attempt_id is not None

        results = await asyncio.gather(
            mark_ingestion_event_published(
                engine,
                event_id,
                publish_attempt_id=claimed.publish_attempt_id,
            ),
            mark_ingestion_event_published(
                engine,
                event_id,
                publish_attempt_id=uuid4(),
            ),
        )

        assert sorted(results) == [False, True]
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT published_at FROM agent_core.ingestion_task_outbox "
                    "WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
            assert result.scalar_one() is not None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reclaim_stale_ingestion_events_preserves_published_and_fresh_events() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    stale_id = uuid4()
    fresh_id = uuid4()
    published_id = uuid4()
    stale_before = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    stale_at = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    fresh_at = datetime(2026, 9, 2, 10, 1, tzinfo=UTC)
    published_at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    rows = (
        (stale_id, stale_at, None),
        (fresh_id, fresh_at, None),
        (published_id, stale_at, published_at),
    )

    try:
        async with engine.begin() as connection:
            for event_id, publishing_at, completed_at in rows:
                await connection.execute(
                    text(
                        "INSERT INTO agent_core.ingestion_task_outbox "
                        "(id, task_id, attempt_id, event_type, payload, "
                        "published_at, publish_attempt_id, publishing_at) "
                        "VALUES (:id, :task_id, :attempt_id, :event_type, "
                        "CAST(:payload AS jsonb), :published_at, "
                        ":publish_attempt_id, :publishing_at)"
                    ),
                    {
                        "id": event_id,
                        "task_id": f"reclaim-{event_id}",
                        "attempt_id": uuid4(),
                        "event_type": "ingestion_task.succeeded",
                        "payload": '{"status": "SUCCEEDED"}',
                        "published_at": completed_at,
                        "publish_attempt_id": uuid4(),
                        "publishing_at": publishing_at,
                    },
                )

        assert (
            await reclaim_stale_ingestion_events(
                engine,
                stale_before=stale_before,
            )
            == 1
        )

        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT id, published_at, publish_attempt_id, publishing_at "
                    "FROM agent_core.ingestion_task_outbox "
                    "WHERE id IN (:stale_id, :fresh_id, :published_id)"
                ),
                {
                    "stale_id": stale_id,
                    "fresh_id": fresh_id,
                    "published_id": published_id,
                },
            )
            snapshots = {row["id"]: row for row in result.mappings()}

        assert snapshots[stale_id]["published_at"] is None
        assert snapshots[stale_id]["publish_attempt_id"] is None
        assert snapshots[stale_id]["publishing_at"] is None
        assert snapshots[fresh_id]["publish_attempt_id"] is not None
        assert snapshots[fresh_id]["publishing_at"] == fresh_at
        assert snapshots[published_id]["published_at"] == published_at
        assert snapshots[published_id]["publish_attempt_id"] is not None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE id IN (:stale_id, :fresh_id, :published_id)"
                ),
                {
                    "stale_id": stale_id,
                    "fresh_id": fresh_id,
                    "published_id": published_id,
                },
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_event_claimers_get_distinct_publish_lease() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    event_id = uuid4()
    task_id = f"event-claim-{event_id}"
    attempt_id = uuid4()

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO agent_core.ingestion_task_outbox "
                    "(id, task_id, attempt_id, event_type, payload) "
                    "VALUES (:id, :task_id, :attempt_id, :event_type, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "id": event_id,
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "event_type": "ingestion_task.succeeded",
                    "payload": '{"status": "SUCCEEDED"}',
                },
            )

        results = await asyncio.gather(
            claim_next_ingestion_event(engine),
            claim_next_ingestion_event(engine),
        )
        claimed = [event for event in results if event is not None]

        assert len(claimed) == 1
        assert claimed[0].id == event_id
        assert claimed[0].publish_attempt_id is not None
        assert claimed[0].publishing_at is not None
        assert await claim_next_ingestion_event(engine) is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_next_claims_get_distinct_pending_tasks() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_prefix = f"ingestion-next-test-{uuid4()}-"
    task_ids = [f"{task_prefix}{index}" for index in range(3)]

    try:
        for task_id in task_ids:
            await create_ingestion_task(engine, task_id)

        claims = await asyncio.gather(
            claim_next_ingestion_task(engine),
            claim_next_ingestion_task(engine),
            claim_next_ingestion_task(engine),
            claim_next_ingestion_task(engine),
        )
        claimed = [task for task in claims if task is not None]

        assert len(claimed) == 3
        assert {task.id for task in claimed} == set(task_ids)
        assert all(task.status == "RUNNING" for task in claimed)
        assert all(task.started_at is not None for task in claimed)
        assert await claim_next_ingestion_task(engine) is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id LIKE :task_prefix"
                ),
                {"task_prefix": f"{task_prefix}%"},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks "
                    "WHERE id LIKE :task_prefix"
                ),
                {"task_prefix": f"{task_prefix}%"},
            )
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_next_workers_process_distinct_tasks() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured.")

    engine = create_database_engine(database_url)
    task_prefix = f"ingestion-run-next-test-{uuid4()}-"
    task_ids = [f"{task_prefix}{index}" for index in range(2)]
    work_calls: list[str] = []

    async def work(task: IngestionTask) -> None:
        work_calls.append(task.id)
        await asyncio.sleep(0.01)

    try:
        for task_id in task_ids:
            await create_ingestion_task(engine, task_id)

        results = await asyncio.gather(
            run_next_ingestion_task_once(engine, work),
            run_next_ingestion_task_once(engine, work),
        )
        completed = [task for task in results if task is not None]

        assert len(completed) == 2
        assert {task.id for task in completed} == set(task_ids)
        assert all(task.status == "SUCCEEDED" for task in completed)
        assert set(work_calls) == set(task_ids)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_task_outbox "
                    "WHERE task_id LIKE :task_prefix"
                ),
                {"task_prefix": f"{task_prefix}%"},
            )
            await connection.execute(
                text(
                    "DELETE FROM agent_core.ingestion_tasks "
                    "WHERE id LIKE :task_prefix"
                ),
                {"task_prefix": f"{task_prefix}%"},
            )
        await engine.dispose()
