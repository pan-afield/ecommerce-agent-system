import { isRefundApplication } from "@/lib/refund-contract";
import { isSafeRefundId, proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";
import { ORDER_ID_MAX_LENGTH } from "@/types/order";

interface RouteContext {
  params: Promise<{ orderId: string }>;
}

export async function GET(_request: Request, context: RouteContext) {
  const orderId = (await context.params).orderId.trim();
  if (!isSafeRefundId(orderId, ORDER_ID_MAX_LENGTH)) {
    return refundErrorResponse(400, "refund_invalid_request", "订单 ID 格式无效。");
  }

  return proxyRefundRequest({
    auth: "customer",
    isSuccessBody: isRefundApplication,
    method: "GET",
    path: `/v1/orders/${encodeURIComponent(orderId)}/refund-application`,
  });
}
