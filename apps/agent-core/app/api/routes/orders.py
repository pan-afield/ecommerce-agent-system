import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import get_current_user_id
from app.services.orders import OrderDetailResponse, fetch_owned_order

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
