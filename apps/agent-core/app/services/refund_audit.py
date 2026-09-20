"""退款审计的事务内写入边界；状态变更由 PostgreSQL trigger 自动记录。"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.refund_sandbox import RefundSandboxResult


async def set_refund_audit_context(
    connection: AsyncConnection,
    *,
    source: str,
    actor_user_id: str | None = None,
    source_event_id: str | None = None,
) -> None:
    """只在当前事务内设置来源，连接归还池后不能串到下一请求。"""
    await connection.execute(
        text("SELECT set_config('app.refund_audit_context', :context, true)"),
        {
            "context": json.dumps(
                {
                    "source": source,
                    "actor_user_id": actor_user_id,
                    "source_event_id": source_event_id,
                }
            )
        },
    )


async def record_refund_audit(
    connection: AsyncConnection,
    *,
    execution_id: str,
    action: str,
    source: str,
    error_code: str | None = None,
    actor_user_id: str | None = None,
    note: str | None = None,
) -> None:
    """追加脱敏业务事件；与调用方状态或任务更新一起提交，不保存外部正文。"""
    await connection.execute(
        text("""INSERT INTO refund_audit_events
            (execution_id,action,source,error_code,actor_user_id,note)
            VALUES (:id,:action,:source,:error,:actor,:note)"""),
        {
            "id": execution_id,
            "action": action,
            "source": source,
            "error": error_code,
            "actor": actor_user_id,
            "note": note,
        },
    )


async def record_ignored_refund_result(
    connection: AsyncConnection,
    *,
    execution_id: str,
    result: RefundSandboxResult,
) -> None:
    """不覆盖既有终态；矛盾终态转人工处理，普通旧通知只追加忽略记录。"""
    current = (
        (
            await connection.execute(
                text(
                    "SELECT status, provider_reference FROM refund_executions "
                    "WHERE id=:id FOR NO KEY UPDATE"
                ),
                {"id": execution_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if current is None:
        return
    conflict = current["status"] in {"SUCCEEDED", "FAILED"} and (
        (result.status in {"SUCCEEDED", "FAILED"} and current["status"] != result.status)
        or (
            current["status"] == result.status == "SUCCEEDED"
            and current["provider_reference"] != result.provider_reference
        )
    )
    # 来源取自事务上下文，避免 webhook/recovery 的重复结果被记成另一条链。
    await connection.execute(
        text("""INSERT INTO refund_audit_events
            (execution_id,action,source,source_event_id,from_status,to_status,error_code,
             provider_reference)
            VALUES (:id,:action,
              COALESCE(NULLIF(current_setting('app.refund_audit_context',true),'')::jsonb->>'source','provider'),
              NULLIF(current_setting('app.refund_audit_context',true),'')::jsonb->>'source_event_id',
              :current,:incoming,:error,:reference)"""),
        {
            "id": execution_id,
            "action": "RESULT_CONFLICT" if conflict else "RESULT_IGNORED",
            "current": current["status"],
            "incoming": result.status,
            "error": "TERMINAL_CONFLICT" if conflict else None,
            "reference": result.provider_reference,
        },
    )
    if conflict:
        await connection.execute(
            text("""INSERT INTO refund_recovery_jobs
                (execution_id,status,last_error_code)
                VALUES (:id,'MANUAL_REQUIRED','TERMINAL_CONFLICT')
                ON CONFLICT (execution_id) DO UPDATE SET status='MANUAL_REQUIRED',
                last_error_code='TERMINAL_CONFLICT',lease_token=NULL,lease_expires_at=NULL,
                updated_at=clock_timestamp()"""),
            {"id": execution_id},
        )
