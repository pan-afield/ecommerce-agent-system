from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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
