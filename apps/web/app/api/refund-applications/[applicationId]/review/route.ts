import { isRefundApplication } from "@/lib/refund-contract";
import { isSafeRefundId, proxyRefundRequest, refundErrorResponse } from "@/lib/refund-bff";
import { REFUND_REVIEW_NOTE_MAX_LENGTH, type RefundReviewDecision } from "@/types/refund";

interface RouteContext {
  params: Promise<{ applicationId: string }>;
}

export async function POST(request: Request, context: RouteContext) {
  const applicationId = (await context.params).applicationId.trim();
  if (!isSafeRefundId(applicationId, 128)) {
    return refundErrorResponse(400, "refund_invalid_request", "退款申请 ID 格式无效。");
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
    !("decision" in body) ||
    (body.decision !== "APPROVED" && body.decision !== "REJECTED") ||
    ("review_note" in body &&
      body.review_note !== undefined &&
      body.review_note !== null &&
      (typeof body.review_note !== "string" ||
        Array.from(body.review_note).length > REFUND_REVIEW_NOTE_MAX_LENGTH))
  ) {
    return refundErrorResponse(400, "refund_invalid_request", "审批决定或备注格式无效。");
  }

  const decision: RefundReviewDecision = body.decision;
  const reviewNote =
    "review_note" in body && typeof body.review_note === "string"
      ? body.review_note.trim() || null
      : null;
  return proxyRefundRequest({
    body: { decision, review_note: reviewNote },
    isSuccessBody: isRefundApplication,
    method: "POST",
    path: `/v1/refund-applications/${encodeURIComponent(applicationId)}/review`,
  });
}
