import logging
from decimal import Decimal
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import get_current_user_id
from app.services.orders import OrderDetailResponse, fetch_owned_order
from app.services.refund import (
    RefundApplication,
    RefundOrderSnapshot,
    assess_refund,
    build_refund_request,
    fetch_non_rejected_refund_application_by_order,
    fetch_refund_application_by_request_id,
    matches_existing_refund_application,
    try_create_refund_application,
)

RefundCurrency = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=3,
        to_upper=True,
    ),
]


class RefundAssessmentPayload(BaseModel):
    requested_amount: Decimal
    requested_currency: RefundCurrency


class RefundAssessmentResponse(BaseModel):
    eligible_for_review: bool
    reason: str
    requires_customer_confirmation: bool


router = APIRouter(prefix="/v1/orders", tags=["orders"])
logger = logging.getLogger(__name__)


async def load_owned_order(
    order_id: str,
    request: Request,
    current_user_id: Annotated[
        str,
        Depends(get_current_user_id),
    ],
) -> OrderDetailResponse | None:
    engine = cast(
        AsyncEngine,
        request.app.state.database_engine,
    )

    try:
        return await fetch_owned_order(
            engine,
            order_id,
            current_user_id,
        )
    except SQLAlchemyError as error:
        logger.warning(
            "Order database query failed: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="订单服务暂时不可用，请稍后重试。",
        ) from error


@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
)
async def get_order(
    order: Annotated[
        OrderDetailResponse | None,
        Depends(load_owned_order),
    ],
) -> OrderDetailResponse:
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在。",
        )

    return order


@router.post(
    "/{order_id}/refund-assessment",
    response_model=RefundAssessmentResponse,
)
async def assess_order_refund(
    order_id: str,
    payload: RefundAssessmentPayload,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    order: Annotated[
        OrderDetailResponse | None,
        Depends(load_owned_order),
    ],
) -> RefundAssessmentResponse:
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在。",
        )

    # 第一步：保留用户本次提交的退款金额和币种。
    application = RefundApplication(
        order_id=order_id,
        requested_amount=payload.requested_amount,
        requested_currency=payload.requested_currency,
    )

    # 第二步：提取订单的可信事实，供后续规则校验使用。
    order_snapshot = RefundOrderSnapshot(
        user_id=current_user_id,
        status=order.status.upper(),
        total_amount=order.total_amount,
        currency=order.currency,
    )

    # 第三步：合并用户申请与订单事实，转换为业务层评估请求。
    refund_request = build_refund_request(
        application,
        order_snapshot,
        current_user_id,
    )
    assessment = assess_refund(refund_request)

    return RefundAssessmentResponse(
        eligible_for_review=assessment.eligible_for_review,
        reason=assessment.reason,
        requires_customer_confirmation=assessment.requires_customer_confirmation,
    )


RefundRequestId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
    ),
]


class RefundApplicationPayload(BaseModel):
    request_id: RefundRequestId
    requested_amount: Decimal
    requested_currency: RefundCurrency


class RefundApplicationResponse(BaseModel):
    id: str
    order_id: str
    request_id: str
    requested_amount: Decimal
    currency: str
    status: str
    created: bool


