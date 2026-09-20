"""持久补偿只补查支付结果；未知结果绝不换幂等键重新退款。"""

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.refund import _apply_refund_sandbox_result_on_connection
from app.services.refund_audit import record_refund_audit, set_refund_audit_context
from app.services.refund_sandbox import (
    RefundSandboxAdapter,
    RefundSandboxRequest,
    RefundSandboxResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryLease:
    execution_id: str
    idempotency_key: str
    token: UUID
    attempts: int


async def claim_refund_recovery(
    engine: AsyncEngine,
    *,
    max_attempts: int,
    lease_seconds: int,
) -> RecoveryLease | None:
    """短事务内领取一条到期任务；崩溃留下的租约到期后可再次领取。"""
    if not 1 <= max_attempts <= 20 or not 10 <= lease_seconds <= 3600:
        raise ValueError("Invalid recovery limits")
    token = uuid4()
    async with engine.begin() as connection:
        # 最后一次领取后进程崩溃，也必须进入人工队列而非永久卡在 LEASED。
        await connection.execute(
            text("""
            WITH expired AS (
              UPDATE refund_recovery_jobs SET status='MANUAL_REQUIRED',
                lease_token=NULL,lease_expires_at=NULL,last_error_code='ATTEMPTS_EXHAUSTED',
                updated_at=clock_timestamp()
              WHERE attempts >= :maximum AND
                (status='READY' OR (status='LEASED' AND lease_expires_at <= clock_timestamp()))
              RETURNING execution_id
            )
            INSERT INTO refund_audit_events (execution_id,action,source,error_code)
            SELECT execution_id,'MANUAL_REQUIRED','compensation','ATTEMPTS_EXHAUSTED' FROM expired
        """),
            {"maximum": max_attempts},
        )
        row = (
            (
                await connection.execute(
                    text("""
            WITH candidate AS (
              SELECT j.execution_id FROM refund_recovery_jobs j
              JOIN refund_executions e ON e.id=j.execution_id
              WHERE e.status IN ('RUNNING','PROCESSING') AND j.attempts < :maximum
                AND ((j.status='READY' AND j.next_attempt_at <= clock_timestamp())
                  OR (j.status='LEASED' AND j.lease_expires_at <= clock_timestamp()))
              ORDER BY j.next_attempt_at,j.execution_id
              FOR UPDATE OF j SKIP LOCKED LIMIT 1
            )
            UPDATE refund_recovery_jobs j SET status='LEASED',attempts=j.attempts+1,
              lease_token=:token,lease_expires_at=clock_timestamp()+make_interval(secs=>:seconds),
              updated_at=clock_timestamp()
            FROM candidate c,refund_executions e WHERE j.execution_id=c.execution_id
              AND e.id=j.execution_id
            RETURNING j.execution_id,j.attempts,e.idempotency_key
        """),
                    {"maximum": max_attempts, "token": token, "seconds": lease_seconds},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        await record_refund_audit(
            connection,
            execution_id=row["execution_id"],
            action="QUERY_STARTED",
            source="compensation",
        )
        return RecoveryLease(row["execution_id"], row["idempotency_key"], token, row["attempts"])


async def finish_refund_recovery(
    engine: AsyncEngine,
    *,
    lease: RecoveryLease,
    result: RefundSandboxResult | None,
    error_code: str | None,
    max_attempts: int,
    retry_seconds: int,
    source: str = "compensation",
    actor_user_id: str | None = None,
) -> bool:
    """只有当前未过期租约可落库；旧任务即使晚返回也不能覆盖新任务或 webhook。"""
    async with engine.begin() as connection:
        # 执行记录在前、补偿任务在后；不改主键，用 NO KEY UPDATE，
        # 允许领取任务时追加审计的外键检查通过，避免相反的锁等待形成死锁。
        execution = (
            (
                await connection.execute(
                    text("SELECT status FROM refund_executions WHERE id=:id FOR NO KEY UPDATE"),
                    {"id": lease.execution_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        job = (
            (
                await connection.execute(
                    text("""
            SELECT attempts FROM refund_recovery_jobs WHERE execution_id=:id
              AND status='LEASED' AND lease_token=:token
              AND lease_expires_at > clock_timestamp() FOR UPDATE
        """),
                    {"id": lease.execution_id, "token": lease.token},
                )
            )
            .mappings()
            .one_or_none()
        )
        if execution is None or job is None:
            return False
        await set_refund_audit_context(connection, source=source, actor_user_id=actor_user_id)
        if result is not None:
            await _apply_refund_sandbox_result_on_connection(
                connection,
                execution_id=lease.execution_id,
                expected_idempotency_key=lease.idempotency_key,
                sandbox_result=result,
            )
        current = await connection.scalar(
            text("SELECT status FROM refund_executions WHERE id=:id"), {"id": lease.execution_id}
        )
        if current in {"SUCCEEDED", "FAILED"}:
            # 终态 trigger 已关闭任务；不能在这里把它重新排入待核对队列。
            return True
        manual = job["attempts"] >= max_attempts
        code = error_code or ("NOT_FOUND" if result is None else "STILL_PROCESSING")
        delay = min(retry_seconds * 2 ** (job["attempts"] - 1), 3600)
        await connection.execute(
            text("""
            UPDATE refund_recovery_jobs SET status=:status,lease_token=NULL,lease_expires_at=NULL,
              next_attempt_at=clock_timestamp()+make_interval(secs=>:delay),
              last_error_code=:code,updated_at=clock_timestamp()
            WHERE execution_id=:id AND lease_token=:token
        """),
            {
                "id": lease.execution_id,
                "token": lease.token,
                "delay": delay,
                "code": code,
                "status": "MANUAL_REQUIRED" if manual else "READY",
            },
        )
        await record_refund_audit(
            connection,
            execution_id=lease.execution_id,
            action="MANUAL_REQUIRED" if manual else "QUERY_DEFERRED",
            source=source,
            actor_user_id=actor_user_id,
            error_code=code,
        )
        return True


async def resubmit_missing_refund(
    engine: AsyncEngine,
    *,
    application_id: str,
    actor_user_id: str,
    note: str,
    adapter: RefundSandboxAdapter,
    lease_seconds: int = 120,
    max_attempts: int = 5,
    retry_seconds: int = 60,
) -> bool:
    """人工确认后按原键重投沙箱，不自动重投、更换金额或重开终态。

    只允许补查耗尽且持续 NOT_FOUND 的 RUNNING 记录。短事务领取后释放连接，
    先查询再提交；即使旧请求此时到达，也由 HTTP 沙箱的持久唯一键去重。
    该操作依赖提供方永久保存幂等记录，接入真实渠道前必须重新确认此契约。
    """
    if not note.strip() or len(note) > 500:
        raise ValueError("A review note is required")
    if not 10 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 20:
        raise ValueError("Invalid recovery limits")
    token = uuid4()
    async with engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    text("""
                    SELECT e.id,e.idempotency_key,e.amount,e.currency,a.user_id,a.order_id
                    FROM refund_executions e JOIN refund_applications a
                      ON a.id=e.refund_application_id
                    WHERE a.id=:id AND a.status='APPROVED' AND e.status='RUNNING'
                      AND e.provider_reference IS NULL FOR NO KEY UPDATE OF e
                    """),
                    {"id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return False
        claimed = await connection.scalar(
            text("""
            UPDATE refund_recovery_jobs SET status='LEASED',attempts=1,lease_token=:token,
              lease_expires_at=clock_timestamp()+make_interval(secs=>:seconds),
              updated_at=clock_timestamp(),last_error_code=NULL
            WHERE execution_id=:id AND status='MANUAL_REQUIRED' AND last_error_code='NOT_FOUND'
            RETURNING execution_id
            """),
            {"id": row["id"], "token": token, "seconds": lease_seconds},
        )
        if claimed is None:
            return False
        await record_refund_audit(
            connection,
            execution_id=row["id"],
            action="RESUBMISSION_AUTHORIZED",
            source="admin",
            actor_user_id=actor_user_id,
            note=note.strip(),
        )
        lease = RecoveryLease(row["id"], row["idempotency_key"], token, 1)
        submission = RefundSandboxRequest(
            refund_application_id=application_id,
            user_id=row["user_id"],
            order_id=row["order_id"],
            amount=row["amount"],
            currency=row["currency"],
            idempotency_key=row["idempotency_key"],
        )
    result = None
    error_code = None
    try:
        result = await adapter.get_result(lease.idempotency_key)
        if result is None:
            result = await adapter.execute(submission)
        if result is None:
            raise RuntimeError("Invalid provider result")
    except TimeoutError:
        error_code = "PROVIDER_TIMEOUT"
    except (RuntimeError, OSError):
        error_code = "PROVIDER_UNAVAILABLE"
    # 失败也完成租约并安排补查，不把未知结果写成资金失败。
    await finish_refund_recovery(
        engine,
        lease=lease,
        result=result,
        error_code=error_code,
        max_attempts=max_attempts,
        retry_seconds=retry_seconds,
        source="admin_resubmit",
        actor_user_id=actor_user_id,
    )
    if error_code:
        raise RuntimeError("Refund resubmission outcome is unknown")
    return True


async def process_refund_recovery_batch(
    engine: AsyncEngine,
    *,
    adapter: RefundSandboxAdapter,
    limit: int = 50,
    max_attempts: int = 5,
    lease_seconds: int = 120,
    retry_seconds: int = 60,
) -> dict[str, int]:
    """逐条领取并补查，采用有限次数和指数退避；不调用 execute，也不长期循环。"""
    if not 1 <= limit <= 100 or not 1 <= retry_seconds <= 3600:
        raise ValueError("Invalid recovery batch configuration")
    counts = {"checked": 0, "errors": 0}
    for _ in range(limit):
        lease = await claim_refund_recovery(
            engine, max_attempts=max_attempts, lease_seconds=lease_seconds
        )
        if lease is None:
            break
        result = None
        error_code = None
        try:
            # 此处没有数据库事务；HTTP 客户端负责限制单次查询耗时。
            result = await adapter.get_result(lease.idempotency_key)
            if result is not None and (
                result.idempotency_key != lease.idempotency_key
                or result.status not in {"SUCCEEDED", "FAILED", "PROCESSING"}
                or (result.status == "SUCCEEDED" and not (result.provider_reference or "").strip())
            ):
                raise RuntimeError("Invalid provider result")
        except TimeoutError:
            error_code = "PROVIDER_TIMEOUT"
            result = None
        except (RuntimeError, OSError):
            error_code = "PROVIDER_UNAVAILABLE"
            result = None
        try:
            await finish_refund_recovery(
                engine,
                lease=lease,
                result=result,
                error_code=error_code,
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
            )
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            # 数据库不可用时无法可靠记录失败，保留租约等待到期回收。
            logger.warning("Refund recovery write failed: error_type=%s", type(error).__name__)
            error_code = "PERSISTENCE_UNAVAILABLE"
        counts["errors" if error_code else "checked"] += 1
    return counts


async def resume_refund_recovery(
    engine: AsyncEngine,
    *,
    application_id: str,
    actor_user_id: str,
    note: str,
) -> bool:
    """管理员确认排查后重新开放有限的只读核对；终态矛盾不能借此重开退款。"""
    if not note.strip() or len(note) > 500:
        raise ValueError("A review note is required")
    async with engine.begin() as connection:
        execution = (
            (
                await connection.execute(
                    text("""
            SELECT id,status FROM refund_executions
            WHERE refund_application_id=:id FOR NO KEY UPDATE
        """),
                    {"id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if execution is None or execution["status"] not in {"RUNNING", "PROCESSING"}:
            return False
        resumed = await connection.scalar(
            text("""
            UPDATE refund_recovery_jobs SET status='READY',attempts=0,lease_token=NULL,
              lease_expires_at=NULL,next_attempt_at=clock_timestamp(),last_error_code=NULL,
              updated_at=clock_timestamp()
            WHERE execution_id=:id AND status='MANUAL_REQUIRED' RETURNING execution_id
        """),
            {"id": execution["id"]},
        )
        if resumed is None:
            return False
        await record_refund_audit(
            connection,
            execution_id=execution["id"],
            action="MANUAL_RESUMED",
            source="admin",
            actor_user_id=actor_user_id,
            note=note.strip(),
        )
        return True


async def acknowledge_refund_conflict(
    engine: AsyncEngine,
    *,
    application_id: str,
    actor_user_id: str,
    note: str,
) -> bool:
    """记录管理员已在提供方核实终态矛盾；保留原资金终态与全部矛盾证据。"""
    if not note.strip() or len(note) > 500:
        raise ValueError("A review note is required")
    async with engine.begin() as connection:
        execution = (
            (
                await connection.execute(
                    text("""
            SELECT id,status FROM refund_executions
            WHERE refund_application_id=:id FOR NO KEY UPDATE
        """),
                    {"id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if execution is None or execution["status"] not in {"SUCCEEDED", "FAILED"}:
            return False
        acknowledged = await connection.scalar(
            text("""
            UPDATE refund_recovery_jobs SET status='COMPLETED',updated_at=clock_timestamp()
            WHERE execution_id=:id AND status='MANUAL_REQUIRED'
              AND last_error_code='TERMINAL_CONFLICT' RETURNING execution_id
        """),
            {"id": execution["id"]},
        )
        if acknowledged is None:
            return False
        await record_refund_audit(
            connection,
            execution_id=execution["id"],
            action="CONFLICT_ACKNOWLEDGED",
            source="admin",
            actor_user_id=actor_user_id,
            note=note.strip(),
        )
        return True
