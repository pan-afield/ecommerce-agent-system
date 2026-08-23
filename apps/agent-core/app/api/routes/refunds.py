import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import get_current_refund_approver_id, get_current_user_id
from app.services.refund import (
    confirm_refund_application,
    fetch_refund_application_by_id,
    try_review_refund_application,
)

router = APIRouter(
    prefix="/v1/refund-applications",
    tags=["refunds"],
)
logger = logging.getLogger(__name__)


class RefundConfirmationResponse(BaseModel):
    id: str
    order_id: str
    request_id: str
    requested_amount: Decimal
    currency: str
    status: str


@router.post(
    "/{application_id}/confirm",
    response_model=RefundConfirmationResponse,
)
async def confirm_refund(
    application_id: str,
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
) -> RefundConfirmationResponse:
    engine = cast(AsyncEngine, request.app.state.database_engine)

    # 先尝试原子状态转换；审批人身份已经由 dependency 校验。
    try:
        application = await confirm_refund_application(
            engine,
            application_id=application_id,
            user_id=current_user_id,
        )
    except SQLAlchemyError as error:
        logger.warning(
            "Refund confirmation database operation failed: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款服务暂时不可用，请稍后重试。",
        ) from error

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="退款申请不存在。",
        )

    return RefundConfirmationResponse(
        id=application.id,
        order_id=application.order_id,
        request_id=application.request_id,
        requested_amount=application.requested_amount,
        currency=application.currency,
        status=application.status,
    )


class RefundReviewPayload(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    review_note: str | None = Field(default=None, max_length=500)

    @field_validator("review_note", mode="before")
    @classmethod
    def normalize_review_note(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None

        return value


class RefundReviewResponse(BaseModel):
    id: str
    order_id: str
    request_id: str
    requested_amount: Decimal
    currency: str
    status: str
    reviewed_by_user_id: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


# 审批
@router.post(
    "/{application_id}/review",
    response_model=RefundReviewResponse,
)
async def review_refund(
    application_id: str,
    reviewer_user_id: Annotated[
        str,
        Depends(get_current_refund_approver_id),
    ],
    request: Request,
    payload: RefundReviewPayload,
) -> RefundReviewResponse:
    engine = cast(AsyncEngine, request.app.state.database_engine)

    # 原子更新只回答“本次是否成功改状态”；失败后必须读取当前记录，
    # 才能把数据库结果翻译成 404、幂等成功或 409 冲突。
    try:
        reviewed = await try_review_refund_application(
            engine,
            application_id=application_id,
            reviewer_user_id=reviewer_user_id,
            decision=payload.decision,
            review_note=payload.review_note,
        )

        if not reviewed:
            # 另一个请求可能已经完成决定，也可能 application_id 根本不存在。
            application = await fetch_refund_application_by_id(
                engine,
                application_id=application_id,
            )

            if application is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="退款申请不存在。",
                )
            if application.status != payload.decision:
                # 相反决定不能覆盖第一次审批；相同决定则继续返回已保存记录。
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="退款申请已被其他审批人处理，请刷新后重试。",
                )
        else:
            # 本次成功抢到状态转换，再读取一次完整审计字段作为响应。
            application = await fetch_refund_application_by_id(
                engine,
                application_id=application_id,
            )
    except SQLAlchemyError as error:
        logger.warning(
            "Refund review database operation failed: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款服务暂时不可用，请稍后重试。",
        ) from error

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="退款申请不存在。",
        )

    return RefundReviewResponse(
        id=application.id,
        order_id=application.order_id,
        request_id=application.request_id,
        requested_amount=application.requested_amount,
        currency=application.currency,
        status=application.status,
        reviewed_by_user_id=application.reviewed_by_user_id,
        reviewed_at=application.reviewed_at,
        review_note=application.review_note,
    )
