import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class IngestionTaskOutboxEvent:
    id: UUID
    task_id: str
    attempt_id: UUID
    event_type: str
    payload: dict[str, object]
    created_at: datetime
    published_at: datetime | None
    publish_attempt_id: UUID | None = None
    publishing_at: datetime | None = None


@dataclass(frozen=True)
class IngestionTask:
    """文档导入任务在某一时刻的不可变数据库快照。

    ``task_id`` 标识任务本身，``attempt_id`` 标识当前一次执行尝试；任务重新
    排队后仍保留同一个 ID，但下次领取会获得新的 attempt ID。
    """

    id: str
    status: str
    created_at: datetime
    started_at: datetime | None
    retry_count: int
    attempt_id: UUID | None = None


async def create_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
) -> IngestionTask:
    """创建一条 PENDING 任务并返回数据库生成的初始快照。

    状态、创建时间和重试次数使用数据库默认值；重复 ``task_id`` 由主键拒绝。
    """

    statement = text(
        """
        INSERT INTO agent_core.ingestion_tasks (id)
        VALUES (:task_id)
        RETURNING id, status, created_at, started_at, retry_count
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"task_id": task_id})
        row = result.mappings().one()

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
        )


async def claim_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
) -> IngestionTask | None:
    """按 ID 原子领取 PENDING 任务，并为本次执行生成 attempt ID。

    任务不存在或已被其他 Worker 领取时返回 ``None``。
    """

    attempt_id = uuid4()
    statement = text(
        """
        UPDATE agent_core.ingestion_tasks
        SET
        status = 'RUNNING',
        started_at = CURRENT_TIMESTAMP,
        attempt_id = :attempt_id
        WHERE id = :task_id
          AND status = 'PENDING'
        RETURNING id, status, created_at, started_at, retry_count, attempt_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"task_id": task_id, "attempt_id": attempt_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def succeed_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
    *,
    attempt_id: UUID,
) -> IngestionTask | None:
    """将当前 attempt 拥有的 RUNNING 任务原子推进为 SUCCEEDED。

    状态或 attempt ID 不匹配时返回 ``None``，避免迟到请求完成新的执行尝试。
    """

    statement = text(
        """
        UPDATE agent_core.ingestion_tasks
        SET status = 'SUCCEEDED'
        WHERE id = :task_id
          AND status = 'RUNNING'
          AND attempt_id = :attempt_id
        RETURNING id, status, created_at, started_at, retry_count, attempt_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"task_id": task_id, "attempt_id": attempt_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None

        event_id = uuid4()
        payload = json.dumps(
            {
                "task_id": task_id,
                "attempt_id": str(attempt_id),
                "status": "SUCCEEDED",
            }
        )

        await connection.execute(
            text(
                """
                INSERT INTO agent_core.ingestion_task_outbox (
                    id,
                    task_id,
                    attempt_id,
                    event_type,
                    payload
                )
                VALUES (
                    :event_id,
                    :task_id,
                    :attempt_id,
                    'ingestion_task.succeeded',
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "event_id": event_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "payload": payload,
            },
        )

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def fail_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
    *,
    attempt_id: UUID,
) -> IngestionTask | None:
    """将当前 attempt 拥有的 RUNNING 任务原子推进为 FAILED。

    状态或 attempt ID 不匹配时返回 ``None``，不会覆盖其他执行尝试的结果。
    """

    statement = text(
        """
        UPDATE agent_core.ingestion_tasks
        SET status = 'FAILED'
        WHERE id = :task_id 
            AND status = 'RUNNING'
            AND attempt_id = :attempt_id
        RETURNING id, status, created_at, started_at, retry_count, attempt_id   
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"task_id": task_id, "attempt_id": attempt_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None
        event_id = uuid4()
        payload = json.dumps(
            {
                "task_id": task_id,
                "attempt_id": str(attempt_id),
                "status": "FAILED",
            }
        )

        await connection.execute(
            text(
                """
                INSERT INTO agent_core.ingestion_task_outbox (
                    id,
                    task_id,
                    attempt_id,
                    event_type,
                    payload
                )
                VALUES (
                    :event_id,
                    :task_id,
                    :attempt_id,
                    'ingestion_task.failed',
                    CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "event_id": event_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "payload": payload,
            },
        )
        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def retry_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
    *,
    max_retries: int,
    attempt_id: UUID,
) -> IngestionTask | None:
    """在重试上限内将当前 attempt 的 FAILED 任务重新放回 PENDING。

    成功重排时清除旧执行时间和 attempt ID，并原子递增重试次数；不满足状态、
    attempt ID 或次数条件时返回 ``None``。
    """

    if max_retries <= 0:
        raise ValueError("max_retries must be greater than zero")
    statement = text(
        """
        UPDATE agent_core.ingestion_tasks
        SET
            status = 'PENDING',
            started_at = NULL,
            attempt_id = NULL,
            retry_count = retry_count + 1
        WHERE id = :task_id
          AND status = 'FAILED'
          AND retry_count < :max_retries
          AND attempt_id = :attempt_id
        RETURNING id, status, created_at, started_at, retry_count, attempt_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(
            statement,
            {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "max_retries": max_retries,
            },
        )
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def run_ingestion_task_once(
    engine: AsyncEngine,
    task_id: str,
    work: Callable[[], Awaitable[None]],
) -> IngestionTask | None:
    """领取并执行指定任务，再根据 ``work`` 的结果写入成功或失败终态。

    未取得执行权时不调用 ``work`` 并返回 ``None``；只捕获普通 ``Exception``，
    协程取消等控制信号不会被当作业务失败吞掉。
    """

    claimed = await claim_ingestion_task(engine, task_id)

    if claimed is None:
        return None

    if claimed.attempt_id is None:
        raise RuntimeError("claimed task is missing attempt_id")

    try:
        await work()
    except Exception:
        return await fail_ingestion_task(engine, task_id, attempt_id=claimed.attempt_id)
    else:
        return await succeed_ingestion_task(engine, task_id, attempt_id=claimed.attempt_id)


async def claim_next_ingestion_task(
    engine: AsyncEngine,
) -> IngestionTask | None:
    """按创建顺序原子领取下一条 PENDING 任务，并生成新的 attempt ID。

    ``FOR UPDATE SKIP LOCKED`` 让并发 Worker 跳过已被其他事务锁定的任务，
    避免等待或重复领取同一行；队列为空时返回 ``None``。
    """

    attempt_id = uuid4()
    statement = text(
        """
        WITH next_task AS (
            SELECT id
            FROM agent_core.ingestion_tasks
            WHERE status = 'PENDING'
            ORDER BY created_at ASC, id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE agent_core.ingestion_tasks AS task
        SET
            status = 'RUNNING',
            started_at = CURRENT_TIMESTAMP,
            attempt_id = :attempt_id
        FROM next_task
        WHERE task.id = next_task.id
        RETURNING task.id, task.status, task.created_at,
          task.started_at, task.retry_count, task.attempt_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"attempt_id": attempt_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def run_next_ingestion_task_once(
    engine: AsyncEngine,
    work: Callable[[IngestionTask], Awaitable[None]],
) -> IngestionTask | None:
    """领取并执行队列中的下一条任务，再用领取到的 attempt ID 写入终态。

    队列为空时不调用 ``work``；领取结果缺少 attempt ID 表示状态数据不完整。
    """

    claimed = await claim_next_ingestion_task(engine)

    if claimed is None:
        return None

    if claimed.attempt_id is None:
        raise RuntimeError("claimed task is missing attempt_id")
    try:
        await work(claimed)
    except Exception:
        return await fail_ingestion_task(engine, claimed.id, attempt_id=claimed.attempt_id)
    else:
        return await succeed_ingestion_task(engine, claimed.id, attempt_id=claimed.attempt_id)


async def get_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
) -> IngestionTask | None:
    """读取指定任务的当前持久化快照；任务不存在时返回 ``None``。"""

    statement = text(
        """
        SELECT id, status, created_at, started_at, retry_count,attempt_id
        FROM agent_core.ingestion_tasks
        WHERE id = :task_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"task_id": task_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def reclaim_stale_ingestion_task(
    engine: AsyncEngine,
    task_id: str,
    *,
    stale_before: datetime,
) -> IngestionTask | None:
    """将开始时间早于截止点的 RUNNING 任务原子放回 PENDING。

    只有状态和 ``started_at`` 都满足条件时才回收；条件不匹配时返回 ``None``，
    避免恢复请求覆盖仍在正常执行或已进入终态的任务。
    """

    statement = text(
        """
        UPDATE agent_core.ingestion_tasks
        SET
            status = 'PENDING',
            started_at = NULL,
            attempt_id = NULL
        WHERE id = :task_id
          AND status = 'RUNNING'
          AND started_at < :stale_before
        RETURNING id, status, created_at, started_at, retry_count, attempt_id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(
            statement,
            {
                "task_id": task_id,
                "stale_before": stale_before,
            },
        )
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTask(
            id=row["id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            retry_count=row["retry_count"],
            attempt_id=row["attempt_id"],
        )


async def get_next_unpublished_ingestion_event(
    engine: AsyncEngine,
) -> IngestionTaskOutboxEvent | None:
    statement = text(
        """
        SELECT id, task_id, attempt_id, event_type, payload, created_at, published_at
        FROM agent_core.ingestion_task_outbox
        WHERE published_at IS NULL
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement)
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTaskOutboxEvent(
            id=row["id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            event_type=row["event_type"],
            payload=row["payload"],
            created_at=row["created_at"],
            published_at=row["published_at"],
        )


async def mark_ingestion_event_published(
    engine: AsyncEngine,
    event_id: UUID,
    *,
    publish_attempt_id: UUID,
) -> bool:
    statement = text(
        """
        UPDATE agent_core.ingestion_task_outbox
        SET
            published_at = CURRENT_TIMESTAMP,
            publish_attempt_id = NULL,
            publishing_at = NULL
        WHERE id = :event_id
          AND published_at IS NULL
          AND publish_attempt_id = :publish_attempt_id
        RETURNING id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(
            statement, {"event_id": event_id, "publish_attempt_id": publish_attempt_id}
        )
        return result.rowcount > 0


async def publish_next_ingestion_event_once(
    engine: AsyncEngine,
    publish: Callable[[IngestionTaskOutboxEvent], Awaitable[None]],
    *,
    stale_before: datetime,
) -> bool:
    await reclaim_stale_ingestion_events(
        engine,
        stale_before=stale_before,
    )
    event = await claim_next_ingestion_event(engine)

    if event is None:
        return False

    if event.publish_attempt_id is None:
        return False

    await publish(event)

    return await mark_ingestion_event_published(
        engine,
        event.id,
        publish_attempt_id=event.publish_attempt_id,
    )


async def claim_next_ingestion_event(
    engine: AsyncEngine,
) -> IngestionTaskOutboxEvent | None:
    publish_attempt_id = uuid4()
    statement = text(
        """
        WITH next_event AS (
            SELECT id
            FROM agent_core.ingestion_task_outbox
            WHERE published_at IS NULL
              AND publish_attempt_id IS NULL
            ORDER BY created_at ASC, id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE agent_core.ingestion_task_outbox AS event
        SET
            publish_attempt_id = :publish_attempt_id,
            publishing_at = CURRENT_TIMESTAMP
        FROM next_event
        WHERE event.id = next_event.id
        RETURNING
            event.id,
            event.task_id,
            event.attempt_id,
            event.event_type,
            event.payload,
            event.created_at,
            event.published_at,
            event.publish_attempt_id,
            event.publishing_at
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"publish_attempt_id": publish_attempt_id})
        row = result.mappings().one_or_none()

        if row is None:
            return None

        return IngestionTaskOutboxEvent(
            id=row["id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            event_type=row["event_type"],
            payload=row["payload"],
            created_at=row["created_at"],
            published_at=row["published_at"],
            publish_attempt_id=row["publish_attempt_id"],
            publishing_at=row["publishing_at"],
        )


async def reclaim_stale_ingestion_events(
    engine: AsyncEngine,
    *,
    stale_before: datetime,
) -> int:
    statement = text(
        """
        UPDATE agent_core.ingestion_task_outbox
        SET
            publish_attempt_id = NULL,
            publishing_at = NULL
        WHERE published_at IS NULL
          AND publish_attempt_id IS NOT NULL
          AND publishing_at < :stale_before
        RETURNING id
        """
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"stale_before": stale_before})
        return result.rowcount
