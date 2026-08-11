export const ORDER_ID_MAX_LENGTH = 100;

export const PROXY_ORDER_ERROR_CODES = [
  "order_invalid_request",
  "order_not_found",
  "order_service_unavailable",
  "order_upstream_unreachable",
  "order_upstream_timeout",
  "order_invalid_upstream_response",
] as const;

export const CLIENT_ORDER_ERROR_CODES = [
  "order_network_error",
  "order_invalid_response",
] as const;

export type ProxyOrderErrorCode = (typeof PROXY_ORDER_ERROR_CODES)[number];
export type ClientOrderErrorCode = (typeof CLIENT_ORDER_ERROR_CODES)[number];
export type OrderErrorCode = ProxyOrderErrorCode | ClientOrderErrorCode;

export interface ShipmentEvent {
  id: string;
  status: string;
  description: string;
  location: string | null;
  occurred_at: string;
}

export interface OrderDetail {
  id: string;
  order_number: string;
  status: string;
  total_amount: string;
  currency: string;
  created_at: string;
  shipment_events: ShipmentEvent[];
}

export interface OrderErrorDetail {
  code: OrderErrorCode;
  message: string;
}

export interface OrderError {
  error: OrderErrorDetail;
}
