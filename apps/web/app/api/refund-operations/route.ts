import { isRefundOperationQueue } from "@/lib/refund-contract";
import { proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";

export async function GET(request: Request) {
  const rawLimit = new URL(request.url).searchParams.get("limit") ?? "50";
  if (!/^\d+$/.test(rawLimit) || Number(rawLimit) < 1 || Number(rawLimit) > 100) {
    return refundErrorResponse(400, "refund_invalid_request", "查询数量必须在 1 到 100 之间。");
  }

  return proxyRefundRequest({
    isSuccessBody: isRefundOperationQueue,
    method: "GET",
    path: `/v1/refund-operations?limit=${rawLimit}`,
  });
}
