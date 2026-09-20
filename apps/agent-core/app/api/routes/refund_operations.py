"""管理员的退款审计与补偿操作；数据库角色校验复用现有 ADMIN 边界。"""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import get_current_refund_approver_id
from app.services.refund_recovery import (
    acknowledge_refund_conflict,
    resubmit_missing_refund,
    resume_refund_recovery,
)

router = APIRouter(prefix="/v1/refund-operations", tags=["refund-operations"])
Admin = Annotated[str, Depends(get_current_refund_approver_id)]


class ReviewNote(BaseModel):
    note: str = Field(min_length=1, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: object) -> object:
        """审计备注必须有实际内容，保留数据库内的人工处理依据。"""
        return value.strip() if isinstance(value, str) else value


@router.get("")
async def list_operations(
    request: Request,
    admin: Admin,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """返回需要人工介入的有界队列，不将客户身份作为查询授权。"""
    engine = cast(AsyncEngine, request.app.state.database_engine)
    try:
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text("""
                SELECT e.refund_application_id,e.status AS execution_status,j.status,
                    j.attempts,j.last_error_code,j.updated_at
                FROM refund_recovery_jobs j JOIN refund_executions e ON e.id=j.execution_id
                WHERE j.status='MANUAL_REQUIRED' ORDER BY j.updated_at,j.execution_id LIMIT :limit
            """),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            return {"items": [dict(row) for row in rows]}
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(503, "退款运维数据暂时不可用。") from error


@router.get("/{application_id}")
async def get_operation(
    application_id: str,
    request: Request,
    admin: Admin,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """审计按递增 ID 翻页；事务快照保证当前状态、任务及该页事件一致。"""
    engine = cast(AsyncEngine, request.app.state.database_engine)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                record = (
                    (
                        await connection.execute(
                            text("""
                    SELECT id,status,provider_reference,amount,currency FROM refund_executions
                    WHERE refund_application_id=:id
                """),
                            {"id": application_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if record is None:
                    raise HTTPException(404, "退款执行记录不存在。")
                job = (
                    (
                        await connection.execute(
                            text("""
                    SELECT status,attempts,next_attempt_at,last_error_code,updated_at
                    FROM refund_recovery_jobs WHERE execution_id=:id
                """),
                            {"id": record["id"]},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                events = (
                    (
                        await connection.execute(
                            text("""
                    SELECT id,action,source,actor_user_id,source_event_id,from_status,to_status,
                        provider_reference,error_code,note,created_at FROM refund_audit_events
                    WHERE execution_id=:id AND id>:after ORDER BY id LIMIT :limit
                """),
                            {"id": record["id"], "after": after_id, "limit": limit},
                        )
                    )
                    .mappings()
                    .all()
                )
                execution = dict(record)
                execution["amount"] = str(record["amount"])
                return {
                    "execution": execution,
                    "recovery": dict(job) if job else None,
                    "events": [dict(event, id=str(event["id"])) for event in events],
                    "next_after_id": str(events[-1]["id"]) if events else str(after_id),
                }
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(503, "退款运维数据暂时不可用。") from error


@router.post("/{application_id}/resume", status_code=204)
async def resume(application_id: str, payload: ReviewNote, request: Request, admin: Admin) -> None:
    """人工排查后重启有次数限制的补查；重复操作返回冲突，不重置运行中任务。"""
    try:
        resumed = await resume_refund_recovery(
            request.app.state.database_engine,
            application_id=application_id,
            actor_user_id=admin,
            note=payload.note,
        )
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(503, "退款运维服务暂时不可用。") from error
    if not resumed:
        raise HTTPException(409, "当前退款不允许重新开启核对。")


@router.post("/{application_id}/acknowledge-conflict", status_code=204)
async def acknowledge(
    application_id: str,
    payload: ReviewNote,
    request: Request,
    admin: Admin,
) -> None:
    """管理员留痕确认已核查矛盾，不修改资金状态、不重新发起退款。"""
    try:
        completed = await acknowledge_refund_conflict(
            request.app.state.database_engine,
            application_id=application_id,
            actor_user_id=admin,
            note=payload.note,
        )
    except (SQLAlchemyError, OSError) as error:
        raise HTTPException(503, "退款运维服务暂时不可用。") from error
    if not completed:
        raise HTTPException(409, "当前退款没有待确认的终态矛盾。")


@router.post("/{application_id}/resubmit", status_code=204)
async def resubmit(
    application_id: str, payload: ReviewNote, request: Request, admin: Admin
) -> None:
    """显式授权重投原沙箱键；客户端不能提供金额、用户或新的幂等键。"""
    adapter = getattr(request.app.state, "refund_sandbox_adapter", None)
    if adapter is None:
        raise HTTPException(503, "退款沙箱尚未配置。")
    settings = request.app.state.settings
    try:
        accepted = await resubmit_missing_refund(
            request.app.state.database_engine,
            application_id=application_id,
            actor_user_id=admin,
            note=payload.note,
            adapter=adapter,
            lease_seconds=settings.refund_recovery_lease_seconds,
            max_attempts=settings.refund_recovery_max_attempts,
            retry_seconds=settings.refund_recovery_retry_seconds,
        )
    except (SQLAlchemyError, OSError, RuntimeError) as error:
        raise HTTPException(503, "退款结果尚未确认，请查询状态与审计记录。") from error
    if not accepted:
        raise HTTPException(409, "只有补查耗尽且沙箱无记录的退款允许原键重提交。")
