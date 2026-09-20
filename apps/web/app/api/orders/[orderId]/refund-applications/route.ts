import { isRefundApplication } from "@/lib/refund-contract";
import {
  isRefundAmount,
  isRefundCurrency,
  isSafeRefundId,
  proxyRefundRequest,
  refundErrorResponse,
} from "@/lib/refund-bff";
import { ORDER_ID_MAX_LENGTH } from "@/types/order";
import { REFUND_REQUEST_ID_MAX_LENGTH } from "@/types/refund";

interface RouteContext {
  params: Promise<{ orderId: string }>;
}

export async function POST(request: Request, context: RouteContext) {
  const orderId = (await context.params).orderId.trim();
  if (!isSafeRefundId(orderId, ORDER_ID_MAX_LENGTH)) {
    return refundErrorResponse(400, "refund_invalid_request", "订单 ID 格式无效。");
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return refundErrorResponse(400, "refund_invalid_request", "请求 JSON 无效。");
  }
  if (
    typeof body !== "object" ||
    body === null ||
    !("request_id" in body) ||
    !("requested_amount" in body) ||
    !("requested_currency" in body) ||
    typeof body.request_id !== "string" ||
    !isSafeRefundId(body.request_id.trim(), REFUND_REQUEST_ID_MAX_LENGTH) ||
    !isRefundAmount(body.requested_amount) ||
    !isRefundCurrency(body.requested_currency)
  ) {
    return refundErrorResponse(400, "refund_invalid_request", "退款申请参数格式无效。");
  }

  return proxyRefundRequest({
    body: {
      request_id: body.request_id.trim(),
      requested_amount: body.requested_amount,
      requested_currency: body.requested_currency.toUpperCase(),
    },
    isSuccessBody: isRefundApplication,
    method: "POST",
    path: `/v1/orders/${encodeURIComponent(orderId)}/refund-applications`,
  });
}
