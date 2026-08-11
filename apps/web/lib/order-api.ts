import { isOrderDetail, isOrderError } from "@/lib/order-contract";
import type { OrderDetail, OrderErrorCode } from "@/types/order";

export class OrderApiError extends Error {
  readonly code: OrderErrorCode;
  readonly status: number;

  constructor(code: OrderErrorCode, message: string, status: number) {
    super(message);
    this.name = "OrderApiError";
    this.code = code;
    this.status = status;
  }
}

export async function getOrder(orderId: string): Promise<OrderDetail> {
  const normalizedOrderId = orderId.trim();
  if (!normalizedOrderId) {
    throw new OrderApiError("order_invalid_request", "请输入订单 ID。", 400);
  }

  let response: Response;
  try {
    response = await fetch(`/api/orders/${encodeURIComponent(normalizedOrderId)}`, {
      method: "GET",
      cache: "no-store",
    });
  } catch {
    throw new OrderApiError(
      "order_network_error",
      "网络连接失败，请检查连接后重试。",
      0,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new OrderApiError(
      "order_invalid_response",
      "订单服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  if (!response.ok) {
    if (isOrderError(body)) {
      throw new OrderApiError(body.error.code, body.error.message, response.status);
    }

    throw new OrderApiError(
      "order_invalid_response",
      "订单服务返回了无法识别的错误，请稍后重试。",
      response.status,
    );
  }

  if (!isOrderDetail(body)) {
    throw new OrderApiError(
      "order_invalid_response",
      "订单服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  return body;
}
