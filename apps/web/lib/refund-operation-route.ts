import { isSafeRefundId, proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";
import { REFUND_REVIEW_NOTE_MAX_LENGTH, type RefundOperationAction } from "@/types/refund";

interface RouteContext {
  params: Promise<{ applicationId: string }>;
}

export async function proxyRefundOperationAction(
  request: Request,
  context: RouteContext,
  action: RefundOperationAction,
) {
  const applicationId = (await context.params).applicationId.trim();
  if (!isSafeRefundId(applicationId)) {
    return refundErrorResponse(400, "refund_invalid_request", "退款申请 ID 格式无效。");
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return refundErrorResponse(400, "refund_invalid_request", "请求 JSON 无效。");
  }
  const note =
    typeof body === "object" && body !== null && "note" in body && typeof body.note === "string"
      ? body.note.trim()
      : "";
  if (note.length < 1 || Array.from(note).length > REFUND_REVIEW_NOTE_MAX_LENGTH) {
    return refundErrorResponse(400, "refund_invalid_request", "处理备注必须为 1 到 500 个字符。");
  }

  return proxyRefundRequest({
    body: { note },
    method: "POST",
    path: `/v1/refund-operations/${encodeURIComponent(applicationId)}/${action}`,
    successStatus: 204,
  });
}
