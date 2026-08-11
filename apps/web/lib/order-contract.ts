import {
  CLIENT_ORDER_ERROR_CODES,
  PROXY_ORDER_ERROR_CODES,
  type OrderDetail,
  type OrderError,
  type OrderErrorCode,
  type ShipmentEvent,
} from "@/types/order";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isShipmentEvent(value: unknown): value is ShipmentEvent {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    isNonEmptyString(value.status) &&
    isNonEmptyString(value.description) &&
    (value.location === null || isNonEmptyString(value.location)) &&
    isNonEmptyString(value.occurred_at)
  );
}

export function isOrderDetail(value: unknown): value is OrderDetail {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    isNonEmptyString(value.order_number) &&
    isNonEmptyString(value.status) &&
    isNonEmptyString(value.total_amount) &&
    isNonEmptyString(value.currency) &&
    isNonEmptyString(value.created_at) &&
    Array.isArray(value.shipment_events) &&
    value.shipment_events.every(isShipmentEvent)
  );
}

export function isOrderErrorCode(value: unknown): value is OrderErrorCode {
  return (
    typeof value === "string" &&
    [...PROXY_ORDER_ERROR_CODES, ...CLIENT_ORDER_ERROR_CODES].includes(
      value as OrderErrorCode,
    )
  );
}

export function isOrderError(value: unknown): value is OrderError {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    isOrderErrorCode(value.error.code) &&
    isNonEmptyString(value.error.message)
  );
}

export function getBackendOrderDetail(value: unknown): string | null {
  if (!isRecord(value) || !isNonEmptyString(value.detail)) {
    return null;
  }

  return value.detail;
}
