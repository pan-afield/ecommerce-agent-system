from decimal import Decimal

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


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


async def fetch_owned_order(
    engine: AsyncEngine,
    order_id: str,
    user_id: str,
) -> OrderDetailResponse | None:
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
    async with engine.connect() as connection:
        result = await connection.execute(
            statement,
            {
                "order_id": order_id,
                "user_id": user_id,
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
