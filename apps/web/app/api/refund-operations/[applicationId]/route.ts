import { isRefundOperationDetail } from "@/lib/refund-contract";
import { isSafeRefundId, proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";

interface RouteContext {
  params: Promise<{ applicationId: string }>;
}

export async function GET(request: Request, context: RouteContext) {
  const applicationId = (await context.params).applicationId.trim();
  const search = new URL(request.url).searchParams;
  const afterId = search.get("after_id") ?? "0";
  const limit = search.get("limit") ?? "50";
  if (!isSafeRefundId(applicationId)) {
    return refundErrorResponse(400, "refund_invalid_request", "退款申请 ID 格式无效。");
  }
  if (!/^\d+$/.test(afterId)) {
    return refundErrorResponse(400, "refund_invalid_request", "审计游标格式无效。");
  }
  if (!/^\d+$/.test(limit) || Number(limit) < 1 || Number(limit) > 100) {
    return refundErrorResponse(400, "refund_invalid_request", "查询数量必须在 1 到 100 之间。");
  }

  return proxyRefundRequest({
    isSuccessBody: isRefundOperationDetail,
    method: "GET",
    path: `/v1/refund-operations/${encodeURIComponent(applicationId)}?after_id=${afterId}&limit=${limit}`,
  });
}
