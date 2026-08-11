import logging
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

router = APIRouter(prefix="/v1/orders", tags=["orders"])
logger = logging.getLogger(__name__)


class ShipmentEventResponse(BaseModel):
    id: str
    status: str
    description: str
    location: str | None
    occurred_at: AwareDatetime


class OrderDetailResponse(BaseModel):
    id: str
    order_number: str
    status: str
    total_amount: Decimal
    currency: str
    created_at: AwareDatetime
    shipment_events: list[ShipmentEventResponse]


def get_demo_user_id(request: Request) -> str:
    """Temporary V0.2 identity boundary; replace with authenticated claims later."""
    settings = cast(Settings, request.app.state.settings)
    return settings.demo_user_id


async def load_owned_order(
    order_id: str,
    request: Request,
    demo_user_id: Annotated[
        str,
        Depends(get_demo_user_id),
    ],
) -> OrderDetailResponse | None:
    engine = cast(
        AsyncEngine,
        request.app.state.database_engine,
    )

    statement = text(
        """
        SELECT
            id,
            order_number,
            LOWER(status::text) AS status,
            total_amount,
            currency,
            created_at
        FROM orders
        WHERE id = :order_id
          AND user_id = :user_id
        """
    )

    shipment_statement = text(
        """
            SELECT
                id,
                status,
                description,
                location,
                occurred_at
            FROM shipment_events
            WHERE order_id = :order_id
            ORDER BY occurred_at ASC, id ASC
            """
    )

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                statement,
                {
                    "order_id": order_id,
                    "user_id": demo_user_id,
                },
            )
            order = result.mappings().one_or_none()

            if order is None:
                return None
            shipment_result = await connection.execute(
                shipment_statement,
                {"order_id": order_id},
            )
            shipment_rows = shipment_result.mappings().all()
    except SQLAlchemyError as error:
        logger.warning(
            "Order database query failed: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="订单服务暂时不可用，请稍后重试。",
        ) from error

    shipment_events = [
        ShipmentEventResponse(
            id=event["id"],
            status=event["status"],
            description=event["description"],
            location=event["location"],
            occurred_at=event["occurred_at"],
        )
        for event in shipment_rows
    ]

    return OrderDetailResponse(
        id=order["id"],
        order_number=order["order_number"],
        status=order["status"],
        total_amount=order["total_amount"],
        currency=order["currency"],
        created_at=order["created_at"],
        shipment_events=shipment_events,
    )


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
