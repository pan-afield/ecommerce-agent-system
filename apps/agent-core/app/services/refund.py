import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.services.refund_audit import (
    record_ignored_refund_result,
    set_refund_audit_context,
)
from app.services.refund_sandbox import (
    RefundSandboxAdapter,
    RefundSandboxRequest,
    RefundSandboxResult,
)
from app.services.refund_webhook import RefundWebhookEvent

logger = logging.getLogger(__name__)

RefundReviewDecision = Literal["APPROVED", "REJECTED"]


@dataclass(frozen=True)
class RefundRequest:
    requester_user_id: str
    order_user_id: str
    order_status: str
    order_total: Decimal
    requested_amount: Decimal
    order_currency: str
    requested_currency: str


@dataclass(frozen=True)
class RefundAssessment:
    eligible_for_review: bool
    reason: str
    requires_customer_confirmation: bool


@dataclass(frozen=True)
class RefundApplication:
    order_id: str
    requested_amount: Decimal
    requested_currency: str


@dataclass(frozen=True)
class RefundOrderSnapshot:
    user_id: str
    status: str
    total_amount: Decimal
    currency: str


@dataclass(frozen=True)
class RefundExecutionRecord:
    id: str
    refund_application_id: str
    idempotency_key: str
    status: str
    amount: Decimal
    currency: str
    provider_reference: str | None


# 退款风控只做“能不能进入人工审核”的判断，不会创建申请，也不会产生支付副作用。
# 检查顺序是稳定的：先确认订单归属，再检查金额、币种、订单状态。
# 这样同一个输入始终得到同一个拒绝原因，路由可以把 reason 安全地返回给前端。
def assess_refund(request: RefundRequest) -> RefundAssessment:
    if request.requester_user_id != request.order_user_id:
        return RefundAssessment(
            eligible_for_review=False,
            reason="order_not_owned",
            requires_customer_confirmation=False,
        )
    if request.requested_amount <= Decimal("0"):
        return RefundAssessment(
            eligible_for_review=False, reason="invalid_amount", requires_customer_confirmation=False
        )
    if request.requested_currency != request.order_currency:
        return RefundAssessment(
            eligible_for_review=False,
            reason="currency_mismatch",
            requires_customer_confirmation=False,
        )
    if request.requested_amount > request.order_total:
        return RefundAssessment(
            eligible_for_review=False,
            reason="amount_exceeds_order_total",
            requires_customer_confirmation=False,
        )
    if request.order_status not in ("PAID", "SHIPPED", "DELIVERED"):
        return RefundAssessment(
            eligible_for_review=False,
            reason="order_not_refundable",
            requires_customer_confirmation=False,
        )

    return RefundAssessment(
        eligible_for_review=True, reason="eligible_for_review", requires_customer_confirmation=True
    )


# 合并用户申请和订单快照，构造退款规则所需的完整请求。
def build_refund_request(
    application: RefundApplication,
    order: RefundOrderSnapshot,
    requester_user_id: str,
) -> RefundRequest:
    return RefundRequest(
        requester_user_id=requester_user_id,
        order_user_id=order.user_id,
        order_status=order.status,
        order_total=order.total_amount,
        requested_amount=application.requested_amount,
        order_currency=order.currency,
        requested_currency=application.requested_currency,
    )