@router.post(
    "/{order_id}/refund-applications",
    response_model=RefundApplicationResponse,
)
async def submit_refund_application(
    order_id: str,
    payload: RefundApplicationPayload,
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    order: Annotated[
        OrderDetailResponse | None,
        Depends(load_owned_order),
    ],
) -> RefundApplicationResponse:
    # 第一步：确认订单存在且属于当前登录用户。
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在。",
        )

    # 第二步：分别保存用户提交的申请参数和数据库中的订单事实。
    application = RefundApplication(
        order_id=order_id,
        requested_amount=payload.requested_amount,
        requested_currency=payload.requested_currency,
    )

    order_snapshot = RefundOrderSnapshot(
        user_id=current_user_id,
        status=order.status.upper(),
        total_amount=order.total_amount,
        currency=order.currency,
    )

    # 第三步：组装业务请求，并执行退款资格校验。
    refund_request = build_refund_request(
        application,
        order_snapshot,
        current_user_id,
    )
    assessment = assess_refund(refund_request)

    if not assessment.eligible_for_review:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=assessment.reason
        )

    # 第四步：生成本次申请 ID，准备使用数据库约束保证幂等。
    # application_id 可以每次重试都不同；真正的幂等边界是当前用户 + request_id。
    engine = cast(AsyncEngine, request.app.state.database_engine)
    application_id = str(uuid4())

    try:
        # 第五步：尝试创建申请；相同用户和 request_id 的重复提交不会重复插入。
        # 数据库负责竞争，Python 只根据返回值决定是否读取原申请。
        created = await try_create_refund_application(
            engine,
            application_id=application_id,
            user_id=current_user_id,
            request_id=payload.request_id,
            application=application,
        )

        if created is True:
            # 首次提交创建成功，等待客户确认后再进入后续退款流程。
            return RefundApplicationResponse(
                id=application_id,
                order_id=application.order_id,
                request_id=payload.request_id,
                requested_amount=application.requested_amount,
                currency=application.requested_currency,
                status="AWAITING_CUSTOMER_CONFIRMATION",
                created=True,
            )
        else:
            # 未创建通常表示幂等键已存在，读取原申请以判断重复提交内容。
            # 读取时仍带 user_id，避免借 request_id 枚举其他用户的申请。
            existing = await fetch_refund_application_by_request_id(
                engine,
                user_id=current_user_id,
                request_id=payload.request_id,
            )
            if existing is None:
                # request_id 不同：说明可能命中了同订单非拒绝申请约束。
                existing = await fetch_non_rejected_refund_application_by_order(
                    engine,
                    user_id=current_user_id,
                    order_id=order_id,
                )

                if existing is None:
                    # INSERT 报告冲突，但两种查询都找不到记录，属于异常状态。
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="退款服务暂时不可用，请稍后重试。",
                    )
            elif not matches_existing_refund_application(existing, application):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="退款请求幂等键已用于其他申请。",
                )
            # 参数一致，返回原申请，保证重复请求具有幂等结果。
            return RefundApplicationResponse(
                id=existing.id,
                order_id=existing.order_id,
                request_id=existing.request_id,
                requested_amount=existing.requested_amount,
                currency=existing.currency,
                status=existing.status,
                created=False,
            )

    except SQLAlchemyError as error:
        # 第六步：将数据库异常转换为统一的服务不可用响应。
        logger.warning(
            "Refund application database operation failed: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="退款服务暂时不可用，请稍后重试。",
        ) from error


class CurrentRefundApplicationResponse(BaseModel):
    id: str
    order_id: str
    request_id: str
    requested_amount: Decimal
    currency: str
    status: str


@router.get(
    "/{order_id}/refund-application",
    response_model=CurrentRefundApplicationResponse,
)
async def get_current_refund_application(
    order_id: str,
    request: Request,
    current_user_id: Annotated[str, Depends(get_current_user_id)],
    order: Annotated[
        OrderDetailResponse | None,
        Depends(load_owned_order),
    ],
) -> CurrentRefundApplicationResponse:
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在。",
        )

    engine = cast(AsyncEngine, request.app.state.database_engine)

    try:
        application = await fetch_non_rejected_refund_application_by_order(
            engine,
            user_id=current_user_id,
            order_id=order_id,
        )

    except SQLAlchemyError as error:
        logger.warning(
            "Refund application database query failed: error_type=%s",
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

    return CurrentRefundApplicationResponse(
        id=application.id,
        order_id=application.order_id,
        request_id=application.request_id,
        requested_amount=application.requested_amount,
        currency=application.currency,
        status=application.status,
    )
