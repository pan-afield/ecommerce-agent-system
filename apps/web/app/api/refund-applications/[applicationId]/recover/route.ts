import { isRefundExecution } from "@/lib/refund-contract";
import { isSafeRefundId, proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";

interface RouteContext {
  params: Promise<{ applicationId: string }>;
}

export async function POST(_request: Request, context: RouteContext) {
  const applicationId = (await context.params).applicationId.trim();
  if (!isSafeRefundId(applicationId)) {
    return refundErrorResponse(400, "refund_invalid_request", "退款申请 ID 格式无效。");
  }

  return proxyRefundRequest({
    isSuccessBody: isRefundExecution,
    method: "POST",
    path: `/v1/refund-applications/${encodeURIComponent(applicationId)}/recover`,
    unknownOutcomeOnTimeout: true,
  });
}