# 以用户和 request_id 为幂等键创建申请，并返回本次是否真的插入了一行。
#
# 首次请求插入 AWAITING_CUSTOMER_CONFIRMATION；重试请求不会覆盖原申请。
# “没有插入”不是数据库异常，调用方还要读取原申请并比较订单、金额和币种，
# 才能区分安全重试与同一个幂等键提交了不同内容。
async def try_create_refund_application(
    engine: AsyncEngine,
    *,
    application_id: str,
    user_id: str,
    request_id: str,
    application: RefundApplication,
) -> bool:
    statement = text(
        """
        INSERT INTO refund_applications (
            id,
            user_id,
            order_id,
            request_id,
            requested_amount,
            currency,
            status
        )
        VALUES (
            :id,
            :user_id,
            :order_id,
            :request_id,
            :requested_amount,
            :currency,
            'AWAITING_CUSTOMER_CONFIRMATION'
        )
        ON CONFLICT DO NOTHING
        RETURNING id
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            statement,
            {
                "id": application_id,
                "user_id": user_id,
                "order_id": application.order_id,
                "request_id": request_id,
                "requested_amount": application.requested_amount,
                "currency": application.requested_currency,
            },
        )

    return result.scalar_one_or_none() is not None


@dataclass(frozen=True)
class RefundApplicationRecord:
    id: str
    user_id: str
    order_id: str
    request_id: str
    requested_amount: Decimal
    currency: str
    status: str
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


# 按用户和请求 ID 查询退款申请，不存在时返回 None。
async def fetch_refund_application_by_request_id(
    engine: AsyncEngine,
    *,
    user_id: str,
    request_id: str,
) -> RefundApplicationRecord | None:
    statement = text(
        """
        SELECT
            id,
            user_id,
            order_id,
            request_id,
            requested_amount,
            currency,
            status::text AS status
        FROM refund_applications
        WHERE user_id = :user_id
          AND request_id = :request_id
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {
                "user_id": user_id,
                "request_id": request_id,
            },
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundApplicationRecord(
        id=row["id"],
        user_id=row["user_id"],
        order_id=row["order_id"],
        request_id=row["request_id"],
        requested_amount=row["requested_amount"],
        currency=row["currency"],
        status=row["status"],
    )


def matches_existing_refund_application(
    existing: RefundApplicationRecord,
    application: RefundApplication,
) -> bool:
    return (
        existing.order_id == application.order_id
        and existing.requested_amount == application.requested_amount
        and existing.currency == application.requested_currency
    )


async def confirm_refund_application(
    engine: AsyncEngine,
    *,
    application_id: str,
    user_id: str,
) -> RefundApplicationRecord | None:
    statement = text(
        """
        -- 客户确认是幂等的：待确认和已进入人工审批都可以返回当前记录。
        -- user_id 同时保护订单归属，客户不能确认其他用户的申请。
        UPDATE refund_applications
        SET
            status = 'PENDING_MANUAL_APPROVAL',
            confirmed_at = COALESCE(confirmed_at, CURRENT_TIMESTAMP),
            updated_at = CASE
                WHEN status = 'AWAITING_CUSTOMER_CONFIRMATION'
                THEN CURRENT_TIMESTAMP
                ELSE updated_at
            END
        WHERE id = :application_id
          AND user_id = :user_id
          AND status IN (
              'AWAITING_CUSTOMER_CONFIRMATION',
              'PENDING_MANUAL_APPROVAL'
          )
        RETURNING
            id,
            user_id,
            order_id,
            request_id,
            requested_amount,
            currency,
            status::text AS status
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            statement,
            {
                "application_id": application_id,
                "user_id": user_id,
            },
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundApplicationRecord(
        id=row["id"],
        user_id=row["user_id"],
        order_id=row["order_id"],
        request_id=row["request_id"],
        requested_amount=row["requested_amount"],
        currency=row["currency"],
        status=row["status"],
    )


# 尝试“抢占”一次人工审批决定。
#
# 关键保护在 SQL 的 WHERE status 条件，而不是 Python 里的先查后改：
# 只有仍处于 PENDING_MANUAL_APPROVAL 的申请才能被本次请求更新。
# 两个请求并发到达时，数据库会保证只有一个请求更新一行并返回 id。
#
# False 只表示 UPDATE 没有更新行，不能单独解释原因；路由必须再读取当前记录，
# 以区分不存在(404)、相同决定重试(200)和相反决定冲突(409)。
async def try_review_refund_application(
    engine: AsyncEngine,
    *,
    application_id: str,
    reviewer_user_id: str,
    decision: RefundReviewDecision,
    review_note: str | None,
) -> bool:
    statement = text(
        """
            -- 状态条件与 UPDATE 在同一条语句中，保证一次申请只有一个首次决定。
            UPDATE refund_applications
            SET
                status = CAST(:decision AS "RefundStatus"),
                reviewed_by_user_id = :reviewer_user_id,
                reviewed_at = CURRENT_TIMESTAMP,
                review_note = :review_note,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :application_id
              AND status = 'PENDING_MANUAL_APPROVAL'
            RETURNING id
        """
    )

    async with engine.begin() as connection:
        result = await connection.execute(
            statement,
            {
                "application_id": application_id,
                "reviewer_user_id": reviewer_user_id,
                "decision": decision,
                "review_note": review_note,
            },
        )
        row = result.mappings().one_or_none()

    # RETURNING 有行表示本次请求赢得了状态转换；没有行表示没有抢到转换权。
    return row is not None


# 按申请 ID 读取当前状态和审批审计信息。
# 这是审批失败后的解释步骤，也是相同决定重试时返回第一次结果的来源。
async def fetch_refund_application_by_id(
    engine: AsyncEngine,
    *,
    application_id: str,
) -> RefundApplicationRecord | None:
    statement = text(
        """
        SELECT
            id,
            user_id,
            order_id,
            request_id,
            requested_amount,
            currency,
            status::text AS status,
            reviewed_by_user_id,
            reviewed_at,
            review_note
        FROM refund_applications
        WHERE id = :application_id
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {
                "application_id": application_id,
            },
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundApplicationRecord(
        id=row["id"],
        user_id=row["user_id"],
        order_id=row["order_id"],
        request_id=row["request_id"],
        requested_amount=row["requested_amount"],
        currency=row["currency"],
        status=row["status"],
        reviewed_by_user_id=row["reviewed_by_user_id"],
        reviewed_at=row["reviewed_at"],
        review_note=row["review_note"],
    )


async def fetch_non_rejected_refund_application_by_order(
    engine: AsyncEngine,
    *,
    user_id: str,
    order_id: str,
) -> RefundApplicationRecord | None:
    statement = text(
        """
        SELECT
            id,
            user_id,
            order_id,
            request_id,
            requested_amount,
            currency,
            status::text AS status,
            reviewed_by_user_id,
            reviewed_at,
            review_note
        FROM refund_applications
        WHERE user_id = :user_id
          AND order_id = :order_id
          AND status != 'REJECTED'
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {
                "user_id": user_id,
                "order_id": order_id,
            },
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundApplicationRecord(
        id=row["id"],
        user_id=row["user_id"],
        order_id=row["order_id"],
        request_id=row["request_id"],
        requested_amount=row["requested_amount"],
        currency=row["currency"],
        status=row["status"],
        reviewed_by_user_id=row["reviewed_by_user_id"],
        reviewed_at=row["reviewed_at"],
        review_note=row["review_note"],
    )


async def get_approved_refund_application(
    engine: AsyncEngine,
    *,
    user_id: str,
    refund_application_id: str,
) -> RefundApplicationRecord | None:
    record = await fetch_refund_application_by_id(
        engine,
        application_id=refund_application_id,
    )

    if record is None:
        return None

    if record.user_id != user_id:
        return None

    if record.status != "APPROVED":
        return None

    return record


async def try_create_refund_execution(
    engine: AsyncEngine,
    *,
    execution_id: str,
    user_id: str,
    refund_application_id: str,
) -> bool:
    statement = text(
        """
        INSERT INTO refund_executions (
            id,
            refund_application_id,
            idempotency_key,
            status,
            amount,
            currency
        )
        SELECT
            :execution_id,
            id,
            'refund:' || id,
            'PENDING',
            requested_amount,
            currency
        FROM refund_applications
        WHERE id = :refund_application_id
          AND user_id = :user_id
          AND status = 'APPROVED'
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """
    )

    async with engine.begin() as connection:
        await set_refund_audit_context(connection, source="execute", actor_user_id=user_id)
        # 同一申请有 application_id 和 idempotency_key 两个唯一索引。
        # 先锁申请行串行创建，避免并发 INSERT 在非冲突目标索引上抛唯一键异常。
        # 不同申请仍可并发，其他主键冲突仍按真正的数据错误处理。
        await connection.execute(
            text("SELECT id FROM refund_applications WHERE id=:id AND user_id=:user FOR UPDATE"),
            {"id": refund_application_id, "user": user_id},
        )
        result = await connection.execute(
            statement,
            {
                "execution_id": execution_id,
                "user_id": user_id,
                "refund_application_id": refund_application_id,
            },
        )
        return result.scalar_one_or_none() is not None


async def try_claim_refund_execution(
    engine: AsyncEngine,
    *,
    execution_id: str,
) -> bool:
    statement = text(
        """
        UPDATE refund_executions
        SET
            status = 'RUNNING',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :execution_id
          AND status = 'PENDING'
        RETURNING id
        """
    )

    async with engine.begin() as connection:
        await set_refund_audit_context(connection, source="execute")
        result = await connection.execute(
            statement,
            {"execution_id": execution_id},
        )
        return result.scalar_one_or_none() is not None


async def fetch_refund_execution(
    engine: AsyncEngine,
    *,
    user_id: str,
    refund_application_id: str,
) -> RefundExecutionRecord | None:
    statement = text(
        """
        SELECT
            execution.id,
            execution.refund_application_id,
            execution.idempotency_key,
            execution.status,
            execution.amount,
            execution.currency,
            execution.provider_reference
        FROM refund_executions AS execution
        JOIN refund_applications AS application
          ON application.id = execution.refund_application_id
        WHERE execution.refund_application_id = :refund_application_id
          AND application.user_id = :user_id
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {
                "user_id": user_id,
                "refund_application_id": refund_application_id,
            },
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundExecutionRecord(
        id=row["id"],
        refund_application_id=row["refund_application_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        amount=row["amount"],
        currency=row["currency"],
        provider_reference=row["provider_reference"],
    )


async def try_succeed_refund_execution(
    engine: AsyncEngine,
    *,
    execution_id: str,
    provider_reference: str,
) -> bool:
    if not provider_reference.strip():
        raise ValueError("provider_reference must not be blank")

    async with engine.begin() as connection:
        return await _try_succeed_refund_execution_on_connection(
            connection,
            execution_id=execution_id,
            provider_reference=provider_reference,
        )


async def _try_succeed_refund_execution_on_connection(
    connection: AsyncConnection,
    *,
    execution_id: str,
    provider_reference: str,
) -> bool:
    statement = text(
        """
        UPDATE refund_executions
        SET
            status = 'SUCCEEDED',
            provider_reference = :provider_reference,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :execution_id
          AND status IN ('RUNNING', 'PROCESSING')
        RETURNING id
        """
    )

    result = await connection.execute(
        statement,
        {
            "execution_id": execution_id,
            "provider_reference": provider_reference,
        },
    )
    return result.scalar_one_or_none() is not None


async def try_mark_refund_processing(
    engine: AsyncEngine,
    *,
    execution_id: str,
    provider_reference: str | None,
) -> bool:
    async with engine.begin() as connection:
        return await _try_mark_refund_processing_on_connection(
            connection,
            execution_id=execution_id,
            provider_reference=provider_reference,
        )


async def _try_mark_refund_processing_on_connection(
    connection: AsyncConnection,
    *,
    execution_id: str,
    provider_reference: str | None,
) -> bool:
    statement = text(
        """
        UPDATE refund_executions
        SET
            status = 'PROCESSING',
            provider_reference = COALESCE(
                NULLIF(:provider_reference, ''),
                provider_reference
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :execution_id
          AND status = 'RUNNING'
        RETURNING id
        """
    )

    result = await connection.execute(
        statement,
        {
            "execution_id": execution_id,
            "provider_reference": provider_reference,
        },
    )
    return result.scalar_one_or_none() is not None


async def run_refund_sandbox(
    engine: AsyncEngine,
    *,
    user_id: str,
    refund_application_id: str,
    execution_id: str,
    adapter: RefundSandboxAdapter,
) -> RefundExecutionRecord | None:
    """只由 PENDING 的抢占者提交退款；重复请求读取既有记录和持久幂等键。"""
    #   第一步查询订单归属
    application = await get_approved_refund_application(
        engine, user_id=user_id, refund_application_id=refund_application_id
    )

    if application is None:
        return None

    #  第二步尝试创建执行记录
    await try_create_refund_execution(
        engine,
        execution_id=execution_id,
        user_id=user_id,
        refund_application_id=refund_application_id,
    )

    #  第三步读取数据库中的原记录
    refund_record = await fetch_refund_execution(
        engine,
        user_id=user_id,
        refund_application_id=refund_application_id,
    )

    # 第4步：如果原记录不是 PENDING，直接返回原记录，不再次调用沙箱；
    if refund_record is None:
        return None

    # 后续所有状态操作都使用数据库中的原记录 ID。
    persisted_execution_id = refund_record.id

    if refund_record.status != "PENDING":
        return refund_record

    # 第5步：尝试抢占执行权
    claimed = await try_claim_refund_execution(
        engine,
        execution_id=persisted_execution_id,
    )

    if claimed is True:
        sandbox_request = RefundSandboxRequest(
            amount=refund_record.amount,
            currency=refund_record.currency,
            idempotency_key=refund_record.idempotency_key,
            refund_application_id=refund_record.refund_application_id,
            order_id=application.order_id,
            user_id=application.user_id,
        )

        # 第6步：调用沙箱执行退款
        sandbox_result = await adapter.execute(sandbox_request)

        if sandbox_result is None:
            raise RuntimeError("Refund sandbox execution returned None")

        await _apply_refund_sandbox_result(
            engine,
            execution_id=refund_record.id,
            expected_idempotency_key=refund_record.idempotency_key,
            sandbox_result=sandbox_result,
        )

    return await fetch_refund_execution(
        engine,
        user_id=user_id,
        refund_application_id=refund_application_id,
    )


async def try_fail_refund_execution(engine: AsyncEngine, *, execution_id: str) -> bool:
    async with engine.begin() as connection:
        return await _try_fail_refund_execution_on_connection(
            connection,
            execution_id=execution_id,
        )


async def _try_fail_refund_execution_on_connection(
    connection: AsyncConnection,
    *,
    execution_id: str,
) -> bool:
    statement = text(
        """
        UPDATE refund_executions
        SET
            status = 'FAILED',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :execution_id
          AND status IN ('RUNNING', 'PROCESSING')
        RETURNING id
        """
    )

    result = await connection.execute(
        statement,
        {
            "execution_id": execution_id,
        },
    )
    return result.scalar_one_or_none() is not None


async def recover_refund_sandbox(
    engine: AsyncEngine,
    *,
    user_id: str,
    refund_application_id: str,
    adapter: RefundSandboxAdapter,
) -> RefundExecutionRecord | None:
    refund_record = await fetch_refund_execution(
        engine, user_id=user_id, refund_application_id=refund_application_id
    )

    if refund_record is None:
        return None

    if refund_record.status not in {"RUNNING", "PROCESSING"}:
        return refund_record

    sandbox_result = await adapter.get_result(refund_record.idempotency_key)

    if sandbox_result is not None:
        await _apply_refund_sandbox_result(
            engine,
            execution_id=refund_record.id,
            expected_idempotency_key=refund_record.idempotency_key,
            sandbox_result=sandbox_result,
            source="recovery",
            actor_user_id=user_id,
        )

    return await fetch_refund_execution(
        engine,
        user_id=user_id,
        refund_application_id=refund_application_id,
    )


async def _apply_refund_sandbox_result(
    engine: AsyncEngine,
    *,
    execution_id: str,
    expected_idempotency_key: str,
    sandbox_result: RefundSandboxResult,
    source: str = "execute",
    actor_user_id: str | None = None,
) -> bool:
    """在独立事务中校验并应用沙箱结果，供执行、恢复等现有调用链使用。

    正常退出 engine.begin() 才提交；校验或数据库操作抛错时回滚。
    需要与其他写操作一起提交的调用方，应使用下方接收 connection 的版本。
    """
    async with engine.begin() as connection:
        await set_refund_audit_context(connection, source=source, actor_user_id=actor_user_id)
        return await _apply_refund_sandbox_result_on_connection(
            connection,
            execution_id=execution_id,
            expected_idempotency_key=expected_idempotency_key,
            sandbox_result=sandbox_result,
        )


async def _apply_refund_sandbox_result_on_connection(
    connection: AsyncConnection,
    *,
    execution_id: str,
    expected_idempotency_key: str,
    sandbox_result: RefundSandboxResult,
) -> bool:
    """校验结果归属并按状态分派写入，加入调用方已经开启的事务。

    本函数不创建、提交、回滚或关闭连接；异常向外传播，由调用方处理事务。
    幂等键和成功引用号必须在写入前校验，防止把错误结果记到另一笔退款上。
    状态更新仍使用条件 SQL；没有更新行可表示已处理或旧通知，不视为新失败。
    返回 True 表示条件 UPDATE 更新了一行；False 表示没有符合条件的行。
    True 不代表事务已经提交，最终提交仍由调用方负责，后续异常仍可触发回滚。
    """
    if sandbox_result.idempotency_key != expected_idempotency_key:
        raise RuntimeError("Refund sandbox idempotency mismatch")

    # 三个分支共用传入的连接，不能调用会另开事务的 engine 包装函数。
    if sandbox_result.status == "SUCCEEDED":
        if not sandbox_result.provider_reference or not sandbox_result.provider_reference.strip():
            raise RuntimeError("Successful refund must have provider reference")

        applied = await _try_succeed_refund_execution_on_connection(
            connection,
            execution_id=execution_id,
            provider_reference=sandbox_result.provider_reference,
        )
    elif sandbox_result.status == "PROCESSING":
        applied = await _try_mark_refund_processing_on_connection(
            connection,
            execution_id=execution_id,
            provider_reference=sandbox_result.provider_reference,
        )
    elif sandbox_result.status == "FAILED":
        applied = await _try_fail_refund_execution_on_connection(
            connection, execution_id=execution_id
        )
    else:
        raise RuntimeError(f"Unexpected sandbox result status: {sandbox_result.status}")

    if not applied:
        await record_ignored_refund_result(
            connection, execution_id=execution_id, result=sandbox_result
        )
    return applied


async def fetch_refund_execution_by_idempotency_key(
    engine: AsyncEngine,
    *,
    idempotency_key: str,
) -> RefundExecutionRecord | None:
    """独立读取执行记录；需要共享事务时使用接收 connection 的版本。"""
    async with engine.connect() as connection:
        return await _fetch_refund_execution_by_idempotency_key_on_connection(
            connection, idempotency_key=idempotency_key
        )


async def _fetch_refund_execution_by_idempotency_key_on_connection(
    connection: AsyncConnection,
    *,
    idempotency_key: str,
) -> RefundExecutionRecord | None:
    """在调用方连接中读取记录，也能看到该事务尚未提交的状态更新。"""
    statement = text(
        """
        SELECT
            execution.id,
            execution.refund_application_id,
            execution.idempotency_key,
            execution.status,
            execution.amount,
            execution.currency,
            execution.provider_reference
        FROM refund_executions AS execution
        JOIN refund_applications AS application
          ON application.id = execution.refund_application_id
        WHERE execution.idempotency_key = :idempotency_key
        """
    )

    result = await connection.execute(statement, {"idempotency_key": idempotency_key})
    row = result.mappings().one_or_none()

    if row is None:
        return None

    return RefundExecutionRecord(
        id=row["id"],
        refund_application_id=row["refund_application_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        amount=row["amount"],
        currency=row["currency"],
        provider_reference=row["provider_reference"],
    )


async def apply_refund_webhook_result(
    engine: AsyncEngine,
    *,
    event: RefundWebhookEvent,
) -> RefundExecutionRecord | None:
    """在一个事务中去重事件、应用状态并读取结果，退出事务后才确认处理完成。

    event_id 标识通知，idempotency_key 标识退款；重复通知必须保持相同内容。
    未知或尚未领取的执行返回 None，且不保存事件，让提供方稍后安全重投。
    数据库错误向外传播，两项写入一起回滚；本函数不调用外部支付服务。
    """
    async with engine.begin() as connection:
        await set_refund_audit_context(
            connection, source="webhook", source_event_id=event.event_id
        )
        execution = await _fetch_refund_execution_by_idempotency_key_on_connection(
            connection, idempotency_key=event.result.idempotency_key
        )
        if execution is None or execution.status == "PENDING":
            return None

        # 同事件并发插入会在唯一约束处等待：对方提交后返回 False，回滚后可插入。
        inserted = await _record_refund_webhook_event_on_connection(connection, event=event)
        if inserted:
            await _apply_refund_sandbox_result_on_connection(
                connection,
                execution_id=execution.id,
                expected_idempotency_key=execution.idempotency_key,
                sandbox_result=event.result,
            )
        else:
            # 相同事件 ID 不能承载另一笔退款或另一种结果；冲突不能伪装为成功重试。
            result = await connection.execute(
                text(
                    "SELECT idempotency_key, status, provider_reference "
                    "FROM refund_webhook_events WHERE event_id = :event_id"
                ),
                {"event_id": event.event_id},
            )
            existing = result.mappings().one_or_none()
            if existing is None or (
                existing["idempotency_key"] != event.result.idempotency_key
                or existing["status"] != event.result.status
                or existing["provider_reference"] != event.result.provider_reference
            ):
                raise ValueError("Refund webhook event conflict")

        # 重投也要重新读取：最初的快照可能早于另一个请求的提交。
        # return 仍需等待 engine.begin() 正常退出；提交失败不会向路由报告成功。
        return await _fetch_refund_execution_by_idempotency_key_on_connection(
            connection, idempotency_key=execution.idempotency_key
        )


async def try_record_refund_webhook_event(
    engine: AsyncEngine,
    *,
    event: RefundWebhookEvent,
) -> bool:
    """独立写入事件的底层入口；webhook 编排应使用共享事务，不能先单独提交事件。"""
    async with engine.begin() as connection:
        return await _record_refund_webhook_event_on_connection(
            connection,
            event=event,
        )


async def _record_refund_webhook_event_on_connection(
    connection: AsyncConnection,
    *,
    event: RefundWebhookEvent,
) -> bool:
    """用事件主键去重插入，不结束调用方事务；False 只表示事件 ID 已存在。"""
    statement = text(
        """
        INSERT INTO refund_webhook_events (
            event_id,
            idempotency_key,
            status,
            provider_reference
        )
        VALUES (
            :event_id,
            :idempotency_key,
            :status,
            :provider_reference
        )
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """
    )

    result = await connection.execute(
        statement,
        {
            "event_id": event.event_id,
            "idempotency_key": event.result.idempotency_key,
            "status": event.result.status,
            "provider_reference": event.result.provider_reference,
        },
    )
    return result.scalar_one_or_none() is not None


async def fetch_refund_reconciliation_keys(
    engine: AsyncEngine,
    *,
    stale_before: datetime,
    limit: int = 50,
) -> list[str]:
    """读取需要核对的退款幂等键，不领取执行权，也不再次发起退款。

    stale_before 是调用方提供的带时区时间。
    返回结果只是查询时的快照，后续处理仍须检查最新状态。
    """
    if stale_before.utcoffset() is None:
        raise ValueError("stale_before must be timezone-aware")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    statement = text(
        """
        SELECT idempotency_key
        FROM refund_executions
        WHERE status IN ('RUNNING', 'PROCESSING')
          AND updated_at < :stale_before
        ORDER BY updated_at ASC, id ASC
        LIMIT :limit
        """
    )

    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {"stale_before": stale_before, "limit": limit},
        )
        return list(result.scalars().all())


async def reconcile_refund_sandbox(
    engine: AsyncEngine,
    *,
    idempotency_key: str,
    adapter: RefundSandboxAdapter,
) -> RefundExecutionRecord | None:
    """核对单笔退款，只查询提供方结果，不重新发起退款。"""
    record = await fetch_refund_execution_by_idempotency_key(
        engine, idempotency_key=idempotency_key
    )
    if record is None:
        return None
    if record.status not in {"RUNNING", "PROCESSING"}:
        return record

    # 前面的查询连接已经释放，等待沙箱期间不占用数据库连接。
    sandbox_result = await adapter.get_result(record.idempotency_key)

    if sandbox_result is not None:
        await _apply_refund_sandbox_result(
            engine,
            execution_id=record.id,
            expected_idempotency_key=record.idempotency_key,
            sandbox_result=sandbox_result,
            source="reconciliation",
        )

    # 即使提供方暂时没有结果，也重新读取，避免返回等待前的旧快照。
    return await fetch_refund_execution_by_idempotency_key(
        engine, idempotency_key=record.idempotency_key
    )


async def reconcile_refund_batch(
    engine: AsyncEngine,
    *,
    stale_before: datetime,
    adapter: RefundSandboxAdapter,
    limit: int = 50,
) -> dict[str, int]:
    """顺序核对一批退款，隔离单笔错误，不重新发起退款。

    checked 表示核对调用正常返回，不代表退款成功。
    errors 表示核对调用抛错，不代表退款失败。
    失败日志只记录本次核对的幂等键与异常类名，退款状态仍以数据库为准。
    """
    # 扫描失败应直接抛出，不能伪装成“没有待核对记录”。
    keys = await fetch_refund_reconciliation_keys(engine, stale_before=stale_before, limit=limit)
    counts = {"checked": 0, "errors": 0}

    for key in keys:
        try:
            await reconcile_refund_sandbox(engine, idempotency_key=key, adapter=adapter)
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            counts["errors"] += 1
            # key/error 属于本次调用；不记录异常正文或堆栈，避免泄露 SQL 和支付响应。
            logger.warning(
                "Refund reconciliation failed: idempotency_key=%s error_type=%s",
                key,
                type(error).__name__,
            )
        else:
            counts["checked"] += 1

    return counts
